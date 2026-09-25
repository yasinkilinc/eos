"""Which models the router may choose, and what each can do.

The policy never names a model. It states a requirement -- a minimum
reasoning and coding capability, optionally an effort -- and asks this
registry for every available model that meets it, cheapest first. Adding a
model, retiring one, or correcting a capability is therefore a config edit,
never a code change (ADR-025).

Config merges into the defaults by id: an entry for an existing id changes
only the fields it names, an entry for a new id adds a model and must say
what it can do. Everything is validated on load, because a registry that
accepted `efforts = ["hgih"]` would let the router hand a provider a value
it refuses -- the one outcome the clamp exists to make impossible.
"""
from __future__ import annotations

import dataclasses
from pathlib import Path

from core.routing import config as _config
from core.routing.defaults import DEFAULT_MODELS
from core.routing.types import EFFORTS, STATUSES, TASK_TYPES, ModelSpec

_FIELDS = tuple(f.name for f in dataclasses.fields(ModelSpec) if f.name != "id")
# What a new model has to state; the rest have defaults.
_REQUIRED_NEW = ("reasoning", "coding", "cost", "efforts")


class Registry:
    def __init__(self, specs: tuple[ModelSpec, ...] | list[ModelSpec]):
        self._specs = tuple(_validated(spec) for spec in specs)
        seen: dict[str, str] = {}
        for spec in self._specs:
            for name in (spec.id,) + spec.aliases:
                key = name.lower()
                if key in seen:
                    raise ValueError(f"model name {name!r} is used by both {seen[key]!r} and {spec.id!r}")
                seen[key] = spec.id
        self._names = seen

    def __iter__(self):
        return iter(self._specs)

    def get(self, name: str | None) -> ModelSpec | None:
        """By id or alias, case-insensitive."""
        if not name:
            return None
        found = self._names.get(str(name).strip().lower())
        if found is None:
            return None
        return next(spec for spec in self._specs if spec.id == found)

    def available(self) -> tuple[ModelSpec, ...]:
        return tuple(sorted((s for s in self._specs if s.available), key=_cheapest_first))

    def ids(self) -> tuple[str, ...]:
        return tuple(spec.id for spec in self.available())

    def candidates(self, *, min_reasoning: int, min_coding: int,
                   effort: str | None = None) -> tuple[ModelSpec, ...]:
        """Available models meeting both minima (and supporting `effort`), cheapest first."""
        return tuple(spec for spec in self.available()
                     if spec.reasoning >= min_reasoning and spec.coding >= min_coding
                     and (effort is None or effort in spec.efforts))

    def strongest(self) -> ModelSpec | None:
        ranked = sorted(self.available(), key=lambda s: (-s.reasoning, -s.coding, s.cost, s.id))
        return ranked[0] if ranked else None


def load(project_root: str | Path | None = None,
         routing_config: _config.RoutingConfig | None = None) -> Registry:
    """The defaults, with the project's `[model_routing.models]` merged in by id."""
    cfg = routing_config if routing_config is not None else _config.load(project_root)
    return from_entries(DEFAULT_MODELS, cfg.models)


def from_entries(base: tuple[ModelSpec, ...], entries: dict) -> Registry:
    specs = {spec.id: spec for spec in base}
    order = [spec.id for spec in base]
    for model_id, entry in entries.items():
        where = f"[model_routing.models.{model_id}]"
        unknown = [key for key in entry if key not in _FIELDS]
        if unknown:
            raise ValueError(f"{where} has an unknown key {unknown[0]!r}; known: {', '.join(_FIELDS)}")
        values = {key: _coerce(where, key, value) for key, value in entry.items()}
        if model_id in specs:
            specs[model_id] = dataclasses.replace(specs[model_id], **values)
            continue
        missing = [key for key in _REQUIRED_NEW if key not in values]
        if missing:
            raise ValueError(f"{where} is a new model and must state {', '.join(missing)}")
        values.setdefault("provider", "unknown")
        values.setdefault("context_window", 0)
        specs[model_id] = ModelSpec(id=model_id, **values)
        order.append(model_id)
    return Registry([specs[model_id] for model_id in order])


def _coerce(where: str, key: str, value):
    if key in ("efforts", "task_types", "aliases"):
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise ValueError(f"{where} {key} must be a list of strings")
        return tuple(value)
    return value


def _validated(spec: ModelSpec) -> ModelSpec:
    where = f"model {spec.id!r}"
    if not isinstance(spec.id, str) or not spec.id.strip():
        raise ValueError("a model needs a non-empty id")
    for key in ("reasoning", "coding"):
        value = getattr(spec, key)
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 5:
            raise ValueError(f"{where}: {key} must be an integer from 1 to 5; got {value!r}")
    if isinstance(spec.cost, bool) or not isinstance(spec.cost, (int, float)) or spec.cost <= 0:
        raise ValueError(f"{where}: cost must be a positive number; got {spec.cost!r}")
    if isinstance(spec.context_window, bool) or not isinstance(spec.context_window, int) \
            or spec.context_window < 0:
        raise ValueError(f"{where}: context_window must be a non-negative integer")
    if not isinstance(spec.provider, str):
        raise ValueError(f"{where}: provider must be a string")
    if spec.status not in STATUSES:
        raise ValueError(f"{where}: status must be one of {', '.join(STATUSES)}; got {spec.status!r}")
    if not spec.efforts:
        raise ValueError(f"{where}: efforts must name at least one level")
    for effort in spec.efforts:
        if effort not in EFFORTS:
            raise ValueError(f"{where}: unknown effort {effort!r}; known: {', '.join(EFFORTS)}")
    for task_type in spec.task_types:
        if task_type not in TASK_TYPES:
            raise ValueError(f"{where}: unknown task type {task_type!r}")
    # Ascending and without repeats, whatever order config wrote them in: the
    # clamp walks this tuple and relies on it.
    efforts = tuple(effort for effort in EFFORTS if effort in spec.efforts)
    return dataclasses.replace(spec, efforts=efforts, cost=float(spec.cost),
                               aliases=tuple(a.strip() for a in spec.aliases if a.strip()))


def _cheapest_first(spec: ModelSpec):
    return (spec.cost, -spec.reasoning, spec.id)
