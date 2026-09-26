"""Which model, and how much effort, a task deserves (ADR-025).

EOS advises and the harness executes: nothing in this package calls a
provider. It classifies a task, scores its complexity, and picks the cheapest
registered model that meets the level's requirement, at an effort that model
accepts. Every surface -- `eos route`, the brief's ROUTE line, the MCP tool,
the optional subagent hook -- calls `route()` and renders what it returns.
"""
from __future__ import annotations

import dataclasses
import os
from pathlib import Path

from core.routing import classify, config, policy, registry, score, trace
from core.routing.policy import OverrideError
from core.routing.types import AUTO, EFFORTS, LEVELS, TASK_TYPES, Decision, task_hash

MODEL_ENV = "EOS_ROUTE_MODEL"
EFFORT_ENV = "EOS_ROUTE_EFFORT"

__all__ = ["route", "Decision", "OverrideError", "MODEL_ENV", "EFFORT_ENV"]


def route(project_root: str | Path | None, task: str, *, files=(), session: str | None = None,
          model: str | None = None, effort: str | None = None, record: bool = True,
          fresh: bool = False) -> Decision:
    """The decision for this task: config → reuse → classify → score → policy → history → record.

    Inside an open run (ADR-022) the first decision is the run's decision: a
    later call returns it again (`reused=True`) instead of classifying anew,
    unless `fresh` is set or the caller names a model or effort explicitly.
    `record=False` decides without writing anything -- for the brief, which
    runs on every prompt and would otherwise log noise rather than memory.
    """
    if not task or not str(task).strip():
        raise ValueError("a task to route is required")
    cfg = config.load(project_root)
    models = registry.load(project_root, cfg)

    run = _open_run(project_root, session)
    if run is not None and not fresh and model is None and effort is None:
        previous = _decided(run, models)
        if previous is not None:
            return previous

    task_class = classify.classify(task, cfg.keywords)
    complexity = score.score(task, task_class, files=tuple(files or ()), project_root=project_root)
    model_value, model_source = _resolved(model, MODEL_ENV, cfg.default_model)
    effort_value, effort_source = _resolved(effort, EFFORT_ENV, cfg.default_effort)
    effort_value = effort_value.lower()

    decision = policy.decide(task_class, complexity, models,
                             model=model_value, effort=effort_value,
                             model_source=model_source, effort_source=effort_source)
    decision = dataclasses.replace(decision, task_hash=task_hash(task),
                                   execution=run.id if run is not None else None)
    decision = policy.adjust_for_history(decision, project_root)

    if record:
        if run is not None:
            _append_decided(project_root, decision, session)
        trace.record(project_root, decision, session=session,
                     work_item=run.work_item if run is not None else None)
    return decision


def _open_run(project_root, session):
    """This session's open execution in this project, or None. Never raises.

    The session pointer names a run wherever it was opened; in a workspace
    that can be another project's ledger, and that run's decision was made
    for that project's task. Found by an evaluation that routed a corpus in a
    scratch project and got the workspace run's decision back for every line.
    """
    if project_root is None:
        return None
    try:
        from core import executions

        found = executions.current(project_root, session)
        if found is None:
            return None
        execution, ledger = found
        if Path(ledger).resolve() != executions.path_for(project_root).resolve():
            return None
        record = next((r for r in executions.load_path(ledger) if r.id == execution), None)
        return record if record is not None and record.open else None
    except Exception:
        return None


def _body(decision: Decision) -> str:
    # Seven tokens and no free text: the ledger is committed and pushed. A
    # model that takes no effort setting is written "-", keeping the count.
    return (f"{decision.task_type} {decision.level} {decision.model} {decision.effort or '-'} "
            f"{decision.override_source} {decision.score:.4f} {decision.confidence:.2f}")


def _append_decided(project_root, decision: Decision, session) -> None:
    try:
        from core import executions

        executions.event(project_root, kind="decided", tool="route",
                         ref=f"route:{decision.task_hash}", body=_body(decision), session=session)
    except Exception:  # the decision stands whether or not the ledger took it
        return


def _decided(run, models) -> Decision | None:
    """The latest decision recorded on this run, rebuilt, if it is still valid."""
    for event in reversed(run.events):
        if event.kind != "decided" or event.tool != "route" or not event.body:
            continue
        parts = event.body.split()
        if len(parts) != 7:
            continue
        kind, level, model_id, effort, source, score_text, confidence_text = parts
        effort = "" if effort == "-" else effort
        spec = models.get(model_id)
        if spec is None or not spec.available or kind not in TASK_TYPES or level not in LEVELS:
            return None
        if (effort not in EFFORTS or effort not in spec.efforts) and (effort or spec.efforts):
            return None
        try:
            score_value, confidence = float(score_text), float(confidence_text)
        except ValueError:
            return None
        ref = event.ref or ""
        return Decision(
            task_type=kind, level=level, score=score_value, model=spec.id, effort=effort,
            reason=f"Reused the decision already made in run {run.id} ({source}); "
                   f"--fresh decides again.",
            confidence=confidence, override_source="run",
            task_hash=ref.removeprefix("route:"), execution=run.id, reused=True)
    return None


def _resolved(flag: str | None, env_name: str, configured: str) -> tuple[str, str]:
    """Flag, then environment, then config, then the policy. An explicit
    `auto` at any level is a choice too: it stops the search there."""
    for value, source in ((flag, "flag"), (os.environ.get(env_name), "env"), (configured, "config")):
        if value is None or not str(value).strip():
            continue
        value = str(value).strip()
        if value.lower() == AUTO:
            return AUTO, "auto"
        return value, source
    return AUTO, "auto"
