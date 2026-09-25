"""`[model_routing]` in `.eos/config.toml`, read once and validated.

Every key is optional. A project with no table gets `configured=False` and
the defaults, and that distinction is what keeps the feature invisible until
asked for: `eos route` answers either way, but the brief prints its ROUTE
line only for a project that wrote the table (ADR-025, backward compatible).

A malformed value is refused with its key named rather than ignored. A
routing policy that silently fell back to defaults because of a typo would
be choosing models on behalf of a configuration nobody wrote.
"""
from __future__ import annotations

import dataclasses
from pathlib import Path

from core.lib.config_io import ConfigIO
from core.routing.types import AUTO, EFFORTS, TASK_TYPES

TABLE = "model_routing"
BRIEF_MODES = ("with-brief", "always", "never")
_KEYS = ("enabled", "default_model", "default_effort", "brief", "hook", "models", "keywords")


@dataclasses.dataclass(frozen=True)
class RoutingConfig:
    configured: bool = False
    enabled: bool = True
    default_model: str = AUTO
    default_effort: str = AUTO
    brief: str = "with-brief"
    hook: bool = False
    models: dict = dataclasses.field(default_factory=dict)
    keywords: dict = dataclasses.field(default_factory=dict)


def config_path(project_root: str | Path) -> Path:
    return Path(project_root).expanduser().resolve() / ".eos" / "config.toml"


def load(project_root: str | Path | None) -> RoutingConfig:
    if project_root is None:
        return RoutingConfig()
    path = config_path(project_root)
    if not path.is_file():
        return RoutingConfig()
    table = ConfigIO.read_toml(path).get(TABLE)
    if table is None:
        return RoutingConfig()
    return parse(table)


def parse(table: object) -> RoutingConfig:
    if not isinstance(table, dict):
        raise ValueError(f"[{TABLE}] must be a table")
    for key in table:
        if key not in _KEYS:
            raise ValueError(f"[{TABLE}] has an unknown key {key!r}; known: {', '.join(_KEYS)}")

    enabled = table.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ValueError(f"[{TABLE}] enabled must be true or false")
    hook = table.get("hook", False)
    if not isinstance(hook, bool):
        raise ValueError(f"[{TABLE}] hook must be true or false")

    default_model = table.get("default_model", AUTO)
    if not isinstance(default_model, str) or not default_model.strip():
        raise ValueError(f"[{TABLE}] default_model must be a model id or \"auto\"")
    default_effort = table.get("default_effort", AUTO)
    if default_effort != AUTO and default_effort not in EFFORTS:
        raise ValueError(f"[{TABLE}] default_effort must be \"auto\" or one of "
                         f"{', '.join(EFFORTS)}; got {default_effort!r}")
    brief = table.get("brief", "with-brief")
    if brief not in BRIEF_MODES:
        raise ValueError(f"[{TABLE}] brief must be one of {', '.join(BRIEF_MODES)}; got {brief!r}")

    models = table.get("models", {})
    if not isinstance(models, dict) or not all(isinstance(v, dict) for v in models.values()):
        raise ValueError(f"[{TABLE}.models] must hold one table per model")

    keywords = table.get("keywords", {})
    if not isinstance(keywords, dict):
        raise ValueError(f"[{TABLE}.keywords] must be a table of type = [words]")
    cleaned: dict[str, tuple[str, ...]] = {}
    for task_type, words in keywords.items():
        if task_type not in TASK_TYPES:
            raise ValueError(f"[{TABLE}.keywords] has an unknown task type {task_type!r}; "
                             f"known: {', '.join(TASK_TYPES)}")
        if not isinstance(words, list) or not all(isinstance(w, str) and w.strip() for w in words):
            raise ValueError(f"[{TABLE}.keywords] {task_type} must be a list of words")
        cleaned[task_type] = tuple(w.strip().lower() for w in words)

    return RoutingConfig(configured=True, enabled=enabled, default_model=default_model.strip(),
                         default_effort=default_effort, brief=brief, hook=hook,
                         models=dict(models), keywords=cleaned)
