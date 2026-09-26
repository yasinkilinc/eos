"""What the router decided, kept small enough to leave on (ADR-025).

One line per recorded decision in `.eos/data/routing.jsonl`: the task type,
level, score and factors, model, effort, reason, confidence, where an
override came from, and the effort the session was running at -- and a hash
of the task, never the task. The text is a query somebody typed, and a log
that kept it would be a liability in every project (ADR-019).

The same rules as telemetry keep it safe: it never fails the command that
writes it, it keeps only its tail, and it writes only into a project that
already has an `.eos/` directory.

A decision made inside an open run is also appended to that run as a
`decided` event (ADR-022). That is what joins a decision to its outcome:
`outcomes()` reads the two together, and `eos route --stats` prints them.
Nothing here concludes anything from that join (ADR-018).
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from core.routing.types import Decision

FILENAME = "routing.jsonl"
MAX_LINES = 5000


# Variables that hold the effort the session is actually running at, tried in
# order. Claude Code exports CLAUDE_EFFORT into every command it runs; a
# harness that does not can set EOS_EFFORT. Read so a later analysis can put
# what was recommended next to what was used -- the model in use is not
# exported, and is read from the harness's own transcript by session instead.
EFFORT_IN_USE_VARIABLES = ("EOS_EFFORT", "CLAUDE_EFFORT")


def path_for(project_root: str | Path) -> Path:
    return Path(project_root).expanduser().resolve() / ".eos" / "data" / FILENAME


def effort_in_use() -> str | None:
    import os

    for name in EFFORT_IN_USE_VARIABLES:
        value = (os.environ.get(name) or "").strip().lower()
        if value:
            return value[:16]
    return None


def record(project_root: str | Path | None, decision: Decision, *, session: str | None = None,
           work_item: str | None = None) -> bool:
    """Append one line. Returns whether it was written; never raises."""
    if project_root is None:
        return False
    try:
        root = Path(project_root).expanduser().resolve()
        if not (root / ".eos").is_dir():
            return False
        from core.knowledge.evidence import utc_now

        entry = {
            "at": utc_now(),
            "session": (session or "")[:64] or None,
            "execution": decision.execution,
            "work_item": work_item,
            "task_hash": decision.task_hash,
            "type": decision.task_type,
            "level": decision.level,
            "model": decision.model,
            "effort": decision.effort,
            "reason": decision.reason,
            "confidence": decision.confidence,
            "override_source": decision.override_source,
            "reused": decision.reused,
            # The numbers the level was cut from, so thresholds and weights
            # can be recalibrated from what actually happened. Numeric only.
            "score": decision.score,
            "factors": {name: value for name, value in decision.factors.items()
                        if isinstance(value, (int, float)) and not isinstance(value, bool)},
            "effort_in_use": effort_in_use(),
        }
        target = path_for(root)
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        _trim(target)
        return True
    except Exception:  # a statistic is never worth a failed command
        return False


def load(project_root: str | Path) -> list[dict]:
    target = path_for(project_root)
    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    found = []
    for line in text.splitlines():
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            found.append(value)
    return found


def seen(project_root: str | Path, session: str | None, task_hash: str, *, tail: int = 500) -> bool:
    """Whether this session already recorded a decision for this task (recent lines only)."""
    if not session:
        return False
    return any(line.get("session") == session[:64] and line.get("task_hash") == task_hash
               for line in load(project_root)[-tail:])


def outcomes(project_root: str | Path) -> list[tuple[str, str, str, str, str]]:
    """(type, level, model, effort, outcome) per recorded decision that has a run.

    `outcome` is the run's own word -- ok, failed, abandoned -- or `open`.
    Decisions made outside a run have nothing to join to and are left out.
    """
    from core import executions

    runs = {record.id: record for record in executions.load(project_root)}
    rows = []
    for line in load(project_root):
        run = runs.get(line.get("execution") or "")
        if run is None:
            continue
        effort = line.get("effort")
        rows.append((line.get("type") or "?", line.get("level") or "?", line.get("model") or "?",
                     effort if effort is not None else "?", run.outcome or "open"))
    return rows


def stats(project_root: str | Path) -> list[tuple[tuple[str, str, str, str], Counter]]:
    """Outcomes counted per (type, level, model, effort), most used first."""
    grouped: dict[tuple[str, str, str, str], Counter] = {}
    for kind, level, model, effort, outcome in outcomes(project_root):
        grouped.setdefault((kind, level, model, effort), Counter())[outcome] += 1
    return sorted(grouped.items(), key=lambda item: (-sum(item[1].values()), item[0]))


def advised_vs_used(project_root: str | Path) -> list[dict]:
    """Per session holding both a decision and a usage line: what was advised
    against what its messages ran at (claude plan 3.5).

    The session's first decision is its advice -- effort is chosen at session
    start. "Used" is read from the harness's transcript fold (`usage`): the
    share of the session's own messages on the advised model and at the
    advised effort, and the share of its subagents' messages on that model,
    which is where a model recommendation can actually be applied.
    """
    from core.routing import registry, usage

    models = registry.load(project_root)

    def family(model_id: str) -> str | None:
        spec = models.get(model_id) or models.get(model_id.split("[", 1)[0])
        return spec.id if spec is not None else None

    first: dict[str, dict] = {}
    for line in load(project_root):
        if line.get("session") and line["session"] not in first:
            first[line["session"]] = line
    rows = []
    for fold in usage.load(project_root):
        advice = first.get(fold.get("session") or "")
        if advice is None:
            continue
        main = fold.get("models") or {}
        subs = fold.get("subagent_models") or {}
        main_total = sum(v.get("messages", 0) for v in main.values())
        sub_total = sum(v.get("messages", 0) for v in subs.values())
        advised_effort = advice.get("effort") or "none"
        rows.append({
            "session": fold["session"], "advised_model": advice.get("model"),
            "advised_effort": advised_effort, "level": advice.get("level"),
            "main_messages": main_total,
            "main_on_model": sum(v.get("messages", 0) for k, v in main.items()
                                 if family(k) == advice.get("model")),
            "main_at_effort": sum((v.get("efforts") or {}).get(advised_effort, 0) for v in main.values()),
            "main_efforts_known": sum(sum((v.get("efforts") or {}).values()) for v in main.values()),
            "subagent_messages": sub_total,
            "subagent_on_model": sum(v.get("messages", 0) for k, v in subs.items()
                                     if family(k) == advice.get("model")),
        })
    return rows


def _trim(target: Path) -> None:
    try:
        lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return
    if len(lines) <= MAX_LINES:
        return
    target.write_text("\n".join(lines[-MAX_LINES:]) + "\n", encoding="utf-8")
