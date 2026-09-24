"""What a session did — the execution ledger (ADR-022).

Notes say what is true, the work ledger says what a session intends; this
says what happened. One append-only file beside the other two, three line
types, folded on read:

  start   an execution opens: title, procedure, work item, session, agent,
          target, and the branch and commit it started from
  event   one thing the session did, as a reference: kind, tool, target, a
          log/cache/build/file ref, exit code, duration -- never a payload
  finish  the session declares the outcome; the commit it ended on

Folding is the only way state is derived, for the reason ADR-020 gives: two
writers appending at once both keep their line, which a rewritten record
cannot promise. An `event` for an id with no `start` still creates the
execution -- a lost line must not hide what happened.

The outcome is what the session says. Nothing here concludes anything from
it (ADR-018); later milestones use outcomes as observations.
"""
from __future__ import annotations

import dataclasses
import json
import os
from pathlib import Path

from core import notes

FILENAME = "executions.jsonl"

KINDS = ("ran", "read", "changed", "called", "verified", "noted", "decided")
OUTCOMES = ("ok", "failed", "abandoned")

LINE_START = "start"
LINE_EVENT = "event"
LINE_FINISH = "finish"

# Where "the execution this session has open" is remembered for callers that
# cannot inherit an environment variable -- a harness that runs every command
# in a fresh shell is the ordinary case, not the exception.
STATE_ENV = "EOS_STATE_DIR"
EXECUTION_ENV = "EOS_EXECUTION"
LEDGER_ENV = "EOS_EXECUTION_LEDGER"


@dataclasses.dataclass(frozen=True)
class Event:
    execution: str
    ord: int | None
    at: str
    kind: str
    tool: str | None = None
    target: str | None = None
    ref: str | None = None
    exit_code: int | None = None
    ms: int | None = None
    body: str | None = None
    session: str | None = None


@dataclasses.dataclass
class Record:
    id: str
    title: str
    procedure: str | None = None
    work_item: str | None = None
    session: str | None = None
    agent: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    outcome: str | None = None
    target: str | None = None
    branch: str | None = None
    commit_start: str | None = None
    commit_end: str | None = None
    lesson: str | None = None
    events: list = dataclasses.field(default_factory=list)

    @property
    def open(self) -> bool:
        return self.outcome is None

    def to_dict(self) -> dict:
        data = dataclasses.asdict(self)
        data["events"] = [dataclasses.asdict(e) for e in self.events]
        return data


def path_for(project_root: str | Path) -> Path:
    """Beside the notes and the work ledger, for the reason they are there:
    `eos scan` rewrites `.eos/data`, and what a session did cannot be
    recomputed from a file tree."""
    return notes.notes_dir(project_root) / FILENAME


# --- the session pointer -----------------------------------------------------------


def _state_dir() -> Path:
    configured = os.environ.get(STATE_ENV)
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".local" / "state" / "eos"


def _pointer(session: str) -> Path:
    safe = "".join(ch for ch in session if ch.isalnum() or ch in "-_.")[:64] or "unnamed"
    return _state_dir() / "current" / safe


def session_for(project_root: str | Path, session: str | None = None) -> str | None:
    """An explicit id wins; otherwise the harness's, read as telemetry reads it."""
    if session:
        return session
    from core import telemetry
    detected, _ = telemetry.detect_session(project_root)
    return detected


def current(project_root: str | Path | None = None, session: str | None = None
            ) -> tuple[str, Path] | None:
    """(execution id, ledger path) for whatever this caller should append to.

    The environment first -- a host that can export a variable should not
    depend on a file -- then the pointer `start` left for this session.
    """
    exported, ledger = os.environ.get(EXECUTION_ENV), os.environ.get(LEDGER_ENV)
    if exported and ledger:
        return exported, Path(ledger)
    if exported and project_root is not None:
        return exported, path_for(project_root)
    sid = session_for(project_root or ".", session)
    if not sid:
        return None
    try:
        text = _pointer(sid).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    execution, _, path = text.partition("\t")
    if not execution or not path:
        return None
    return execution, Path(path)


def _write_pointer(session: str, execution: str, ledger: Path) -> None:
    target = _pointer(session)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"{execution}\t{ledger}\n", encoding="utf-8")
    except OSError:
        pass  # capture falls back to explicit ids; recording itself never fails on this


def _clear_pointer(session: str | None, execution: str) -> None:
    if not session:
        return
    target = _pointer(session)
    try:
        if target.read_text(encoding="utf-8").split("\t", 1)[0].strip() == execution:
            target.unlink()
    except OSError:
        pass


# --- writing -----------------------------------------------------------------------


def _append(ledger: Path, line: dict) -> None:
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with open(ledger, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(line, ensure_ascii=False) + "\n")


def _checked(value: str | None, what: str) -> str | None:
    """The refusals the work ledger makes, for the same reason: this file is
    committed and pushed like the notes are."""
    from core import work
    work._refuse_unusable(value, what)
    return value


def start(project_root: str | Path, title: str, *, procedure: str | None = None,
          work_item: str | None = None, session: str | None = None,
          agent: str | None = None, target: str | None = None) -> Record:
    """Open an execution and remember it as this session's current one."""
    from core import work
    from core.knowledge.evidence import utc_now

    if not title or not title.strip():
        raise ValueError("an execution without a title is not findable by anyone")
    _checked(title, "title")
    sid = session_for(project_root, session)
    commit, branch = work.git_head(project_root)
    record = Record(id=f"x-{work.make_id(title)}", title=title.strip(), procedure=procedure,
                    work_item=work_item, session=sid, agent=agent, started_at=utc_now(),
                    target=target, branch=branch, commit_start=commit)
    ledger = path_for(project_root)
    line = {"type": LINE_START, **{k: v for k, v in record.to_dict().items() if k != "events"}}
    _append(ledger, line)
    if sid:
        _write_pointer(sid, record.id, ledger.resolve())
    return record


def event(project_root: str | Path, execution: str | None = None, *, kind: str,
          tool: str | None = None, target: str | None = None, ref: str | None = None,
          exit_code: int | None = None, ms: int | None = None, body: str | None = None,
          session: str | None = None) -> Event:
    """Append one thing the session did. Without an id, to this session's current execution."""
    from core.knowledge.evidence import utc_now

    if kind not in KINDS:
        raise ValueError(f"kind must be one of {', '.join(KINDS)}; got {kind!r}")
    ledger = path_for(project_root)
    if not execution:
        found = current(project_root, session)
        if found is None:
            raise ValueError("no execution given and none is open for this session; "
                             "`eos run start` opens one")
        execution, ledger = found
    _checked(ref, "ref")
    _checked(body, "body")
    entry = Event(execution=execution, ord=None, at=utc_now(), kind=kind, tool=normalize_tool(tool),
                  target=normalize_target(target), ref=ref, exit_code=exit_code, ms=ms, body=body,
                  session=session_for(project_root, session))
    _append(ledger, {"type": LINE_EVENT, **dataclasses.asdict(entry)})
    return entry


def finish(project_root: str | Path, execution: str | None = None, *, outcome: str,
           lesson: str | None = None, session: str | None = None,
           next_time: str | None = None) -> Record:
    """Close an execution with the outcome the session declares.

    Idempotent: finishing again with the same outcome changes nothing. A
    different outcome for a finished execution is refused -- two outcomes for
    one run is a contradiction the next reader could not resolve.
    """
    from core import work
    from core.knowledge.evidence import utc_now

    if outcome not in OUTCOMES:
        raise ValueError(f"outcome must be one of {', '.join(OUTCOMES)}; got {outcome!r}")
    ledger = path_for(project_root)
    if not execution:
        found = current(project_root, session)
        if found is None:
            raise ValueError("no execution given and none is open for this session")
        execution, ledger = found
    _checked(lesson, "lesson")
    _checked(next_time, "next time")
    existing = {r.id: r for r in load_path(ledger)}.get(execution)
    if existing is not None and existing.outcome is not None:
        if existing.outcome == outcome:
            return existing
        raise ValueError(f"{execution} already finished as {existing.outcome!r}; "
                         f"it cannot also be {outcome!r}")
    has_lesson = bool(lesson and lesson.strip())
    if outcome == "failed" and not has_lesson and not notes.lessons_for(project_root, execution=execution):
        # ADR-024: a failure nobody learned from is paid for again by the next
        # session. Refused before anything is written, with both ways through.
        raise ValueError(
            f"a failed run must leave a lesson. Finish it with --lesson \"<what went wrong and "
            f"what to do differently>\", or record one first: eos note add . --kind lesson "
            f"--execution {execution} --title \"…\" --body \"## What went wrong ... "
            f"## What was learned ... ## Next time ...\"")
    if outcome == "failed" and has_lesson:
        # Written before the finish line: if the note cannot be written, the
        # run stays open rather than finished with its lesson lost.
        _write_lesson(project_root, existing or Record(id=execution, title=execution),
                      lesson, next_time)
    commit, _ = work.git_head(project_root)
    at = utc_now()
    _append(ledger, {"type": LINE_FINISH, "id": execution, "at": at,
                     "outcome": outcome, "lesson": lesson, "commit_end": commit})
    _clear_pointer(existing.session if existing else session_for(project_root, session), execution)
    # The one place a procedure's observations move (ADR-023). After the
    # ledger line, so the ledger -- which `procedure audit` recomputes from --
    # is never behind the counters it justifies.
    if existing is not None and existing.procedure:
        notes.record_procedure_run(project_root, existing.procedure, outcome=outcome,
                                   execution=execution, at=at, lesson=lesson)
    return {r.id: r for r in load_path(ledger)}[execution]


# --- reading -----------------------------------------------------------------------

_RECORD_FIELDS = {f.name for f in dataclasses.fields(Record)} - {"events"}
_EVENT_FIELDS = [f.name for f in dataclasses.fields(Event)]


def load_path(ledger: Path) -> list[Record]:
    """Every execution in one ledger, in the order it was started."""
    if not ledger.is_file():
        return []
    try:
        text = ledger.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    records: dict[str, Record] = {}

    def get(execution: str) -> Record:
        record = records.get(execution)
        if record is None:
            record = Record(id=execution, title=execution)
            records[execution] = record
        return record

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except ValueError:
            continue
        if not isinstance(data, dict):
            continue
        kind = data.get("type")
        if kind == LINE_START and data.get("id"):
            record = get(data["id"])
            for name in _RECORD_FIELDS:
                if data.get(name) is not None:
                    setattr(record, name, data[name])
        elif kind == LINE_EVENT and data.get("execution") and data.get("kind"):
            record = get(data["execution"])
            values = {name: data.get(name) for name in _EVENT_FIELDS}
            values["ord"] = len(record.events)
            # An event appended without a session id belongs to the session
            # that opened the run: a wrapper called from a context with no
            # harness variable did not stop being part of it.
            if not values.get("session"):
                values["session"] = record.session
            values["tool"] = normalize_tool(values.get("tool"))
            values["target"] = normalize_target(values.get("target"))
            try:
                record.events.append(Event(**values))
            except TypeError:
                continue
            if record.started_at is None:
                record.started_at = values["at"]
        elif kind == LINE_FINISH and data.get("id"):
            record = get(data["id"])
            if record.outcome is None:
                record.outcome = data.get("outcome")
                record.finished_at = data.get("at")
                record.lesson = data.get("lesson")
                record.commit_end = data.get("commit_end")
    return list(records.values())


def load(project_root: str | Path) -> list[Record]:
    return load_path(path_for(project_root))


def by_session(project_root: str | Path, session: str) -> list[Record]:
    """Executions this session started or acted in."""
    return [r for r in load(project_root)
            if r.session == session or any(e.session == session for e in r.events)]


def resolve(records: list[Record], needle: str) -> Record:
    """By exact id, then by id prefix, then by title words -- one match or an error."""
    exact = [r for r in records if r.id == needle]
    if exact:
        return exact[0]
    matches = [r for r in records if r.id.startswith(needle)] or \
              [r for r in records if needle.casefold() in r.title.casefold()]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise ValueError(f"no execution matches {needle!r}")
    raise ValueError(f"{len(matches)} executions match {needle!r}: "
                     + ", ".join(r.id for r in matches[:5]))


# --- procedures, from the ledger's side (ADR-023) ---------------------------------

NOT_VERIFIED_AFTER_DAYS = 30


def of_procedure(project_root: str | Path, slug: str) -> list[Record]:
    return [r for r in load(project_root) if r.procedure == slug]


def audit_procedures(project_root: str | Path, now: str | None = None) -> list[dict]:
    """What `eos procedure audit` reports, one dict per procedure.

    Counters are recomputed from the ledger, which is the source of truth
    they summarise; a disagreement is a hand edit or a lost write, and is
    reported rather than repaired -- which side is right is not knowable here.
    """
    import datetime

    from core.knowledge.evidence import utc_now

    clock = datetime.datetime.fromisoformat(now or utc_now())
    stale_names = {entry["note"] for entry in notes.stale_notes(project_root)}
    report = []
    for note in notes.procedures(project_root):
        runs = of_procedure(project_root, note.procedure or "")
        ok = sum(1 for r in runs if r.outcome == "ok")
        failed = sum(1 for r in runs if r.outcome == "failed")
        verified = max((r.finished_at for r in runs if r.outcome == "ok" and r.finished_at), default=None)
        finished = [r for r in runs if r.outcome]
        problems = []
        if (note.runs_ok or 0, note.runs_failed or 0) != (ok, failed):
            problems.append(f"counters say {note.runs_ok or 0} ok / {note.runs_failed or 0} failed, "
                            f"the ledger says {ok} / {failed}")
        if finished and finished[-1].outcome == "failed":
            problems.append(f"the latest run failed ({finished[-1].id})")
        if verified is None:
            problems.append("never verified by a finished run")
        else:
            age = (clock - datetime.datetime.fromisoformat(verified)).days
            if age > NOT_VERIFIED_AFTER_DAYS:
                problems.append(f"last verified {age} days ago")
        if note.path.name in stale_names:
            problems.append("a file it is scoped to changed since it was written")
        report.append({"procedure": note.procedure, "title": note.title, "path": str(note.path),
                       "runs_ok": ok, "runs_failed": failed, "last_verified": verified,
                       "problems": problems,
                       "mismatch": any(p.startswith("counters say") for p in problems)})
    return report


# --- retrieval (M3) --------------------------------------------------------------


def _ubiquitous(records: list[Record]) -> set[str]:
    """Words in more than half of the records' titles, targets and procedures.

    Nine of ten runs here were titled "Run the <x> scenario on env1", so a
    prompt containing "run" -- most prompts -- matched every one of them and
    the brief listed scenario runs under a task about nothing of the kind. A
    word that describes most of the ledger describes none of it.
    """
    if len(records) < 2:
        return set()
    counts: dict[str, int] = {}
    for record in records:
        text = " ".join(part for part in (record.title, record.target, record.procedure) if part)
        for word in notes._words(text):
            counts[word] = counts.get(word, 0) + 1
    return {word for word, n in counts.items() if n * 2 > len(records)}


def _task_overlap(record: Record, task_words: set[str]) -> int:
    text = " ".join(part for part in (record.title, record.target, record.procedure) if part)
    return len(notes._words(text) & task_words)


def ranked(project_root: str | Path, *, procedure: str | None = None, target: str | None = None,
           task: str | None = None, limit: int = 3, records: list[Record] | None = None
           ) -> list[Record]:
    """The executions a session about to do this should read, best first.

    Recency decides the order, because the last run is the one most likely to
    still describe the system. The ledger's own line order breaks ties: runs
    finished inside one second share a timestamp, and the later line is the
    later run. Which runs are candidates is decided before that:

      procedure given   runs of that procedure only
      else task given   runs whose title/target/procedure share a word with it
      else              every run

    and, when a target is given, runs against it lead -- a staging run says
    more about the next staging run than a production one does.

    A failed run with a lesson is not promoted above newer runs here; the
    brief shows it separately when it falls outside the top N, so the order
    stays "most recent first" and nothing a session needs is cut.
    """
    found = records if records is not None else load(project_root)
    ordered = list(reversed(found))  # ledger order, newest first
    if procedure:
        ordered = [r for r in ordered if r.procedure == procedure]
    elif task:
        words = notes._words(task) - _ubiquitous(found)
        scored = [(r, _task_overlap(r, words)) for r in ordered]
        ordered = [r for r, overlap in sorted(scored, key=lambda pair: -pair[1]) if overlap > 0]
    if target:
        ordered = sorted(ordered, key=lambda r: r.target != target)
    return ordered[:limit] if limit else ordered


def last_lesson(records: list[Record]) -> Record | None:
    """The most recent failed run that left a lesson, among `records`."""
    for record in reversed(records):
        if record.outcome == "failed" and record.lesson:
            return record
    return None


# --- lessons (ADR-024) ------------------------------------------------------------

SEEN_AGAIN = "Seen again"


def _write_lesson(project_root: str | Path, record: Record, lesson: str, next_time: str | None) -> Path:
    """The lesson note a failed run leaves behind, or one more sighting of it."""
    first = " ".join(lesson.strip().splitlines()[0].split())
    title = first if len(first) <= 100 else first[:99] + "…"
    broke = [e for e in record.events if isinstance(e.exit_code, int) and e.exit_code != 0]
    went_wrong = [f"The run \"{record.title}\"" + (f" against {record.target}" if record.target else "")
                  + f" ({record.id}) failed."]
    for e in broke[:5]:
        went_wrong.append(f"- {e.tool or e.kind} exited {e.exit_code}" + (f" ({e.ref})" if e.ref else ""))
    body = "\n".join([
        "## What went wrong", "", *went_wrong, "",
        "## What was learned", "", lesson.strip(), "",
        "## Next time", "",
        (next_time or "").strip() or f"Read this before running "
        f"{record.procedure or record.title} again.",
    ])
    try:
        return notes.add_note(project_root, kind="lesson", title=title, body=body, tags=["lesson"],
                              session=record.session, procedure=record.procedure,
                              execution=record.id)
    except notes.DuplicateNoteError:
        # The same lesson again is a recurring failure, and should read as one.
        key = notes.normalized_title_key(title)
        for note in notes.lessons_for(project_root):
            if notes.normalized_title_key(note.title) == key:
                notes.append_to_note_section(note.path, SEEN_AGAIN,
                                             f"{(record.started_at or '')[:10]} {record.id}")
                return note.path
        # A different note already holds this title; keep the lesson, under the run's name.
        return notes.add_note(project_root, kind="lesson", title=f"{title} ({record.id})", body=body,
                              tags=["lesson"], session=record.session, procedure=record.procedure,
                              execution=record.id)


# --- tool and change memory (M5) ----------------------------------------------------

_TOOL_SUFFIXES = (".sh", ".py", ".js", ".ts", ".exe", ".cmd", ".bat")


def normalize_tool(name: str | None) -> str | None:
    """`automation/jenkins.sh`, `./Jenkins.sh` and `jenkins` are one tool.

    The wrapper or program name, lower-cased, without a path or a script
    suffix -- so "which tools ran" counts a tool once however a wrapper
    happened to spell its own name.
    """
    if not name or not str(name).strip():
        return None
    base = str(name).strip().replace("\\", "/").rstrip("/").rsplit("/", 1)[-1].lower()
    for suffix in _TOOL_SUFFIXES:
        if base.endswith(suffix) and len(base) > len(suffix):
            base = base[: -len(suffix)]
            break
    return base or None


def normalize_target(name: str | None) -> str | None:
    return str(name).strip().lower() or None if name else None


@dataclasses.dataclass
class ToolUse:
    tool: str
    count: int = 0
    failures: int = 0
    runs: int = 0
    last_outcome: str | None = None
    last_at: str | None = None
    targets: list = dataclasses.field(default_factory=list)


def tools(project_root: str | Path, *, procedure: str | None = None,
          target: str | None = None, records: list[Record] | None = None) -> list[ToolUse]:
    """Which tools ran, how often, how often they exited non-zero, and the
    outcome of the most recent run each was part of -- most used first."""
    found = records if records is not None else load(project_root)
    wanted = normalize_target(target)
    usage: dict[str, ToolUse] = {}
    for record in found:  # ledger order, so "last" is the latest run
        if procedure and record.procedure != procedure:
            continue
        seen_here: set[str] = set()
        for e in record.events:
            tool = normalize_tool(e.tool)
            event_target = normalize_target(e.target) or normalize_target(record.target)
            if not tool or (wanted and event_target != wanted):
                continue
            use = usage.setdefault(tool, ToolUse(tool=tool))
            use.count += 1
            if isinstance(e.exit_code, int) and e.exit_code != 0:
                use.failures += 1
            if event_target and event_target not in use.targets:
                use.targets.append(event_target)
            if tool not in seen_here:
                use.runs += 1
                seen_here.add(tool)
            use.last_outcome = record.outcome
            use.last_at = e.at
    return sorted(usage.values(), key=lambda u: (-u.count, u.tool))


def diff(project_root: str | Path, execution: str) -> dict:
    """What an execution changed: the paths its `changed` events name, and the
    commit range it ran across with the commits and files inside it.

    References only (ADR-022): paths and commit ids, resolved through git for
    as long as the repository holds them. No content is stored or printed.
    """
    import shutil
    import subprocess

    record = resolve(load(project_root), execution)
    paths: list[str] = []
    for e in record.events:
        if e.kind == "changed" and e.ref and e.ref not in paths:
            paths.append(e.ref)
    out = {"execution": record.id, "paths": paths, "commit_start": record.commit_start,
           "commit_end": record.commit_end, "commits": [], "committed_paths": []}
    git = shutil.which("git")
    if git and record.commit_start and record.commit_end and record.commit_start != record.commit_end:
        span = f"{record.commit_start}..{record.commit_end}"
        try:
            log = subprocess.run([git, "-C", str(project_root), "log", "--format=%H%x09%s", span],
                                 capture_output=True, text=True, timeout=30)
            files = subprocess.run([git, "-C", str(project_root), "diff", "--name-only", span],
                                   capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            return out
        if log.returncode == 0:
            out["commits"] = [line.split("\t", 1) for line in log.stdout.splitlines() if "\t" in line]
        if files.returncode == 0:
            out["committed_paths"] = [line for line in files.stdout.splitlines() if line.strip()]
    return out
