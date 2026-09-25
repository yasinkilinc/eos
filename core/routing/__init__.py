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

from core.routing import classify, config, policy, registry, score
from core.routing.policy import OverrideError
from core.routing.types import AUTO, Decision, task_hash

MODEL_ENV = "EOS_ROUTE_MODEL"
EFFORT_ENV = "EOS_ROUTE_EFFORT"

__all__ = ["route", "Decision", "OverrideError", "MODEL_ENV", "EFFORT_ENV"]


def route(project_root: str | Path | None, task: str, *, files=(), session: str | None = None,
          model: str | None = None, effort: str | None = None, record: bool = True,
          fresh: bool = False) -> Decision:
    """The decision for this task: config → classify → score → policy → history seam."""
    if not task or not str(task).strip():
        raise ValueError("a task to route is required")
    cfg = config.load(project_root)
    models = registry.load(project_root, cfg)

    task_class = classify.classify(task, cfg.keywords)
    complexity = score.score(task, task_class, files=tuple(files or ()), project_root=project_root)
    model_value, model_source = _resolved(model, MODEL_ENV, cfg.default_model)
    effort_value, effort_source = _resolved(effort, EFFORT_ENV, cfg.default_effort)
    effort_value = effort_value.lower()

    decision = policy.decide(task_class, complexity, models,
                             model=model_value, effort=effort_value,
                             model_source=model_source, effort_source=effort_source)
    decision = dataclasses.replace(decision, task_hash=task_hash(task))
    return policy.adjust_for_history(decision, project_root)


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
