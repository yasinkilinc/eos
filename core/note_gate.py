"""The Stop gate on notes: this session changed a project's source and recorded nothing,
or left a hand-written note that no longer agrees with the file it is about.

Moved into the engine from a host's Stop hook (2.x roadmap C5). Instructions do
not produce notes -- that host's skill had told agents to write one since its
first day, and it produced 9 notes on 2 calendar days; a Stop hook that
answers "block" does, because the model is shown the reason and the turn goes
on. Opt-in per project (`[hooks] notes = true` in the project's own config):
in a workspace, the projects whose source is gated are not the root whose
scripts a session edits all day.

It never blocks over its own failure: every "cannot tell" -- an unreadable
store, a note that will not parse, a file that cannot be hashed -- means do not
block. A gate that wedges a session over its plumbing is a gate that gets
deleted.
"""
from __future__ import annotations

import hashlib
import subprocess
import time
from pathlib import Path

# Files whose change asks for a note. Source, not documents or notes themselves.
SOURCE_SUFFIXES = (".java", ".kt", ".ts", ".tsx", ".js", ".py", ".go", ".sql", ".xml", ".yaml", ".yml")
# Total wall clock the message may spend on `git log` across every stale note.
ENRICHMENT_BUDGET_S = 5.0
MAX_DIGEST_BYTES = 8 * 1024 * 1024


def recorded(project_root: Path, session: str) -> bool:
    """An explicit skip for this session, or a note carrying this session's id.

    Keyed on the session id, never on mtime: the store is usually in git, and a
    pull rewrites every note's mtime at once (252 of 271 notes on one host
    shared three mtime minutes). Notes are parsed one at a time, so one bad
    file costs itself; an unreadable directory is "cannot tell" -- glob()
    returns [] on it, the same as an empty store, and empty is what blocks."""
    import os

    from core import notes

    try:
        if notes.was_skipped(project_root, session):
            return True
        directory = notes.notes_dir(project_root)
        if not directory.is_dir():
            return False
        if not os.access(directory, os.R_OK):
            return True
        for path in directory.glob("*.md"):
            try:
                if notes.parse_note(path).session == session:
                    return True
            except Exception:  # noqa: BLE001 - one bad note must not hide the rest
                continue
        return False
    except Exception:  # noqa: BLE001 - cannot check, do not block
        return True


def _digest_of(path: str) -> str | None:
    try:
        if Path(path).stat().st_size > MAX_DIGEST_BYTES:
            return None
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except (OSError, ValueError):
        return None


def _edited_copy_matches(stored, paths: list[str], cache: dict) -> bool:
    """Whether the note's recorded hash is the hash of a copy this session
    edited. `stale_notes` reads the main checkout only, so a session editing a
    worktree copy that matches the note exactly was blocked over a file it
    never touched. False on every uncertainty: an exemption suppresses a block."""
    if stored is None:
        return False
    for path in paths:
        if path not in cache:
            cache[path] = _digest_of(path)
        if cache[path] is not None and cache[path] == stored:
            return True
    return False


def stale_handwritten(project_root: Path, edited: dict[str, list[str]]) -> list[dict]:
    """Hand-written notes scoping a file this session changed (`edited`:
    project-relative path -> the absolute paths edited for it) whose recorded
    hash no longer matches. Generated notes are left out: one pom.xml change
    marks six of them, and they are fixed by re-running the generator.

    `gone` and `changed` are aggregated over the whole note, because
    `amend_note` validates every scope entry and each mix has its own remedy
    (`amend_flags`)."""
    from core import notes

    try:
        found, digests = [], {}
        for item in notes.stale_notes(project_root):
            if item.get("generated", True):
                continue
            issues = item.get("issues", [])
            if not any(issue.get("scope") in edited for issue in issues):
                continue
            # Through notes_dir(): a project may keep its notes anywhere.
            note_path = notes.notes_dir(project_root) / item["note"]
            try:
                parsed = notes.parse_note(note_path)
                title, kind = parsed.title, parsed.kind
                stored_by_scope = dict(zip(parsed.scope, parsed.scope_hashes))
            except Exception:  # noqa: BLE001 - still stale; nothing to exempt it with
                title, kind, stored_by_scope = item["note"], "", {}
            matched = next((issue for issue in issues if issue.get("scope") in edited
                            and not _edited_copy_matches(stored_by_scope.get(issue["scope"]),
                                                         edited[issue["scope"]], digests)), None)
            if matched is None:
                continue
            found.append({"note": item["note"], "path": note_path, "scope": matched["scope"], "title": title,
                          "kind": kind,
                          "gone": [i["scope"] for i in issues if i.get("reason") == "removed"],
                          "changed": [i["scope"] for i in issues if i.get("reason") != "removed"]})
        return found
    except Exception:  # noqa: BLE001 - cannot tell, do not block
        return []


def amend_flags(item: dict) -> str:
    """The flags of the one `eos note amend` that can clear this note:
    nothing removed -> `--body`; everything removed -> `--scope`; both ->
    `--scope` and `--body` in one command (each other form refuses on it,
    measured). No apostrophe inside a placeholder: it is single-quoted."""
    if not item["gone"]:
        return "--body '<what is true now>'"
    if not item["changed"]:
        return "--scope '<the new project-relative path>'"
    return "--scope '<the project-relative paths this note is still about>' --body '<what is true now>'"


def remedy_lines(item: dict, root: Path, session: str | None) -> list[str]:
    """The amend command for one stale note and the prose that makes it run on
    the first try. Without a session (a pre-PR check) no `--session` is
    printed: a made-up one would satisfy this gate falsely."""
    prefix = f"eos note amend {item['path']} {root} "
    flags = amend_flags(item)
    tail = f" --session {session}" if session else ""
    lines = []
    if item["gone"]:
        lines.append(f"{', '.join(item['gone'])} no longer exists, so --body and --reaffirm can only refuse "
                     "(they re-hash that path).")
        lines.append(f"{', '.join(item['changed'])} did change, so a scope-only amend is refused too -- "
                     "one command does both:" if item["changed"] else "Re-point the note at where the code moved:")
    lines.append(f"{prefix}{flags}{tail}")
    if "--body" in flags and item["kind"] == "defect":
        lines.append("  (a defect note's new body must keep its Root cause, Solution and Metric sections)")
    if item["gone"]:
        if not item["changed"]:
            lines.append("...and add --body '<what is true now>' to that same command if the move changed "
                         "what the note claims.")
        return lines
    lines.append(f"...or, if the change does not affect what the note claims: {prefix.strip()} "
                 f"--reaffirm '<why it still holds>'{tail}")
    return lines


def _commits_touching(root: Path, relative: str, timeout: float) -> list[str]:
    """Up to five recent commit subjects that changed the file; decoration only."""
    try:
        done = subprocess.run(["git", "-C", str(root), "log", "-n", "5", "--oneline", "--", relative],
                              capture_output=True, text=True, timeout=timeout, check=False)
        return [line for line in done.stdout.splitlines() if line.strip()]
    except Exception:  # noqa: BLE001
        return []


def message(pending: dict, stale: dict, session: str) -> str:
    """`pending`: {project: (root, paths)} that recorded nothing; `stale`:
    {project: (root, stale items)}."""
    lines: list[str] = []
    if pending:
        lines += ["This session changed source and recorded nothing about it.",
                  "Before finishing, run -- do not just describe -- one command below.", ""]
        for name, (root, paths) in pending.items():
            shown = "\n".join(f"    {p}" for p in sorted(set(paths))[:5])
            lines.append(f"  {name} ({root}):\n{shown}")
        lines += ["", "Write what the next agent working here would need and could not derive:",
                  "  a trap you fell into, the constraint that made this the only fix,",
                  "  a rule for next time, a measured before/after, and what you did NOT verify.", ""]
        for name, (root, _paths) in pending.items():
            lines += [f"  eos note add {root} --kind finding --title '...' --body '...' \\",
                      # Quoted: bare, a shell reads `<` as a redirect before eos starts.
                      "    --scope '<the file this is about, project-relative -- or @parent:<label>/<path> "
                      "if it is really about a linked parent>' \\",
                      f"    --source '<ticket or origin>' --session {session}", ""]
        lines += ["If that is refused because the note already exists, append what is new",
                  f"to the file it names and add a `session: {session}` line to that note's",
                  "front matter -- appending alone does not satisfy this check, which reads",
                  "that field. Do not reach for `skip` to get past a duplicate.", ""]
        lines.append("If nothing durable was learned, say so and this half will not be asked again -- "
                     "the false note below still needs its own answer:" if stale else
                     "If nothing durable was learned, say so and it will not be asked again:")
        for name, (root, _paths) in pending.items():
            lines.append(f"  eos note skip {root} --reason '...' --session {session}")
    enriched: dict = {}
    deadline = time.monotonic() + ENRICHMENT_BUDGET_S
    for name, (root, found) in stale.items():
        # Not "a note you made false": who changed the file is never checked,
        # and usually it was not this session.
        lines += ["", f"{name}: a note here no longer agrees with the file it is about.",
                  "Angle-bracketed text below is a placeholder: replace it, do not run it as printed."]
        for item in found:
            lines.append(f"  {item['title']!r} ({item['note']}) -- scope {item['scope']}")
            key = (str(root), item["scope"])
            if key not in enriched:
                remaining = deadline - time.monotonic()
                enriched[key] = _commits_touching(root, item["scope"], remaining) if remaining > 0 else []
                lines += [f"      changed by {commit}" for commit in enriched[key]]
            lines += [f"  {line}" for line in remedy_lines(item, root, session)]
    return "\n".join(lines).strip("\n")


def fallback(pending: dict, stale: dict, session: str) -> str:
    """The reason when composing the full one failed: a block already decided
    on still carries a way out of itself."""
    lines = ["This session's note check has something to report and could not compose its full text. "
             "Run one command below before finishing.",
             "Quoted and angle-bracketed text below is a placeholder: replace it, do not run it as printed.", ""]
    for name, (root, _paths) in pending.items():
        lines.append(f"  {name}: eos note add {root} --kind finding --title '...' --body '...' --session {session}")
    for name, (root, found) in stale.items():
        lines += [f"  {name}: eos note amend {item['path']} {root} {amend_flags(item)} --session {session}"
                  for item in found]
    return "\n".join(lines)


def decide(edited: dict[str, tuple[Path, dict[str, list[str]]]], session: str) -> str:
    """The block reason for the projects this session edited, '' for none.

    `edited`: {project name: (project root, {relative path: [absolute paths]})},
    already narrowed to gated projects and source files."""
    pending, stale = {}, {}
    for name, (root, files) in edited.items():
        paths = [path for copies in files.values() for path in copies]
        if not recorded(root, session):
            pending[name] = (root, paths)
        found = stale_handwritten(root, files)
        if found:
            stale[name] = (root, found)
    if not pending and not stale:
        return ""
    try:
        return message(pending, stale, session)
    except Exception:  # noqa: BLE001 - a decided block is never lost to its own text
        return fallback(pending, stale, session)
