"""One token estimator for the whole engine (2.x roadmap C1/N3).

`core/context_cost.py` measured 2.22 characters per token on the host's own
transcripts; every other estimate in the engine (brief budgets at 3.0, the
injection budget at 2.7, telemetry and bench at chars/4) was a separate guess.
This module is the single ratio; the sites that used to carry their own
constant now import it (some as a re-export, so `brief.CHARS_PER_TOKEN` and
`inject.CHARS_PER_TOKEN` keep working for existing callers).
"""
from __future__ import annotations

CHARS_PER_TOKEN = 2.22


def tokens(chars: float) -> int:
    """Characters to tokens, truncated -- an estimate is never rounded up
    past what the budget it guards actually allows."""
    return int(chars / CHARS_PER_TOKEN)


def chars(tokens: float) -> int:
    """Tokens to characters, for a budget expressed in tokens."""
    return int(tokens * CHARS_PER_TOKEN)
