"""Per-service SQLite index of what EOS knows about a project.

Derived, never a source. Markdown notes in git stay the source of truth and the
way teammates share knowledge -- a predecessor kept knowledge in one committed
JSON file and every merge conflicted on it. This database is rebuilt from
scratch out of the notes, the brain docs, the graph and git history, written
to a temp file and moved onto `.eos/data/eos.db` in one
step, so it is always safe to delete and a crash never leaves half of one.

`.eos/eos.db` is a different, dead file left by an unrelated tool. Nothing here
reads or writes it.
"""
from __future__ import annotations

import dataclasses
import datetime
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import time
from pathlib import Path

from core import extensions
from core import inspector
from core import links
from core import notes
from core.lib.config_io import ConfigIO

SCHEMA_VERSION = 5

# Search results scoring below this fraction of the best BM25 score are cut.
# The OR query (0.35.0) made a question in a sentence find its answer; it also
# made every row sharing one word a result. Swept on the golden set over a
# real 106-note service index: recall@1/@5 unchanged at every floor up to 0.5,
# mean rows per query 20 -> 6.8 at 0.5. BM25 is on its own scale, so this is
# not the same number as notes.SCORE_FLOOR and is not meant to be.
SCORE_FLOOR = 0.5

# Bounds measured on the 18 FM repos: the longest history is 1,339 commits and
# only one commit anywhere touches more than 200 files (430).
MAX_COMMITS = 2000
MAX_FILES_PER_COMMIT = 200

# Default: the conventional JIRA-style key (PROJ-123). Projects with a
# different convention override it with [index] ticket_pattern in
# .eos/config.toml -- see _ticket_pattern().
TICKET_PATTERN = r"\b[A-Z][A-Z0-9]+-[0-9]+\b"

_VERSION_FILE = Path(__file__).resolve().parent / "VERSION"

_SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE build_issue (source TEXT NOT NULL, ref TEXT, problem TEXT NOT NULL);

CREATE TABLE note (
    id INTEGER PRIMARY KEY,
    file TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    created TEXT,
    updated TEXT,
    source TEXT,
    session TEXT,
    generated INTEGER NOT NULL,
    body TEXT NOT NULL,
    sha256 TEXT NOT NULL
);
CREATE TABLE note_tag (
    note_id INTEGER NOT NULL REFERENCES note(id),
    tag TEXT NOT NULL,
    PRIMARY KEY (note_id, tag)
) WITHOUT ROWID;
CREATE INDEX note_tag_by_tag ON note_tag(tag);
CREATE TABLE note_scope (
    note_id INTEGER NOT NULL REFERENCES note(id),
    ord INTEGER NOT NULL,
    entry TEXT NOT NULL,
    hash TEXT,
    PRIMARY KEY (note_id, ord)
) WITHOUT ROWID;
CREATE INDEX note_scope_by_entry ON note_scope(entry);

-- What sessions took on, folded from the append-only ledger (ADR-020). The
-- ledger stays the source of truth and `eos work` reads it directly; this is
-- here so work can be *joined* -- against the commits naming its ticket,
-- against an extension's own rows, against the notes written while it ran.
--
-- Deliberately no `stale` column. Staleness is a question about now, and a
-- value computed at build time would answer it with the moment the index was
-- built instead -- the confidently-wrong shape the rest of this schema is
-- arranged to avoid. `updated_at` is here; the caller compares it to its own
-- clock.
CREATE TABLE work_item (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    status TEXT NOT NULL,
    ticket TEXT,
    opened_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    holder_count INTEGER NOT NULL,
    reason TEXT,
    last TEXT,
    events INTEGER NOT NULL
);
CREATE INDEX work_item_by_status ON work_item(status);
CREATE INDEX work_item_by_ticket ON work_item(ticket);
-- One row per session still holding an item. More than one is a collision,
-- and it is a row rather than a rendered string so "what is this session
-- holding" is a query rather than a LIKE.
CREATE TABLE work_holder (
    item TEXT NOT NULL REFERENCES work_item(id),
    session TEXT NOT NULL,
    agent TEXT,
    claimed_at TEXT,
    PRIMARY KEY (item, session)
) WITHOUT ROWID;
CREATE INDEX work_holder_by_session ON work_holder(session);
CREATE TABLE work_event (
    item TEXT NOT NULL REFERENCES work_item(id),
    ord INTEGER NOT NULL,
    event TEXT NOT NULL,
    at TEXT NOT NULL,
    session TEXT,
    agent TEXT,
    body TEXT,
    branch TEXT,
    commit_sha TEXT,
    PRIMARY KEY (item, ord)
) WITHOUT ROWID;
CREATE INDEX work_event_by_session ON work_event(session);

-- What a session did (ADR-022). Folded from executions.jsonl by the same
-- code the CLI reads it with. Like work_item, no column answers a question
-- about now: an open execution is one with no outcome, and how long it has
-- been open is the caller's arithmetic against its own clock.
CREATE TABLE execution (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    procedure TEXT,
    work_item TEXT,
    session TEXT,
    agent TEXT,
    started_at TEXT,
    finished_at TEXT,
    outcome TEXT,
    target TEXT,
    branch TEXT,
    commit_start TEXT,
    commit_end TEXT,
    lesson TEXT,
    events INTEGER NOT NULL
);
CREATE INDEX execution_by_session ON execution(session);
CREATE INDEX execution_by_procedure ON execution(procedure);
CREATE INDEX execution_by_target ON execution(target);
CREATE TABLE execution_event (
    execution TEXT NOT NULL REFERENCES execution(id),
    ord INTEGER NOT NULL,
    at TEXT NOT NULL,
    kind TEXT NOT NULL,
    tool TEXT,
    target TEXT,
    ref TEXT,
    exit_code INTEGER,
    ms INTEGER,
    body TEXT,
    session TEXT,
    agent TEXT,
    source TEXT,
    status TEXT,
    tool_use_id TEXT,
    PRIMARY KEY (execution, ord)
) WITHOUT ROWID;
CREATE INDEX execution_event_by_tool ON execution_event(tool);
CREATE INDEX execution_event_by_session ON execution_event(session);
-- What was run for a behaviour code and what came back (ADR-018), folded from
-- verifications.jsonl. Joinable to the run it happened in (ADR-024) and
-- searchable, which it was not until an audit asked why.
CREATE TABLE verification (
    ord INTEGER PRIMARY KEY,
    code TEXT NOT NULL,
    outcome TEXT NOT NULL,
    command TEXT NOT NULL,
    exit_code INTEGER,
    recorded_at TEXT NOT NULL,
    commit_sha TEXT,
    log TEXT,
    verdict TEXT,
    note TEXT,
    session TEXT,
    execution TEXT
);
CREATE INDEX verification_by_code ON verification(code);
CREATE INDEX verification_by_execution ON verification(execution);

CREATE TABLE brain_doc (name TEXT PRIMARY KEY, content TEXT NOT NULL, sha256 TEXT NOT NULL);
CREATE TABLE node (
    nid INTEGER PRIMARY KEY,
    id TEXT NOT NULL UNIQUE,
    type TEXT NOT NULL,
    label TEXT,
    path TEXT NOT NULL,
    dir TEXT NOT NULL,
    language TEXT,
    origin TEXT NOT NULL,
    is_entry INTEGER NOT NULL,
    doc TEXT
);
CREATE INDEX node_by_path ON node(path);
CREATE INDEX node_by_label ON node(label);
CREATE TABLE node_symbol (
    nid INTEGER NOT NULL REFERENCES node(nid),
    role TEXT NOT NULL,
    name TEXT NOT NULL,
    PRIMARY KEY (nid, role, name)
) WITHOUT ROWID;
CREATE INDEX node_symbol_by_name ON node_symbol(name);
CREATE TABLE edge (
    src INTEGER NOT NULL REFERENCES node(nid),
    dst INTEGER NOT NULL REFERENCES node(nid),
    kind TEXT NOT NULL,
    imported TEXT NOT NULL,
    PRIMARY KEY (src, dst, kind, imported)
) WITHOUT ROWID;
CREATE INDEX edge_by_dst ON edge(dst, kind);

CREATE TABLE git_commit (
    sha TEXT PRIMARY KEY,
    ord INTEGER NOT NULL,
    parents TEXT NOT NULL,
    author_name TEXT,
    author_email TEXT,
    authored_at TEXT,
    committed_at TEXT,
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    is_merge INTEGER NOT NULL,
    pr INTEGER,
    files_total INTEGER NOT NULL
);
CREATE INDEX git_commit_by_ord ON git_commit(ord);
CREATE TABLE git_commit_file (
    sha TEXT NOT NULL REFERENCES git_commit(sha),
    path TEXT NOT NULL,
    PRIMARY KEY (sha, path)
) WITHOUT ROWID;
CREATE INDEX git_commit_file_by_path ON git_commit_file(path);
CREATE TABLE git_commit_ticket (
    sha TEXT NOT NULL REFERENCES git_commit(sha),
    key TEXT NOT NULL,
    source TEXT NOT NULL,
    PRIMARY KEY (sha, key)
) WITHOUT ROWID;
CREATE INDEX git_commit_ticket_by_key ON git_commit_ticket(key);

-- Provenance for derived facts. One generic table rather than columns on
-- node/edge: those are 1:1 projections of graph.json, while one node carries a
-- role, several annotations, a bean name and an endpoint. A new detector adds
-- predicates here, not a schema change.
--
-- `subject` addresses a node by its id, or an edge as `src|dst|kind|imported` --
-- the edge table's own key, because node.nid is assigned in graph.json
-- iteration order and every rebuild renumbers it.
--
-- Note `origin` here is not `node.origin`: this one is how the fact was come by
-- (extracted / documented / inferred / verified), that one is whether the file
-- belongs to this project or a linked parent. Both names are load-bearing in
-- queries people have already written, so neither was renamed.
CREATE TABLE fact (
    fid INTEGER PRIMARY KEY,
    subject_kind TEXT NOT NULL,
    subject TEXT NOT NULL,
    predicate TEXT NOT NULL,
    object TEXT,
    origin TEXT NOT NULL,
    confidence REAL NOT NULL,
    detector TEXT NOT NULL,
    source_ref TEXT,
    observed_at TEXT NOT NULL
);
CREATE INDEX fact_by_subject ON fact(subject_kind, subject);
CREATE INDEX fact_by_predicate ON fact(predicate, object);

-- What each detector was asked about, whether or not it found anything. A
-- project with no Java has no row for a Java detector ("not looked for"); a
-- project with 2,652 Java files and hits = 0 has one ("looked, found nothing").
-- Without the distinction both print as silence.
--
-- Counted per eligible file, so `hits` is what the detector produced *from
-- files* -- the folders producer's 3,734 is one edge per file, not the 47,425
-- folder-to-folder edges the directory tree itself contributes.
CREATE TABLE coverage (
    detector TEXT NOT NULL,
    predicate TEXT NOT NULL,
    files_eligible INTEGER NOT NULL,
    files_with_hits INTEGER NOT NULL,
    hits INTEGER NOT NULL,
    PRIMARY KEY (detector, predicate)
) WITHOUT ROWID;

-- What the scan did not index, by the rule that excluded it. Printed by
-- `eos scan` since it was written; kept here so a later reader can ask.
CREATE TABLE scan_exclusion (
    kind TEXT NOT NULL,
    rule TEXT NOT NULL,
    files INTEGER NOT NULL,
    PRIMARY KEY (kind, rule)
) WITHOUT ROWID;
"""

# Same table name and columns either way, so `SELECT ... FROM search WHERE body
# LIKE ...` works on every build; MATCH only where FTS5 was compiled in.
# `terms` holds camelCase identifiers split into words: unicode61 keeps
# FmCalculateMsisdnCommand as one token, so "Msisdn" would never match it.
_FTS_SCHEMA = (
    "CREATE VIRTUAL TABLE search USING fts5("
    "source UNINDEXED, ref UNINDEXED, title, body, terms, tokenize='unicode61');"
)
_LIKE_SCHEMA = "CREATE TABLE search (source TEXT NOT NULL, ref TEXT NOT NULL, title TEXT, body TEXT, terms TEXT);"

_COUNTS = {
    "notes": "SELECT COUNT(*) FROM note",
    "work items": "SELECT COUNT(*) FROM work_item",
    "brain docs": "SELECT COUNT(*) FROM brain_doc",
    "nodes": "SELECT COUNT(*) FROM node",
    "edges": "SELECT COUNT(*) FROM edge",
    "commits": "SELECT COUNT(*) FROM git_commit",
    "facts": "SELECT COUNT(*) FROM fact",
}

_GIT_FORMAT = "%x1e%H%x1f%P%x1f%aN%x1f%aE%x1f%aI%x1f%cI%x1f%s%x1f%b%x1d"
_PULL_REQUEST = re.compile(r"^(?:Merge pull request|Pull request) #([0-9]+)")

_CAMEL_WORD = re.compile(r"[A-Za-z0-9]*[a-z0-9][A-Z][A-Za-z0-9]*")
_HUMP = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|[0-9]+")

_READ_ACTIONS = frozenset({
    sqlite3.SQLITE_SELECT,
    sqlite3.SQLITE_READ,
    sqlite3.SQLITE_FUNCTION,
    sqlite3.SQLITE_RECURSIVE,
    sqlite3.SQLITE_PRAGMA,
})


class IndexBuildError(Exception):
    """The index was not written. Any previous index is still in place."""


@dataclasses.dataclass(frozen=True)
class BuildResult:
    path: Path
    counts: dict
    fts5: bool
    issues: list
    seconds: float
    size: int


@dataclasses.dataclass
class BuildContext:
    """What a build in progress carries. Also the contract index extensions see.

    `notes_dir` is empty until the notes are loaded, which happens first; an
    extension always sees it set. See core/extensions.py.
    """

    conn: sqlite3.Connection
    root: Path
    notes_dir: Path | None = None
    issues: list = dataclasses.field(default_factory=list)
    meta: dict = dataclasses.field(default_factory=dict)

    def read(self, path: Path) -> tuple[bytes, str]:
        data = path.read_bytes()
        return data, hashlib.sha256(data).hexdigest()

    def issue(self, source: str, ref: str | None, problem: str) -> None:
        self.issues.append((source, ref, problem))

    def search(self, source: str, ref: str, title: str | None, body: str) -> None:
        _add_search(self, source, ref, title, body)


def db_path(project_root: str | Path) -> Path:
    return Path(project_root).expanduser().resolve() / ".eos" / "data" / "eos.db"


def fts5_available(conn: sqlite3.Connection) -> bool:
    """Whether this interpreter's SQLite was compiled with FTS5."""
    try:
        conn.execute("CREATE VIRTUAL TABLE temp.eos_fts5_probe USING fts5(x)")
    except sqlite3.OperationalError:
        return False
    conn.execute("DROP TABLE temp.eos_fts5_probe")
    return True


def build(project_root: str | Path) -> BuildResult:
    """Rebuild `.eos/data/eos.db` from scratch.

    A source that is missing or unreadable is left out and recorded in
    `build_issue`; only a failure to write the database itself raises.
    """
    root = _project(project_root)
    return _build(root, _sources_digest(root))


def refresh(project_root: str | Path) -> BuildResult | None:
    """Rebuild the index when its sources changed since it was built; None when it is current.

    No single git hook sees every way the sources move -- a checkout, a merge
    finished by `git commit`, a regenerated snapshot, a hook's rebuild that was
    killed -- so a reader checks before it trusts the index.
    """
    root = _project(project_root)
    sources = _sources_digest(root)
    if _built_from(db_path(root)) == sources:
        return None
    return _build(root, sources)


def _project(project_root: str | Path) -> Path:
    root = Path(project_root).expanduser().resolve()
    if not (root / ".eos").is_dir():
        raise IndexBuildError(f"No .eos directory found at {root}. Run 'eos init' first.")
    return root


def _built_from(database: Path) -> str | None:
    """The sources digest an existing index was built from, or None if there is no readable one."""
    if not database.is_file():
        return None
    try:
        conn = connect_read_only(database)
        try:
            row = conn.execute("SELECT value FROM meta WHERE key = 'inputs_sha256'").fetchone()
        finally:
            conn.close()
    except sqlite3.Error:
        return None
    return row[0] if row else None


def _build(root: Path, sources: str) -> BuildResult:
    started = time.perf_counter()
    target = db_path(root)
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, name = tempfile.mkstemp(prefix="eos.db.", suffix=".tmp", dir=target.parent)
    os.close(handle)
    temp = Path(name)
    try:
        conn = sqlite3.connect(temp)
        try:
            fts5, issues, queries = _populate(conn, root, sources)
            conn.commit()
            counts = {}
            for label, sql in queries.items():
                try:
                    counts[label] = conn.execute(sql).fetchone()[0]
                except sqlite3.Error as exc:
                    # An extension's own COUNTS entry. Its rows are written; only
                    # the headline number is missing, which is not worth the build.
                    issues.append(("extension", label, f"count query failed ({exc})"))
        finally:
            conn.close()
        with open(temp, "rb+") as written:
            os.fsync(written.fileno())
        try:
            # os.replace, not os.rename: rename refuses an existing target on
            # Windows. Windows also refuses to replace a file another process
            # still has open, which an agent's query connection can be.
            os.replace(temp, target)
        except PermissionError as exc:
            raise IndexBuildError(f"{target} is in use by another process; kept the previous index") from exc
    except BaseException:
        try:
            temp.unlink()
        except OSError:
            pass  # the original error is the one worth reporting
        raise
    return BuildResult(
        path=target,
        counts=counts,
        fts5=fts5,
        issues=issues,
        seconds=time.perf_counter() - started,
        size=target.stat().st_size,
    )


def connect_read_only(path: str | Path) -> sqlite3.Connection:
    """Open the index so that nothing through this connection can write.

    `mode=ro` alone still lets ATTACH create and write a second database file,
    so an authorizer also refuses every action that is not a read. The URI comes
    from Path.as_uri(): a hand-built `file:` string breaks on Windows drive
    letters and on paths with spaces, `#` or `?`.
    """
    conn = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    conn.set_authorizer(_authorize_read)
    return conn


def has_tables(conn: sqlite3.Connection, names: tuple[str, ...]) -> bool:
    """Whether this index carries the given tables -- an older one may not."""
    present = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'")}
    return set(names) <= present


def open_for_read(project_root: str | Path) -> sqlite3.Connection | None:
    """The project's index, opened read-only, or None when there is not one.

    Never raises: callers are agent-facing surfaces that must answer before the
    first scan, so "no index" is a normal state, not an error.

    Every caller opens per call and closes. That is not an optimisation: a
    build replaces the database with os.replace(), and Windows refuses to
    replace a file another process still holds open -- a long-lived handle here
    would break `eos scan` intermittently and invisibly.
    """
    database = db_path(project_root)
    if not database.is_file():
        return None
    try:
        return connect_read_only(database)
    except sqlite3.Error:
        return None


# Bounded on purpose. Import graphs on a real service have cycles and hubs; an
# unbounded walk answers "what does this affect" with most of the repository,
# which is as useless as answering with nothing.
MAX_IMPACT_DEPTH = 5

# Every relation that means "this file reaches that one". Imports alone are the
# least informative edge on a Spring codebase: measured on one service, 142 of
# 143 test-to-subject relationships are same-package and all 142 carry no
# import, so an import-only answer reported zero dependents for every one.
# folder-hierarchy is excluded -- it says where a file sits, not what it uses.
IMPACT_KINDS = ("import", "extends", "implements", "field", "new", "calls")
MAX_IMPACT_ROWS = 500

# UNION, not UNION ALL: Java packages import each other in cycles and the CTE
# would not terminate. Depth is carried so a caller can tell a direct dependent
# from a third-hop one, and the node join happens inside this query so no nid
# ever leaves it -- _load_brain assigns nid in graph.json iteration order, so a
# nid held across two builds silently names a different file.
_IMPACT_SQL = """
WITH RECURSIVE reachable(nid, depth) AS (
    SELECT :start, 0
    UNION
    SELECT e.{far}, r.depth + 1
      FROM reachable r
      JOIN edge e ON e.{near} = r.nid
     WHERE r.depth < :depth AND e.kind IN ({kinds})
)
SELECT n.path, MIN(r.depth) AS depth, n.origin
  FROM reachable r
  JOIN node n ON n.nid = r.nid
 WHERE r.nid != :start
 GROUP BY n.path, n.origin
 ORDER BY depth, n.path
 LIMIT :limit
"""


def impact_rows(conn: sqlite3.Connection, path: str, depth: int = 1,
                kinds: tuple[str, ...] = IMPACT_KINDS) -> dict:
    """Files this one reaches, and files that reach it, out to `depth` hops.

    Returns None for `file` when the path is not in the index, so the caller
    decides whether that is an error or a reason to fall back.
    """
    depth = max(1, min(int(depth), MAX_IMPACT_DEPTH))
    kinds = tuple(kinds) or IMPACT_KINDS
    row = conn.execute("SELECT nid, path FROM node WHERE path = ?", (path,)).fetchone()
    if row is None:
        return {"file": None, "dependencies": [], "dependents": [], "truncated": False}
    start, resolved = row

    placeholders = ", ".join("?" * len(kinds))
    out = {"file": resolved, "truncated": False}
    for label, near, far in (("dependencies", "src", "dst"), ("dependents", "dst", "src")):
        sql = _IMPACT_SQL.format(near=near, far=far, kinds=placeholders)
        # Named and positional parameters cannot be mixed, so the kinds are
        # spliced as placeholders and every value is passed positionally.
        sql = sql.replace(":start", "?").replace(":depth", "?").replace(":limit", "?")
        params = [start, depth, *kinds, start, MAX_IMPACT_ROWS + 1]
        found = conn.execute(sql, params).fetchall()
        if len(found) > MAX_IMPACT_ROWS:
            out["truncated"] = True
            found = found[:MAX_IMPACT_ROWS]
        out[label] = [{"path": p, "depth": d, "origin": o} for p, d, o in found]
    return out


def run_query(path: str | Path, sql: str) -> tuple[list[str], list[tuple]]:
    conn = connect_read_only(path)
    try:
        cursor = conn.execute(sql)
        columns = [column[0] for column in cursor.description or ()]
        return columns, cursor.fetchall()
    finally:
        conn.close()


def search(path: str | Path, text: str, limit: int = 20) -> tuple[list[str], list[tuple]]:
    """Full-text search over notes and brain docs, best match first.

    Words are ORed and the rows ranked, not ANDed. A query is a question in
    somebody's own words, and one word that happens to match nothing -- `fails`,
    `why`, a misspelling, a term the writer of the note never used -- must cost
    rank, never the answer. Measured on a real 461-note corpus while this was
    still AND: `rate plan change` returned the note that answers it, and `rate
    plan change fails 500` -- the same question asked in a sentence -- returned
    nothing. The more naturally a question was phrased, the worse it was
    answered, which is backwards.

    BM25 is what makes OR safe. It scores a row by how much of the query it
    carries and how rare those words are across the corpus, so a row matching
    every word still outranks one carrying a single common word: the AND result
    stays at the top of the OR result rather than being replaced by it. The
    words that would have ANDed away the answer now just sort it down a little.

    Words are quoted as FTS5 phrases, so `PROJ-123` or a stray quote is searched
    for rather than parsed as query syntax.
    """
    columns = ["source", "ref", "title", "snippet"]
    words = [word for word in text.split() if re.search(r"\w", word)]
    if not words:
        return columns, []
    conn = connect_read_only(path)
    try:
        row = conn.execute("SELECT value FROM meta WHERE key = 'fts5'").fetchone()
        if row and row[0] == "1":
            query = " OR ".join('"' + word.replace('"', '""') + '"' for word in words)
            ranked = conn.execute(
                "SELECT source, ref, title, snippet(search, 3, '[', ']', '...', 12), rank FROM search "
                "WHERE search MATCH ? ORDER BY rank LIMIT ?",
                (query, limit),
            ).fetchall()
            # rank is BM25 negated: smaller is better, so the cut is a ceiling.
            cut = ranked[0][4] * SCORE_FLOOR if ranked else 0.0
            rows = [row[:4] for row in ranked if row[4] <= cut]
        else:
            # No BM25 without FTS5, so rank by how many of the query's words the
            # row carries. That is a coarser signal -- it cannot tell a rare word
            # from a common one -- but it keeps the same promise: more of the
            # query matched sorts higher, and nothing matched is the only way to
            # be left out. The sum is computed in a subquery because SQLite will
            # not have an output alias in WHERE.
            hits = " + ".join(
                "(title LIKE ? ESCAPE '\\' OR body LIKE ? ESCAPE '\\')" for _ in words
            )
            params = [pattern for word in words for pattern in (_like(word), _like(word))]
            counted = conn.execute(
                "SELECT source, ref, title, substr(body, 1, 160), hits FROM "
                f"(SELECT source, ref, title, body, {hits} AS hits FROM search) "
                "WHERE hits > 0 ORDER BY hits DESC LIMIT ?",
                (*params, limit),
            ).fetchall()
            # No BM25 here; the same promise on the coarser signal: a row
            # carrying far fewer of the query's words than the best one is cut.
            best = counted[0][4] if counted else 0
            rows = [row[:4] for row in counted if row[4] >= best * SCORE_FLOOR]
    finally:
        conn.close()
    return columns, rows


def _authorize_read(action, *_):
    return sqlite3.SQLITE_OK if action in _READ_ACTIONS else sqlite3.SQLITE_DENY


def _like(word: str) -> str:
    return "%" + word.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _populate(conn: sqlite3.Connection, root: Path, sources: str) -> tuple[bool, list, dict]:
    # A crash throws the temp file away, so neither a journal nor per-commit
    # syncs buy anything here; the finished file is fsynced once before rename.
    conn.execute("PRAGMA journal_mode=OFF")
    conn.execute("PRAGMA synchronous=OFF")
    fts5 = fts5_available(conn)
    conn.executescript(_SCHEMA + (_FTS_SCHEMA if fts5 else _LIKE_SCHEMA))

    # Imported before anything is written: an extension the project configured
    # but that cannot be imported is a configuration error, and the build must
    # not get as far as replacing a good index with one silently missing it.
    declared = _extensions(root)
    build = BuildContext(conn, root)
    usable = _create_extension_schema(build, declared)
    counts = dict(_COUNTS)

    build.notes_dir = _load_notes(build)
    _load_work(build)
    _load_executions(build)
    _load_verifications(build)
    _load_brain(build)
    _load_evidence(build)
    for extension in usable:
        try:
            counts.update(extension.counts)
            extension.load(build)
        except Exception as exc:  # noqa: BLE001 - one extension may not cost the index
            build.issue("extension", extension.name, f"load failed ({exc}); its tables are empty")
    _load_history(build)

    if declared:
        build.meta["extensions"] = extensions.provenance(declared)
    build.meta.update({
        "schema_version": str(SCHEMA_VERSION),
        "built_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "engine_version": _engine_version(),
        "sqlite_version": sqlite3.sqlite_version,
        "fts5": "1" if fts5 else "0",
        "project_root": str(root),
        "service": root.name,
        "inputs_sha256": sources,
    })
    conn.executemany("INSERT INTO meta(key, value) VALUES (?, ?)", sorted(build.meta.items()))
    conn.executemany("INSERT INTO build_issue(source, ref, problem) VALUES (?, ?, ?)", build.issues)
    return fts5, build.issues, counts


def _extensions(root: Path) -> list:
    """Every configured extension, imported. A configuration mistake stops the build.

    Reported as an IndexBuildError so a caller that already handles "the
    index was not written" needs nothing new, and so the previous index
    stays in place while the config is wrong.
    """
    try:
        return extensions.load_all(root)
    except extensions.ExtensionError as exc:
        raise IndexBuildError(str(exc)) from exc


def _create_extension_schema(build: BuildContext, declared: list) -> list:
    """Run each extension's SCHEMA. An extension whose schema fails is dropped.

    Dropped rather than tolerated: without its tables its `load` would fail on
    the first insert anyway, and half-created tables would make a later query
    look like missing data rather than a broken extension.
    """
    usable = []
    for extension in declared:
        schema = extension.schema
        if not schema.strip():
            usable.append(extension)
            continue
        try:
            build.conn.executescript(schema)
        except sqlite3.Error as exc:
            build.issue("extension", extension.name, f"SCHEMA failed ({exc}); extension skipped")
            continue
        usable.append(extension)
    return usable


def _engine_version() -> str:
    try:
        return _VERSION_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return "unknown"


def _sources_digest(root: Path) -> str:
    """A digest of every source a build reads, taken before the build reads any of them.

    Taken first, a source that changes while a build runs leaves the index
    looking stale rather than current, so the next check rebuilds it.
    """
    digest = hashlib.sha256()

    def add(*fields) -> None:
        line = "\x1f".join(str(field) for field in fields) + "\n"
        digest.update(line.encode("utf-8", errors="surrogateescape"))

    add("schema", SCHEMA_VERSION, _engine_version())
    directory = notes.notes_dir(root)
    add("notes", directory)
    if directory.is_dir():
        for path in sorted(directory.glob("*.md")):
            add("note", path.name, _file_digest(path))
    # The ledger is appended to far more often than notes are written, so it
    # is the input most likely to make an index stale -- and an index that
    # still shows an item as claimed after a session closed it would be worse
    # than one that never had the table.
    from core import work

    add("work", _file_digest(work.path_for(root)))
    from core import executions
    add("executions", _file_digest(executions.path_for(root)))
    from core import verification
    add("verifications", _file_digest(verification.path_for(root)))
    data = root / ".eos" / "data"
    for path in (data / "last_scan.json", data / "brain" / "graph.json",
                 data / "brain" / "evidence.jsonl",
                 *(data / "brain" / name for name in inspector.BRAIN_FILES)):
        # With mtimes: they decide whether _load_brain takes the scan as complete.
        add("brain", path.name, _file_digest(path), *_stat(path))
    add("ticket_pattern", _ticket_pattern(root).pattern)
    merge_branch_pattern = _merge_branch_pattern(root)
    # A configured merge_branch_pattern changes which branch names get pulled
    # out of merge commit bodies -- the same kind of build input ticket_pattern
    # is, so it must invalidate a cached index the same way. "" is a safe
    # sentinel for "unset": _merge_branch_pattern treats an empty configured
    # value as unset too, so a real configured pattern's .pattern can never
    # be "" here -- unset and set-to-something always digest differently.
    add("merge_branch_pattern", merge_branch_pattern.pattern if merge_branch_pattern is not None else "")
    add("git", _git_head(root))
    _add_extension_sources(add, root, directory)
    return digest.hexdigest()


def _add_extension_sources(add, root: Path, notes_dir: Path) -> None:
    """Fold each extension's own file and its declared sources into the digest.

    The extension's file counts as a source of itself: editing what it indexes
    out of an unchanged snapshot has to make the index stale, or the change
    lands only on whoever next deletes the database by hand.

    An extension that raises while listing its sources is not allowed to make
    the digest unstable -- that would rebuild the index on every single read.
    The failure is digested as a constant and reported by the build instead.
    """
    for extension in _extensions(root):
        add("extension", extension.name, extension.digest)
        try:
            for fields in extension.sources(root, notes_dir):
                add("extension-source", extension.name, *fields)
        except Exception as exc:  # noqa: BLE001 - reported by the build that follows
            add("extension-source", extension.name, "unavailable", type(exc).__name__)


def _file_digest(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        return type(exc).__name__


def _stat(path: Path) -> tuple:
    try:
        stat = path.stat()
    except OSError as exc:
        return (type(exc).__name__,)
    return stat.st_size, stat.st_mtime_ns


def _text(data: bytes) -> str:
    return data.decode("utf-8", errors="replace").replace("\r\n", "\n")


def _front_matter(text: str) -> dict[str, str]:
    """The `key: value` lines of a leading `---` block; list items are skipped."""
    if not text.startswith("---\n"):
        return {}
    front, separator, _ = text[4:].partition("\n---\n")
    if not separator:
        return {}
    fields = {}
    for line in front.splitlines():
        key, colon, value = line.partition(":")
        if colon and key and not key[0].isspace() and value.strip():
            fields[key.strip()] = value.strip()
    return fields


def _heading(text: str) -> str | None:
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return None


def _terms(text: str) -> str:
    parts = (part for word in _CAMEL_WORD.findall(text) for part in _HUMP.findall(word))
    return " ".join(dict.fromkeys(parts))


def _add_search(build: BuildContext, source: str, ref: str, title: str | None, body: str) -> None:
    build.conn.execute(
        "INSERT INTO search(source, ref, title, body, terms) VALUES (?, ?, ?, ?, ?)",
        (source, ref, title, body, _terms(f"{title or ''}\n{body}")),
    )


# Text helpers an index extension may reuse rather than reimplement. Public
# because the private spellings above are free to change; these are not.
text_of = _text
front_matter = _front_matter
heading_of = _heading
file_digest = _file_digest


# --- notes -----------------------------------------------------------------------


def _load_notes(build: BuildContext) -> Path:
    directory = notes.notes_dir(build.root)
    build.meta["notes_dir"] = str(directory)
    if not directory.is_dir():
        return directory
    # File by file, not notes.load_notes(): one unreadable or merge-conflicted
    # note would abort that whole load.
    for path in sorted(directory.glob("*.md")):
        name = path.name
        try:
            raw, digest = build.read(path)
            note = notes.parse_note(path)
        except (OSError, UnicodeDecodeError) as exc:
            build.issue("note", name, f"unreadable ({exc}); not indexed")
            continue
        if not note.kind or not note.title:
            build.issue("note", name, "no front matter (conflicted or hand-edited?); not indexed")
            continue
        # `updated` is written by `eos note amend` but is not a Note field.
        updated = _front_matter(_text(raw)).get("updated")
        cursor = build.conn.execute(
            "INSERT INTO note(file, kind, title, created, updated, source, session, generated, body, sha256) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (name, note.kind, note.title, note.created or None, updated, note.source, note.session,
             int(notes.is_generated(note)), note.body, digest),
        )
        note_id = cursor.lastrowid
        build.conn.executemany(
            "INSERT OR IGNORE INTO note_tag(note_id, tag) VALUES (?, ?)",
            [(note_id, tag) for tag in note.tags],
        )
        build.conn.executemany(
            "INSERT INTO note_scope(note_id, ord, entry, hash) VALUES (?, ?, ?, ?)",
            [
                (note_id, ord_, entry, note.scope_hashes[ord_] if ord_ < len(note.scope_hashes) else None)
                for ord_, entry in enumerate(note.scope)
            ],
        )
        _add_search(build, "note", name, note.title, note.body)
    return directory


def _load_work(build: BuildContext) -> None:
    """Fold the work ledger into the index, events and all.

    Read through `work.fold` rather than re-implemented here: two answers to
    "what is the status of this item" is one more than a system should have,
    and the CLI's answer is the one people have read.

    A ledger that cannot be parsed costs its own tables and nothing else --
    `work.load_path` already skips a line it cannot read, so the worst case is
    a partial fold rather than a failed build.
    """
    from core import work

    ledger = work.path_for(build.root)
    build.meta["work_ledger"] = str(ledger)
    if not ledger.is_file():
        return
    events = work.load_path(ledger)
    if not events:
        return

    by_item: dict[str, list] = {}
    for entry in events:
        by_item.setdefault(entry.id, []).append(entry)

    for item in work.fold(events):
        build.conn.execute(
            "INSERT INTO work_item(id, title, status, ticket, opened_at, updated_at, "
            "holder_count, reason, last, events) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (item.id, item.title, item.status, item.ticket, item.opened_at,
             item.updated_at, len(item.holders), item.reason, item.last, item.events),
        )
        build.conn.executemany(
            "INSERT OR IGNORE INTO work_holder(item, session, agent, claimed_at) "
            "VALUES (?, ?, ?, ?)",
            [(item.id, holder.get("session") or "", holder.get("agent"), holder.get("at"))
             for holder in item.holders],
        )
        build.conn.executemany(
            "INSERT INTO work_event(item, ord, event, at, session, agent, body, branch, commit_sha) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(item.id, ord_, entry.event, entry.at, entry.session, entry.agent,
              entry.body, entry.branch, entry.commit)
             for ord_, entry in enumerate(by_item.get(item.id, []))],
        )
        # Searchable by what it is about, not by its id: a session searching
        # "top-up" should find the item someone claimed this morning next to
        # the note written about it last month.
        body = " ".join(part for part in (item.ticket, item.reason, item.last) if part)
        _add_search(build, "work", item.id, item.title, body)


def _load_executions(build: BuildContext) -> None:
    """Fold the execution ledger into the index (ADR-022).

    Through `executions.load_path`, for the reason `_load_work` goes through
    `work.fold`: one answer to "what happened in this run", the one the CLI
    prints. Searchable by title, target, tool and lesson, so a session looking
    for "deploy staging" finds the runs beside the notes.
    """
    from core import executions

    ledger = executions.path_for(build.root)
    build.meta["execution_ledger"] = str(ledger)
    records = executions.load_path(ledger)
    for record in records:
        build.conn.execute(
            "INSERT OR IGNORE INTO execution(id, title, procedure, work_item, session, agent, "
            "started_at, finished_at, outcome, target, branch, commit_start, commit_end, "
            "lesson, events) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (record.id, record.title, record.procedure, record.work_item, record.session,
             record.agent, record.started_at, record.finished_at, record.outcome,
             record.target, record.branch, record.commit_start, record.commit_end,
             record.lesson, len(record.events)),
        )
        build.conn.executemany(
            "INSERT OR IGNORE INTO execution_event(execution, ord, at, kind, tool, target, ref, "
            "exit_code, ms, body, session, agent, source, status, tool_use_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(record.id, e.ord, e.at or "", e.kind, e.tool, e.target, e.ref,
              e.exit_code if isinstance(e.exit_code, int) else None,
              e.ms if isinstance(e.ms, int) else None, e.body, e.session,
              e.agent, e.source, e.status, e.tool_use_id)
             for e in record.events],
        )
        tools = " ".join(sorted({e.tool for e in record.events if e.tool}))
        body = " ".join(part for part in (record.outcome, record.target, record.procedure,
                                          tools, record.lesson) if part)
        _add_search(build, "execution", record.id, record.title, body)


def _load_verifications(build: BuildContext) -> None:
    """Every recorded run of a behaviour code, in the order it was recorded."""
    from core import verification

    records = verification.load(build.root)
    build.meta["verifications"] = str(verification.path_for(build.root))
    for ord_, r in enumerate(records):
        build.conn.execute(
            "INSERT INTO verification(ord, code, outcome, command, exit_code, recorded_at, "
            "commit_sha, log, verdict, note, session, execution) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (ord_, r.code, r.outcome, r.command, r.exit_code, r.recorded_at, r.commit, r.log,
             r.verdict, r.note, r.session, r.execution))
        body = " ".join(part for part in (r.outcome, r.command, r.verdict, r.note, r.execution) if part)
        _add_search(build, "verification", f"{r.code}#{ord_}", f"{r.code} {r.outcome}", body)


# --- brain and graph ---------------------------------------------------------------


def _load_brain(build: BuildContext) -> None:
    data = build.root / ".eos" / "data"
    last_scan_path = data / "last_scan.json"
    graph_path = data / "brain" / "graph.json"
    if not last_scan_path.exists() and not graph_path.exists():
        return
    doc_paths = [data / "brain" / name for name in inspector.BRAIN_FILES]

    # `eos scan` writes last_scan.json last and none of its writes is atomic,
    # so anything newer than it, or a graph whose size it does not describe,
    # belongs to a scan that is still running or died halfway.
    try:
        scanned_at = last_scan_path.stat().st_mtime_ns
        newer = [path.name for path in (graph_path, *doc_paths) if path.stat().st_mtime_ns > scanned_at]
        if newer:
            build.issue("brain", None, f"{', '.join(newer)} newer than last_scan.json (scan running or interrupted); brain not indexed")
            return
        last_scan = json.loads(build.read(last_scan_path)[0])
        graph = json.loads(build.read(graph_path)[0])
        docs = [(path.name, *build.read(path)) for path in doc_paths]
    except (OSError, ValueError) as exc:
        build.issue("brain", None, f"scan output unreadable ({exc}); brain not indexed")
        return
    if not isinstance(graph, dict) or not isinstance(last_scan, dict):
        build.issue("brain", None, "graph.json or last_scan.json is not a JSON object; brain not indexed")
        return
    nodes = graph.get("nodes") or []
    edges = graph.get("edges") or []
    if last_scan.get("nodes") != len(nodes) or last_scan.get("edges") != len(edges):
        build.issue("brain", None, "last_scan.json counts do not match graph.json (scan interrupted); brain not indexed")
        return

    build.meta.update({
        "languages": json.dumps(graph.get("languages") or []),
        "tech_stack": json.dumps(graph.get("tech_stack") or []),
        "entry_points": json.dumps(graph.get("entry_points") or []),
        "last_scan": json.dumps(last_scan, sort_keys=True),
    })
    for name, raw, digest in docs:
        content = _text(raw)
        build.conn.execute("INSERT INTO brain_doc(name, content, sha256) VALUES (?, ?, ?)", (name, content, digest))
        _add_search(build, "brain", name, _heading(content) or name, content)

    # File nodes only. Folder nodes and their folder-hierarchy edges are most of
    # graph.json, repeat themselves and point ancestor -> descendant; a node's
    # `path` and `dir` already say where it lives.
    entry_points = set(graph.get("entry_points") or [])
    nids: dict[str, int] = {}
    node_rows, symbol_rows = [], []
    for node in nodes:
        node_id = node.get("id")
        if node.get("type") == "folder" or not node_id or node_id in nids:
            continue
        nid = len(nids) + 1
        nids[node_id] = nid
        path = node.get("path") or ""
        origin = "own"
        if path.startswith(links.PARENT_PREFIX):
            origin = path[len(links.PARENT_PREFIX):].split("/", 1)[0]
        node_rows.append((
            nid, node_id, node.get("type") or "", node.get("label"), path, path.rpartition("/")[0],
            node.get("language"), origin, int(node_id in entry_points), node.get("doc"),
        ))
        metadata = node.get("metadata") or {}
        symbol_rows.extend((nid, "export", str(name)) for name in metadata.get("exports") or [])
        symbol_rows.extend((nid, "top", str(name)) for name in metadata.get("top_symbols") or [])
    build.conn.executemany(
        "INSERT INTO node(nid, id, type, label, path, dir, language, origin, is_entry, doc) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        node_rows,
    )
    build.conn.executemany("INSERT OR IGNORE INTO node_symbol(nid, role, name) VALUES (?, ?, ?)", symbol_rows)
    build.conn.executemany(
        "INSERT OR IGNORE INTO edge(src, dst, kind, imported) VALUES (?, ?, ?, ?)",
        [
            (nids[edge["source"]], nids[edge["target"]], edge.get("kind") or "",
             str((edge.get("metadata") or {}).get("imported") or ""))
            for edge in edges
            if edge.get("kind") != "folder-hierarchy" and edge.get("source") in nids and edge.get("target") in nids
        ],
    )
    build.meta["with_parents"] = "1" if any(row[7] != "own" for row in node_rows) else "0"


def _load_evidence(build: BuildContext) -> None:
    """Stream .eos/data/brain/evidence.jsonl into fact / coverage / scan_exclusion.

    Streamed, not loaded: structural extraction produces tens of thousands of
    facts on a real service, and this runs inside every index build.

    The header's counts are checked against last_scan.json's before a single
    row is written. `eos scan` writes the sidecar before last_scan.json, so a
    scan that died in between leaves counts that disagree -- and indexing it
    anyway would leave `eos why` confidently citing provenance for edges that
    belong to a previous graph, which is the one failure this file exists to
    prevent.
    """
    data = build.root / ".eos" / "data"
    path = data / "brain" / "evidence.jsonl"
    if not path.exists():
        # A project whose brain was written by an older engine has a graph but
        # no provenance. Recorded, because the alternative is an empty coverage
        # table that reads as "every detector found nothing".
        if (data / "brain" / "graph.json").exists():
            build.issue("evidence", None,
                        "no evidence.jsonl beside graph.json (brain predates provenance); "
                        "run 'eos scan' to produce it")
        return
    last_scan_path = data / "last_scan.json"
    try:
        last_scan = json.loads(build.read(last_scan_path)[0]) if last_scan_path.exists() else {}
    except (OSError, ValueError):
        last_scan = {}
    if not isinstance(last_scan, dict):
        last_scan = {}

    facts, coverage, exclusions = [], [], []
    header = None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for number, line in enumerate(handle, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    build.issue("evidence", str(number), "unparseable line; skipped")
                    continue
                kind = row.get("type")
                if kind == "header":
                    header = row
                elif kind == "fact":
                    facts.append((row.get("subject_kind"), row.get("subject"), row.get("predicate"),
                                  row.get("object"), row.get("origin"), row.get("confidence"),
                                  row.get("detector"), row.get("source_ref"), row.get("observed_at")))
                elif kind == "coverage":
                    coverage.append((row.get("detector"), row.get("predicate"), row.get("files_eligible"),
                                     row.get("files_with_hits"), row.get("hits")))
                elif kind == "exclusion":
                    exclusions.append((row.get("kind"), row.get("rule"), row.get("files")))
    except OSError as exc:
        build.issue("evidence", None, f"unreadable ({exc}); provenance not indexed")
        return

    if header is None:
        build.issue("evidence", None, "no header line (scan interrupted); provenance not indexed")
        return
    claimed = (header.get("facts"), header.get("coverage"), header.get("exclusions"))
    actual = (len(facts), len(coverage), len(exclusions))
    if claimed != actual:
        build.issue("evidence", None,
                    f"header claims {claimed} fact/coverage/exclusion rows, file holds {actual} "
                    "(scan interrupted); provenance not indexed")
        return
    if "facts" in last_scan and last_scan.get("facts") != len(facts):
        build.issue("evidence", None,
                    f"last_scan.json says {last_scan.get('facts')} facts, evidence.jsonl holds "
                    f"{len(facts)} (scan interrupted); provenance not indexed")
        return

    build.conn.executemany(
        "INSERT INTO fact(subject_kind, subject, predicate, object, origin, confidence, detector, "
        "source_ref, observed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", facts)
    build.conn.executemany(
        "INSERT OR REPLACE INTO coverage(detector, predicate, files_eligible, files_with_hits, hits) "
        "VALUES (?, ?, ?, ?, ?)", coverage)
    build.conn.executemany(
        "INSERT OR REPLACE INTO scan_exclusion(kind, rule, files) VALUES (?, ?, ?)", exclusions)
    if header.get("generated_at"):
        build.meta["evidence_generated_at"] = header["generated_at"]


# --- history -----------------------------------------------------------------------


def _ticket_pattern(root: Path) -> re.Pattern:
    config = root / ".eos" / "config.toml"
    configured = None
    if config.is_file():
        configured = ConfigIO.read_toml(config).get("index", {}).get("ticket_pattern")
    try:
        return re.compile(configured or TICKET_PATTERN)
    except re.error as exc:
        raise IndexBuildError(f"[index] ticket_pattern in {config} is not a valid regular expression: {exc}") from exc


# No merge-commit body format is universal, so guessing one for every project
# is worse than extracting nothing. GitHub's format is already covered by
# _PULL_REQUEST on the subject line; a project whose host writes source
# branch names into the merge commit body opts in with
# [index] merge_branch_pattern in .eos/config.toml.
def _merge_branch_pattern(root: Path) -> re.Pattern | None:
    config = root / ".eos" / "config.toml"
    configured = None
    if config.is_file():
        configured = ConfigIO.read_toml(config).get("index", {}).get("merge_branch_pattern")
    if not configured:
        return None
    try:
        # re.MULTILINE: the pattern matches one line inside a multi-line
        # commit body, not the whole body -- the same behavior the constant
        # this replaced had. Without it, "^" anchors to the very first
        # character of the body, so a body with any trailer or blank line
        # before the matched line silently extracts nothing.
        return re.compile(configured, re.MULTILINE)
    except re.error as exc:
        raise IndexBuildError(
            f"[index] merge_branch_pattern in {config} is not a valid regular expression: {exc}"
        ) from exc


def _ticket_keys(pattern: re.Pattern, text: str) -> list[str]:
    return [match.group(0) for match in pattern.finditer(text)]


# What `git rev-parse --local-env-vars` lists. Inherited, GIT_DIR and friends
# outrank `git -C`, so `eos index` run from inside another repository's git
# context would read that repository's history instead of the project's.
_GIT_REPOSITORY_ENV = frozenset({
    "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_CONFIG", "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_COUNT",
    "GIT_OBJECT_DIRECTORY", "GIT_DIR", "GIT_WORK_TREE", "GIT_IMPLICIT_WORK_TREE", "GIT_GRAFT_FILE",
    "GIT_INDEX_FILE", "GIT_NO_REPLACE_OBJECTS", "GIT_REPLACE_REF_BASE", "GIT_PREFIX",
    "GIT_SHALLOW_FILE", "GIT_COMMON_DIR",
})


def _git_environment() -> dict:
    return {key: value for key, value in os.environ.items() if key not in _GIT_REPOSITORY_ENV}


def _git_head(root: Path) -> str:
    git = shutil.which("git")
    if git is None:
        return ""
    try:
        done = subprocess.run([git, "-C", str(root), "rev-parse", "--verify", "--quiet", "HEAD"],
                              capture_output=True, timeout=30, env=_git_environment())
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return done.stdout.decode("utf-8", errors="replace").strip() if done.returncode == 0 else ""


def _load_history(build: BuildContext) -> None:
    pattern = _ticket_pattern(build.root)
    merge_branch_pattern = _merge_branch_pattern(build.root)
    git = shutil.which("git")
    if git is None:
        build.issue("history", None, "git is not on PATH; history not indexed")
        return
    command = [
        git, "-C", str(build.root),
        "-c", "core.quotepath=off", "-c", "log.showSignature=false", "-c", "i18n.logOutputEncoding=UTF-8",
        "log", "--no-color", "--no-renames", "--no-ext-diff", "--name-only",
        f"--max-count={MAX_COMMITS}", f"--format={_GIT_FORMAT}", "HEAD", "--",
    ]
    try:
        done = subprocess.run(command, capture_output=True, timeout=120, env=_git_environment())
    except (OSError, subprocess.TimeoutExpired) as exc:
        build.issue("history", None, f"git log failed ({exc}); history not indexed")
        return
    if done.returncode != 0:
        reason = _text(done.stderr).strip().splitlines()
        build.issue("history", None, f"git log failed ({reason[0] if reason else done.returncode}); history not indexed")
        return

    # Commit messages written on Windows can carry CRs; none may reach a
    # subject, a body or a ticket key.
    output = done.stdout.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "")
    records = [record for record in output.split("\x1e") if record.strip()]
    commits, files, tickets = [], [], []
    for ord_, record in enumerate(records):
        header, _, listing = record.partition("\x1d")
        fields = header.split("\x1f", 7)
        if len(fields) != 8:
            build.issue("history", None, f"unparseable git log record {header[:40]!r}")
            continue
        sha, parents, author, email, authored, committed, subject, body = (field.strip() for field in fields)
        paths = [line for line in listing.split("\n") if line.strip()]
        pull_request = _PULL_REQUEST.match(subject)
        commits.append((
            sha, ord_, parents, author, email.lower(), authored, committed, subject, body,
            int(len(parents.split()) > 1), int(pull_request.group(1)) if pull_request else None, len(paths),
        ))
        files.extend((sha, path) for path in paths[:MAX_FILES_PER_COMMIT])
        keys: dict[str, str] = {}
        for key in _ticket_keys(pattern, subject):
            keys.setdefault(key, "subject")
        if merge_branch_pattern is not None:
            for branch in merge_branch_pattern.findall(body):
                for key in _ticket_keys(pattern, branch):
                    keys.setdefault(key, "branch")
        for key in _ticket_keys(pattern, body):
            keys.setdefault(key, "body")
        tickets.extend((sha, key, source) for key, source in keys.items())

    build.conn.executemany(
        "INSERT OR IGNORE INTO git_commit(sha, ord, parents, author_name, author_email, authored_at, committed_at, "
        "subject, body, is_merge, pr, files_total) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        commits,
    )
    build.conn.executemany("INSERT OR IGNORE INTO git_commit_file(sha, path) VALUES (?, ?)", files)
    build.conn.executemany("INSERT OR IGNORE INTO git_commit_ticket(sha, key, source) VALUES (?, ?, ?)", tickets)
    if commits:
        build.meta["git_head"] = commits[0][0]
