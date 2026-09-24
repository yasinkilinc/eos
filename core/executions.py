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
    entry = Event(execution=execution, ord=None, at=utc_now(), kind=kind, tool=tool,
                  target=target, ref=ref, exit_code=exit_code, ms=ms, body=body,
                  session=session_for(project_root, session))
    _append(ledger, {"type": LINE_EVENT, **dataclasses.asdict(entry)})
    return entry


def finish(project_root: str | Path, execution: str | None = None, *, outcome: str,
           lesson: str | None = None, session: str | None = None) -> Record:
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
    existing = {r.id: r for r in load_path(ledger)}.get(execution)
    if existing is not None and existing.outcome is not None:
        if existing.outcome == outcome:
            return existing
        raise ValueError(f"{execution} already finished as {existing.outcome!r}; "
                         f"it cannot also be {outcome!r}")
    commit, _ = work.git_head(project_root)
    _append(ledger, {"type": LINE_FINISH, "id": execution, "at": utc_now(),
                     "outcome": outcome, "lesson": lesson, "commit_end": commit})
    _clear_pointer(existing.session if existing else session_for(project_root, session), execution)
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
