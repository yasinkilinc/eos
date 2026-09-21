"""The first call a session makes, and the only one it has to make.

Offered as twelve MCP tools on a real workspace, EOS was reached for in 4 of
25 sessions. That number is not a documentation problem. A tool an agent has
to *decide* to call competes, on every question, against a Read it can do
without asking -- and for most questions the Read wins, correctly. What does
not compete is the part nothing else can produce: what another session is
doing right now, and what earlier sessions already learned about this branch.

So this is one command, with no arguments to choose, that answers exactly
those two and stops. It is meant to be run by a hook at session start, where
there is no decision to make and no menu to skip, and it is deliberately
small: a session-start block that costs as much as reading two files is one
somebody turns off inside a week.

It derives its own query from the branch name and the ticket keys in it,
because at session start nobody has typed a task yet -- and the branch is the
one thing the machine already knows about the work about to happen.
"""
from __future__ import annotations

import re
from pathlib import Path

from core import notes
from core import work

# Caps, not budgets. Everything here is one line, and the value of the block
# is that it is read rather than skimmed -- twenty items is a document, five
# is a glance.
WORK_LIMIT = 5
NOTE_LIMIT = 4

# Branch words that rank nothing: every branch in the repository has one.
_NOISE = {"feature", "features", "bugfix", "fix", "hotfix", "release", "chore",
          "main", "master", "develop", "dev", "test", "task", "story", "wip"}

_WORD = re.compile(r"[A-Za-z][A-Za-z0-9]+")


def ticket_keys(project_root: str | Path, text: str) -> list[str]:
    """Issue keys in `text`, by the project's own configured pattern."""
    if not text:
        return []
    try:
        from core.index import _ticket_pattern

        pattern = _ticket_pattern(Path(project_root).expanduser().resolve())
    except Exception:  # noqa: BLE001 - a bad pattern is index's error to raise, not this one's
        return []
    return list(dict.fromkeys(pattern.findall(text)))


def query_from(branch: str | None, keys: list[str]) -> str:
    """What to rank notes against when nobody has said what they are doing.

    The branch name is a weak signal and is treated as one: it decides which
    notes are shown first, never which are kept. `search_notes` drops anything
    below its own relevance floor, so a branch called `develop` ranks nothing
    and the block says so rather than padding itself with the four most
    recent notes.
    """
    words = [word.lower() for word in _WORD.findall(branch or "")]
    return " ".join(keys + [word for word in words if word.lower() not in _NOISE])


def build(project_root: str | Path, *, session: str | None = None,
          agent: str | None = None) -> str:
    """The session-start block, as text meant to be read once and acted on."""
    import datetime

    root = Path(project_root).expanduser().resolve()
    now = datetime.datetime.now(datetime.timezone.utc)
    commit, branch = work.git_head(root)
    keys = ticket_keys(root, branch or "")

    lines = [f"EOS brief — {root.name}" + (f" @ {branch}" if branch else "")]

    in_flight = work.items(root)
    stale = [item for item in in_flight if item.is_stale(now)]
    if in_flight:
        head = f"IN FLIGHT ({len(in_flight)}"
        head += f", {len(stale)} stale)" if stale else ")"
        lines.append("")
        lines.append(head)
        for item in in_flight[:WORK_LIMIT]:
            lines.extend(_work_lines(item, now, session))
        if len(in_flight) > WORK_LIMIT:
            lines.append(f"  …{len(in_flight) - WORK_LIMIT} more: eos work list .")
    else:
        lines.append("")
        lines.append("IN FLIGHT (0) — nothing is claimed here. "
                     "Claim what you start: eos work add . --title \"…\" --claim")

    query = query_from(branch, keys)
    matched = notes.search_notes(root, query, limit=NOTE_LIMIT) if query else []
    recorded = len(notes.load_notes(root))
    lines.append("")
    if matched:
        lines.append(f"KNOWN HERE ({len(matched)} of {recorded} notes match this branch)")
        for note in matched:
            lines.append(f"  - {note.title}")
        lines.append('  Read one: eos note show . "<title>"')
    elif recorded:
        # "Nothing matched" and "nothing was written" send a session to
        # different places, and both print as an empty list.
        lines.append(f"KNOWN HERE (0 of {recorded} notes match this branch) — "
                     'search by subject: eos note search . "<subject>"')
    else:
        lines.append("KNOWN HERE (0 notes) — nothing has been recorded in this "
                     "project yet. `eos note add` records the first.")

    state = work.sync_state(root)
    if state.get("state") not in ("pushed", "committed", "absent"):
        # Printed only when something written here cannot be seen by anyone
        # else. In the healthy case it is a line nobody needs every session.
        lines.append("")
        lines.append(work.sync_sentence(state))

    return "\n".join(lines)


def _work_lines(item, now, session: str | None) -> list[str]:
    mine = " (yours)" if session and any(
        holder.get("session") == session for holder in item.holders) else ""
    held = " and ".join(item.holder_labels) if item.holders else "unclaimed"
    marks = []
    if item.ticket:
        marks.append(item.ticket)
    if item.is_stale(now):
        marks.append("STALE")
    if item.contested:
        marks.append("CONTESTED")
    tail = ("  " + " ".join(marks)) if marks else ""
    lines = [f"  {item.status:<7} {item.id}  {item.title}  "
             f"[{held}, {work.ago(item.updated_at, now)}]{tail}{mine}"]
    if item.status == work.BLOCKED and item.reason:
        lines.append(f"      blocked on: {item.reason}")
    elif item.last:
        lines.append(f"      last: {item.last}")
    return lines
