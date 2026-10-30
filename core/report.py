"""`eos report`: what happened while the user was away, in three parts.

Done is a run whose outcome the record proves: it finished ok, its events recompute
to verified, and the events behind that were written by a wrapper or a hook. The
`outcome_source` on its finish line is only text, and `eos run event` lets a session
type a passing check. Everything else stays out of done and is only counted.

Waiting is the parked items that end by themselves, with their condition. Questions
are what only the user can answer: open assumptions and `--until user` items.

Read-only, except the one mark `note_shown` keeps: when a report was last shown here.
"""
from __future__ import annotations

import dataclasses
import datetime
import hashlib
import os
from pathlib import Path

from core import brief, decisions, executions, work

# Per section in the session-start brief; `eos report` prints them all.
BRIEF_LIMIT = 5
EVIDENCE_LIMIT = 3
_REF_CHARS = brief.DETAIL_CHARS
_MARK_DIR = "last-report"
_NO_CHECK_RESULT = "last check: not in the ledger"
_DEAD_OUTCOMES = ("failed", "abandoned")


@dataclasses.dataclass(frozen=True)
class Shown:
    """A report about to be shown: the hook marks it once the text is assembled."""

    text: str
    at: datetime.datetime
    ids: tuple[str, ...]


def parse_since(text: str) -> datetime.datetime:
    """An ISO 8601 date or instant; one without an offset is local time."""
    try:
        parsed = datetime.datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(f"--since {text!r} is not an ISO 8601 date or time, "
                         "e.g. 2026-10-30 or 2026-10-30T08:00:00Z") from None
    return parsed if parsed.tzinfo else parsed.astimezone()


def _mark_file(project_root: str | Path) -> Path:
    root = str(Path(project_root).expanduser().resolve())
    return executions._state_dir() / _MARK_DIR / hashlib.sha256(root.encode("utf-8")).hexdigest()[:16]


def note_shown(project_root: str | Path, at: datetime.datetime | None = None, ids: tuple[str, ...] = ()) -> None:
    """Remember when a report was last shown here, and which entries of that second it
    held: stamps have whole seconds, so the second itself stays in the next window. A
    mark that cannot be written only makes the next report repeat."""
    path = _mark_file(project_root)
    stamp = at or datetime.datetime.now(datetime.timezone.utc)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join((stamp.isoformat(), *ids)), encoding="utf-8")
    except OSError:
        pass


def _mark_lines(project_root: str | Path) -> list[str]:
    try:
        return _mark_file(project_root).read_text(encoding="utf-8").splitlines()
    except (OSError, ValueError):
        return []


def last_shown(project_root: str | Path) -> datetime.datetime | None:
    lines = _mark_lines(project_root)
    if not lines:
        return None
    try:
        parsed = datetime.datetime.fromisoformat(lines[0].strip())
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.astimezone()


def shown_ids(project_root: str | Path) -> frozenset[str]:
    """The entries of the mark's own second that the last report held."""
    return frozenset(line.strip() for line in _mark_lines(project_root)[1:] if line.strip())


def resumed_session(project_root: str | Path, session: str | None) -> bool:
    """Whether a runner of parked items started this session: it claims the item under
    the session id before the session exists, and what it prompts is not the user."""
    if not session:
        return False
    from core import workspace

    root = Path(project_root).expanduser().resolve()
    for ledger_root in filter(None, (root, workspace.of(root))):
        if any(e.session == session and e.agent == work.RESUME_AGENT for e in work.load(ledger_root)):
            return True
    return False


def _child_session(project_root: str | Path, session: str | None) -> bool:
    """Whether this session inherited another session's run: a child `claude -p`."""
    if not os.environ.get(executions.EXECUTION_ENV):
        return False
    execution, ledger = executions.current(project_root, session)
    run = next(iter(executions.load_path(ledger, only=execution)), None)
    return run is not None and run.session != session


def _fresh(at: str | None, entry_id: str, since: datetime.datetime | None, shown: frozenset[str]) -> bool:
    """Whether an entry is in the window. The mark's whole second stays in it, less
    what the last report already held of that second."""
    if since is None:
        return True
    floor = since.replace(microsecond=0)
    when = decisions._when(at).replace(microsecond=0)
    return when > floor or (when == floor and entry_id not in shown)


def _done(root: Path, records: list[executions.Record], since: datetime.datetime | None,
          shown: frozenset[str]) -> tuple[list[dict], list[str]]:
    done, unproven = [], []
    runs = {r.id: r for r in records if r.outcome == "ok"}
    wanted = [r for r in runs.values() if _fresh(r.finished_at, r.id, since, shown)]
    # Each run is proven against its own project's scopes; one read per ledger, not per run.
    # The finish line is only a filter: it can hide a run, never prove one, and recomputing
    # every run of a large ledger costs more than a prompt hook's timeout.
    owners = {}
    if any(r.outcome_source == "verified" for r in wanted):
        owners = {r.id: project for project in reversed(decisions.project_roots(root))
                  for r in executions.load(project)}
    for run in wanted:
        owner = owners.get(run.id, root)
        proof = []
        if run.outcome_source == "verified" and executions.outcome_source(owner, run)[0] == "verified":
            proof = executions.proof(owner, run)
        if not proof:
            unproven.append(run.id)
            continue
        done.append({"id": run.id, "title": run.title, "at": run.finished_at, "work_item": run.work_item,
                     "evidence": [{"id": e.id, "tool": brief._clip(e.tool, _REF_CHARS) if e.tool else e.tool,
                                   "ref": brief._clip(e.ref, _REF_CHARS) if e.ref else e.ref} for e in proof]})
    return _newest_first(done), unproven


def _items(root: Path) -> list[work.Item]:
    found: dict[str, work.Item] = {}
    for project in decisions.project_roots(root):
        for item in work.items(project, status="all"):
            found.setdefault(item.id, item)
    return list(found.values())


def _waiting(items: list[work.Item]) -> list[dict]:
    return [{"id": i.id, "title": i.title, "until": i.until, "check": i.check, "at": i.updated_at,
             "last_check": i.last_check}
            for i in items if i.status == work.BLOCKED and i.until and i.until != "user"]


def _assumptions(records: list[executions.Record], items: list[work.Item]) -> list[dict]:
    """Open assumptions someone can still act on: not of dropped work, not of a run
    with no work item that failed or was abandoned. Done work keeps its questions: a
    guess is no less a guess because the work went on."""
    dropped = {i.id for i in items if i.status == work.DROPPED}
    runs = {r.id: r for r in records}
    live = []
    for item in decisions.all_open(records):
        if item.work_item in dropped:
            continue
        run = runs.get(item.execution)
        if not item.work_item and run is not None and run.outcome in _DEAD_OUTCOMES:
            continue
        live.append({"kind": "assumption", "id": item.id, "text": item.claim, "at": item.at,
                     "scope": item.work_item or item.execution})
    return live


def _user_waits(items: list[work.Item]) -> list[dict]:
    return [{"kind": "user", "id": i.id, "text": f"{i.title}: {i.reason}" if i.reason else i.title,
             "at": i.updated_at, "scope": None}
            for i in items if i.status == work.BLOCKED and i.until == "user"]


def _newest_first(entries: list[dict]) -> list[dict]:
    """Newest first; entries of one second by id, so the order does not depend on read order."""
    by_id = sorted(entries, key=lambda entry: entry["id"])
    return sorted(by_id, key=lambda entry: decisions._when(entry["at"]).replace(microsecond=0), reverse=True)


def build(project_root: str | Path, *, since: datetime.datetime | None = None,
          session: str | None = None, shown: frozenset[str] = frozenset()) -> dict:
    root = Path(project_root).expanduser().resolve()
    records = decisions.all_records(root, session)
    items = _items(root)
    done, unproven = _done(root, records, since, shown)
    return {"project": root.name, "since": since.isoformat() if since else None, "done": done,
            "unproven": unproven, "waiting": _newest_first(_waiting(items)),
            "blocked_no_wait": sum(i.status == work.BLOCKED and not i.until for i in items),
            "questions": _newest_first(_assumptions(records, items) + _user_waits(items))}


def is_new(found: dict, since: datetime.datetime | None, shown: frozenset[str] = frozenset()) -> bool:
    """A verified run, or a wait or question that moved. Runs without proof are not
    news: every read-only session finishes one. Nor is a block with no condition."""
    moved = found["waiting"] + found["questions"]
    return bool(found["done"]) or any(_fresh(entry["at"], entry["id"], since, shown) for entry in moved)


def _section(heading: str, entries: list, limit: int | None, more: str, render_one) -> list[str]:
    lines = [f"{heading} ({len(entries)})"]
    for entry in entries[:limit]:
        lines.extend(render_one(entry))
    if limit is not None and len(entries) > limit:
        lines.append(f"  …{len(entries) - limit} more: {more}")
    return lines


def render(found: dict, limit: int | None = None) -> str:
    now = datetime.datetime.now(datetime.timezone.utc)
    since = found["since"]
    more = f"eos report . --since {since}" if since else "eos report ."
    when = decisions._when(since).astimezone().strftime("%Y-%m-%d %H:%M") if since else "the start of the record"

    def done_lines(entry: dict) -> list[str]:
        cited = [" ".join(part for part in (e["id"], e["tool"], e["ref"]) if part) for e in entry["evidence"]]
        shown = "; ".join(cited[:EVIDENCE_LIMIT])
        if len(cited) > EVIDENCE_LIMIT:
            shown += f"; …{len(cited) - EVIDENCE_LIMIT} more: eos run show . {entry['id']}"
        return [f"  {entry['id']}  {brief._clip(entry['title'], brief.DETAIL_CHARS)}  [{work.ago(entry['at'], now)}]",
                f"      evidence: {shown}"]

    def waiting_lines(entry: dict) -> list[str]:
        last = entry["last_check"]
        result = f"last check: {last['result']} [{work.ago(last['at'], now)}]" if last else _NO_CHECK_RESULT
        check = (f"check: {brief._clip(entry['check'], brief.DETAIL_CHARS)}  {result}" if entry["check"]
                 else "no check (a clock)")
        return [f"  {entry['id']}  {brief._clip(entry['title'], brief.DETAIL_CHARS)}  until {entry['until']}  "
                f"{check}  [{work.ago(entry['at'], now)}]"]

    def question_lines(entry: dict) -> list[str]:
        scope = f"{entry['scope']}, " if entry["scope"] else ""
        return [f"  {entry['id']}  {brief._clip(entry['text'], brief.DETAIL_CHARS)}  "
                f"[{scope}{work.ago(entry['at'], now)}]"]

    lines = [f"EOS report — {found['project']} since {when}", ""]
    lines += _section("DONE", found["done"], limit, more, done_lines)
    if found["unproven"]:
        lines.append(f"  not listed: {len(found['unproven'])} run(s) finished ok with no proof on record")
    lines += [""] + _section("WAITING", found["waiting"], limit, more, waiting_lines)
    if found["blocked_no_wait"]:
        lines.append(f"  not listed: {found['blocked_no_wait']} blocked with no condition")
    lines += [""] + _section("QUESTIONS", found["questions"], limit, more, question_lines)
    return "\n".join(lines)


def for_brief(project_root: str | Path, session: str | None) -> Shown | None:
    """The report a session start or a user prompt shows, or None: nothing is new since
    the last one shown, or a runner or a parent started this session. Nothing is marked
    here: the caller marks it with `mark` once the report is in its text."""
    if resumed_session(project_root, session) or _child_session(project_root, session):
        return None
    at = datetime.datetime.now(datetime.timezone.utc)  # before the read: what lands during it is not lost
    since, shown = last_shown(project_root), shown_ids(project_root)
    found = build(project_root, since=since, session=session, shown=shown)
    if not is_new(found, since, shown):
        return None
    second = at.replace(microsecond=0)
    held = [e["id"] for e in found["done"] + found["waiting"] + found["questions"]
            if decisions._when(e["at"]).replace(microsecond=0) == second]
    return Shown(render(found, limit=BRIEF_LIMIT), at, tuple(held))


def mark(project_root: str | Path, shown: Shown) -> None:
    note_shown(project_root, at=shown.at, ids=shown.ids)
