"""A note is tokenised once per process, not once per pass.

The task brief weighed the corpus three times (`word_weights`) and scored it
again (`relevance`): 16,511 `_words` calls and 1.5M `fold` calls for 2,217
notes, a third of a 1.7 s prompt hook (2026-11-04).
"""
import pytest

from core.notes import render


def test_the_same_text_is_tokenised_once(monkeypatch):
    calls = []
    real = render.fold

    def counting(text):
        calls.append(text)
        return real(text)

    monkeypatch.setattr(render, "fold", counting)
    text = "Deploy the wallet service to staging after the PROJ-17 review"

    first = render._words(text)
    folded = len(calls)
    second = render._words(text)

    assert folded > 0 and len(calls) == folded
    assert first == second


def test_a_caller_cannot_change_what_the_next_caller_gets():
    words = render._words("rotate the database credentials")
    with pytest.raises(AttributeError):
        words.add("intruder")
    assert "intruder" not in render._words("rotate the database credentials")


def test_stemming_is_part_of_what_is_remembered():
    assert render._words("deployments", stem_words=True) != render._words("deployments")
