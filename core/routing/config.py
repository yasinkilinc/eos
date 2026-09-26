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
_KEYS = ("enabled", "default_model", "default_effort", "brief", "hook", "hook_dry_run",
         "hook_min_confidence", "record_prompts", "models", "keywords", "rules", "factors")
# A stem (`word*`) shorter than this matches too much to mean anything.
MIN_STEM = 3


@dataclasses.dataclass(frozen=True)
class RoutingConfig:
    configured: bool = False
    enabled: bool = True
    default_model: str = AUTO
    default_effort: str = AUTO
    brief: str = "with-brief"
    hook: bool = False
    # With `hook`, record what the subagent hook would set instead of setting
    # it -- the week of evidence the live switch waits for (claude plan 3.2).
    hook_dry_run: bool = True
    # A live hook applies a decision only at or above this confidence; below
    # it the decision is recorded and the call goes ahead untouched.
    hook_min_confidence: float = 0.0
    # Record the brief's decision for every task prompt, not only for runs.
    record_prompts: bool = False
    models: dict = dataclasses.field(default_factory=dict)
    keywords: dict = dataclasses.field(default_factory=dict)
    rules: dict = dataclasses.field(default_factory=dict)
    factors: dict = dataclasses.field(default_factory=dict)


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
    record_prompts = table.get("record_prompts", False)
    if not isinstance(record_prompts, bool):
        raise ValueError(f"[{TABLE}] record_prompts must be true or false")
    hook_dry_run = table.get("hook_dry_run", True)
    if not isinstance(hook_dry_run, bool):
        raise ValueError(f"[{TABLE}] hook_dry_run must be true or false")
    hook_min_confidence = table.get("hook_min_confidence", 0.0)
    if isinstance(hook_min_confidence, bool) or not isinstance(hook_min_confidence, (int, float)) \
            or not 0.0 <= hook_min_confidence <= 1.0:
        raise ValueError(f"[{TABLE}] hook_min_confidence must be a number from 0 to 1")

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

    cleaned = _word_table(table.get("keywords", {}), "keywords", TASK_TYPES, "task type")
    from core.routing.classify import RULE_LISTS
    from core.routing.score import FACTOR_LISTS

    rules = _word_table(table.get("rules", {}), "rules", RULE_LISTS, "rule list")
    factors = _word_table(table.get("factors", {}), "factors", tuple(FACTOR_LISTS), "factor")

    return RoutingConfig(configured=True, enabled=enabled, default_model=default_model.strip(),
                         default_effort=default_effort, brief=brief, hook=hook,
                         hook_dry_run=hook_dry_run, hook_min_confidence=float(hook_min_confidence),
                         record_prompts=record_prompts, models=dict(models), keywords=cleaned,
                         rules=rules, factors=factors)


def _word_table(value: object, name: str, allowed: tuple[str, ...], what: str) -> dict:
    """`[model_routing.<name>]`: each key one of `allowed`, each value a list of
    words or stems (`word*`), normalised the way task text is."""
    from core.routing.types import normalise

    if not isinstance(value, dict):
        raise ValueError(f"[{TABLE}.{name}] must be a table of {what} = [words]")
    cleaned: dict[str, tuple[str, ...]] = {}
    for key, words in value.items():
        if key not in allowed:
            raise ValueError(f"[{TABLE}.{name}] has an unknown {what} {key!r}; known: {', '.join(allowed)}")
        if not isinstance(words, list) or not all(isinstance(w, str) and w.strip() for w in words):
            raise ValueError(f"[{TABLE}.{name}] {key} must be a list of words")
        normal = tuple(normalise(w) for w in words)
        for word in normal:
            if word.endswith("*") and len(word.rstrip("*")) < MIN_STEM:
                raise ValueError(f"[{TABLE}.{name}] {key}: the stem {word!r} is shorter than "
                                 f"{MIN_STEM} characters and would match almost anything")
            if "*" in word[:-1]:
                raise ValueError(f"[{TABLE}.{name}] {key}: {word!r} -- `*` may only end a word")
        cleaned[key] = normal
    return cleaned
