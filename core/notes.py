"""Authored knowledge notes.

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
import fnmatch
import datetime
import hashlib
import json
import math
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
        value = cfg.get("knowledge", {}).get("dir")
        if value:
            configured = str(value)

    return (project / configured).resolve()


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


KINDS = ("defect", "finding", "procedure")

# Words that routinely follow "password:" or "token=" in prose about an API.
# Without them the guard fires on ordinary sentences, and a guard that fires on
# ordinary sentences gets routed around instead of obeyed.
_PLACEHOLDER_VALUES = {
    "required", "optional", "null", "none", "empty", "blank", "missing",
    "redacted", "present", "absent", "invalid", "valid", "expired", "unset",
}

# `\b` cannot open this pattern: `_` is a word character, so there is no
# boundary in front of the keyword in `aws_secret_access_key` or before the
# capital in `clientSecret`, and neither shape was ever caught. The keyword may
# also carry a suffix (`secret` + `_access_key`), and the workspace's notes
# write credentials into markdown tables as often as into assignments, so `|`
# counts as a separator.
#
# NOT the same rule as .githooks/pre-commit's SECRET_ASSIGNMENT_PATTERN, and it
# must not be described as such -- see the note above _CREDENTIAL_VALUE, which
# lists the ways the two disagree. The keyword lists differ too: `authorization`
# is here and not there.
_SECRET_ASSIGNMENT = re.compile(
    r"(?:^|[^A-Za-z0-9])"
    r"(?P<key>password|passwd|pwd|passphrase|client[-_]?secret|aws[-_]?secret[-_]?access[-_]?key"
    r"|secret|token|api[_-]?key|access[_-]?key|private[_-]?key|credential|authorization)"
    r"[A-Za-z0-9_-]*"
    r"\s*[=:|]\s*(?P<value>\S+)",
    re.IGNORECASE,
)

# A credential is recognised by its VALUE, not by the word in front of it.
# Reading only the keyword refused "the gateway strips Authorization: Bearer
# headers" -- a sentence, not a secret. This requires a 12+ character run of
# credential-charset characters somewhere in the value -- the same FLOOR
# .githooks/pre-commit's SECRET_ASSIGNMENT_PATTERN uses, checked with
# .search() rather than a full-string match so that one stray character
# (leading or trailing) does not exempt an otherwise credential-shaped run.
# This is NOT equivalent to the commit hook and must not be described as
# such: the hook is a three-stage pipeline (SECRET_ASSIGNMENT_PATTERN, then
# a PLACEHOLDER_PATTERN carve-out, then a REFERENCE_PATTERN carve-out that
# whitelists any value containing a credential keyword itself, e.g.
# "password = password_field.get()"), and this function reproduces none of
# the placeholder or reference stages.
#
# The two agree on the real corpus and disagree in BOTH directions on
# constructed input. Measured by running this guard and the hook itself, as
# actual shell, over the same files -- 271 notes / 13,326 lines: 0 refusals
# each, 0 disagreements. On an adversarial set, 9 disagreements:
#
#   refused here, accepted by the hook: `Authorization: Basic <base64>` and
#     `Authorization: Bearer <opaque>` (no `authorization` keyword there, and
#     it wants the 12-char run immediately after the `[:=]`); `clientSecret:
#     <secret>` (its keyword needs a separator before `secret`); a PEM block;
#     a `Bearer eyJ...` JWT (it has neither pattern).
#   refused by the hook, accepted here: `credential: acknowledge` ending in
#     a full stop -- the period pushes an 11-letter word to 12 charset
#     characters there, and the hook refused this very comment block until
#     the period was moved out of the quote; `PGPASSWORD=` and
#     `SSH_BASTION_PASS=` assignments, which the hook
#     names explicitly and _SECRET_ASSIGNMENT cannot reach at all, because
#     `(?:^|[^A-Za-z0-9])` finds no boundary inside PGPASSWORD. That last
#     pair is a pre-existing hole here, not a regression, and is left alone.
#
# So this is deliberately the simpler check the note store needs, not a
# reimplementation of the commit gate, and neither layer subsumes the other.
_CREDENTIAL_VALUE = re.compile(r"[A-Za-z0-9+/=_.~-]{12,}")

# An HTTP `Authorization:` header does not put its credential where an
# assignment does. `_SECRET_ASSIGNMENT`'s value group reads the first \S+ after
# the separator, which for header syntax is the SCHEME WORD -- so
# "Authorization: Basic <28 chars of base64>" was measured as a 5-character
# value, failed the 12-character floor and was accepted. The credential is the
# token after the scheme. `.githooks/pre-commit` misses these too (no
# `authorization` keyword, and it wants the run immediately after the `[:=]`),
# so this is the only layer that sees them, and the note store IS committed.
_AUTH_HEADER = re.compile(
    r"authorization[A-Za-z0-9_-]*\s*[=:|]\s*"
    r"(?:basic|bearer|digest|negotiate|ntlm|token|apikey)\s+"
    r"(?P<value>\S+)",
    re.IGNORECASE,
)

_CREDENTIAL_PATTERNS = (
    ("private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("JSON Web Token", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")),
    ("AWS access key id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    # The keyword rule reads an assignment, so it cannot see a password carried
    # in the userinfo of a connection URI: the name in front of the `:` there is
    # the database user, not a keyword. That is exactly the shape this
    # workspace's DB notes are written in (`mongodb://user:pass@host`), and the
    # shape of the one leak .githooks/pre-commit records as having happened.
    # `$`, `<` and `{` stay out of the value charset so placeholders survive.
    ("connection string credential",
     re.compile(r"://[A-Za-z0-9_.~%-]+:[A-Za-z0-9+/=_.~%-]{8,}@")),
)


def _looks_opaque(value: str) -> bool:
    """Whether a value is a token rather than a word.

    "Authorization: Basic authentication is required" is prose;
    "Authorization: Basic Zm11c2VyOlMz..." is a credential. Length does not
    separate them -- "authentication" clears the 12-character floor on its own
    -- so shape has to: an opaque token, a UUID or a base64 blob carries a
    digit or a case change, and an English word carries neither. Base64 of any
    real `user:password` pair mixes case even when it happens to carry no
    digit.

    Used only on the `Authorization:` branch below, which accepts everything
    today, so it can only tighten. Applying it to the assignment branch as
    well would loosen that one -- an all-lowercase passphrase is a real
    password shape -- and this fix is not about relaxing what already works.
    """
    return any(char.isdigit() for char in value) or (
        value != value.lower() and value != value.upper()
    )


def _credential_value(raw_value: str) -> bool:
    """Whether a captured value is a real credential rather than a placeholder.

    Length/charset test strips only TRAILING punctuation, not leading, before
    searching -- neither extreme is right. Testing the fully stripped value let
    a leading-dot-padded run get trimmed below the 12-char floor (the
    dot_padded case in tests/test_note_cli.py). Testing raw_value un-stripped
    went too far the other way: a sentence-ending period is itself a
    credential-charset character, so an 11-character word immediately followed
    by a full stop reached 12 raw and was refused -- exactly the over-refusal
    this guard exists to avoid (see the prose cases in
    test_root_cause_prose_is_not_mistaken_for_a_credential). A trailing period
    is always sentence punctuation in this corpus, never part of a credential;
    a leading one is not assumed to be either way, so it is left alone.
    """
    value = raw_value.strip("\"'`.,;|")
    if not value or value.lower() in _PLACEHOLDER_VALUES:
        return False
    if value.startswith(("<", "$", "{")) or set(value) <= {"*", ".", "x", "X"}:
        return False
    return bool(_CREDENTIAL_VALUE.search(raw_value.rstrip("\"'`.,;|")))


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


def _find_credential(text: str) -> str | None:
    """Return a description of the first credential-looking thing found."""
    for description, pattern in _CREDENTIAL_PATTERNS:
        if pattern.search(text):
            return description

    for match in _AUTH_HEADER.finditer(text):
        raw_value = match.group("value")
        if _credential_value(raw_value) and _looks_opaque(raw_value.strip("\"'`.,;|")):
            return "Authorization header credential"

    for match in _SECRET_ASSIGNMENT.finditer(text):
        if _credential_value(match.group("value")):
            return f"assigned secret ({match.group('key')})"
    return None


def _compose_body(kind, body, cause, solution, metric) -> str:
    """Validate the fields this kind requires and render the note body.

    A defect must carry cause, solution and metric. This is the one part of the
    earlier design worth copying verbatim: the schema that made all three
    mandatory is the only one that ever accumulated records worth re-reading,
    while every optional-field schema beside it stayed empty.
    """
    if kind not in KINDS:
        raise ValueError(f"Unknown note kind {kind!r}; expected one of: {', '.join(KINDS)}")

    if kind == "defect":
        provided = {"cause": cause, "solution": solution, "metric": metric}
        missing = [name for name, value in provided.items() if not (value or "").strip()]
        if missing:
            raise ValueError(
                "A defect note must record cause, solution and metric; "
                f"missing: {', '.join(missing)}"
            )
        sections = []
        if (body or "").strip():
            sections += [body.strip(), ""]
        sections += [
            "## Root cause", "", cause.strip(), "",
            "## Solution", "", solution.strip(), "",
            "## Metric", "", metric.strip(),
        ]
        return "\n".join(sections)

    if kind == "procedure":
        if not steps_in(body or ""):
            raise ValueError(
                "A procedure note must have a `## Steps` section with at least one "
                "numbered or bulleted step. Without steps it is a finding, and "
                "should be written as one.")
        return body.strip()

    if not (body or "").strip():
        raise ValueError("A finding note must have a body")
    return body.strip()


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


SKIPS_FILE = ".skips.jsonl"


def skips_path(project_root: str | Path) -> Path:
    """Where 'this session had nothing worth recording' decisions are kept.

    A dotfile inside the notes directory: load_notes globs '*.md', so this is
    invisible to every reader of the corpus and cannot dilute retrieval.
    """
    return notes_dir(project_root) / SKIPS_FILE


def record_skip(project_root: str | Path, reason: str, session: str | None = None) -> Path:
    """Record a decision not to write a note. Appends; never rewrites.

    The reason is placeholder-guarded for the same reason `add_note`'s title
    is: the gate prints `eos note skip <root> --reason '...'` and its
    `--reason` IS quoted, so unlike the `eos note add` line beside it this one
    runs verbatim to exit 0 -- silencing the gate for the whole session on a
    reason that says nothing. Found by running every command the gate prints,
    in every shape, through a real shell.

    Credential-guarded for the same reason `add_note`'s body is, and with the
    same function: this is the writer an agent is routed to under gate
    pressure, and "<connection URI> is what broke it" is exactly the shape a
    reason takes. `.skips.jsonl` is gitignored (.gitignore:191), so a
    credential written here does not travel the way one in a note would --
    that bounds the damage, it does not remove the reason to refuse it.
    """
    _refuse_placeholder(reason, "skip reason")
    found = _find_credential(reason)
    if found:
        raise ValueError(
            "Refusing to write a skip reason containing what looks like a "
            f"credential: {found}. Describe the value instead of pasting it."
        )
    path = skips_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "date": datetime.date.today().isoformat(),
        "session": session,
        "reason": reason,
    }
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return path


def was_skipped(project_root: str | Path, session: str) -> bool:
    """Whether `session` already recorded a decision not to write a note.

    Hardened the same way the gate's per-file `*.md` read is, though not for
    the same reason: the notes beside it are git-tracked, this file is not
    (`.skips.jsonl` is gitignored at .gitignore:191, deliberately -- it
    records a local, momentary fact). So a bad line here is one machine's
    problem, not every machine's; it is still hand-editable, still read by the
    gate on every Stop, and still able to silence it.
    `read_text()` with no `errors=` raised UnicodeDecodeError on
    non-UTF-8 bytes, and `.get()` on whatever `json.loads` returned raised
    AttributeError on a bare JSON scalar -- neither is a ValueError, both
    escaped to the gate's broad `except`, and the gate reads any exception as
    "cannot check -> do not block". Measured: gate 2 with no skips file, gate 0
    with either of those present.

    So the whole read fails CLOSED. A lookup that cannot read its file has
    found no skip, which is what False means here; failing open would silence
    the gate over a corrupt file, which is exactly the bug. Per-line, so one
    mangled line costs that line and not the file.
    """
    path = skips_path(project_root)
    if not path.is_file():
        return False
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    for line in content.splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        # A scalar or a list is not a skip record. `.get()` on one raises, and
        # a raise here reads as "do not block" three frames up.
        if isinstance(entry, dict) and entry.get("session") == session:
            return True
    return False


class DuplicateNoteError(ValueError):
    """A note that restates one already in the store."""


def _duplicate_escape(session: str | None) -> str:
    """The tail of a duplicate refusal: how to get past it, exactly.

    The old message ended at "read that file and edit it directly", which is a
    dead end when the writer is answering a Stop-hook block. Reproduced end to
    end: a previous session records a finding; this session meets the refusal,
    appends to the existing note as instructed, and the gate still exits 2 --
    because the gate matches on the note's `session` front-matter field, which
    the append does not touch. The only exit left was `eos note skip`, which
    records that nothing was learned: precisely the wrong answer.

    Adding this session's id to that note's front matter is what actually
    satisfies the check (verified), and nothing said so. With no session id in
    hand there is nothing to name, so that half is left off rather than
    printed with a blank after it. `eos note amend` did not exist when this
    was written; it does now, and does exactly what this message describes
    doing by hand (rewrite the body, set the session) in one command.
    """
    if not session:
        return "Append anything new to that file rather than writing a second note."
    return (
        "Append anything new to that file, then record this session against it "
        f"by adding a `session: {session}` line to its front matter. Appending "
        "alone does not satisfy the Stop-hook gate, which reads that field -- "
        "and `eos note skip` is not the way past a duplicate."
    )


# A value that is nothing but an unsubstituted placeholder from a printed
# command: `<what is true now>`, `<TICKET>`, or a bare `...`/`…`. Anchored end
# to end on the whole stripped value, so a real body that mentions `List<T>`,
# quotes a diff, or trails off in an ellipsis mid-sentence is untouched -- the
# refusal is for a value that carries no claim at all.
_PLACEHOLDER_RE = re.compile(r"\A(?:<[^<>]*>|\.{3,}|…)\Z")


def _is_placeholder(value: str) -> bool:
    """Whether this value is an unsubstituted placeholder.

    The Stop-hook gate prints commands meant to be run, and an agent runs them
    as printed: `--body '<what is true now>'` and `--title '...' --body '...'`
    both arrive here intact. This is the same class of nothing as the empty
    and unchanged bodies already refused, and in `amend_note` it is worse than
    either -- amend re-hashes `scope_hashes`, so accepting a placeholder
    overwrites the note's real claim AND clears its stale flag, taking it
    permanently off the gate's radar on exit 0. A loud refusal naming the
    placeholder is the only safe answer.
    """
    return bool(_PLACEHOLDER_RE.match(value.strip()))


def _refuse_scope(scope: list[str] | None) -> None:
    """Every scope entry must name a real file, or the note monitors nothing.

    Two ways that failed silently, both found in the corpus rather than
    imagined. A printed command copied verbatim keeps its `--scope '<files>'`,
    and the entry was stored as the literal string with a null hash beside it:
    the note reads as scoped, `note audit` can never report it stale, and the
    injector can never match it to a touched file. And an empty entry among
    real ones (`["a", "", "b"]`) was dropped without a word, so a note ended up
    watching fewer files than its author wrote and nothing said which.

    Refused at the door, because neither is recoverable afterwards: the hashes
    are taken at write time, so a note that was never scoped correctly cannot
    be told from one whose files changed.
    """
    if not scope:
        return
    for position, entry in enumerate(scope, start=1):
        where = f"scope entry {position} of {len(scope)}"
        if not entry.strip():
            raise ValueError(
                f"The {where} is empty. An empty entry used to be dropped in "
                "silence, which left the note scoped to fewer files than it "
                "names; say the file or leave it out."
            )
        _refuse_placeholder(entry, where)


def _refuse_placeholder(value: str, what: str) -> None:
    """Raise if `value` is nothing but a placeholder, saying what to do."""
    if _is_placeholder(value):
        raise ValueError(
            f"The {what} is still the placeholder {value.strip()!r}. It was "
            "printed as an example, not as a value: replace it with the "
            f"{what} in your own words before running the command."
        )


def body_digest(body: str) -> str:
    """Hash of a note body with whitespace normalised.

    Byte-identical bodies already exist 15 times over in the corpus, the
    largest group spanning 12 services -- all of them generated. A writer that
    is forced to produce a note on every session will add more.
    """
    return hashlib.sha256(" ".join(body.split()).encode("utf-8")).hexdigest()


def normalized_title_key(title: str) -> frozenset[str]:
    """Token set of a title, case- and punctuation-insensitive, digits masked.

    Measured over the corpus this key catches all 15 genuine duplicate groups
    and false-merges 5 -- api part-notes whose titles differ only by a number,
    which is why the digits are masked rather than dropped. On the 9
    hand-written notes it produces zero collisions.
    """
    lowered = re.sub(r"\d+", "#", title.lower())
    return frozenset(w for w in re.findall(r"[a-z#]+", lowered) if len(w) > 2)


def add_note(
    project_root: str | Path,
    kind: str,
    title: str,
    body: str | None = None,
    tags: list[str] | None = None,
    scope: list[str] | None = None,
    source: str | None = None,
    cause: str | None = None,
    solution: str | None = None,
    metric: str | None = None,
    session: str | None = None,
    procedure: str | None = None,
) -> Path:
    """Write one note and return its path.

    One file per note, named ``<YYYYMMDD>-<slug>``. A single shared store was
    the obvious alternative and is the wrong one: notes are committed, and
    every author appending to one file turns routine work into merge conflicts.

    ``session``, like `record_skip`'s, is optional and purely a marker of
    which session wrote this note -- not identity or authorship in any wider
    sense. It exists so a caller (the Stop-hook gate) can ask "did THIS
    session record something" exactly, rather than guessing from mtime: this
    store is git-tracked, so a pull, checkout, stash pop or reinstall rewrites
    every file's mtime at once, and measured on the real corpus, 252 of 271
    notes already share just three mtime minutes -- bulk filesystem events,
    not writes.
    """
    # Before composing, so a placeholder is named as what the caller typed
    # rather than as whatever _compose_body wrapped it in. The gate's fallback
    # message prints `--title '...' --body '...'` and that runs verbatim too.
    _refuse_placeholder(title, "note title")
    if body is not None:
        _refuse_placeholder(body, "note body")
    if source is not None:
        _refuse_placeholder(source, "note source")
    _refuse_scope(scope)
    content = _compose_body(kind, body, cause, solution, metric)

    found = _find_credential(f"{title}\n{content}")
    if found:
        raise ValueError(
            f"Refusing to write a note containing what looks like a credential: {found}. "
            "Describe the value instead of pasting it."
        )

    directory = notes_dir(project_root)
    directory.mkdir(parents=True, exist_ok=True)

    stamp = datetime.date.today().strftime("%Y%m%d")
    # _slug strips every character that is not a letter or digit, so a title
    # can never introduce a path separator or a parent reference here.
    path = directory / f"{stamp}-{_slug(title)}.md"

    existing = load_notes(project_root)
    new_digest = body_digest(content)
    for note in existing:
        if body_digest(note.body) == new_digest:
            raise DuplicateNoteError(
                f"This body is already recorded in {note.path} "
                f"({note.title!r}). " + _duplicate_escape(session)
            )
    new_key = normalized_title_key(title)
    # An empty key means the title carried no comparable signal (e.g. "AB",
    # "Q3") -- not that it matches every other title reduced the same way.
    for note in (existing if new_key else ()):
        if normalized_title_key(note.title) == new_key:
            raise DuplicateNoteError(
                f"{note.path} ({note.title!r}) already covers this title. "
                f"Choose a title that says something different, or keep this "
                f"one and stay in that note. " + _duplicate_escape(session)
            )

    if path.exists():
        raise FileExistsError(
            f"A note already exists at {path}. Notes are append-only; "
            "edit that file directly or choose a different title."
        )

    project = Path(project_root).expanduser().resolve()
    scope_hashes = (
        [_hash_file(_resolve_scope_entry(project, entry)) for entry in scope] if scope else None
    )

    document = _front_matter(
        {
            "kind": kind,
            "title": title,
            "created": stamp[:4] + "-" + stamp[4:6] + "-" + stamp[6:],
            "source": source,
            "tags": tags,
            "scope": scope,
            "scope_hashes": scope_hashes,
            "session": session,
            **(_procedure_front(procedure or _slug(title), 0, 0, None, None)
               if kind == "procedure" else {}),
        }
    )
    path.write_text(f"{document}\n\n{content}\n", encoding="utf-8")
    return path


# `_compose_body` writes these as literal `## Name` lines. A raw substring
# check against that literal is wrong in both directions: it is
# case-sensitive, so a heading written `## Root Cause` was refused as
# missing; and it is not anchored to a line, so `### Root cause` (a real,
# differently-leveled heading) happened to satisfy it only because its
# characters overlap "## Root cause" one position over -- the same substring
# would just as happily match inside a sentence that merely *mentions* the
# heading text and never states it. Anchoring to the start of a line and
# allowing any run of `#` fixes both: a real heading at any level matches for
# the right reason, prose that quotes the heading text does not.
_DEFECT_SECTIONS = ("Root cause", "Solution", "Metric")
_DEFECT_HEADING = {
    name: re.compile(rf"^#+[ \t]*{re.escape(name)}\b", re.IGNORECASE | re.MULTILINE)
    for name in _DEFECT_SECTIONS
}


def _canonical_scope(entries) -> frozenset:
    """A scope list reduced to what this system actually reads from it.

    Nothing anywhere reads a scope entry's position: `stale_notes` zips each
    entry to its own hash, `add_note` and `amend_note` hash entry by entry,
    retrieval never sorts. A repeated entry hashes twice to the same value.
    So "does this --scope re-point the note" is a question about the SET of
    entries, and comparing lists answers a different, order- and
    multiplicity-sensitive question -- which left `B,A` and `A,A,B` as
    working ways to re-hash a note while re-pointing nothing.
    """
    return frozenset(entries)


def amend_note(
    note_path: str | Path,
    project_root: str | Path,
    body: str | None = None,
    reaffirm: str | None = None,
    scope: list[str] | None = None,
    session: str | None = None,
) -> Path:
    """Rewrite one note in place and re-hash its scope.

    Re-hashing is the point: `scope_hashes` is what `stale_notes` compares
    against, so this is the only operation that can clear a stale flag. That
    is also why an unchanged body is refused -- re-hashing without revising
    would close the flag without doing the work, and the gate would have
    taught agents to do exactly that.

    `--reaffirm` exists for the case where the file changed in a way that
    does not touch the note's claim. Without it the only escape would be to
    alter the body artificially, which is the dead end `skip` became in
    phase 1.

    `--scope`, on its own or alongside `--body`/`--reaffirm`, replaces the
    note's scope list wholesale (same shape as `add_note`'s `scope`). This
    is the escape for a deleted or moved scoped file: `stale_notes` reports
    that as `reason: "removed"`, and re-hashing the OLD path can only ever
    refuse -- there is no file there to hash. Re-pointing the note at the
    new location is the actual remedy, and it must not force a prose edit:
    a note whose claim is still correct needs only `--scope`, nothing else.
    Omitting `--scope` leaves the recorded scope untouched.

    One invariant governs every refusal below: an amend either records
    something a human wrote -- a revised body, or a reaffirm reason -- or it
    genuinely re-points the note at different files. It may never leave a
    previously-monitored note un-monitored, and it may never clear a stale
    flag while recording nothing. Three shapes of `--scope` violate that and
    are refused:

    - An explicitly empty `--scope`, never read as "clear the scope":
      `effective_scope` would end up `[]`, `scope_hashes` would never be
      computed, and `_front_matter` drops both `scope` and `scope_hashes`
      for being empty -- the note would be un-monitored with no trace, on
      exit 0, on the one command that is the escape from a blocking gate.
      Clearing scope entirely is not a thing this function does; if it is
      ever wanted it must be its own loud, explicit operation, not a side
      effect of a blank value.
    - A `--scope` naming the same SET of entries the note already has,
      for the same reason an unchanged `--body` is refused. Set, not list:
      see `_canonical_scope`.
    - A `--scope` that would leave no entry resolving to a real file, and a
      scope-only `--scope` that retires the stale flag of an entry which
      still exists and changed -- by keeping and re-hashing it, or by
      dropping it so the note stops claiming to be about it. All of these
      clear a flag while saying nothing; all become legal the moment the
      same call carries `--body` or `--reaffirm`, because then a human has
      written down why. Dropping an entry whose file is GONE is not one of
      them: that is the maintenance `--scope` exists for, and it needs no
      reason.

    `session` is overwritten, not appended, only when the caller supplies
    one; omitting it preserves whatever session the note already carried.
    Its single consumer is the Stop gate asking "did THIS session record
    something", so it means "the session that last wrote this note", and an
    omitted `--session` must not erase that record. Time provenance lives in
    the created/updated pair instead.

    `note_path` must resolve inside `notes_dir(project_root)` and must parse
    as a note (front matter with a non-empty kind and title). Both guard the
    same failure mode: a wrong or defaulted `--path` -- or a target that is
    not a note at all -- must refuse loudly rather than silently rewrite the
    wrong file or drop scope_hashes to null and un-monitor the note forever.
    Deliberately keyed off `notes_dir()`, not `project_root` itself: a
    project's notes commonly live outside its own tree (an FM service's
    `[knowledge] dir` points at a sibling), so containment has to follow
    wherever notes actually live, not assume it is under the project root.

    A scope entry that had a recorded hash is refused if the re-hash comes
    back None -- checked against whichever scope list ends up being written,
    old or replaced by `--scope` -- because that is "the file I described is
    gone", which must reach a human, not retire the note silently. A new
    body is also refused if it duplicates another note's body (the same
    rule `add_note` enforces, checked only for `--body`: a `--reaffirm`
    string is built as `body + "[date] Still holds: <reason>"`, so the dated
    line makes it near-unique by construction and this check could only
    misfire on a pre-existing twin the caller cannot see, on the one
    command that is the escape from a blocking gate), if it is empty, or --
    for a `defect` note -- if it drops the Root cause / Solution / Metric
    sections `_compose_body` requires. A `--reaffirm` reason must not be
    empty either, or it produces a note that still looks recorded and says
    nothing.
    """
    if body is not None and reaffirm is not None:
        raise ValueError("amend accepts only one of --body or --reaffirm")
    if body is None and reaffirm is None and scope is None:
        raise ValueError("amend needs at least one of --body, --reaffirm, or --scope")
    if scope is not None and not scope:
        raise ValueError(
            "--scope must name at least one file; an empty value is refused "
            "rather than treated as 'clear the scope'. Clearing scope entirely "
            "is not supported today."
        )
    _refuse_scope(scope)

    project = Path(project_root).expanduser().resolve()
    path = Path(note_path).expanduser().resolve()

    directory = notes_dir(project_root)
    try:
        path.relative_to(directory)
    except ValueError as exc:
        raise ValueError(
            f"{path} is not inside the notes directory ({directory}). Check "
            "--path: it defaults to the current directory, and a wrong or "
            "omitted --path would silently stop monitoring the note instead "
            "of failing loudly."
        ) from exc

    note = parse_note(path)
    if not note.kind or not note.title:
        raise ValueError(
            f"{path} does not look like a note (front matter has no kind or "
            "title); refusing to rewrite a file amend_note did not write."
        )

    if body is not None:
        if not body.strip():
            raise ValueError("amend body must not be empty or whitespace-only")
        _refuse_placeholder(body, "amend body")
        if body_digest(body) == body_digest(note.body):
            raise ValueError(
                "The body is unchanged. Amend is for revising a note that no "
                "longer matches the code; use --reaffirm to say the note still "
                "holds despite the file changing."
            )
        if note.kind == "defect":
            missing = [
                name for name in _DEFECT_SECTIONS if not _DEFECT_HEADING[name].search(body)
            ]
            if missing:
                raise ValueError(
                    "A defect note must keep its Root cause, Solution and Metric "
                    f"sections; missing: {', '.join(missing)}"
                )
        new_digest = body_digest(body)
        for other in load_notes(project_root):
            if other.path == path:
                continue
            if body_digest(other.body) == new_digest:
                raise DuplicateNoteError(
                    f"This body already appears in {other.path} ({other.title!r}). "
                    "Choose a body that says something that note does not, or "
                    "revise that note instead."
                )
        content = body.strip()
    elif reaffirm is not None:
        if not reaffirm.strip():
            raise ValueError("amend reaffirm reason must not be empty or whitespace-only")
        _refuse_placeholder(reaffirm, "amend reaffirm reason")
        stamp = datetime.date.today().isoformat()
        content = f"{note.body.strip()}\n\n[{stamp}] Still holds: {reaffirm.strip()}"
    else:
        if _canonical_scope(scope) == _canonical_scope(note.scope):
            raise ValueError(
                "--scope names the same set of entries the note already has "
                "(reordering them, or repeating one, re-points nothing), so "
                "re-hashing would clear the stale flag while recording "
                "neither a revision nor a reason. Use --body to revise what "
                "the note claims, or --reaffirm to record why it still holds "
                "despite the change."
            )
        content = note.body.strip()

    found = _find_credential(f"{note.title}\n{content}")
    if found:
        raise ValueError(
            f"Refusing to write a note containing what looks like a credential: {found}. "
            "Describe the value instead of pasting it."
        )

    effective_scope = scope if scope is not None else note.scope
    # A --body or --reaffirm in the same call is the human record that buys
    # every transition below. A scope-only amend has to stand on the scope
    # change alone, and there are things a scope change cannot buy.
    records_a_reason = body is not None or reaffirm is not None
    # Keyed by the entry STRING, not position: once --scope can replace the
    # list wholesale, index i in the new list has no relationship to index i
    # in the old one. A positional check refused a harmless reorder (claiming
    # an entry had a recorded hash it never had), silently skipped checking
    # every entry past the shortest of the two lists (b and c in [a,b,c] -> [a]
    # were dropped with no check at all), and let a null-hash position wave
    # through a new entry that resolves to nothing.
    old_hash_by_entry = dict(zip(note.scope, note.scope_hashes))

    scope_hashes = None
    if effective_scope:
        scope_hashes = []
        for entry in effective_scope:
            new_hash = _hash_file(_resolve_scope_entry(project, entry))
            old_hash = old_hash_by_entry.get(entry)
            if old_hash is not None and new_hash is None:
                raise ValueError(
                    f"Scope entry {entry!r} no longer resolves to a file, though "
                    "it had a recorded hash. Either the file was deleted or moved "
                    "-- fix the note's `scope:` to point at its new location -- "
                    "or --path names the wrong project root."
                )
            scope_hashes.append(new_hash)

    # stale_notes skips a null hash, so a note left with no non-null hash can
    # never be flagged again. Reaching that state through `--scope` -- naming
    # a class, a directory, a typo -- is the same permanent un-monitoring an
    # empty `--scope` is refused for, with a `scope:` line as its only trace.
    # A human-written reason buys the transition, because code a note
    # described genuinely can be deleted; nothing else does. Notes that never
    # had a resolving entry (five in the real corpus are scoped to class
    # names) lose nothing here and are not refused.
    was_monitored = any(hash_ is not None for hash_ in note.scope_hashes)
    if (
        not records_a_reason
        and was_monitored
        and not any(hash_ is not None for hash_ in scope_hashes or ())
    ):
        monitored = ", ".join(
            repr(entry) for entry, hash_ in old_hash_by_entry.items() if hash_ is not None
        )
        raise ValueError(
            "This amend would leave the note with no scope entry that resolves "
            "to a file, so nothing could ever flag it stale again; today it is "
            f"monitored through {monitored}. Point --scope at a file that exists, "
            "or -- if the code this note described is gone -- record that with "
            "--body or --reaffirm in the same call."
        )

    # One rule with two arms, and the last of the three because it is the
    # least severe: a scope-only amend may not retire the stale flag of an
    # entry that had a recorded hash, still resolves to a real file, and now
    # hashes differently -- whether it KEEPS that entry (and re-hashes it in
    # place, which appending one unrelated file to the scope was enough to
    # reach) or DROPS it (the note quietly stops claiming to be about the file
    # that moved under it). Both clear a flag and record nothing about the
    # code. The dropping arm is the lesson `eos note skip` taught in phase 1:
    # under gate pressure agents find the cheapest exit and the cheapest exit
    # becomes the default, and "I am no longer about that file" costs one flag
    # and says nothing. If a narrowing is genuine, the sentence explaining it
    # is the sentence worth having in the note anyway.
    #
    # Dropping an entry whose file is GONE is the opposite: that is the
    # maintenance --scope exists for (round 2 added it for exactly a deleted
    # or moved file) and it stays free of any reason. So does dropping an
    # entry that never resolved -- it was never monitored, so nothing retires.
    if not records_a_reason:
        for entry, old_hash in old_hash_by_entry.items():
            if old_hash is None:
                continue
            try:
                current = _hash_file(_resolve_scope_entry(project, entry))
            except ValueError:
                # An entry that cannot be resolved at all any more (an
                # `@parent:` label the config no longer configures) is in the
                # same position as a deleted file: there is nothing to compare
                # and re-pointing away from it is the remedy, not an evasion.
                # An entry the caller KEEPS still raises this, unchanged, from
                # the hashing loop above.
                continue
            if current is None or current == old_hash:
                continue
            action = (
                "re-hash it in place"
                if entry in effective_scope
                else "drop it from the scope"
            )
            raise ValueError(
                f"Scope entry {entry!r} changed since this note was recorded and "
                f"the file still exists, so a scope-only amend cannot {action}: "
                "that clears the stale flag while recording nothing about the "
                "change. Add --reaffirm to say the note still holds, or --body to "
                "revise what it claims. (Dropping an entry whose file is gone "
                "needs no reason.)"
            )

    document = _front_matter(
        {
            "kind": note.kind,
            "title": note.title,
            "created": note.created,
            "updated": datetime.date.today().isoformat(),
            "source": note.source,
            "tags": note.tags,
            "scope": effective_scope,
            "scope_hashes": scope_hashes,
            "session": session if session is not None else note.session,
            # A revised step list must not reset a procedure's history to zero.
            **(_procedure_front(note.procedure or _slug(note.title), note.runs_ok or 0,
                                note.runs_failed or 0, note.last_verified, note.last_execution)
               if note.kind == "procedure" else {}),
        }
    )
    path.write_text(f"{document}\n\n{content}\n", encoding="utf-8")
    return path


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
    )


def _count(value) -> int | None:
    """A counter from front matter; anything that is not a whole number is
    absent rather than zero, so a hand-mangled count reads as missing."""
    try:
        return int(str(value).strip()) if value not in (None, "") else None
    except ValueError:
        return None


def load_notes(project_root: str | Path) -> list[Note]:
    """Every note for this project, oldest first (filenames start with a date)."""
    directory = notes_dir(project_root)
    if not directory.is_dir():
        return []
    return [parse_note(path) for path in sorted(directory.glob("*.md"))]


# Similarity carried over from the one earlier iteration that actually ran a
# note store: word overlap weighted 0.6, tag overlap 0.4, and anything under
# the threshold treated as no match. Deliberately not embeddings — the runtime
# is copied into arbitrary projects and has to stay stdlib-only and offline.
_RELEVANCE_THRESHOLD = 0.15
_WORD = re.compile(r"[a-z0-9]+")


def _words(text: str) -> set[str]:
    return {word for word in _WORD.findall(text.lower()) if len(word) > 2}


def word_weights(corpus: list[Note], query_words: set[str]) -> dict[str, float]:
    """How much each query word is worth here: `log(notes / notes using it)`.

    Without this every word counts the same, and a question asked in a sentence
    is decided by its filler. Measured on a 106-note corpus with a 20-question
    golden set: "which Java class decides the order of the steps" could not find
    the note that answers it inside ten results, because "which", "class",
    "order" and "steps" are in half the corpus and outvoted the rest.

    A word in every note scores 0 and stops voting, which is the intent. A word
    in no note scores 0 too -- it cannot discriminate either, and treating an
    unmatched word as evidence of anything is what made long queries worse than
    short ones. When every word of a query is worthless the caller falls back to
    equal weights, so a search for a single ubiquitous word still answers with
    what it matched rather than with nothing.
    """
    if not corpus or not query_words:
        return {}
    total = len(corpus)
    seen = {word: 0 for word in query_words}
    for note in corpus:
        present = _words(note.title) | _words(" ".join(note.tags)) | _words(note.body)
        for word in query_words & present:
            seen[word] += 1
    return {word: math.log(total / count) if count else 0.0 for word, count in seen.items()}


def relevance(note: Note, query_words: set[str],
              weights: dict[str, float] | None = None) -> float:
    """How well one note answers a query, in 0.0 - 1.0.

    Scores how much of the *query* the note covers, rather than the Jaccard
    ratio over the union of both word sets. Jaccard divided by the note's own
    length, so a long descriptive title was a penalty: a note titled
    "AuditGroup must not call sink.record before super().invoke" scored 0.055
    for the query "AuditGroup" against a 0.15 threshold, and could not be found
    by a word out of its own title. Across the real corpus a term appearing in
    113 note bodies scored 0.000 everywhere.

    The body is included at a lower weight than the title. Excluding it
    entirely was the original fix for a different problem -- a paragraph of
    root-cause prose diluting a short query -- which coverage scoring does not
    have, because the denominator is the query and never the note.

    `weights` says what each query word is worth (see `word_weights`); without
    it every word is worth the same, which is the behaviour this had before a
    golden set showed what it costs. Coverage stays a ratio either way, so the
    0.0-1.0 range and the threshold mean what they always did.
    """
    if not query_words:
        return 0.0

    title_words = _words(note.title)
    tag_words = _words(" ".join(note.tags))
    body_words = _words(note.body)

    if weights:
        budget = sum(weights.get(word, 0.0) for word in query_words)
    else:
        budget = float(len(query_words))
    if budget <= 0:
        return 0.0

    def coverage(words: set[str]) -> float:
        if not words:
            return 0.0
        matched = words & query_words
        if weights:
            return sum(weights.get(word, 0.0) for word in matched) / budget
        return len(matched) / budget

    return min(
        1.0,
        0.5 * coverage(title_words)
        + 0.3 * coverage(tag_words)
        + 0.2 * coverage(body_words),
    )


def search_notes(project_root: str | Path, query: str, limit: int | None = None) -> list[Note]:
    """Notes relevant to `query`, most relevant first."""
    query_words = _words(query)
    corpus = load_notes(project_root)
    weights = word_weights(corpus, query_words)
    # Every word ubiquitous (or the query is one such word): weighting has
    # nothing left to say, and falling through to equal weights answers with
    # what matched instead of with nothing.
    if not any(weight > 0 for weight in weights.values()):
        weights = None
    scored = [(relevance(note, query_words, weights), note) for note in corpus]
    matches = [pair for pair in scored if pair[0] >= _RELEVANCE_THRESHOLD]
    matches.sort(key=lambda pair: (-pair[0], pair[1].path.name))
    ranked = [note for _, note in matches]
    return ranked[:limit] if limit else ranked


# How many titles to list for notes that did not fit. Sized so the index costs
# roughly one note's worth of characters.
_OMITTED_TITLE_LIMIT = 25


def _render_note(note: Note) -> str:
    header = f"### {note.title} ({note.kind})"
    meta_bits = []
    if note.tags:
        meta_bits.append("tags: " + ", ".join(note.tags))
    if note.source:
        meta_bits.append(f"source: {note.source}")
    meta = f"_{' | '.join(meta_bits)}_\n\n" if meta_bits else ""
    return f"{header}\n\n{meta}{note.body}"


def render_context_section(
    project_root: str | Path, query: str | None, max_chars: int
) -> str:
    """Render a bounded '## Accumulated Knowledge' block for get_context.

    With a query, notes are ranked by relevance and unrelated ones are left
    out entirely rather than padded in at low rank; without one (plain
    `eos context` has no task to score against) the most recently written
    notes lead, on the theory that recency is the next best signal absent a
    query. Either way, notes are dropped whole once the budget is spent, and
    the ones that did not fit are listed by title, so a trimmed context does
    not look like a complete one.
    """
    if query:
        candidates = search_notes(project_root, query)
    else:
        candidates = list(reversed(load_notes(project_root)))
    if not candidates:
        return ""

    # Findings lead, indexes trail. Without this a bulk-generated inventory
    # entry outranks a trap someone learned the hard way purely by being newer,
    # and since the budget fits one or two full notes it can take the only slot.
    # Measured on a 64-note service 2026-09-18: 23 of the 64 were generated.
    # With a query, relevance already decides the order and must not be undone.
    #
    # `is_bulk_index`, not `is_generated`: a journey note is written by a script
    # out of prose a person wrote, and sorting it with the endpoint tables put
    # 56 of this workspace's best notes behind every note carrying a ticket key.
    if not query:
        candidates.sort(key=is_bulk_index)

    lines = ["## Accumulated Knowledge"]
    included = 0
    for note in candidates:
        entry = _render_note(note)
        projected = len("\n\n".join(lines + [entry]))
        if included > 0 and projected > max_chars:
            break
        lines.append(entry)
        included += 1

    # Titles for what did not fit, not just a count. A note the reader cannot
    # see the existence of cannot be asked for, and the budget fits only one or
    # two full notes against a corpus of sixty -- so the old
    # "63 more note(s) omitted" line hid the entire corpus behind a number.
    # A title line is ~80 characters against a note's ~3,000, so the index is
    # affordable where the bodies are not; it is capped and reports its own
    # remainder so a trimmed list still says what it left out.
    remaining = candidates[included:]
    if remaining:
        lines.append(
            f"_{len(remaining)} more note(s) did not fit. "
            "Read one with `eos note show <project> <name>`._"
        )
        # The index obeys the same budget the bodies do. It is allowed to be
        # partial -- a truncated list still names notes the reader could not
        # otherwise know exist -- but it may not be the thing that blows the
        # budget, which would make a small context larger than a large one.
        listed = 0
        for note in remaining[:_OMITTED_TITLE_LIMIT]:
            entry = f"- {note.title}"
            if len("\n\n".join(lines + [entry])) > max_chars:
                break
            lines.append(entry)
            listed += 1
        if len(remaining) > listed:
            tail = f"- _…and {len(remaining) - listed} more_"
            if len("\n\n".join(lines + [tail])) <= max_chars or listed == 0:
                lines.append(tail)

    # No hard truncation here: slicing the joined text by character count can
    # as easily eat the "N more note(s) did not fit" line above as it can eat note
    # prose, which would silently hide exactly the fact this function exists
    # to report. A note whose own rendering exceeds max_chars is the one
    # allowed overage — one full note beats a mid-sentence cut or a lost
    # drop-count, and it is still exactly one note, not an unbounded list.
    return "\n\n".join(lines)


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
_STEP_TOOL = re.compile(r"\(tool:\s*(?P<tool>[^)]+?)\s*\)", re.IGNORECASE)
KNOWN_FAILURES = "Known failures"


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


def procedure_steps(note: Note) -> list[str]:
    """The ordered steps, as written, markers stripped."""
    return steps_in(note.body)


def procedure_tools(note: Note) -> list[str]:
    """Tools named by the steps' `(tool: …)` marks, in first-use order."""
    seen: list[str] = []
    for step in procedure_steps(note):
        for m in _STEP_TOOL.finditer(step):
            tool = m.group("tool").strip()
            if tool not in seen:
                seen.append(tool)
    return seen


def procedure_prerequisites(note: Note) -> list[str]:
    return _items(section_in(note.body, "Prerequisites"))


def procedure_success(note: Note) -> list[str]:
    return _items(section_in(note.body, "Success"))


def procedure_known_failures(note: Note) -> list[str]:
    return _items(section_in(note.body, KNOWN_FAILURES))


def _procedure_front(slug: str, runs_ok: int, runs_failed: int,
                     last_verified: str | None, last_execution: str | None) -> dict:
    # Counts are written as strings: `_front_matter` drops falsy values, and a
    # procedure that has run zero times must still say so rather than look as
    # though nobody ever counted.
    return {"procedure": slug, "runs_ok": str(runs_ok), "runs_failed": str(runs_failed),
            "last_verified": last_verified, "last_execution": last_execution}


def procedures(project_root: str | Path) -> list[Note]:
    return [n for n in load_notes(project_root) if n.kind == "procedure"]


def find_procedure(project_root: str | Path, needle: str) -> Note:
    """By slug, then by slug prefix, then by title words -- one match or an error."""
    found = procedures(project_root)
    exact = [n for n in found if n.procedure == needle]
    if exact:
        return exact[0]
    matches = [n for n in found if (n.procedure or "").startswith(needle)] or \
              [n for n in found if needle.casefold() in n.title.casefold()]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise ValueError(f"no procedure matches {needle!r} "
                         f"({len(found)} recorded; `eos procedure list` names them)")
    raise ValueError(f"{len(matches)} procedures match {needle!r}: "
                     + ", ".join(n.procedure or n.path.name for n in matches[:5]))


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


def _append_known_failure(body: str, line: str) -> str:
    """One bullet under `## Known failures`, the section created at the end
    when it does not exist yet."""
    bullet = f"- {line}"
    lines = body.rstrip("\n").split("\n")
    for index, current in enumerate(lines):
        heading = _HEADING.match(current)
        if heading and heading.group("name").strip().casefold() == KNOWN_FAILURES.casefold():
            end = index + 1
            while end < len(lines) and not _HEADING.match(lines[end]):
                end += 1
            while end > index + 1 and not lines[end - 1].strip():
                end -= 1
            lines.insert(end, bullet)
            return "\n".join(lines) + "\n"
    return "\n".join(lines) + f"\n\n## {KNOWN_FAILURES}\n\n{bullet}\n"


def record_procedure_run(project_root: str | Path, slug: str, *, outcome: str,
                         execution: str, at: str, lesson: str | None = None) -> Path | None:
    """Move a procedure's observations for one finished execution (ADR-023).

    The only writer of `runs_ok`, `runs_failed`, `last_verified` and
    `last_execution`. `abandoned` moves `last_execution` alone: a run given up
    says nothing about whether the procedure works. Returns the note's path, or
    None when the project has no procedure by that slug -- an execution may
    name one recorded elsewhere, and that is not an error here.
    """
    try:
        note = find_procedure(project_root, slug)
    except ValueError:
        return None
    if note.procedure != slug:
        return None  # only an exact slug moves counters; a prefix is a guess
    fields: dict = {"last_execution": execution}
    if outcome == "ok":
        fields["runs_ok"] = str((note.runs_ok or 0) + 1)
        fields["last_verified"] = at
    elif outcome == "failed":
        fields["runs_failed"] = str((note.runs_failed or 0) + 1)
    raw = note.path.read_text(encoding="utf-8")
    updated = _set_front(raw, fields)
    if outcome == "failed" and lesson and lesson.strip():
        front, separator, body = updated[4:].partition("\n---\n")
        first = lesson.strip().splitlines()[0]
        updated = "---\n" + front + separator + _append_known_failure(body, f"{at[:10]} {execution}: {first}")
    note.path.write_text(updated, encoding="utf-8")
    return note.path
