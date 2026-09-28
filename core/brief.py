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

import math
import re
from pathlib import Path

from core import notes
from core import work
from core.context.budget import CHARS_PER_TOKEN  # noqa: F401 -- re-exported for importers

# The task brief's budget, in tokens, at the engine's one measured rate
# (core/context/budget.py, 2.22 chars/token).
TASK_BUDGET = 1500
# The session-start brief is read once per session and re-read by every call
# after it; measured on a host at ~1,045 tokens before it had a budget.
BRANCH_BUDGET = 800
DETAIL_CHARS = 140

RUN_LIMIT = 3
RELATED_LIMIT = 3
FAILURE_LIMIT = 3
# How much of a prompt's information a procedure's title and tags must hold to
# count as named when the coverage share is diluted (see `best_procedure`):
# the weight of one word that fewer than one note in seven carries, log(7).
# "upgrade" weighed 2.56 on a real store and named its procedure; "env1"
# weighed 0.62 and on its own names nothing. On a store too small for any word
# to be that rare (log N < log 7 for N < 7), one note in sqrt(N) instead.
PROCEDURE_NAMED_RARITY = 7.0
# How a procedure's `## Rules` item is printed; also how the budget loop
# recognises it.
RULE_MARK = "  RULE  "
# C5c's open point: a procedure's steps stay relative to the workspace by
# convention, but the header only ever named the store (this project), not
# where its own steps run -- a session in a service directory had no way to
# tell a step's relative paths resolve from the workspace root rather than
# its own cwd. Printed only when the project belongs to one (`workspace.of`);
# exempt from the budget for the same reason a rule is.
WORKSPACE_MARK = "  steps run from the workspace root: "
# RULE_MARK's own content is bounded at write time (notes.RULES_MAX_CHARS);
# WORKSPACE_MARK's is a filesystem path, never written through that guard, so
# it needs its own cap -- an exemption from the budget with no bound at all
# would let an unusually deep workspace path blow well past it (RVg).
WORKSPACE_PATH_MAX_CHARS = 400
# The routing decision's two lines (ADR-025). Exempt from the budget like a
# procedure's rules: a model recommendation cut off is one that did not arrive.
ROUTE_MARKS = ("ROUTE  ", "  Apply: ", "  Context: ")
# A step is clipped at this many characters. 160 clipped four of eight lines of
# a real procedure and sent the fresh-session eval to `procedure show` for the
# rest; at 240 the same brief is ~950 tokens against the 1,500 budget, whole.
STEP_CHARS = 240

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


def note_label(note) -> str:
    """`[kind, age]` for a note a brief names (2.x roadmap C2): a two-day-old
    lesson and a quarter-old finding weigh differently.

    A lesson naming neither the run that taught it nor other evidence gets a
    short `[no evidence]` mark appended (N10): nothing here says why it can be
    trusted, and the brief is where a session decides whether to act on it.
    """
    import datetime

    try:
        days = (datetime.date.today() - datetime.date.fromisoformat(str(note.created)[:10])).days
        age = "today" if days <= 0 else f"{days}d"
    except ValueError:
        age = "undated"
    label = f"[{note.kind}, {age}]"
    if note.kind == "lesson" and not note.execution and not note.evidence:
        label += " [no evidence]"
    return label


HANDOFF_BUDGET = 400


def for_subagent(project_root: str | Path, task: str, *, session: str | None = None,
                 budget: int = HANDOFF_BUDGET) -> str:
    """What a subagent should know before it starts (2.x roadmap C6).

    A subagent starts with none of the brief its parent session was given: it
    runs the raw command a wrapper covers, improvises a recorded procedure and
    rediscovers a finding. This is the part that changes what it does -- the
    parent run, the procedure's rules, the wrappers the task names, the notes
    whose titles meet it -- within `budget` tokens; empty when none applies.
    """
    from core import capabilities, executions

    root = Path(project_root).expanduser().resolve()
    lines = []
    found = executions.current(root, session) if session else None
    if found is not None:
        execution, ledger = found
        title = next((r.title for r in executions.load_path(Path(ledger)) if r.id == execution), "")
        lines.append(f"- parent run: {execution}" + (f" ({title})" if title else ""))
    corpus = notes.load_notes(root)
    procedure = best_procedure(root, task, corpus)
    if procedure is not None:
        rules = notes.procedure_rules(procedure)[:3]
        lines.append(f"- procedure {procedure.procedure}: follow its steps"
                     + (" -- " + " / ".join(_clip(rule, 120) for rule in rules) if rules else ""))
    try:
        wanted = capabilities.for_task(task, capabilities.load(root))[:3]
    except Exception:  # noqa: BLE001 - a broken registry costs the line
        wanted = []
    if wanted:
        lines.append("- use: " + "; ".join(f"{c.run} ({c.does})" if c.does else c.run for c in wanted)
                     + " -- not the raw command")
    # Canonicalized the same way `search_notes` scored them (N1's synonym
    # groups), or a note it ranked relevant through a synonym alone is found
    # by search and then silently dropped again by this literal-word check.
    canon = notes.synonym_groups(notes.note_synonyms(root))
    words = notes.canonical_words(notes._words(task), canon)
    related = [n for n in notes.search_notes(root, task, limit=RELATED_LIMIT + 3)
               if n.kind != "procedure" and not notes.is_bulk_index(n)
               and notes.canonical_words(notes._words(n.title) | notes._words(" ".join(n.tags)), canon)
               & words][:RELATED_LIMIT]
    if related:
        lines.append("- known here: " + "; ".join(f"{n.title} {note_label(n)}" for n in related)
                     + ' (eos note show . "<title>")')
    if not lines:
        return ""
    text = "EOS handoff (this project's knowledge for your task):\n" + "\n".join(lines)
    cap = int(budget * CHARS_PER_TOKEN)
    return text if len(text) <= cap else text[:cap - 1].rstrip() + "…"


def resume(project_root: str | Path, session: str) -> str:
    """What one session left (2.x roadmap M7): its open runs with their last
    event, the work it holds, and its runs that failed with their lesson."""
    import datetime

    from core import executions

    root = Path(project_root).expanduser().resolve()
    now = datetime.datetime.now(datetime.timezone.utc)
    records = [r for r in executions.load(root) if r.session == session]
    open_runs = [r for r in records if r.open]
    failed = [r for r in records if r.outcome == "failed"]
    held = [item for item in work.items(root)
            if any(holder.get("session") == session for holder in item.holders)]
    lines = [f"EOS — what session {session[:8]} left in {root.name}"]
    if open_runs:
        lines += ["", f"RUNS OPEN ({len(open_runs)}) — finish each: eos run finish . <id> --outcome ok|failed|abandoned"]
        for record in open_runs[:RUN_LIMIT]:
            last = record.events[-1] if record.events else None
            tail = f"; last: {last.kind} {last.tool or ''}".rstrip() if last else "; no events"
            lines.append(f"  {record.id}  {record.title}  [{work.ago(record.started_at, now)}{tail}]")
    if held:
        lines += ["", f"WORK HELD ({len(held)})"]
        lines += [f"  {item.status:<7} {item.id}  {item.title}" for item in held[:WORK_LIMIT]]
    if failed:
        lines += ["", f"FAILED HERE ({len(failed)})"]
        for record in failed[-FAILURE_LIMIT:]:
            lesson = (record.lesson or "").splitlines()[0] if record.lesson else "no lesson"
            lines.append(f"  {record.id}  {record.title}: {_clip(lesson, DETAIL_CHARS)}")
    if len(lines) == 1:
        lines.append("Nothing open from this session: no runs, no held work, no failures.")
    return "\n".join(lines) + "\n"


def _branch_brief(root: Path, *, session: str | None, agent: str | None) -> str:
    """Within BRANCH_BUDGET: fewer work items are shown until it fits (the count stays)."""
    import datetime

    # Everything is read once; only the rendering is repeated with fewer work
    # items until it fits (branch review finding 9: five full rebuilds of git,
    # search and ledger folds could pass the SessionStart timeout).
    now = datetime.datetime.now(datetime.timezone.utc)
    commit, branch = work.git_head(root)
    keys = ticket_keys(root, branch or "")
    query = query_from(branch, keys)
    facts = {
        "now": now, "branch": branch, "in_flight": work.items(root),
        "matched": notes.search_notes(root, query, limit=NOTE_LIMIT) if query else [],
        "recorded": len(notes.load_notes(root)), "open_runs": _open_runs(root),
        "state": work.sync_state(root),
        "elsewhere": _elsewhere(root, keys, now) if keys else [],
    }
    cap = int(BRANCH_BUDGET * CHARS_PER_TOKEN)
    text = ""
    for shown in range(WORK_LIMIT, 0, -1):
        text = _branch_text(root, facts, session=session, work_limit=shown)
        if len(text) <= cap or len(facts["in_flight"]) <= 1:
            break
    return text


ELSEWHERE_LIMIT = 3
LINK_LIMIT = 3


def _elsewhere(root: Path, keys: list[str], now) -> list[str]:
    """Open work and runs naming the branch's ticket in the sibling projects
    that share this project's knowledge root (2.x roadmap C5, read-only): a
    ticket worked from the workspace root is otherwise invisible from the
    service a session opens in."""
    from core import executions

    # A key matched whole: FM-1 is not in FM-12 (review 12).
    patterns = [re.compile(rf"(?<![A-Z0-9]){re.escape(key.upper())}(?![0-9])") for key in keys]

    def names(text: str) -> bool:
        return any(pattern.search(text.upper()) for pattern in patterns)

    def mentioned(paths) -> bool:
        # Cheap before a full parse: a sibling ledger that never names the key is skipped.
        for path in paths:
            try:
                if names(path.read_text(encoding="utf-8", errors="replace")):
                    return True
            except OSError:
                continue
        return False

    own = work.path_for(root)
    found: list[str] = []
    try:
        ledgers = work.across_ledgers(root)
    except Exception:  # noqa: BLE001 - a sibling that cannot be read costs its lines, not the brief
        return []
    for label, ledger in ledgers:
        if ledger == own:
            continue
        run_ledger = ledger.parent / executions.FILENAME
        run_parts = [run_ledger] + [executions.rotated(run_ledger, n) for n in range(1, executions.KEEP_ROTATED + 1)]
        try:
            items = work.fold(work.load_path(ledger)) if mentioned([ledger]) else []
            runs = executions.load_path(run_ledger) if mentioned(run_parts) else []
        except Exception:  # noqa: BLE001
            continue
        for item in items:
            text = f"{item.id} {item.title or ''} {item.ticket or ''}"
            if item.status in (work.OPEN, work.ACTIVE, work.BLOCKED) and names(text):
                found.append(f"  {label}: {item.status} {item.id}  {_clip(item.title or '', DETAIL_CHARS)}")
        for record in runs:
            if record.open and names(record.title or ""):
                found.append(f"  {label}: run open {record.id}  {_clip(record.title or '', DETAIL_CHARS)}  "
                             f"[{work.ago(record.started_at, now)}]")
    return found


def _branch_text(root: Path, facts: dict, *, session: str | None, work_limit: int) -> str:
    now, branch = facts["now"], facts["branch"]
    lines = [f"EOS brief — {root.name}" + (f" @ {branch}" if branch else "")]

    in_flight = facts["in_flight"]
    stale = [item for item in in_flight if item.is_stale(now)]
    if in_flight:
        head = f"IN FLIGHT ({len(in_flight)}"
        head += f", {len(stale)} stale)" if stale else ")"
        lines.append("")
        lines.append(head)
        previous = None
        for item in in_flight[:work_limit]:
            lines.extend(_work_lines(item, now, session, previous))
            previous = item.reason if item.status == work.BLOCKED else None
        if len(in_flight) > work_limit:
            lines.append(f"  …{len(in_flight) - work_limit} more: eos work list .")
    else:
        lines.append("")
        lines.append("IN FLIGHT (0) — nothing is claimed here. "
                     "Claim what you start: eos work add . --title \"…\" --claim")

    elsewhere = facts.get("elsewhere") or []
    if elsewhere:
        lines.append("")
        lines.append(f"ELSEWHERE ({len(elsewhere)}) — this branch's ticket in sibling projects")
        lines.extend(elsewhere[:ELSEWHERE_LIMIT])
        if len(elsewhere) > ELSEWHERE_LIMIT:
            lines.append(f"  …{len(elsewhere) - ELSEWHERE_LIMIT} more: eos work list . --across")

    matched, recorded = facts["matched"], facts["recorded"]
    lines.append("")
    if matched:
        lines.append(f"KNOWN HERE ({len(matched)} of {recorded} notes match this branch)")
        for note in matched:
            lines.append(f"  - {note.title}  {note_label(note)}")
        lines.append('  Read one: eos note show . "<title>"')
    elif recorded:
        # "Nothing matched" and "nothing was written" send a session to
        # different places, and both print as an empty list.
        lines.append(f"KNOWN HERE (0 of {recorded} notes match this branch) — "
                     'search by subject: eos note search . "<subject>"')
    else:
        lines.append("KNOWN HERE (0 notes) — nothing has been recorded in this "
                     "project yet. `eos note add` records the first.")

    open_runs = facts["open_runs"]
    if open_runs:
        lines.append("")
        lines.append(f"RUNS OPEN ({len(open_runs)}) — finish them: eos run finish . <id> --outcome ok|failed|abandoned")
        for record in open_runs[:RUN_LIMIT]:
            lines.append(f"  {record.id}  {record.title}  [{(record.session or 'no session')[:8]}, "
                         f"{work.ago(record.started_at, now)}, {len(record.events)} event(s)]")

    state = facts["state"]
    if state.get("state") not in ("pushed", "committed", "absent"):
        # Printed only when something written here cannot be seen by anyone
        # else. In the healthy case it is a line nobody needs every session.
        lines.append("")
        lines.append(work.sync_sentence(state))

    return "\n".join(lines)


def _work_lines(item, now, session: str | None, previous_reason: str | None = None) -> list[str]:
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
        said = "(same as above)" if item.reason == previous_reason else _clip(item.reason, DETAIL_CHARS)
        lines.append(f"      blocked on: {said}")
    elif item.last:
        lines.append(f"      last: {_clip(item.last, DETAIL_CHARS)}")
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

    Title and tags only. A procedure's body is steps and known failures, and
    one of its words is no sign the prompt names the task: measured, "ok,
    continue" printed the scenario procedure because step 3 says "continue
    from where it stopped" -- about 2,000 characters for a reply that asked
    for nothing. A task is named in the words its procedure is titled and
    tagged with; that is what tags are for.
    """
    corpus = corpus if corpus is not None else notes.load_notes(root)
    replaced = notes.superseded(corpus)
    candidates = [n for n in corpus if n.kind == "procedure" and n.path.name not in replaced
                 and not notes.expired(n)]
    if not candidates:
        return None
    words = notes._words(task)
    weights = notes.word_weights(corpus, words)
    if not any(weight > 0 for weight in weights.values()):
        weights = None
    score, note = max(((notes.relevance(n, words, weights, body=False), n) for n in candidates),
                      key=lambda pair: pair[0])
    heading = notes._words(note.title) | notes._words(" ".join(note.tags))
    matched = words & heading
    # One matched word names a task only when it is most of the prompt
    # ("push'la", "PR aç"). In a longer prompt a lone word whose neighbours
    # are in no note carries the whole share by default: "dosyayı aç ve oku"
    # (open the file and read it) named the PR procedure through "aç" alone.
    # An issue key and a bare number are the task's reference, not its
    # wording, so they do not count: "PROJ-1700'e başla" is one word long.
    wording = {word for word in words if not word.isdigit() and not notes._KEY.fullmatch(word)}
    if len(matched) == 1 and len(wording) > 2:
        return None
    if score >= notes._RELEVANCE_THRESHOLD:
        return note
    # Coverage is a share of the prompt, and a prompt in another language than
    # the notes carries words that are rare only because the notes are in
    # English -- "bunu", "ekle" weigh more than "upgrade" and left "bunu env1
    # upgrade'ine ekle" at 0.147 against 0.15. So a procedure is also named
    # when its title and tags hold enough of the prompt's information on their
    # own, whatever else the prompt says.
    # Two words at least: one word rare only because the notes are in another
    # language is a verb as often as a task -- "koş" (run) named the scenario
    # procedure for "run a service's tests", "aç" (open) would name the PR
    # procedure for "open the file". A task is named by an object and a verb.
    named = sum(weights[word] for word in matched) if weights else float(len(matched))
    floor = math.log(min(PROCEDURE_NAMED_RARITY, math.sqrt(len(corpus))))
    return note if len(matched) >= 2 and named >= floor else None


def _clip(text: str, limit: int = STEP_CHARS) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _run_line(record, parsed=None) -> str:
    """One run; `parsed` (its procedure's typed steps) adds where a failed run failed."""
    from core.executions import Record  # noqa: F401 - the type this reads
    tools = []
    for e in record.events:
        if e.tool and e.tool not in tools:
            tools.append(e.tool)
    when = (record.finished_at or record.started_at or "")[:10]
    parts = [f"  {when}  {(record.outcome or 'open'):<9}", record.target or "-"]
    if tools:
        parts.append(f"{len(record.events)} event(s): {', '.join(tools[:4])}")
    if record.outcome == "failed" and parsed:
        from core import steps

        number = steps.failed_step(parsed, record.events)
        if number:
            parts.append(f"failed at step {number}")
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
        from core import workspace

        home = workspace.of(root)
        if home is not None:
            path_text = str(home)
            if len(path_text) > WORKSPACE_PATH_MAX_CHARS:
                path_text = path_text[: WORKSPACE_PATH_MAX_CHARS - 1] + "…"
            head.append(f"{WORKSPACE_MARK}{path_text}")
        # Whole, never clipped, never trimmed by the budget (`_with_task`).
        head += [f"{RULE_MARK}{rule}" for rule in notes.procedure_rules(procedure)]
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

    # The wrappers a task's words point at, before its first raw command
    # (ADR-026). Declared words only, so a prompt that names no system gets
    # nothing -- this is unrequested context and owes the same bar as notes.
    try:
        from core import capabilities

        wanted = capabilities.for_task(task, capabilities.load(root))
    except Exception:  # noqa: BLE001 - a malformed registry costs the block, never the brief
        wanted = []
    if wanted:
        found = True
        sections.append(["WRAPPERS FOR THIS TASK — use these rather than the raw command"]
                        + [f"  {capability.line()}" for capability in wanted]
                        + ["  Every wrapper here: eos capabilities ."])

    catalogued = _catalogue_state(root, task)
    if runs or catalogued:
        found = True
        total = len(pool)
        from core import steps

        parsed = steps.parse(procedure.body) if procedure is not None else None
        block = [f"LAST RUNS ({len(runs)} of {total})"] + [_run_line(r, parsed) for r in runs]
        # What the host's own catalogue recorded for a scenario the task names
        # -- a second history, indexed through the procedures extension, that
        # an audit found the brief never read.
        for name, env, status, updated, failed_step in catalogued:
            where = f" at {failed_step}" if failed_step else ""
            block.append(f"  {(updated or '')[:10]}  {status.lower():<9}  {env:<8} catalogue: {name}{where}")
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
    # And the note must share a word with the task in its title or tags: a
    # match on body alone is one word of prose, and for a one-word prompt
    # ("continue", in any language) it put two unrelated notes in front of a
    # session that asked for nothing. The brief is unrequested context; it
    # owes a higher bar than a search somebody typed.
    task_words = notes._words(task)
    related = [n for n in notes.search_notes(root, task, limit=RELATED_LIMIT + 6)
               if n.kind != "procedure" and not notes.is_bulk_index(n)
               and (notes._words(n.title) | notes._words(" ".join(n.tags))) & task_words
               ][:RELATED_LIMIT]
    if related:
        found = True
        sections.append(["RELATED NOTES"] + [f"  - {n.title}  {note_label(n)}" for n in related]
                        + ['  Read one: eos note show . "<title>"'])

    # The note graph: what the procedure, or the nearest note, is linked to --
    # a lesson, a note citing it, one watching the same file or ticket.
    anchor = procedure if procedure is not None else (related[0] if related else None)
    if anchor is not None:
        from core import note_graph

        shown = {n.path.name for n in related} | {anchor.path.name} | set(notes.superseded(corpus))
        linked = [(n, why) for n, why in note_graph.related(corpus, anchor.path.name, note_graph.pattern_for(root),
                                                            limit=LINK_LIMIT + len(shown), project_root=root)
                  if n.path.name not in shown and not notes.is_bulk_index(n)][:LINK_LIMIT]
        if linked:
            found = True
            sections.append([f"LINKED TO {_clip(anchor.title, 60)}"]
                            + [f"  - {n.title}  {note_label(n)}  ({why})" for n, why in linked]
                            + ['  All of them: eos note related . "<title>"'])

    slug = procedure.procedure if procedure is not None else "<slug>"
    sections.append([f"Record this run: eos run start . --title \"…\" "
                     + (f"--procedure {slug}" if procedure is not None else "")
                     + "  (wrappers add events; finish with eos run finish . --outcome ok|failed|abandoned)"])
    return sections, found


def _route_section(root: Path, task: str, *, session, agent) -> tuple[list[str], bool]:
    """The ROUTE lines, and whether they alone make the brief worth printing.

    Only for a project that wrote a `[model_routing]` table: without one every
    brief stays byte-for-byte what it was. With `brief = "with-brief"` the
    lines ride along with a brief that has something else to say; `"always"`
    makes them enough on their own. This runs on every prompt, so it records
    nothing unless `record_prompts` asks it to (`_record_prompt`), and any
    failure costs the line, never the brief.
    """
    try:
        import core.routing as routing
        from core.routing import adapters, config

        cfg = config.load(root)
        if not cfg.configured or not cfg.enabled:
            return [], False
        decision = routing.route(root, task, session=session, record=False)
        if cfg.record_prompts:
            _record_prompt(root, task, session, decision, cfg)
        if cfg.brief == "never":
            return [], False
        return adapters.brief_lines(decision, agent), cfg.brief == "always"
    except Exception:  # noqa: BLE001 - the brief must not fail because of the router
        return [], False


def _record_prompt(root: Path, task: str, session, decision, cfg) -> None:
    """Keep the decision for a task prompt, once, so data exists without a run.

    Skipped for a prompt the classifier found no task words in ("ok", "go
    on") -- that is conversation, not a task -- for a decision already reused
    from the open run, and for a task this session already recorded.
    """
    import core.routing as routing
    from core.routing import classify, trace

    if decision.reused or not classify.classify(task, cfg.keywords, cfg.rules).matched:
        return
    if trace.seen(root, session, decision.task_hash):
        return
    routing.route(root, task, session=session, record=True)


# Conversational filler: a prompt made only of these is not a task, and the
# hook that fires on every prompt says nothing to it. English only; a project
# adds its own language under `[brief] filler`. Words under three letters are
# never task words (`notes._words`), so "ok", "go", "on" need no entry.
FILLER = frozenset({
    "okay", "yes", "yeah", "yep", "nope", "sure", "fine", "great", "good", "cool", "nice", "thanks",
    "thank", "please", "continue", "proceed", "carry", "keep", "going", "next", "done", "then", "now",
    "and", "right", "alright", "agreed", "sounds", "let", "lets", "that", "this", "the", "ahead", "again",
    "you", "can", "just", "will",
})


def _filler(root: Path) -> frozenset:
    try:
        from core.lib.config_io import ConfigIO

        extra = (ConfigIO.read_toml(root / ".eos" / "config.toml").get("brief") or {}).get("filler") or []
    except Exception:  # noqa: BLE001 - a malformed table leaves the default
        extra = []
    return FILLER | {str(word).casefold() for word in extra if isinstance(word, str)}


def is_conversation(root: Path, task: str) -> bool:
    """Whether every word of the prompt is filler (matching still uses all of them)."""
    return not (notes._words(task) - _filler(root))


def _with_task(root: Path, task: str, *, session, agent, budget: int, task_only: bool) -> str:
    if task_only and is_conversation(root, task):
        return ""
    sections, found = _task_sections(root, task)
    route_lines, route_alone = _route_section(root, task, session=session, agent=agent)
    if route_lines:
        sections.append(route_lines)
        found = found or route_alone
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
            # A procedure's header and its rules are past the budget: a rule
            # cut for length did not arrive. Bounded at write time
            # (notes.RULES_MAX_CHARS), so the exemption cannot grow. The ROUTE
            # lines are two or three, and exempt for the same reason; once the budget
            # is spent, only exempt lines are still let through.
            exempt = line.startswith(("PROCEDURE  ", RULE_MARK, WORKSPACE_MARK) + ROUTE_MARKS)
            if trimmed and not exempt:
                continue
            if not exempt and len("\n".join(lines + [line])) > cap:
                trimmed = True
                continue
            if trimmed and line.startswith(ROUTE_MARKS[0]) and lines[-1]:
                lines.append("")
            lines.append(line)
    if trimmed:
        lines.append("…trimmed to the brief's budget — eos procedure show / eos run list / eos note search for the rest")
    return "\n".join(lines).rstrip() + "\n"


def _catalogue_state(root: Path, task: str) -> list[tuple]:
    """Rows of the indexed catalogue whose scenario the task names, newest first.

    Read from the index because the catalogue is the host's export and the
    extension's tables are where it lands; no index or no extension means no
    rows, and the brief says nothing about it.
    """
    import sqlite3

    from core import index

    db = index.db_path(root)
    if not db.exists():
        return []
    words = notes._words(task)
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    except sqlite3.Error:
        return []
    try:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        if "procedure_state" not in tables:
            return []
        rows = conn.execute(
            "SELECT p.name, s.env, COALESCE(s.status, '?'), s.updated_at, s.failed_step "
            "FROM catalogue_procedure p JOIN procedure_state s ON s.procedure = p.name "
            "ORDER BY s.updated_at DESC").fetchall()
    except sqlite3.Error:
        return []
    finally:
        conn.close()
    def named(name: str) -> bool:
        parts = set(name.lower().replace("_", "-").split("-"))
        return name.lower() in task.lower() or bool((parts - {"the", "a", "an"}) & words and parts <= words)
    return [row for row in rows if named(row[0])][:RUN_LIMIT]
