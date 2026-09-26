"""The contracts every routing module speaks (ADR-025).

Kept apart from the modules that produce them so that a surface -- the CLI,
the brief, the MCP server, a hook -- can render a decision without importing
the policy that made it, and so the vocabulary is written down once.

Three orders are load-bearing and are tuples for that reason: `EFFORTS`
ascends, and clamping an effort means walking it down; `LEVELS` ascends, and
a floor or a cap is an index into it; `TASK_TYPES` is the tie-break when two
types classify equally. Iterating a dict instead would make a decision depend
on insertion order, which is the one thing a deterministic router may not do.
"""
from __future__ import annotations

import dataclasses
import re

EFFORTS = ("low", "medium", "high", "xhigh", "max")
LEVELS = ("LOW", "MEDIUM", "HIGH", "CRITICAL")
TASK_TYPES = (
    "trivial_edit",
    "simple_implementation",
    "normal_implementation",
    "debugging",
    "refactoring",
    "code_review",
    "test_generation",
    "investigation",
    "architecture",
    "planning",
    "complex_reasoning",
    "repository_wide_change",
)
STATUSES = ("available", "unavailable")
# Where the half of a decision that was not left to the policy came from.
SOURCES = ("auto", "flag", "env", "config", "run")
AUTO = "auto"


@dataclasses.dataclass(frozen=True)
class ModelSpec:
    """One model the router may choose. Capabilities are 1..5, cost is relative."""

    id: str
    provider: str
    reasoning: int
    coding: int
    context_window: int
    cost: float
    efforts: tuple[str, ...]
    task_types: tuple[str, ...] = ()
    status: str = "available"
    aliases: tuple[str, ...] = ()

    @property
    def available(self) -> bool:
        return self.status == "available"


@dataclasses.dataclass(frozen=True)
class TaskClass:
    type: str
    confidence: float
    # The words that decided it, so "why refactoring?" has an answer.
    matched: tuple[str, ...] = ()


@dataclasses.dataclass(frozen=True)
class Complexity:
    score: float
    level: str
    # Each factor 0..1. A few non-numeric markers (e.g. where a count came
    # from) ride along under a `_source` suffix; they are never summed.
    factors: dict = dataclasses.field(default_factory=dict)


@dataclasses.dataclass(frozen=True)
class Decision:
    task_type: str
    level: str
    score: float
    model: str
    effort: str
    reason: str
    confidence: float
    override_source: str
    factors: dict = dataclasses.field(default_factory=dict)
    # Other models that also met the requirement, cheapest first.
    alternatives: tuple[str, ...] = ()
    task_hash: str = ""
    execution: str | None = None
    reused: bool = False

    def to_dict(self) -> dict:
        data = dataclasses.asdict(self)
        data["alternatives"] = list(self.alternatives)
        return data


def normalise(text: str) -> str:
    """Lower-case, whitespace collapsed: what is hashed and what is matched.

    `str.lower()` turns U+0130 (capital I with dot) into "i" plus a combining
    dot (U+0307), so "İptal" never matched a configured "iptal". The dotted
    capital is mapped to a plain "i" first and any stray combining dot above an
    "i" is dropped -- a Unicode fact, not a language rule: the dotless capital
    "I" still lowers to "i", as every English word needs.
    """
    text = str(text or "").replace("\u0130", "i")
    text = re.sub(r"\s+", " ", text).strip().lower()
    return text.replace("i\u0307", "i")


def task_hash(text: str) -> str:
    """FNV-1a 32-bit of the normalised task, as hex.

    The trace needs to tell two decisions for the same task apart from two for
    different tasks, and nothing more; a hash that short cannot be reversed
    into what somebody typed (ADR-019).
    """
    value = 0x811C9DC5
    for byte in normalise(text).encode("utf-8"):
        value ^= byte
        value = (value * 0x01000193) & 0xFFFFFFFF
    return f"{value:08x}"


def effort_rank(effort: str) -> int:
    return EFFORTS.index(effort)


def level_rank(level: str) -> int:
    return LEVELS.index(level)
