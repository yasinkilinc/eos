"""How much a task asks of a model, as a number anyone can take apart (ADR-025).

Seven named factors, each between 0 and 1, weighted into one score and cut
into four levels. Every factor is returned with the result, so "why HIGH?"
is answered by reading them, not by trusting the router.

Two rules sit on top of the thresholds, both about what a sum cannot see:

- Some types are never cheap. An architecture question, a repository-wide
  change or a piece of complex reasoning is at least HIGH, however short the
  sentence that asks for it.
- A trivial edit stays LOW unless it touches something that hurts when it
  breaks (`failure_risk` of 0.5 or more).

What is deliberately absent is an escalation gate on uncertainty. A router
elsewhere promoted a task one tier whenever its own confidence was low, and
its confidence was structurally low for every short task -- so nearly every
trivial request went to a stronger model (recorded there as issue #2250).
Here uncertainty is a factor with the smallest weight and nothing more.

The one factor that reads the project is `dependency_count`: given the files
a task touches, the index says how many others depend on them. Without an
index, or without files, it is 0 and says so under `dependency_count_source`
-- a guess would be a number nobody could check.
"""
from __future__ import annotations

import re
from pathlib import Path

from core.routing import classify
from core.routing.types import LEVELS, Complexity, TaskClass, level_rank, normalise

WEIGHTS = (
    ("scope", 0.15),
    ("file_count", 0.15),
    ("dependency_count", 0.10),
    ("architectural_impact", 0.20),
    ("reasoning_required", 0.20),
    ("failure_risk", 0.15),
    ("uncertainty", 0.05),
)
# Upper bounds of LOW, MEDIUM and HIGH; anything at or above the last is CRITICAL.
# Calibrated against the sentences in tests/test_routing_score.py: most tasks
# arrive without file paths, so `file_count` and `dependency_count` sit near
# zero and a typical score is small. Cuts at .30/.55/.80 left every ordinary
# implementation at LOW and CRITICAL unreachable without file data.
THRESHOLDS = (0.15, 0.35, 0.55)

SCOPE = {
    "trivial_edit": 0.10, "simple_implementation": 0.25, "normal_implementation": 0.45,
    "test_generation": 0.35, "code_review": 0.35, "debugging": 0.50, "refactoring": 0.60,
    "investigation": 0.50, "planning": 0.50, "architecture": 0.85,
    "complex_reasoning": 0.85, "repository_wide_change": 1.00,
}
REASONING = {
    "complex_reasoning": 1.0, "architecture": 0.8, "debugging": 0.7, "investigation": 0.6,
    "planning": 0.6, "refactoring": 0.5, "normal_implementation": 0.4, "code_review": 0.4,
    "simple_implementation": 0.2, "trivial_edit": 0.1,
}
REASONING_OTHERWISE = 0.3
ARCHITECTURAL = {"architecture": 1.0, "repository_wide_change": 1.0, "refactoring": 0.6}
ARCHITECTURAL_WORDS = ("schema", "migration", "public api", "contract", "interface",
                       "boundary", "boundaries")
RISK_WORDS = ("production", "prod", "payment", "billing", "auth", "authentication",
              "authorization", "security", "data loss", "migration", "irreversible")
HEDGES = ("not sure", "somehow", "maybe", "unknown")
# What `[model_routing.factors]` may extend, each onto the list named.
FACTOR_LISTS = {"architectural": "ARCHITECTURAL_WORDS", "risk": "RISK_WORDS", "hedges": "HEDGES"}
AT_LEAST_HIGH = ("architecture", "repository_wide_change", "complex_reasoning")

FILES_FOR_FULL = 8
DEPENDENTS_FOR_FULL = 20
_NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
                 "seven": 7, "eight": 8, "nine": 9, "ten": 10, "a dozen": 12, "dozens of": 24}
_FILES_IN_TEXT = re.compile(
    r"\b(\d+|" + "|".join(map(re.escape, _NUMBER_WORDS)) + r")\s+(?:\w+\s+)?(?:files?|modules?|classes)\b")


def score(task: str, task_class: TaskClass, *, files: tuple[str, ...] | list[str] = (),
          project_root: str | Path | None = None, factor_words: dict | None = None) -> Complexity:
    """The complexity of a task. `factor_words` adds words to the built-in
    architectural, risk and hedge lists (never replaces them)."""
    text = normalise(task)
    extra = factor_words or {}
    architectural_words = ARCHITECTURAL_WORDS + tuple(extra.get("architectural", ()))
    risk_words = RISK_WORDS + tuple(extra.get("risk", ()))
    hedges = HEDGES + tuple(extra.get("hedges", ()))
    kind = task_class.type
    factors: dict = {}

    factors["scope"] = SCOPE[kind]

    count, count_source = _file_count(text, files)
    factors["file_count"] = min(count / FILES_FOR_FULL, 1.0)
    factors["file_count_source"] = count_source

    dependents, dependency_source = _dependents(project_root, files)
    factors["dependency_count"] = min(dependents / DEPENDENTS_FOR_FULL, 1.0)
    factors["dependency_count_source"] = dependency_source

    architectural = ARCHITECTURAL.get(kind, 0.0)
    if any(classify.matches(text, word) for word in architectural_words):
        architectural += 0.3
    factors["architectural_impact"] = min(architectural, 1.0)

    factors["reasoning_required"] = REASONING.get(kind, REASONING_OTHERWISE)

    risk = 0.4 if any(classify.matches(text, word) for word in risk_words) else 0.0
    if kind == "debugging":
        risk += 0.3
    factors["failure_risk"] = min(risk, 1.0)

    uncertainty = 1.0 - task_class.confidence
    if any(classify.matches(text, word) for word in hedges):
        uncertainty += 0.2
    factors["uncertainty"] = min(max(uncertainty, 0.0), 1.0)

    for name, _ in WEIGHTS:
        factors[name] = round(factors[name], 4)
    total = round(sum(factors[name] * weight for name, weight in WEIGHTS), 4)
    return Complexity(score=total, level=_level(total, kind, factors["failure_risk"]), factors=factors)


def level_for(score_value: float) -> str:
    for level, bound in zip(LEVELS, THRESHOLDS):
        if score_value < bound:
            return level
    return LEVELS[-1]


def distance_to_threshold(score_value: float) -> float:
    """How far the score sits from the nearest cut -- the policy's confidence input."""
    return min(abs(score_value - bound) for bound in THRESHOLDS)


def _level(total: float, kind: str, failure_risk: float) -> str:
    level = level_for(total)
    if kind in AT_LEAST_HIGH and level_rank(level) < level_rank("HIGH"):
        level = "HIGH"
    if kind == "trivial_edit" and failure_risk < 0.5:
        level = "LOW"
    return level


def _file_count(text: str, files) -> tuple[int, str]:
    if files:
        return len(files), "files"
    found = _FILES_IN_TEXT.search(text)
    if found:
        word = found.group(1)
        return (int(word) if word.isdigit() else _NUMBER_WORDS[word]), "text"
    return 1, "default"


def _dependents(project_root, files) -> tuple[int, str]:
    if project_root is None or not files:
        return 0, "none"
    from core import inspector

    total = 0
    source = "none"
    for path in files:
        try:
            answer = inspector.impact(project_root, path)
        except Exception:  # an unindexed file or project degrades, never fails
            continue
        if answer.get("file") is None:
            continue
        total += len(answer.get("dependents") or ())
        source = answer.get("source") or "index"
    return total, source
