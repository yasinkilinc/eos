"""From a classified, scored task to one model and one effort (ADR-025).

Three rules carry the policy, and each has exactly one place in this file:

**The cheapest sufficient model wins** (`_choose_model`). A level states a
minimum reasoning and coding capability; the registry answers with every
available model that meets it, cheapest first, and the first is taken. Only
when nothing meets it is the strongest model used -- and the reason says so.

**Effort is clamped, never invented** (`clamp`). It is the only function that
returns an effort. A requested value the model accepts is kept; otherwise the
highest accepted value below it; otherwise the lowest the model has. No path
can pair a model with a value its provider would refuse.

**An explicit choice is respected** (`decide`). A model or effort given by
flag, environment or config replaces the policy for that half. An explicit
model that is unknown or unavailable is refused with the available ids named,
never silently swapped for another: a substitution the caller did not see is
worse than an error it did.
"""
from __future__ import annotations

import dataclasses
from pathlib import Path

from core.routing import score as _score
from core.routing.registry import Registry
from core.routing.types import (AUTO, EFFORTS, Complexity, Decision, ModelSpec, TaskClass,
                                effort_rank)

# level -> (min_reasoning, min_coding, effort band, preferred first)
REQUIREMENTS = {
    "LOW": (1, 2, ("low", "medium")),
    "MEDIUM": (3, 3, ("medium", "high")),
    "HIGH": (4, 4, ("high", "xhigh")),
    "CRITICAL": (5, 4, ("xhigh", "max")),
}
CODING_HEAVY = ("code_review", "test_generation")
REASONING_HEAVY = ("investigation", "planning", "complex_reasoning")
# Half the width of a middle band: a score this far from every cut is as
# settled as a level gets.
SETTLED_DISTANCE = 0.10
_SOURCE_ORDER = ("flag", "env", "config")


class OverrideError(ValueError):
    """An explicit model or effort that cannot be honoured."""


def clamp(spec: ModelSpec, requested: str) -> str:
    if requested in spec.efforts:
        return requested
    lower = [e for e in spec.efforts if effort_rank(e) < effort_rank(requested)]
    return lower[-1] if lower else spec.efforts[0]


def auto_effort(spec: ModelSpec, band: tuple[str, ...]) -> str:
    for effort in band:
        if effort in spec.efforts:
            return effort
    return clamp(spec, band[0])


def requirement(level: str, task_type: str) -> tuple[int, int, tuple[str, ...]]:
    min_reasoning, min_coding, band = REQUIREMENTS[level]
    if task_type in CODING_HEAVY:
        min_coding = min(min_coding + 1, 5)
    if task_type in REASONING_HEAVY:
        min_reasoning = min(min_reasoning + 1, 5)
    return min_reasoning, min_coding, band


def decide(task_class: TaskClass, complexity: Complexity, registry: Registry, *,
           model: str = AUTO, effort: str = AUTO,
           model_source: str = "auto", effort_source: str = "auto") -> Decision:
    level = complexity.level
    base_reasoning, base_coding, band = REQUIREMENTS[level]
    min_reasoning, min_coding, _ = requirement(level, task_class.type)
    notes: list[str] = []

    if effort != AUTO and effort not in EFFORTS:
        raise OverrideError(f"unknown effort {effort!r}; known: {', '.join(EFFORTS)}")

    if model != AUTO:
        spec = registry.get(model)
        if spec is None:
            raise OverrideError(f"unknown model {model!r}; available: {', '.join(registry.ids())}")
        if not spec.available:
            raise OverrideError(f"model {spec.id!r} is unavailable; available: {', '.join(registry.ids())}")
        alternatives: tuple[str, ...] = ()
        notes.append(f"model {spec.id} as requested")
    else:
        spec, alternatives = _choose_model(registry, level, (min_reasoning, min_coding),
                                           (base_reasoning, base_coding), effort, notes)

    if effort == AUTO:
        chosen_effort = auto_effort(spec, band)
        if chosen_effort not in band:
            notes.append(f"{spec.id} accepts none of {'/'.join(band)}; effort clamped to {chosen_effort}")
        else:
            notes.append(f"effort {chosen_effort}")
    else:
        chosen_effort = clamp(spec, effort)
        if chosen_effort != effort:
            notes.append(f"{spec.id} does not accept effort {effort}; clamped to {chosen_effort}")
        else:
            notes.append(f"effort {chosen_effort} as requested")

    fixed = [source for value, source in ((model, model_source), (effort, effort_source)) if value != AUTO]
    override_source = next((s for s in _SOURCE_ORDER if s in fixed), "auto")

    return Decision(
        task_type=task_class.type,
        level=level,
        score=complexity.score,
        model=spec.id,
        effort=chosen_effort,
        reason=_reason(task_class, complexity, notes),
        confidence=_confidence(task_class, complexity),
        override_source=override_source,
        factors=dict(complexity.factors),
        alternatives=alternatives,
    )


def adjust_for_history(decision: Decision, project_root: str | Path | None) -> Decision:
    """The one seam where past outcomes could someday change a decision. Today: none.

    ADR-018: the engine records runs and draws no conclusion from them on its
    own. `trace.outcomes()` already joins each recorded decision to its run's
    outcome, so a later version -- once a person decides it should -- can read
    how a (type, level, model) combination has fared here and adjust. Until
    then the decision passes through unchanged, and every caller already goes
    through this function, so adding that later changes no call site.
    """
    return decision


def _choose_model(registry: Registry, level: str, adjusted: tuple[int, int], base: tuple[int, int],
                  effort: str, notes: list[str]) -> tuple[ModelSpec, tuple[str, ...]]:
    wanted = effort if effort != AUTO else None
    for (min_reasoning, min_coding), label in ((adjusted, ""), (base, " (type adjustment relaxed)")):
        found = registry.candidates(min_reasoning=min_reasoning, min_coding=min_coding, effort=wanted)
        if found:
            notes.append(f"cheapest model meeting {level}{label}")
            return found[0], tuple(spec.id for spec in found[1:])
    if wanted is not None:
        # Nothing that meets the level accepts that effort: keep the level,
        # let the clamp bring the effort down, and say so.
        found = registry.candidates(min_reasoning=adjusted[0], min_coding=adjusted[1]) \
            or registry.candidates(min_reasoning=base[0], min_coding=base[1])
        if found:
            notes.append(f"no model meeting {level} accepts effort {effort}")
            return found[0], tuple(spec.id for spec in found[1:])
    strongest = registry.strongest()
    if strongest is None:
        raise OverrideError("no available model in the registry")
    notes.append(f"no registered model meets {level}; using the strongest available")
    return strongest, ()


def _confidence(task_class: TaskClass, complexity: Complexity) -> float:
    settled = min(1.0, _score.distance_to_threshold(complexity.score) / SETTLED_DISTANCE)
    return round(0.5 * task_class.confidence + 0.5 * settled, 2)


def _reason(task_class: TaskClass, complexity: Complexity, notes: list[str]) -> str:
    weights = dict(_score.WEIGHTS)
    contributions = sorted(
        ((name, complexity.factors.get(name, 0.0)) for name, _ in _score.WEIGHTS),
        key=lambda item: (-item[1] * weights[item[0]], [n for n, _ in _score.WEIGHTS].index(item[0])))
    strongest = [f"{name.replace('_', ' ')} {value:.2f}" for name, value in contributions[:2] if value > 0]
    what = task_class.type.replace("_", " ")
    detail = ", ".join(strongest)
    if complexity.factors.get("file_count_source") in ("files", "text"):
        files = round(complexity.factors["file_count"] * _score.FILES_FOR_FULL)
        detail += f", {files}{'+' if complexity.factors['file_count'] >= 1 else ''} file(s)"
    head = f"{what[0].upper()}{what[1:]}, {complexity.level}" + (f" ({detail})" if detail else "")
    return f"{head}; " + "; ".join(notes) + "."
