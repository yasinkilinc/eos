"""`eos report`: what happened while the user was away, in three parts.

Done is a run whose outcome the record proves: it finished ok, `outcome_source`
says verified, and the events behind that were written by a wrapper or a hook.
`outcome_source` alone is not enough, because `eos run event` lets a session type
a passing check. Everything else stays out of done and is only counted.

Waiting is the parked items that end by themselves, with their condition. Questions
are what only the user can answer: open assumptions and `--until user` items.

Read-only, except the one mark `note_prompt` keeps: when the user last spoke here.
"""
from __future__ import annotations

import datetime
import hashlib
from pathlib import Path

from core import brief, decisions, executions, work

# Per section in the session-start brief; `eos report` prints them all.
BRIEF_LIMIT = 5
EVIDENCE_LIMIT = 3
_PROMPT_DIR = "last-prompt"
_NO_CHECK_RESULT = "last check: not in the ledger"
_DEAD_OUTCOMES = ("failed", "abandoned")
_CLOSED = (work.DONE, work.DROPPED)


def parse_since(text: str) -> datetime.datetime:
    """An ISO 8601 date or instant; one without an offset is local time."""
    try:
        parsed = datetime.datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(f"--since {text!r} is not an ISO 8601 date or time, "
                         "e.g. 2026-10-30 or 2026-10-30T08:00:00Z") from None
    return parsed if parsed.tzinfo else parsed.astimezone()


def _prompt_file(project_root: str | Path) -> Path:
    root = str(Path(project_root).expanduser().resolve())
    return executions._state_dir() / _PROMPT_DIR / hashlib.sha256(root.encode("utf-8")).hexdigest()[:16]


def note_prompt(project_root: str | Path, at: datetime.datetime | None = None) -> None:
    """Remember when the user last spoke here. A mark that cannot be written only
    makes the next report's window longer."""
    path = _prompt_file(project_root)
    stamp = at or datetime.datetime.now(datetime.timezone.utc)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(stamp.isoformat(), encoding="utf-8")
    except OSError:
        pass


def last_prompt(project_root: str | Path) -> datetime.datetime | None:
    try:
        return datetime.datetime.fromisoformat(_prompt_file(project_root).read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


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


def _done(root: Path, records: list[executions.Record], since: datetime.datetime | None
          ) -> tuple[list[dict], list[str]]:
    done, unproven = [], []
    runs = {r.id: r for r in records if r.outcome == "ok"}
    wanted = [r for r in runs.values() if since is None or decisions._when(r.finished_at) >= since]
    # Each run is proven against its own project's scopes; one read per ledger, not per run.
    owners = {}
    if any(r.outcome_source == "verified" for r in wanted):
        owners = {r.id: project for project in reversed(decisions.project_roots(root))
                  for r in executions.load(project)}
    for run in wanted:
        proof = []
        if run.outcome_source == "verified":
            proof = executions.proof(owners.get(run.id, root), run)
        if not proof:
            unproven.append(run.id)
            continue
        done.append({"id": run.id, "title": run.title, "at": run.finished_at, "work_item": run.work_item,
                     "evidence": [{"id": e.id, "tool": e.tool, "ref": e.ref} for e in proof]})
    return sorted(done, key=lambda d: decisions._when(d["at"]), reverse=True), unproven


def _items(root: Path) -> list[work.Item]:
    found: dict[str, work.Item] = {}
    for project in decisions.project_roots(root):
        for item in work.items(project, status="all"):
            found.setdefault(item.id, item)
    return list(found.values())


def _waiting(items: list[work.Item]) -> list[dict]:
    """`last_check` is always None today: a check that does not pass writes nothing to
    the work ledger, and a log line there is not a check result."""
    return [{"id": i.id, "title": i.title, "until": i.until, "check": i.check, "at": i.updated_at,
             "last_check": None}
            for i in items if i.status == work.BLOCKED and i.until and i.until != "user"]


def _assumptions(root: Path, records: list[executions.Record], items: list[work.Item]) -> list[dict]:
    """Open assumptions someone can still act on: not of a closed work item, not of a
    run with no work item that failed or was abandoned."""
    deciding = [r for r in records if any(e.kind == "decided" for e in r.events)]
    closed = {i.id for i in items if i.status in _CLOSED}
    runs = {r.id: r for r in records}
    scopes = {(r.work_item, None if r.work_item else r.id) for r in deciding}
    found: dict[str, decisions.Decision] = {}
    for work_item, execution in scopes:
        for item in decisions.open_assumptions(root, work_item=work_item, execution=execution, records=deciding):
            found.setdefault(item.id, item)
    live = []
    for item in found.values():
        if item.work_item in closed:
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
    return sorted(entries, key=lambda entry: decisions._when(entry["at"]), reverse=True)


def build(project_root: str | Path, *, since: datetime.datetime | None = None,
          session: str | None = None) -> dict:
    root = Path(project_root).expanduser().resolve()
    records = decisions.all_records(root, session)
    items = _items(root)
    done, unproven = _done(root, records, since)
    return {"project": root.name, "since": since.isoformat() if since else None, "done": done,
            "unproven": unproven, "waiting": _newest_first(_waiting(items)),
            "questions": _newest_first(_assumptions(root, records, items) + _user_waits(items))}


def is_new(found: dict, since: datetime.datetime) -> bool:
    """A verified run, or a wait or question that moved. Runs without proof are not
    news: every read-only session finishes one."""
    moved = found["waiting"] + found["questions"]
    return bool(found["done"]) or any(decisions._when(entry["at"]) >= since for entry in moved)


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
        check = (f"check: {brief._clip(entry['check'], brief.DETAIL_CHARS)}  {_NO_CHECK_RESULT}" if entry["check"]
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
    lines += [""] + _section("QUESTIONS", found["questions"], limit, more, question_lines)
    return "\n".join(lines)


def for_brief(project_root: str | Path, session: str | None) -> str:
    """The report for a session start: empty before any prompt was seen here, when
    nothing is new since the last one, and for a session a runner started."""
    since = last_prompt(project_root)
    if since is None or resumed_session(project_root, session):
        return ""
    found = build(project_root, since=since, session=session)
    return render(found, limit=BRIEF_LIMIT) if is_new(found, since) else ""
