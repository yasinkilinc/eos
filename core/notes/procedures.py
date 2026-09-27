"""Procedures: a note kind with steps, rules, success checks and run counters."""
from __future__ import annotations

import re
from pathlib import Path

from core.notes.store import (
    Note, _items, _set_front, append_bullet, load_notes, section_in, steps_in,
    superseded,
)


_STEP_TOOL = re.compile(r"\(tool:\s*(?P<tool>[^)]+?)\s*\)", re.IGNORECASE)


KNOWN_FAILURES = "Known failures"


# ADR-023 addendum. The rules that must hold while a task runs -- the ones a
# host moved out of its always-loaded instructions because they only matter
# during this task. The brief prints them whole and past its budget, because a
# clipped rule is a rule that did not arrive; the cap is enforced when the
# note is written, so the exemption stays bounded.
RULES_SECTION = "Rules"


RULES_MAX_CHARS = 600


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


def procedure_rules(note: Note) -> list[str]:
    return _items(section_in(note.body, RULES_SECTION))


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
    """The procedures in use: one another replaced is left out."""
    corpus = load_notes(project_root)
    replaced = superseded(corpus)
    return [n for n in corpus if n.kind == "procedure" and n.path.name not in replaced]


def replacing_procedure(project_root: str | Path, slug: str) -> Note | None:
    """The procedure that replaced the one with `slug`, when one did."""
    corpus = load_notes(project_root)
    replaced = superseded(corpus)
    old = next((n for n in corpus if n.kind == "procedure" and n.procedure == slug), None)
    if old is None or old.path.name not in replaced:
        return None
    by_name = {n.path.name: n for n in corpus}
    return by_name.get(replaced[old.path.name][-1])


def find_procedure(project_root: str | Path, needle: str) -> Note:
    """By slug, then by slug prefix, then by title words -- one match or an error.
    A replaced procedure is still found by its slug: its runs finish against it."""
    found = [n for n in load_notes(project_root) if n.kind == "procedure"]
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


def _append_known_failure(body: str, line: str) -> str:
    return append_bullet(body, KNOWN_FAILURES, line)


def record_procedure_run(project_root: str | Path, slug: str, *, outcome: str,
                         execution: str, at: str, lesson: str | None = None) -> Path | None:
    """Move a procedure's observations for one finished execution (ADR-023).

    The only writer of `runs_ok`, `runs_failed`, `last_verified` and
    `last_execution`. `abandoned` moves `last_execution` alone: a run given up
    says nothing about whether the procedure works. Returns the note's path, or
    None when the project has no procedure by that slug -- an execution may
    name one recorded elsewhere, and that is not an error here.
    """
    from core.lib import atomic, lock

    try:
        note = find_procedure(project_root, slug)
    except ValueError:
        return None
    if note.procedure != slug:
        return None  # only an exact slug moves counters; a prefix is a guess
    # Two sessions finishing runs of one procedure at once each read the old
    # count and wrote count + 1: the note is re-read under its lock (F2).
    with lock.locked(note.path):
        note = find_procedure(project_root, slug)
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
        atomic.write_text(note.path, updated)
    return note.path


# --- confidence (ADR-024) -------------------------------------------------------------

FRESH_DAYS = 30


AGING_DAYS = 90


CONFIDENCE_WORDS = ("failing", "unverified", "fresh", "aging", "stale")


def procedure_confidence(note: Note, now: str | None = None) -> str:
    """One word for "can I follow this", derived when asked and never stored.

    From the ledger beside the note and the note's own observations: the
    latest finished run failing outranks everything, then whether any run
    ever finished ok, then how long ago one did. Stored, the word would be
    true when written and false a month later with nothing to say so -- the
    shape ADR-020 refuses for `stale`.
    """
    import datetime

    from core import executions

    ledger = note.path.parent / executions.FILENAME
    finished = [r for r in executions.load_path(ledger)
                if r.procedure == note.procedure and r.outcome in ("ok", "failed")]
    if finished and finished[-1].outcome == "failed":
        return "failing"
    verified = note.last_verified or max(
        (r.finished_at for r in finished if r.outcome == "ok" and r.finished_at), default=None)
    if not verified:
        return "unverified"
    clock = datetime.datetime.fromisoformat(now) if now else datetime.datetime.now(datetime.timezone.utc)
    try:
        age = (clock - datetime.datetime.fromisoformat(verified)).days
    except ValueError:
        return "unverified"
    if age <= FRESH_DAYS:
        return "fresh"
    return "aging" if age <= AGING_DAYS else "stale"


def lessons_for(project_root: str | Path, *, execution: str | None = None,
                procedure: str | None = None) -> list[Note]:
    return [n for n in load_notes(project_root) if n.kind == "lesson"
            and (execution is None or n.execution == execution)
            and (procedure is None or n.procedure == procedure)]
