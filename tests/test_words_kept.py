"""A note body is tokenised once, not once per prompt.

Every prompt hook is a new process, so the per-process memory of `_words`
starts empty each time: tokenising 2,217 notes cost 163 ms of every prompt,
loading what an earlier process kept costs 32 ms (2026-11-05). Long texts
are kept in the state directory; a query or a title is cheaper to tokenise
than to look up and is never written there.
"""
import pickle

from core.notes import render

LONG = "Deploy the wallet service to staging after the PROJ-17 review. " * 6
OTHER = "Rotate the database credentials before the release window closes. " * 6


def _next_process():
    render._kept_save()
    render._words.cache_clear()
    render._kept = None
    render._kept_file = None
    render._kept_used = set()
    render._kept_dirty = False


def _folds(monkeypatch):
    calls = []
    real = render.fold

    def counting(text):
        calls.append(text)
        return real(text)

    monkeypatch.setattr(render, "fold", counting)
    return calls


def _kept_file(tmp_path):
    return tmp_path / "state" / render.KEPT_FILENAME


def test_a_long_text_is_not_tokenised_again_in_the_next_process(monkeypatch):
    first = render._words(LONG)
    _next_process()
    calls = _folds(monkeypatch)

    assert render._words(LONG) == first
    assert calls == []


def test_a_short_text_is_never_written(tmp_path):
    render._words("rotate the database credentials")
    render._kept_save()
    assert not _kept_file(tmp_path).exists()


def test_words_kept_by_another_eos_version_are_not_used(monkeypatch):
    render._words(LONG)
    _next_process()
    monkeypatch.setattr(render, "_KEPT_VERSION", "another")
    calls = _folds(monkeypatch)

    render._words(LONG)

    assert calls != []


def test_a_damaged_file_is_tokenised_again(tmp_path, monkeypatch):
    expected = render._words(LONG)
    _next_process()
    _kept_file(tmp_path).write_bytes(b"not kept words")

    assert render._words(LONG) == expected


def test_words_unused_for_a_month_are_dropped_at_the_next_save(tmp_path, monkeypatch):
    monkeypatch.setattr(render, "_today", lambda: 1000)
    render._words(LONG)
    _next_process()
    monkeypatch.setattr(render, "_today", lambda: 1000 + render.KEPT_DAYS + 1)
    render._words(OTHER)
    _next_process()

    kept = pickle.loads(_kept_file(tmp_path).read_bytes())

    assert len(kept["entries"]) == 1
