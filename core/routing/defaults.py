"""The registry a project gets before it configures one.

These are defaults to be corrected in `.eos/config.toml`, not facts about
vendors. They are here, as a Python constant rather than a data file, because
the runtime updater copies `*.py` only (`core/lib/updater.py`): a `.toml`
beside this module would never reach an installed project.

The ids are the three model aliases the Claude Code subagent tool's `model`
argument accepts, as its documentation read on 2026-09-25
(code.claude.com/docs/en/model-config, /sub-agents). An id is what the
harness receives, so it has to be one the harness takes.

Capability numbers are chosen so the policy's level table picks the first for
LOW, the second for MEDIUM and HIGH, the third for CRITICAL -- and they are
relative to each other, nothing more. Cost is relative to the cheapest.

Every entry lists all five effort levels. The same documentation gives one
set of five for the session and for an agent definition and does not say
which models refuse which; inventing a difference would be guessing. A
project that observes a refusal narrows that model's `efforts` in config, and
the clamp does the rest.
"""
from __future__ import annotations

from core.routing.types import EFFORTS, ModelSpec

DEFAULT_MODELS: tuple[ModelSpec, ...] = (
    ModelSpec(
        id="haiku",
        provider="anthropic",
        reasoning=2,
        coding=3,
        context_window=200_000,
        cost=1.0,
        efforts=EFFORTS,
        task_types=("trivial_edit", "simple_implementation", "test_generation"),
    ),
    ModelSpec(
        id="sonnet",
        provider="anthropic",
        reasoning=4,
        coding=5,
        context_window=200_000,
        cost=3.0,
        efforts=EFFORTS,
        task_types=("normal_implementation", "debugging", "refactoring",
                    "code_review", "test_generation", "investigation"),
    ),
    ModelSpec(
        id="opus",
        provider="anthropic",
        reasoning=5,
        coding=5,
        context_window=200_000,
        cost=5.0,
        efforts=EFFORTS,
        task_types=("architecture", "planning", "complex_reasoning",
                    "repository_wide_change"),
    ),
)
