"""Decisions and assumptions: a claim is worth acting on only with evidence.

A decision is one `decided` event (tool "decide", ref = the decision id) in the
execution ledger; a state change is another event with the same ref, and the
latest one wins. Evidence is a list of ledger event ids. An id counts only when
a tool recorded the event, it did not fail, it is not a subagent's own report,
it is not stale, and it belongs to this work item. Anything else leaves the
decision an "assumption", with every refusal code that applied.
"""
from __future__ import annotations

import datetime
import re
import secrets
from dataclasses import dataclass
from pathlib import Path

from core import executions

STATES = ("evidenced", "assumption", "resolved", "approved")
LIVE_TOOLS = frozenset({"db", "obs", "admintoolbox", "argo", "bssapi", "apitest", "scenario",
                        "env-ready", "grafana", "kafka", "ntf", "eca", "usermgmt"})
LIVE_MAX_AGE = datetime.timedelta(hours=24)

TOOL = "decide"
_ACCEPTED_KINDS = ("ran", "called", "read", "verified")
_ACCEPTED_SOURCES = ("wrapper", "hook")
_HEDGES = re.compile(r"\b(muhtemel\w*|olmali\w*|saniyor\w*|sanirim|galiba|herhalde|probably|"
                     r"likely|should|seem|seems|seemed|assume|assumed|assumes|assuming|"
                     r"presumably)\b")
_EVIDENCE_ID = re.compile(r"^(e-[0-9a-f]{8}|x-[a-z0-9]+(?:-[a-z0-9]+)*-[0-9a-f]{4}#[0-9]+)$")
_FOLD = str.maketrans("ışğüöç", "isguoc")


@dataclass(frozen=True)
class Decision:
    id: str
    execution: str
    work_item: str | None
    at: str
    claim: str
    evidence: tuple[str, ...]
    state: str
    check: str | None
    reasons: tuple[str, ...]
    agent: str | None


def _hedged(claim: str) -> bool:
    folded = claim.replace("İ", "i").replace("I", "ı").lower().translate(_FOLD)
    return _HEDGES.search(folded) is not None


def _parse(stamp: str) -> datetime.datetime:
    parsed = datetime.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=datetime.timezone.utc)


def _refusal(entry: executions.Event, owner: executions.Record, run: executions.Record,
             now: datetime.datetime) -> str | None:
    if run.work_item:
        if owner.work_item != run.work_item:
            return "other-work-item"
    elif owner.id != run.id:
        return "other-work-item"
    if entry.kind == "verified" and entry.tool == "subagent":
        return "subagent-report"
    if entry.kind not in _ACCEPTED_KINDS or entry.source not in _ACCEPTED_SOURCES:
        return "not-recorded-by-a-tool"
    if entry.kind == "called" and entry.exit_code:
        return "failed-call"
    if entry.tool in LIVE_TOOLS and now - _parse(entry.at) > LIVE_MAX_AGE:
        return "stale"
    return None


def _well_formed(evidence: list[str]) -> None:
    for cited in evidence:
        executions._checked(cited, "evidence")
        if not _EVIDENCE_ID.match(cited):
            raise ValueError(f"evidence must be event ids (e-xxxxxxxx or <execution>#<n>); "
                             f"got {cited!r}")


def _ledgers(project_root: str | Path, session: str | None = None) -> list[Path]:
    """Every ledger this project knows, each with its rotated parts read by
    `load_path`: the session's run ledger, the project's own, and the ledgers of
    the projects of the workspace the project is, or stands in
    (`workspace.ledger_holding` resolves a workspace run the same way)."""
    from core import workspace

    found = executions.current(project_root, session)
    paths = [found[1]] if found else []
    paths.append(executions.path_for(project_root))
    root = Path(project_root).expanduser().resolve()
    for candidate in (root, workspace.of(root)):
        if candidate is None:
            continue
        paths.extend(executions.path_for(p.root) for p in workspace.load_quiet(candidate))
    unique: dict[Path, Path] = {}
    for path in paths:
        unique.setdefault(path.expanduser().resolve(), path)
    return list(unique.values())


def _all_records(project_root: str | Path, session: str | None = None
                 ) -> list[executions.Record]:
    return [r for ledger in _ledgers(project_root, session) for r in executions.load_path(ledger)]


def _reasons(records: list[executions.Record], run: executions.Record, evidence: list[str],
             now: str | None) -> list[str]:
    clock = _parse(now) if now else datetime.datetime.now(datetime.timezone.utc)
    index = {e.id: (r, e) for r in records for e in r.events}
    found: list[str] = []
    for cited in evidence:
        hit = index.get(cited)
        reason = "unknown-id" if hit is None else _refusal(hit[1], hit[0], run, clock)
        if reason and reason not in found:
            found.append(reason)
    return found


def _open_run(project_root: str | Path, session: str | None
              ) -> tuple[executions.Record, Path, list[executions.Record]]:
    found = executions.current(project_root, session)
    if found is None:
        raise ValueError("no execution is open for this session; `eos run start` opens one")
    execution, ledger = found
    records = _all_records(project_root, session)
    run = next((r for r in records if r.id == execution), None)
    if run is None:
        raise ValueError(f"execution {execution} is not in {ledger}")
    return run, ledger, records


def _in_scope(record: executions.Record, work_item: str | None, execution: str | None) -> bool:
    if work_item:
        return record.work_item == work_item
    return execution is not None and record.id == execution


def _fold(records: list[executions.Record], work_item: str | None, execution: str | None
          ) -> list[Decision]:
    latest: dict[str, Decision] = {}
    seen: set[str] = set()
    for record in records:
        if not _in_scope(record, work_item, execution):
            continue
        for entry in record.events:
            if entry.kind != "decided" or entry.tool != TOOL or not entry.ref:
                continue
            if entry.id in seen:
                continue
            seen.add(entry.id)
            if entry.claim_state == "approved" and entry.source != "hook":
                continue
            latest[entry.ref] = Decision(
                id=entry.ref, execution=record.id, work_item=record.work_item, at=entry.at,
                claim=entry.body or "", evidence=tuple(entry.evidence or ()),
                state=entry.claim_state or "assumption", check=entry.check,
                reasons=tuple(entry.reasons or ()),
                agent=entry.agent)
    return list(latest.values())


def _write(project_root: str | Path, run: executions.Record, ledger: Path, decision_id: str,
           claim: str, evidence: list[str], state: str, check: str | None, *,
           session: str | None, agent: str | None, source: str, reasons: list[str]
           ) -> Decision:
    entry = executions.event(project_root, run.id, ledger=ledger, kind="decided", tool=TOOL,
                             ref=decision_id, body=claim, evidence=evidence, claim_state=state,
                             check=check, reasons=reasons, session=session, agent=agent,
                             source=source)
    return Decision(id=decision_id, execution=run.id, work_item=run.work_item, at=entry.at,
                    claim=claim, evidence=tuple(evidence), state=state, check=check,
                    reasons=tuple(reasons), agent=agent)


def record(project_root: str | Path, claim: str, evidence: list[str], *,
           check: str | None = None, session: str | None = None, agent: str | None = None,
           now: str | None = None) -> Decision:
    run, ledger, records = _open_run(project_root, session)
    executions._checked(claim, "claim")
    _well_formed(evidence)
    reasons = _reasons(records, run, evidence, now)
    if _hedged(claim):
        reasons.append("hedged")
    if not evidence:
        reasons.append("no-evidence")
    return _write(project_root, run, ledger, f"d-{secrets.token_hex(4)}", claim, list(evidence),
                  "assumption" if reasons else "evidenced", check, session=session, agent=agent,
                  source="cli", reasons=reasons)


def _existing(records: list[executions.Record], run: executions.Record, decision_id: str
              ) -> Decision:
    found = [d for d in _fold(records, run.work_item, run.id) if d.id == decision_id]
    if not found:
        raise ValueError(f"no decision {decision_id} in this work item")
    if found[0].state != "assumption":
        raise ValueError(f"decision {decision_id} is {found[0].state}, not an open assumption")
    return found[0]


def resolve(project_root: str | Path, decision_id: str, evidence: list[str], *,
            session: str | None = None, now: str | None = None) -> Decision:
    """Close an assumption with evidence. Evidence that is refused writes nothing:
    the decision stays as it was and the refusal codes come back on the result."""
    run, ledger, records = _open_run(project_root, session)
    before = _existing(records, run, decision_id)
    _well_formed(evidence)
    reasons = _reasons(records, run, evidence, now)
    if not evidence:
        reasons.append("no-evidence")
    if reasons:
        return Decision(**{**before.__dict__, "reasons": tuple(reasons)})
    return _write(project_root, run, ledger, decision_id, before.claim, list(evidence), "resolved",
                  before.check, session=session, agent=before.agent, source="cli", reasons=[])


def approve(project_root: str | Path, decision_id: str, *, prompt_excerpt: str,
            session: str | None = None) -> Decision:
    """The user said yes. Only the prompt hook calls this; the excerpt is kept as
    the decision's check."""
    run, ledger, records = _open_run(project_root, session)
    before = _existing(records, run, decision_id)
    return _write(project_root, run, ledger, decision_id, before.claim, list(before.evidence),
                  "approved", prompt_excerpt, session=session, agent=before.agent, source="hook",
                  reasons=[])


def for_scope(project_root: str | Path, *, work_item: str | None, execution: str | None,
              session: str | None = None) -> list[Decision]:
    return _fold(_all_records(project_root, session), work_item, execution)


def open_assumptions(project_root: str | Path, *, work_item: str | None, execution: str | None,
                     session: str | None = None) -> list[Decision]:
    return [d for d in for_scope(project_root, work_item=work_item, execution=execution,
                                 session=session)
            if d.state == "assumption"]
