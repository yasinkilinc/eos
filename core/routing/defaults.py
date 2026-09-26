"""The registry a project gets before it configures one.

These are defaults to be corrected in `.eos/config.toml`, not facts about
vendors. They are here, as a Python constant rather than a data file, because
the runtime updater copies code and templates only (`*.py`, `*.md`,
`core/lib/updater.py`): a `.toml` beside this module would never reach an
installed project.

The ids are the model aliases the Claude Code subagent tool's `model` argument
accepts (code.claude.com/docs/en/model-config, /sub-agents): an id is what the
harness receives, so it has to be one the harness takes. The full model ids
each alias has resolved to are aliases here, so a transcript's `claude-opus-5-5`
joins to the decision that said `opus` (claude plan 3.1).

What an alias resolves to moves: measured in this workspace's transcripts,
`opus` gave `claude-opus-5[1m]` 40 times and then `claude-opus-5-5[1m]` 6 times
(2026-09). Cost follows the current resolution and is relative to the cheapest,
from the published per-token input price: Haiku 4.5 $1, Sonnet 5 $2, Opus 5.5
$4 (Opus 5 was $5), Fable 5.1 $10.

Effort levels are what each model accepts, not what anyone wishes it did:
Haiku 4.5 takes no effort setting at all (an effort sent to it is an API error,
and a hook payload from a Haiku session carries no `effort` field -- observed),
so its tuple is empty and a decision for it carries no effort. The others take
all five. Capability numbers are relative to each other, nothing more; the
policy's level table picks the first model for LOW, the second for MEDIUM and
HIGH, the third for CRITICAL. Fable equals Opus on both numbers and costs more,
so it is never the cheapest sufficient choice -- it is here so an explicit
`--model fable` is honoured rather than refused.
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
        efforts=(),
        task_types=("trivial_edit", "simple_implementation", "test_generation"),
        aliases=("claude-haiku-4-5", "claude-haiku-4-5-20251001"),
    ),
    ModelSpec(
        id="sonnet",
        provider="anthropic",
        reasoning=4,
        coding=5,
        context_window=1_000_000,
        cost=2.0,
        efforts=EFFORTS,
        task_types=("normal_implementation", "debugging", "refactoring",
                    "code_review", "test_generation", "investigation"),
        aliases=("claude-sonnet-5", "sonnet[1m]"),
    ),
    ModelSpec(
        id="opus",
        provider="anthropic",
        reasoning=5,
        coding=5,
        context_window=1_000_000,
        cost=4.0,
        efforts=EFFORTS,
        task_types=("architecture", "planning", "complex_reasoning",
                    "repository_wide_change"),
        aliases=("claude-opus-5-5", "claude-opus-5", "opus[1m]"),
    ),
    ModelSpec(
        id="fable",
        provider="anthropic",
        reasoning=5,
        coding=5,
        context_window=1_000_000,
        cost=10.0,
        efforts=EFFORTS,
        task_types=("architecture", "complex_reasoning", "repository_wide_change"),
        aliases=("claude-fable-5-1", "claude-fable-5", "fable[1m]"),
    ),
)
