"""Gates: ask the ledger whether an action is backed by evidence.

The questions live here; the host (a hook) decides what an action is and what a
refusal does. `check` reads decisions and events only, never the repository.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from core import decisions, executions

MODES = ("off", "warn", "block")
ACTIONS = ("edit-test", "edit-main", "edit-other", "confirm", "env-write", "commit", "push")
TOOL = "gate"

_LOOKED_AT = ("evidenced", "resolved")
_RED_TOOLS = ("mvn", "apitest")
_EXEMPT_TOOL = "product-upgrade"
_EXEMPT_PROCEDURE = "upgrade"
_FIX_WORDS = re.compile(r"\b(bug|fix|hata|duzelt\w*)\b")
_ROOT_CAUSE = ("root cause:", "kok neden:")
_VERIFY_KINDS = ("ran", "verified", "called")
_VERIFY_SOURCES = ("wrapper", "hook")

_FIX_RUN = 'eos run start . --title "…" --work <id>'
_FIX_LOOK = 'eos decide --claim "…" --evidence <e-id>'
_FIX_RED = ("automation/mvn.sh test <service> or automation/apitest.sh run env1 <spec> "
            "showing the failure")
_FIX_ROOT = 'eos decide --claim "kök neden: …" --evidence <e-id>'


@dataclass(frozen=True)
class Refusal:
    rule: str
    detail: str
    fix: str
    ids: tuple[str, ...]


def mode(project_root: str | Path) -> str:
    """`[gates] mode` of `.eos/config.toml`; anything unreadable or unknown is `off`."""
    from core.lib.config_io import ConfigIO

    try:
        table = ConfigIO.read_toml(Path(project_root) / ".eos" / "config.toml").get("gates")
    except (OSError, ValueError):
        return "off"
    value = table.get("mode") if isinstance(table, dict) else None
    return value if value in MODES else "off"


def _folded(text: str) -> str:
    return text.replace("İ", "i").replace("I", "ı").lower().translate(decisions._FOLD)


def _scope_events(records: list[executions.Record], run: executions.Record):
    return [e for r in records if decisions._in_scope(r, run.work_item, run.id) for e in r.events]


def _rules(action: str) -> tuple[str, ...]:
    if action == "edit-test":
        return ("nothing-looked-at",)
    if action == "edit-main":
        return ("open-assumption", "nothing-looked-at", "red-first")
    if action == "env-write":
        return ("open-assumption", "nothing-looked-at", "root-cause")
    if action in ("confirm", "commit", "push"):
        return ("open-assumption", "nothing-looked-at")
    return ()


def _red_exempt(run: executions.Record, tool: str | None) -> bool:
    return tool == _EXEMPT_TOOL or (run.procedure or "").startswith(_EXEMPT_PROCEDURE)


def check(project_root: str | Path, *, session: str | None, action: str, path: str | None = None,
          tool: str | None = None, records: list[executions.Record] | None = None,
          now: str | None = None) -> list[Refusal]:
    """The refusals for `action`, empty when the ledger backs it. `path` and `now` are
    the host's context; no rule reads them yet."""
    if action not in ACTIONS:
        raise ValueError(f"action must be one of {', '.join(ACTIONS)}; got {action!r}")
    if records is None:
        records = decisions.all_records(project_root, session)
    found = executions.current(project_root, session)
    run = next((r for r in records if found and r.id == found[0]), None)
    if run is None:
        return [Refusal("no-open-run", "no execution is open for this session", _FIX_RUN, ())]
    scope = decisions._fold(records, run.work_item, run.id)
    wanted = _rules(action)
    refusals: list[Refusal] = []
    pending = [d for d in scope if d.state == "assumption"]
    if "open-assumption" in wanted and pending:
        first = pending[0].id
        refusals.append(Refusal(
            "open-assumption",
            "; ".join(f"{d.id} unverified, check: {d.check or '-'}" for d in pending),
            f"eos decide --resolve {first} --evidence <e-id> or the user writes `{first} onay`",
            tuple(d.id for d in pending)))
    if "nothing-looked-at" in wanted and not any(d.state in _LOOKED_AT for d in scope):
        refusals.append(Refusal("nothing-looked-at",
                                "no decision in this scope rests on evidence", _FIX_LOOK, ()))
    if "red-first" in wanted and not _red_exempt(run, tool) and not any(
            e.kind == "ran" and e.tool in _RED_TOOLS and e.exit_code not in (None, 0)
            for e in _scope_events(records, run)):
        refusals.append(Refusal("red-first", "no failing mvn or apitest run before this edit",
                                _FIX_RED, ()))
    if "root-cause" in wanted and _FIX_WORDS.search(_folded(run.title)) and not any(
            d.state in _LOOKED_AT and _folded(d.claim).lstrip().startswith(_ROOT_CAUSE)
            for d in scope):
        refusals.append(Refusal("root-cause", "a fix run writes to an environment with no root "
                                "cause decision", _FIX_ROOT, ()))
    return refusals


def fresh_verification(records: list[executions.Record], run: executions.Record, *,
                       after: str | None) -> executions.Event | None:
    """The latest event of `run` a tool recorded as a passing check, at or after `after`
    (whole-second stamps: the same second counts). A subagent's report, a cli-written
    event and a non-zero exit never count."""
    best: executions.Event | None = None
    for record in records:
        if record.id != run.id:
            continue
        for entry in record.events:
            if (entry.kind not in _VERIFY_KINDS or entry.source not in _VERIFY_SOURCES
                    or entry.tool == "subagent" or entry.exit_code != 0):
                continue
            if after and decisions._when(entry.at) < decisions._when(after):
                continue
            if best is None or decisions._when(entry.at) >= decisions._when(best.at):
                best = entry
    return best


def _repo_relative(target: str) -> str:
    path = Path(target)
    if not path.is_absolute():
        return target
    try:
        top = subprocess.run(["git", "-C", str(path.parent), "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, check=True).stdout.strip()
        return path.resolve().relative_to(Path(top).resolve()).as_posix()
    except (OSError, ValueError, subprocess.CalledProcessError):
        return path.name


def record_refusals(project_root: str | Path, refusals: list[Refusal], *, session: str | None,
                    action: str, target: str | None) -> None:
    """One `noted` event, tool `gate`, per (rule, target) per run; a repeat writes nothing.
    The body is the rule, the action and the decision ids; the target is repo-relative."""
    found = executions.current(project_root, session)
    if found is None or not refusals:
        return
    execution, ledger = found
    relative = _repo_relative(target) if target else None
    seen = {(e.body or "").split(":")[0] for r in executions.load_path(ledger) if r.id == execution
            for e in r.events if e.kind == "noted" and e.tool == TOOL and e.ref == relative}
    for refusal in refusals:
        if refusal.rule in seen:
            continue
        seen.add(refusal.rule)
        body = f"{refusal.rule}: {action}" + (f" {' '.join(refusal.ids)}" if refusal.ids else "")
        executions.event(project_root, execution, ledger=ledger, kind="noted", tool=TOOL,
                         ref=relative, body=body, source="hook", session=session)
