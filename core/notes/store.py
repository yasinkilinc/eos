"""The note store: where notes live, how one is parsed and loaded, and the
section helpers every other part of the package reads bodies with.

Every other artifact EOS writes is a pure function of a source scan and is
rebuilt on the next one. Notes are the opposite: they are written by a human or
an agent, they cannot be recomputed from the code, and losing one loses the
reason it was written. So they live outside the derived tree and no scan,
clean or update path may touch them.

The directory is configurable because the project that owns the code is often
not the project that may commit notes about it.
"""
from __future__ import annotations

import dataclasses
import datetime
import fnmatch
import hashlib
import json
import re
from pathlib import Path

from core.inspector import SENSITIVE_PATTERNS
from core.lib.config_io import ConfigIO


DEFAULT_NOTES_DIR = ".eos/knowledge"


def notes_dir(project_root: str | Path) -> Path:
    """Resolve the directory holding this project's notes.

    Defaults to ``.eos/knowledge``. A ``[knowledge] dir`` entry in
    ``.eos/config.toml`` overrides it; relative values resolve against the
    project root, so a project whose own repository is not a good home for
    notes can point them somewhere that is.
    """
    project = Path(project_root).expanduser().resolve()
    configured = DEFAULT_NOTES_DIR

    config_path = project / ".eos" / "config.toml"
    if config_path.is_file():
        cfg = ConfigIO.read_toml(config_path)
        knowledge = cfg.get("knowledge")
        # `knowledge = "…"` is not a table: the setting is absent, not a crash.
        value = knowledge.get("dir") if isinstance(knowledge, dict) else None
        if value:
            configured = str(value)

    return (project / configured).resolve()


def note_synonyms(project_root: str | Path) -> object:
    """The ``[notes] synonyms`` table of a project's config, as written.

    A list of word groups (``[["wiki", "confluence"], ["sms", "notification"]]``)
    read by the scorer; absent, or a config that is not a table, is ``None``.
    Validation is the scorer's (`synonym_groups`), which drops what it cannot use.
    """
    config_path = Path(project_root).expanduser().resolve() / ".eos" / "config.toml"
    if not config_path.is_file():
        return None
    section = ConfigIO.read_toml(config_path).get("notes")
    return section.get("synonyms") if isinstance(section, dict) else None


def _slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")


def _front_matter(fields: dict) -> str:
    """Serialize note metadata.

    Hand-rolled rather than PyYAML: the runtime is deployed by copying files
    into arbitrary projects, so it must stay stdlib-only. Scalars are JSON
    quoted, which is valid YAML and survives colons and quotes in titles.
    """
    lines = ["---"]
    for key, value in fields.items():
        if value in (None, "", [], ()):
            continue
        if isinstance(value, (list, tuple)):
            lines.append(f"{key}:")
            lines.extend(f"  - {_scalar(item)}" for item in value)
        else:
            lines.append(f"{key}: {_scalar(value)}")
    lines.append("---")
    return "\n".join(lines)


# Plain words are left unquoted so a note reads naturally in a git diff, which
# is where these are reviewed. Anything that could change how YAML parses gets
# JSON quoting, which is valid YAML.
_SAFE_SCALAR = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.\-/@+]*$")


def _scalar(value) -> str:
    if value is None:
        return "null"
    text = str(value)
    if _SAFE_SCALAR.match(text):
        return text
    return json.dumps(text, ensure_ascii=False)


KINDS = ("defect", "finding", "procedure", "lesson", "decision")


# Exact `source:` values a bulk generator writes, measured against the real
# corpus (272 notes): api-inventory (206), journey-map (46), repo-topology
# (3). Everything else -- a ticket key, "api-inventory-finding" (a
# hand-analyzed derivative, not the raw table), or no `source` field at all
# -- is hand-written. An allowlist, not a keyword search, so a ticket whose
# text mentions "api" is never misread as a generator name.
#
# This distinction decides whether the Stop gate blocks: a generated note
# going stale is fixed by re-running its generator, not by an agent revising
# prose, and one pom.xml change staleness-marks six of them at once.
GENERATOR_SOURCES = frozenset({"api-inventory", "journey-map", "repo-topology"})


# The same three sources answer a SECOND question badly, so ranking asks a
# narrower one. "Who may edit this" and "is this filler" are not the same
# property, and conflating them cost the corpus its best notes:
#
#   api-inventory, repo-topology  an endpoint table, a directory listing --
#                                 extracted from code, nothing learned
#   journey-map                   prose a person wrote in a journey document,
#                                 moved into a note by a script
#
# A journey note is mechanically written and hand-authored at the same time.
# Measured with `note eval` on a 106-note service and a 20-question golden
# set: five of the twenty answers carried `source: journey-map`, so treating
# the whole source set as filler dropped recall@1 from 0.95 to 0.75 and
# pushed all 56 such notes to the back of the context budget behind anything
# with a ticket key on it.
#
# Excluding only the two real inventories left recall@1 at 0.95 and took the
# last bulk row out of the top five, which is the split this encodes.
BULK_INDEX_SOURCES = frozenset({"api-inventory", "repo-topology"})


def is_generated(note: "Note") -> bool:
    """Whether a bulk generator wrote this note rather than a person or agent.

    An unfamiliar source falls to hand-written, which is the safe direction:
    the cost is one avoidable block, against silently never blocking on a
    real finding.

    This answers "may an agent revise this by hand" -- it decides whether the
    Stop gate blocks. For "is this an index rather than a finding", which is
    what ranking wants, use `is_bulk_index`.
    """
    return note.source in GENERATOR_SOURCES


def is_bulk_index(note: "Note") -> bool:
    """Whether this note is an extracted listing rather than something learned.

    The ranking question. Every bulk index is generated; not every generated
    note is a bulk index (see BULK_INDEX_SOURCES).
    """
    return note.source in BULK_INDEX_SOURCES


def _is_sensitive(path: Path) -> bool:
    """Same fnmatch-against-name rule as core.inspector._is_sensitive."""
    name = path.name.casefold()
    return any(fnmatch.fnmatch(name, pattern) for pattern in SENSITIVE_PATTERNS)


def _scope_anchor(project: Path, entry: str) -> tuple[Path, Path] | None:
    """The (root, candidate) a scope entry resolves against.

    A note about an FM overlay is usually about the parent class it extends,
    so an ``@parent:<label>/<relative>`` entry resolves against that linked
    parent's root instead of the project's; a plain entry resolves against
    the project root as before. None means an ``@parent:`` entry names a
    link `.eos/config.toml` does not configure -- shared by
    `_resolve_scope_entry` (which turns that into a refusal) and
    `stale_notes` (which turns it into "removed", since either way the file
    cannot be located).
    """
    from core import links  # deferred: links imports config_io, avoid a module-load cycle

    if entry.startswith(links.PARENT_PREFIX):
        label, _, relative = entry[len(links.PARENT_PREFIX):].partition("/")
        link = links.read_links(project).get(label)
        if link is None:
            return None
        root = links.resolve_link_path(project, link)
        return root, (root / relative).resolve()
    return project, (project / entry).resolve()


def _resolve_scope_entry(project: Path, entry: str) -> Path:
    """Resolve one scope entry and refuse it if it escapes its root (the
    project root, or a linked parent's root for an ``@parent:`` entry) or
    names a sensitive file.

    Mirrors core.inspector.read_file's containment check
    (`candidate.relative_to(project)` inside try/except ValueError) and its
    SENSITIVE_PATTERNS check, applied here to scope paths for the same
    reason: scope is hashed and the hash is committed, so an out-of-bounds or
    sensitive path must never reach _hash_file.
    """
    from core import links  # deferred: links imports config_io, avoid a module-load cycle

    anchor = _scope_anchor(project, entry)
    if anchor is None:
        raise ValueError(f"Scope entry names an unconfigured parent link: {entry!r}")
    root, candidate = anchor
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        kind = "parent" if entry.startswith(links.PARENT_PREFIX) else "project"
        raise ValueError(f"Scope entry escapes the {kind} root: {entry!r}") from exc
    if _is_sensitive(candidate):
        raise ValueError(f"Scope entry names a sensitive file: {entry!r}")
    return candidate


def _hash_file(path: Path) -> str | None:
    """sha256 hexdigest of a file's bytes, or None if `path` is not a file.

    A scope entry documented as "files or classes this note is about" is
    often a class name, not a path -- that resolves to a non-existent file
    and must hash to None rather than raise.
    """
    if not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _one_note(corpus: list[Note], needle: str, what: str) -> Note:
    """The one note a file name or title names; ValueError naming `what` otherwise."""
    wanted = needle.casefold()
    exact = [n for n in corpus if n.path.name.casefold() == wanted or n.title.casefold() == wanted]
    matches = exact or [n for n in corpus if wanted in n.path.name.casefold() or wanted in n.title.casefold()]
    if len(matches) != 1:
        names = "; ".join(f"{n.path.name} ({n.title!r})" for n in matches[:5])
        raise ValueError(f"{what} {needle!r} names {len(matches)} notes{': ' + names if names else ''}; "
                         "name exactly one by its file name or title")
    return matches[0]


def superseded(corpus: list[Note]) -> dict[str, list[str]]:
    """{file name of a replaced note: the notes replacing it}. A pair inside a
    cycle (hand edits, a merge) replaces nothing: hiding both would lose both."""
    replaces = {note.path.name: note.supersedes for note in corpus if note.supersedes}

    def in_cycle(start: str) -> bool:
        seen, current = set(), start
        while current in replaces and current not in seen:
            seen.add(current)
            current = replaces[current]
            if current == start:
                return True
        return False

    found: dict[str, list[str]] = {}
    for name, target in sorted(replaces.items()):
        if not in_cycle(name):
            found.setdefault(target, []).append(name)
    return found


@dataclasses.dataclass(frozen=True)
class Note:
    """One authored note, as read back from disk."""

    path: Path
    kind: str
    title: str
    created: str
    source: str | None
    tags: list[str]
    scope: list[str]
    scope_hashes: list[str | None]
    body: str
    session: str | None = None
    # kind: procedure (ADR-023). `procedure` is the slug an execution names;
    # the other four are observations only `eos run finish` writes.
    procedure: str | None = None
    runs_ok: int | None = None
    runs_failed: int | None = None
    last_verified: str | None = None
    last_execution: str | None = None
    # kind: lesson (ADR-024) -- the execution that taught it.
    execution: str | None = None
    # The file name of the note this one replaces (ADR-033 addendum).
    supersedes: str | None = None
    # Who wrote it and until when it holds (2.x M1, forward-only): absent on
    # every note written before. Validity starts at `created`.
    provenance: str | None = None
    agent: str | None = None
    valid_until: str | None = None


def _unscalar(text: str) -> str:
    text = text.strip()
    if text.startswith('"'):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text
    return text


def parse_note(path: Path) -> Note:
    """Read one note file. Understands only the front matter this module writes."""
    raw = path.read_text(encoding="utf-8")
    meta: dict = {}
    body = raw

    if raw.startswith("---\n"):
        front, separator, body = raw[4:].partition("\n---\n")
        if not separator:
            front, body = "", raw
        pending_list = None
        for line in front.splitlines():
            if line.startswith("  - "):
                if pending_list is not None:
                    item = line[4:].strip()
                    if pending_list == "scope_hashes" and item == "null":
                        meta[pending_list].append(None)
                    else:
                        meta[pending_list].append(_unscalar(line[4:]))
                continue
            key, _, value = line.partition(":")
            key, value = key.strip(), value.strip()
            if not key:
                continue
            if value:
                meta[key] = _unscalar(value)
                pending_list = None
            else:
                meta[key] = []
                pending_list = key

    return Note(
        path=path,
        kind=str(meta.get("kind", "")),
        title=str(meta.get("title", "")),
        created=str(meta.get("created", "")),
        source=meta.get("source") or None,
        tags=list(meta.get("tags") or []),
        scope=list(meta.get("scope") or []),
        scope_hashes=list(meta.get("scope_hashes") or []),
        body=body.strip(),
        session=meta.get("session") or None,
        procedure=meta.get("procedure") or None,
        runs_ok=_count(meta.get("runs_ok")),
        runs_failed=_count(meta.get("runs_failed")),
        last_verified=meta.get("last_verified") or None,
        last_execution=meta.get("last_execution") or None,
        execution=meta.get("execution") or None,
        supersedes=meta.get("supersedes") or None,
        provenance=meta.get("provenance") or None,
        agent=meta.get("agent") or None,
        valid_until=meta.get("valid_until") or None,
    )


PROVENANCES = ("human", "agent", "generated")


def expired(note: Note, today: datetime.date | None = None) -> bool:
    """True when the note's `valid_until` is before today. A date that does not
    parse is not an expiry: a hand-mangled field never hides a note."""
    if not note.valid_until:
        return False
    try:
        until = datetime.date.fromisoformat(str(note.valid_until))
    except ValueError:
        return False
    return until < (today or datetime.date.today())


def _count(value) -> int | None:
    """A counter from front matter; anything that is not a whole number is
    absent rather than zero, so a hand-mangled count reads as missing."""
    try:
        return int(str(value).strip()) if value not in (None, "") else None
    except ValueError:
        return None


def load_notes(project_root: str | Path) -> list[Note]:
    """Every note for this project, oldest first (filenames start with a date)."""
    return load_dir(notes_dir(project_root))


def load_dir(directory: str | Path) -> list[Note]:
    """The notes directly in one store directory, oldest first; subdirectories
    (an `archive/`) are not read. A host searching several stores as one corpus
    loads each with this and ranks the union (2.x roadmap F3)."""
    directory = Path(directory)
    if not directory.is_dir():
        return []
    return [parse_note(path) for path in sorted(directory.glob("*.md"))]


# Letters and digits of any script. ASCII-only cut words in any other
# language into fragments: a prompt "PR aç" lost both words, "planı" became
# "plan" and matched an unrelated "rate-plan-change" in a procedure's body.
_WORD = re.compile(r"[^\W_]+")


def stale_notes(project_root: str | Path) -> list[dict]:
    """Notes whose scope files have moved since they were recorded.

    Pairs each note's scope entries with the hashes recorded for them at
    write time. A note written before scope_hashes existed has an empty
    scope_hashes list, so zip() naturally yields nothing for it -- that is
    the backward-compatibility behaviour, not a case handled separately.
    A stored hash of None means the entry never resolved to a real file
    (a class name, typically) and is skipped rather than flagged.

    Resolves each entry through `_scope_anchor`, the same function
    `_resolve_scope_entry` uses to hash it at write time -- an `@parent:`
    entry checked against `(project / scope_entry)` instead would never
    exist under the project root and every such note would be reported
    "removed" regardless of the parent file's real state.
    """
    project = Path(project_root).expanduser().resolve()
    stale = []
    for note in load_notes(project_root):
        issues = []
        for scope_entry, stored_hash in zip(note.scope, note.scope_hashes):
            if stored_hash is None:
                continue
            anchor = _scope_anchor(project, scope_entry)
            resolved = anchor[1] if anchor else None
            if resolved is None or not resolved.exists():
                issues.append({"scope": scope_entry, "reason": "removed"})
            elif _hash_file(resolved) != stored_hash:
                issues.append({"scope": scope_entry, "reason": "changed"})
        if issues:
            # `source`/`generated` are additive: verify-journey-docs.py reads
            # `note` and `issues` and must keep working unchanged. The gate
            # needs the classification here rather than re-opening the file,
            # so that "is this generated" is decided in exactly one place.
            stale.append({
                "note": note.path.name,
                "issues": issues,
                "source": note.source,
                "generated": is_generated(note),
            })
    return stale


def is_notes_dir_gitignored(project_root: str | Path) -> bool:
    """Whether the resolved notes directory falls under the project's own
    .gitignore, using the same fnmatch-against-path-or-basename rule the
    scanner itself uses (see Scanner.is_ignored).

    True by default: DEFAULT_NOTES_DIR sits inside .eos/, and every project
    this tool has seen ignores .eos/ wholesale as generated state. Notes
    written there never reach a commit until the project either carves out
    an exception or reconfigures knowledge_dir outside .eos/ — silently, so
    this is worth a doctor warning rather than a debugging session.
    """
    project = Path(project_root).expanduser().resolve()
    gitignore = project / ".gitignore"
    if not gitignore.is_file():
        return False

    directory = notes_dir(project_root)
    try:
        rel = directory.relative_to(project).as_posix()
    except ValueError:
        return False  # reconfigured outside the project entirely

    patterns = [
        line.strip()
        for line in gitignore.read_text(encoding="utf-8", errors="replace").splitlines()
        if line.strip() and not line.strip().startswith("#") and not line.strip().startswith("!")
    ]
    return any(_gitignore_pattern_matches(rel, pattern) for pattern in patterns)


def _gitignore_pattern_matches(rel_path: str, pattern: str) -> bool:
    """Approximate gitignore matching for one path against one pattern.

    A plain fnmatch of the full path is not enough: the common case is a
    directory pattern like ``.eos/`` that must match everything *under* it,
    not just that exact string. Checked against every ancestor of rel_path
    (each partial path and each path segment on its own), which covers
    directory patterns, bare names and simple globs. Does not implement
    gitignore's anchoring (leading ``/``) or ``**`` semantics — those matter
    for a full ignore engine, not for "is this one known path excluded".
    """
    pattern = pattern.rstrip("/")
    parts = Path(rel_path).parts
    for i in range(1, len(parts) + 1):
        prefix = "/".join(parts[:i])
        if fnmatch.fnmatch(prefix, pattern) or fnmatch.fnmatch(parts[i - 1], pattern):
            return True
    return False


# --- procedures (ADR-023) -----------------------------------------------------------
#
# A procedure is a note whose body carries `## Steps`. The engine parses the
# sections and never interprets them; the only fields it writes are the four
# observations below, and only `record_procedure_run` writes those.

_HEADING = re.compile(r"^#{1,6}\s+(?P<name>.+?)\s*$")


_LIST_ITEM = re.compile(r"^\s*(?:\d+[.)]|[-*+])\s+(?P<text>.+?)\s*$")


def section_in(body: str, name: str) -> str | None:
    """The text under one `## <name>` heading, up to the next heading of any
    level; None when there is no such heading. Case-insensitive, so a person
    writing `## steps` is not told their procedure has none."""
    lines = (body or "").splitlines()
    inside, found = False, []
    for line in lines:
        heading = _HEADING.match(line)
        if heading:
            if inside:
                break
            inside = heading.group("name").strip().casefold() == name.casefold()
            continue
        if inside:
            found.append(line)
    return "\n".join(found).strip() if inside or found else None


def _items(text: str | None) -> list[str]:
    return [m.group("text") for m in map(_LIST_ITEM.match, (text or "").splitlines()) if m]


def steps_in(body: str) -> list[str]:
    return _items(section_in(body, "Steps"))


def _set_front(raw: str, fields: dict) -> str:
    """Replace or add scalar front-matter lines, leaving every other byte alone.

    A targeted edit rather than a re-serialisation: the file carries fields
    this module did not write (`updated`, a person's own keys), and a counter
    update must not be the thing that loses them.
    """
    if not raw.startswith("---\n"):
        raise ValueError("not a note: no front matter")
    front, separator, rest = raw[4:].partition("\n---\n")
    if not separator:
        raise ValueError("not a note: front matter is not closed")
    lines = front.splitlines()
    for key, value in fields.items():
        rendered = f"{key}: {_scalar(value)}"
        for index, line in enumerate(lines):
            if line.split(":", 1)[0].strip() == key and not line.startswith("  - "):
                lines[index] = rendered
                break
        else:
            lines.append(rendered)
    return "---\n" + "\n".join(lines) + "\n---\n" + rest


def append_bullet(body: str, section: str, line: str) -> str:
    """One bullet under `## <section>`, the section created at the end when it
    does not exist yet."""
    KNOWN = section
    bullet = f"- {line}"
    lines = body.rstrip("\n").split("\n")
    for index, current in enumerate(lines):
        heading = _HEADING.match(current)
        if heading and heading.group("name").strip().casefold() == KNOWN.casefold():
            end = index + 1
            while end < len(lines) and not _HEADING.match(lines[end]):
                end += 1
            while end > index + 1 and not lines[end - 1].strip():
                end -= 1
            lines.insert(end, bullet)
            return "\n".join(lines) + "\n"
    return "\n".join(lines) + f"\n\n## {KNOWN}\n\n{bullet}\n"


def append_to_note_section(path: Path, section: str, line: str) -> None:
    """Append one bullet to a section of an existing note, front matter untouched."""
    from core.lib import atomic, lock

    with lock.locked(path):
        raw = path.read_text(encoding="utf-8")
        if not raw.startswith("---\n"):
            raise ValueError(f"{path} is not a note")
        front, separator, body = raw[4:].partition("\n---\n")
        atomic.write_text(path, "---\n" + front + separator + append_bullet(body, section, line))
