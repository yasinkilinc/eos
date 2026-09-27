"""`core/context/budget.py`: the one token estimator the whole engine shares (2.x roadmap C1/N3)."""
from core.context import budget


def test_chars_per_token_is_the_measured_ratio():
    assert budget.CHARS_PER_TOKEN == 2.22


def test_tokens_converts_characters_to_tokens():
    assert budget.tokens(500) == 225
    assert budget.tokens(0) == 0


def test_tokens_truncates_rather_than_rounds():
    # 221 / 2.22 = 99.55..., truncated down like the sites it replaces.
    assert budget.tokens(221) == 99


def test_chars_converts_tokens_to_characters():
    assert budget.chars(100) == 222
    assert budget.chars(0) == 0
