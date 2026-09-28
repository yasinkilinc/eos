"""What is already known about a file, put in front of the agent that just touched it.

Moved into the engine from a host's PostToolBatch hook (2.x roadmap C5), where
it had been measured and reviewed for a year of sessions; the numbers below are
that host's. Two tiers, because the measurement allows nothing else: over 19
real sessions and 74 (project, file) touches, an exact-file key hit 5.4% and
cost 12,112 tokens; widened to the package it hit 21.6% for 121,523; the whole
project hit everything for 1.2M. As one title line per note, a whole-project
index is 41-7,621 tokens, median ~250. So the exact file gets the body and
anything wider gets titles, under three invariants each earned by a bug a
review caught in a real session:

1. `seen` records delivery, not consideration: a note is marked only once its
   text is in the returned string, and only an earlier *body* delivery keeps a
   note from a body slot -- a digest mention never does.
2. Body slots rank hand-written before bulk index, newest first within each,
   and are a fixed top-N per file: a repeat touch never promotes the next N
   (measured before: six touches of one file delivered 3 fresh bodies each,
   93,376 tokens to exhaust one 108-note project).
3. A segment that does not fit is skipped and the rest are still tried: one
   oversized note must not silence everything after it.

PostToolBatch rather than PostToolUse: it fires once for every file a batch of
calls touched, so the corpus is read once per batch, not once per file.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from core.context.budget import CHARS_PER_TOKEN  # noqa: F401 -- re-exported for importers

MAX_TOKENS = 3000
MAX_BODY_NOTES = 3
# The digest tier names notes the touched file does not own; a title list is
# useful only while it is short enough to read.
MAX_DIGEST_LINES = 8
# A per-touch cap does not bound a session that touches forty files. Measured
# over 92 sessions: the digest tier put a p95 of 6,174 tokens of titles into one
# session, the body tier 4,481. Past these, a touched file's notes become one
# pointer line -- nothing is hidden, only not pre-paid.
SESSION_MAX_TOKENS = 4000
SESSION_DIGEST_LINES = 24
# A file larger than this is not hashed (a packaged jar took ~110 ms and
# ~190 MB on its own); its notes are never marked stale on an unknown digest.
MAX_DIGEST_BYTES = 8 * 1024 * 1024
FILE_TOOLS = ("Read", "Edit", "Write", "NotebookEdit", "MultiEdit")

# An instruction file is already in the session's context, a touch of it says
# nothing about a task, and a note scoped to it is stale on every edit of it.
# Measured before this rule: a touch of CLAUDE.md delivered 87 digest titles.
INSTRUCTION_NAMES = frozenset({"CLAUDE.md", "AGENTS.md", "SKILL.md", "MEMORY.md"})
INSTRUCTION_DIRS = ("/.claude/agents/", "/.claude/rules/", "/.devin/agents/", "/.devin/rules/")
# A project's AGENTS.md imported as notes: one batch, same date, it would fill
# every digest by date alone.
AGENTS_MD_SOURCE = "agents-md"

_TRUNCATED = "\n\n[truncated: note context exceeded the injection budget]"
_STALE_MARK = "[STALE: this note was written on {created}, the file has changed since]"
_STALE_SUFFIX = " [STALE {created}]"


def is_instruction_file(path: str) -> bool:
    return Path(path).name in INSTRUCTION_NAMES or any(part in path for part in INSTRUCTION_DIRS)


def _digest_of(path: str) -> str | None:
    """sha256 of the file, or None when it cannot be hashed cheaply -- ValueError
    too: a NUL in a path raises it from stat(), and one such path must cost
    itself, not the batch."""
    try:
        if Path(path).stat().st_size > MAX_DIGEST_BYTES:
            return None
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except (OSError, ValueError):
        return None


def _stale_for(note, relative: str, digests: set) -> bool:
    """Whether the note's recorded hash for `relative` matches none of `digests`.

    False on every uncertainty -- no hash recorded, a null hash, no digest for
    the file in this batch, or any digest unknown (a worktree and the main
    checkout of one file can both be in a batch, and the unreadable one may be
    the copy the note describes). A wrong stale mark teaches the agent to
    distrust the mark, which costs every true one after it."""
    stored_by_scope = dict(zip(note.scope, note.scope_hashes))
    stored = stored_by_scope.get(relative)
    if stored is None or not digests or None in digests:
        return False
    return stored not in digests


def _rank_for_body(notes: list) -> list:
    """Hand-written before bulk index, newest first within each (file names
    start with the date). By date alone, a bulk endpoint table outranked a
    hand-written trap warning on the project that motivated this."""
    from core.notes import is_bulk_index

    recent_first = sorted(notes, key=lambda n: n.path.name, reverse=True)
    return sorted(recent_first, key=is_bulk_index)


def _near_first(notes: list, relative: str) -> list:
    """Wide notes for the digest: scoped into the touched file's directory
    first, then newest, an imported AGENTS.md last."""
    folder = relative.rsplit("/", 1)[0] + "/" if "/" in relative else ""
    newest = sorted(notes, key=lambda n: n.path.name, reverse=True)
    if folder:
        newest = sorted(newest, key=lambda n: not any(s.startswith(folder) for s in n.scope))
    return sorted(newest, key=lambda n: n.source == AGENTS_MD_SOURCE)


def context_for(targets: list[tuple[str, Path, str, str, str]], seen: set, *, pointer: str) -> tuple[str, set]:
    """(text, seen after) for touched files, each `(project name, project root,
    project path as the session types it, relative path, absolute path)`;
    `seen` holds `(tier, note file name)`.

    Two passes: body slots are decided across every file first (a note exact to
    two touched files gets one body attempt), then the digest tier from what is
    left. Notes are grouped by project so a `pom.xml` of one project is never
    compared with another project's `pom.xml` (953 false stale marks against 72
    true ones before this grouping). `pointer` is the command that reads the
    rest, formatted with `{path}` and `{words}`."""
    from core.context import dedup
    from core.notes import is_bulk_index, load_notes

    prior = set(seen)
    considered = set(prior)
    digests_by_target: dict[tuple, set] = {}
    roots: dict[str, tuple[Path, str]] = {}
    cache: dict[str, str | None] = {}
    for name, root, label, relative, absolute in targets:
        roots[name] = (root, label)
        if absolute not in cache:
            cache[absolute] = _digest_of(absolute)
        digests_by_target.setdefault((name, relative), set()).add(cache[absolute])

    # C4: a note whose body already sits in a file the harness always loads
    # (`[ai] loaded`) never takes a body slot -- the harness has it in
    # context already. Computed once per touched project; "" (unset, the
    # common case) matches nothing, so a project without the key is
    # byte-identical to before this existed.
    loaded_text = {name: dedup.always_loaded_text(root) for name, (root, _label) in roots.items()}
    left_out = 0
    by_project: dict[str, list] = {}
    for name, relative in digests_by_target:
        by_project.setdefault(name, []).append((relative, digests_by_target[(name, relative)]))

    limit = int(MAX_TOKENS * CHARS_PER_TOKEN) - len(_TRUNCATED)
    per_path, body_segments = [], []
    corpus: dict[str, list] = {}
    for name, relative in digests_by_target:
        if name not in corpus:
            try:
                corpus[name] = [n for n in load_notes(roots[name][0]) if n.title]
            except Exception:  # noqa: BLE001 - one unreadable store costs its notes
                corpus[name] = []
        notes = corpus[name]
        exact = [n for n in notes if relative in n.scope]
        wide = _near_first([n for n in notes if n not in exact], relative)
        ranked = _rank_for_body(exact)
        per_path.append((relative, name, ranked, wide))
        digests = digests_by_target[(name, relative)]
        # A bulk index never takes a body slot: every touch of a pom.xml
        # delivered ~7,000 characters of endpoint tables before this rule.
        for note in [n for n in ranked if not is_bulk_index(n)][:MAX_BODY_NOTES]:
            key = ("body", note.path.name)
            if key in considered:
                continue
            if loaded_text.get(name) and dedup.already_loaded(note.body, loaded_text[name]):
                left_out += 1
                continue
            mark = (_STALE_MARK.format(created=note.created or "?") + "\n") \
                if _stale_for(note, relative, digests) else ""
            text = f"### Known about {relative}\n{mark}{note.title}\n\n{note.body}"
            # A body larger than the whole budget can never be delivered; the
            # slot is declined so its title still reaches the digest tier.
            if len(text) > limit:
                continue
            considered.add(key)
            body_segments.append(((key,), text, False, None))

    if left_out:
        body_segments.append(((), f"_{left_out} note(s) left out: already in an always-loaded file._",
                              False, None))

    digest_segments = []
    queued: set = set()
    listed: dict[str, int] = {}
    for relative, name, ranked, wide in per_path:
        pool = []
        for note in ranked + wide:
            note_id = note.path.name
            if note_id in queued or ("body", note_id) in considered or ("digest", note_id) in considered:
                continue
            queued.add(note_id)
            pool.append(note)
        room = max(MAX_DIGEST_LINES - listed.get(name, 0), 0)
        for index, note in enumerate(pool[:room]):
            considered.add(("digest", note.path.name))
            stale = any(_stale_for(note, rel, digs) for rel, digs in by_project.get(name, []))
            suffix = _STALE_SUFFIX.format(created=note.created or "?") if stale else ""
            first = listed.get(name, 0) == 0 and index == 0
            line = f"- {note.title}{suffix}"
            digest_segments.append(((("digest", note.path.name),),
                                    f"### Other notes on {name}\n{line}" if first else line, not first, name))
        shown = min(room, len(pool))
        listed[name] = listed.get(name, 0) + shown
        if len(pool) > shown and listed[name]:
            digest_segments.append(((), f"- … {len(pool) - shown} more on {name}: "
                                        + pointer.format(path=roots[name][1], words="<words>"), True, name))

    # The notice's width is reserved for every candidate, so a segment accepted
    # whole is never cut afterwards.
    buf, delivered, truncated = "", set(), False
    header_ok: dict = {}
    for ids, text, continues, group in body_segments + digest_segments:
        if continues and not header_ok.get(group):
            truncated = True
            continue
        candidate = buf + ("\n" if (continues and buf) else ("\n\n" if buf else "")) + text
        if len(candidate) > limit:
            truncated = True
            if not continues:
                header_ok[group] = False
            continue
        buf = candidate
        delivered.update(ids)
        if not continues:
            header_ok[group] = True
    if truncated and buf:
        buf += _TRUNCATED
    return buf, prior | delivered


def budgeted(text: str, spent: int, digest_used: int, *, pointer: str) -> tuple[str, int, int]:
    """`text` cut to what the session has left; (text, spent, digest lines used)."""
    if not text:
        return text, spent, digest_used
    budget = int(SESSION_MAX_TOKENS * CHARS_PER_TOKEN)
    blocks: list[list[str]] = []
    for part in text.split("\n\n"):
        if part.startswith("### ") or not blocks:
            blocks.append([part])
        else:
            blocks[-1].append(part)  # a body's own paragraphs, or the truncation notice
    out, pointed = [], set()
    for block in blocks:
        body = "\n\n".join(block)
        head = block[0].splitlines()[0]
        if head.startswith("### Other notes on"):
            lines = body.splitlines()
            titles = [line for line in lines[1:] if line.startswith("- ") and not line.startswith("- … ")]
            more = [line for line in lines[1:] if line.startswith("- … ")]
            room = SESSION_DIGEST_LINES - digest_used
            if room <= 0:
                continue
            kept = titles[:room]
            digest_used += len(kept)
            cut = len(titles) - len(kept)
            tail = more[:1] or ([f"- … {cut} more: " + pointer.format(path=".", words="<words>")] if cut else [])
            body = "\n".join([lines[0]] + kept + tail)
        elif head.startswith("### Known about ") and spent + len(body) > budget:
            path = head[len("### Known about "):].strip()
            if path in pointed:
                continue
            pointed.add(path)
            body = (f"### Known about {path}: notes withheld, this session's note budget "
                    f"({SESSION_MAX_TOKENS:,} tokens) is spent -- " + pointer.format(path=".", words=path))
        spent += len(body)
        out.append(body)
    return "\n\n".join(out), spent, digest_used


# --- per-session state: which notes were delivered, and what was spent ----------------


def load_state(path: Path) -> tuple[set, int, int]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return ({tuple(item) for item in raw.get("seen", [])}, int(raw.get("spent", 0)),
                int(raw.get("digest", 0)))
    except (OSError, ValueError, TypeError, AttributeError):
        return set(), 0, 0


def save_state(path: Path, seen: set, spent: int, digest_used: int) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"seen": sorted(seen), "spent": spent, "digest": digest_used}),
                        encoding="utf-8")
    except OSError:
        pass  # a note delivered twice is not worth a failed hook
