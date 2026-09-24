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

# The task brief's budget, in tokens, and the rate it is converted at. The
# conversion is conservative on purpose: markdown with identifiers and paths
# tokenizes denser than prose (measured 2.7-4.4 chars/token on a real note
# store), and a budget that is exceeded by exactly the dense cases is not one.
TASK_BUDGET = 1500
CHARS_PER_TOKEN = 3.0

RUN_LIMIT = 3
RELATED_LIMIT = 3
FAILURE_LIMIT = 3
STEP_CHARS = 160

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
          agent: str | None = None, task: str | None = None,
          budget: int | None = None, task_only: bool = False) -> str:
    """The session-start block, as text meant to be read once and acted on.

    With `task`, the block leads with what this task needs and nothing else
    can supply cheaply: the procedure recorded for it, the last runs of it and
    what the failed ones taught, then the notes nearest to it -- under one
    budget (tokens, default TASK_BUDGET). The task text is a query and is
    discarded: nothing here writes it anywhere (ADR-019).

    `task_only` is for the hook that fires on every prompt: it returns only
    the task sections, and an empty string when none of them found anything,
    so a prompt with nothing recorded behind it costs nothing.
    """
    root = Path(project_root).expanduser().resolve()
    if task and task.strip():
        return _with_task(root, task, session=session, agent=agent,
                          budget=budget or TASK_BUDGET, task_only=task_only)
    if task_only:
        return ""
    return _branch_brief(root, session=session, agent=agent)


def _branch_brief(root: Path, *, session: str | None, agent: str | None) -> str:
    import datetime

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

    open_runs = _open_runs(root)
    if open_runs:
        lines.append("")
        lines.append(f"RUNS OPEN ({len(open_runs)}) — finish them: eos run finish . <id> --outcome ok|failed|abandoned")
        for record in open_runs[:RUN_LIMIT]:
            lines.append(f"  {record.id}  {record.title}  [{record.session or 'no session'}, "
                         f"{work.ago(record.started_at, now)}, {len(record.events)} event(s)]")

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


# --- the task brief (M3) -------------------------------------------------------------


def _open_runs(root: Path) -> list:
    from core import executions
    return [r for r in reversed(executions.load(root)) if r.open]


def best_procedure(root: Path, task: str, corpus: list | None = None):
    """The procedure note that best matches the task, or None.

    Ranked by the same weighted coverage note search uses, with word weights
    taken over the whole corpus -- a procedure's own few words are too small a
    sample to say which of them are rare.
    """
    corpus = corpus if corpus is not None else notes.load_notes(root)
    candidates = [n for n in corpus if n.kind == "procedure"]
    if not candidates:
        return None
    words = notes._words(task)
    weights = notes.word_weights(corpus, words)
    if not any(weight > 0 for weight in weights.values()):
        weights = None
    score, note = max(((notes.relevance(n, words, weights), n) for n in candidates),
                      key=lambda pair: pair[0])
    return note if score >= notes._RELEVANCE_THRESHOLD else None


def _clip(text: str, limit: int = STEP_CHARS) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _run_line(record) -> str:
    from core.executions import Record  # noqa: F401 - the type this reads
    tools = []
    for e in record.events:
        if e.tool and e.tool not in tools:
            tools.append(e.tool)
    when = (record.finished_at or record.started_at or "")[:10]
    parts = [f"  {when}  {(record.outcome or 'open'):<9}", record.target or "-"]
    if tools:
        parts.append(f"{len(record.events)} event(s): {', '.join(tools[:4])}")
    if record.outcome == "failed" and record.lesson:
        parts.append(f"lesson: {_clip(record.lesson.splitlines()[0], 100)}")
    parts.append(record.id)
    return "  ".join(parts)


def _task_sections(root: Path, task: str) -> tuple[list[list[str]], bool]:
    """The task brief as prioritised sections, and whether any found anything."""
    from core import executions

    corpus = notes.load_notes(root)
    procedure = best_procedure(root, task, corpus)
    records = executions.load(root)
    sections: list[list[str]] = []
    found = False

    if procedure is not None:
        found = True
        head = [f"PROCEDURE  {procedure.title}  [{procedure.procedure}]  "
                f"{procedure.runs_ok or 0} ok / {procedure.runs_failed or 0} failed, "
                f"last verified {(procedure.last_verified or 'never')[:10]}, "
                f"{notes.procedure_confidence(procedure).upper()}"]
        head += [f"  {n}. {_clip(step)}" for n, step in enumerate(notes.procedure_steps(procedure), start=1)]
        for label, items in (("prerequisites", notes.procedure_prerequisites(procedure)),
                             ("success", notes.procedure_success(procedure))):
            if items:
                head.append(f"  {label}: " + "; ".join(_clip(item, 100) for item in items))
        sections.append(head)
        runs = executions.ranked(root, procedure=procedure.procedure, limit=RUN_LIMIT, records=records)
        pool = [r for r in records if r.procedure == procedure.procedure]
    else:
        sections.append(["PROCEDURE — none recorded for this task. Do not present improvised steps "
                         "as this project's; `eos procedure new` records one once it has been done."])
        runs = executions.ranked(root, task=task, limit=RUN_LIMIT, records=records)
        words = notes._words(task)
        pool = [r for r in records if executions._task_overlap(r, words) > 0]

    if runs:
        found = True
        total = len(pool)
        block = [f"LAST RUNS ({len(runs)} of {total})"] + [_run_line(r) for r in runs]
        lesson = executions.last_lesson(pool)
        if lesson is not None and lesson.id not in {r.id for r in runs}:
            block.append(f"  earlier failure worth reading: {(lesson.finished_at or '')[:10]} {lesson.id}: "
                         f"{_clip(lesson.lesson.splitlines()[0], 100)}")
        block.append("  Timeline of one: eos run show . <id>")
        sections.append(block)
    elif procedure is not None:
        sections.append(["LAST RUNS (0) — this procedure has no recorded run yet."])

    if procedure is not None:
        failures = notes.procedure_known_failures(procedure)[-FAILURE_LIMIT:]
        if failures:
            sections.append(["KNOWN FAILURES"] + [f"  - {_clip(item, 140)}" for item in failures])

    # Findings only: an endpoint table matching "deploy" is not something a
    # session about to deploy needs read to it (notes.BULK_INDEX_SOURCES).
    related = [n for n in notes.search_notes(root, task, limit=RELATED_LIMIT + 6)
               if n.kind != "procedure" and not notes.is_bulk_index(n)][:RELATED_LIMIT]
    if related:
        found = True
        sections.append(["RELATED NOTES"] + [f"  - {n.title}" for n in related]
                        + ['  Read one: eos note show . "<title>"'])

    slug = procedure.procedure if procedure is not None else "<slug>"
    sections.append([f"Record this run: eos run start . --title \"…\" "
                     + (f"--procedure {slug}" if procedure is not None else "")
                     + "  (wrappers add events; finish with eos run finish . --outcome ok|failed|abandoned)"])
    return sections, found


def _with_task(root: Path, task: str, *, session, agent, budget: int, task_only: bool) -> str:
    sections, found = _task_sections(root, task)
    if task_only and not found:
        return ""
    lines = [f"EOS brief for this task — {root.name}"]
    if not task_only:
        branch_lines = _branch_brief(root, session=session, agent=agent).splitlines()[1:]
        while branch_lines and not branch_lines[0].strip():
            branch_lines.pop(0)
        sections.append(branch_lines)
    cap = int(budget * CHARS_PER_TOKEN)
    trimmed = False
    for section in sections:
        for line in [""] + section:
            if len("\n".join(lines + [line])) > cap:
                trimmed = True
                break
            lines.append(line)
        if trimmed:
            break
    if trimmed:
        lines.append("…trimmed to the brief's budget — eos procedure show / eos run list / eos note search for the rest")
    return "\n".join(lines).rstrip() + "\n"
