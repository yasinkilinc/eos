"""What the router decided, kept small enough to leave on (ADR-025).

One line per recorded decision in `.eos/data/routing.jsonl`: the task type,
level, model, effort, reason, confidence and where an override came from --
and a hash of the task, never the task. The text is a query somebody typed,
and a log that kept it would be a liability in every project (ADR-019).

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


def path_for(project_root: str | Path) -> Path:
    return Path(project_root).expanduser().resolve() / ".eos" / "data" / FILENAME


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
        rows.append((line.get("type") or "?", line.get("level") or "?", line.get("model") or "?",
                     line.get("effort") or "?", run.outcome or "open"))
    return rows


def stats(project_root: str | Path) -> list[tuple[tuple[str, str, str, str], Counter]]:
    """Outcomes counted per (type, level, model, effort), most used first."""
    grouped: dict[tuple[str, str, str, str], Counter] = {}
    for kind, level, model, effort, outcome in outcomes(project_root):
        grouped.setdefault((kind, level, model, effort), Counter())[outcome] += 1
    return sorted(grouped.items(), key=lambda item: (-sum(item[1].values()), item[0]))


def _trim(target: Path) -> None:
    try:
        lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return
    if len(lines) <= MAX_LINES:
        return
    target.write_text("\n".join(lines[-MAX_LINES:]) + "\n", encoding="utf-8")
