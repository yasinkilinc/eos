"""Measuring retrieval, and telling a miss apart from a broken measurement.

The distinction these tests exist for: a golden line naming a note that no
longer exists looks exactly like a search failure in the numbers, and drags
the score down forever after a rename. One is a result, the other is an error,
and a harness that cannot separate them stops being believed.
"""
import pytest

from core import notes, retrieval


# Notes written straight to disk. `add_note` refuses near-duplicate titles and
# bodies, which is right for a corpus and wrong for a ranking fixture that needs
# twenty deliberately similar notes to crowd one answer out.
def _write_note(project, name, title, body):
    directory = notes.notes_dir(project)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(
        f"---\nkind: finding\ntitle: {title}\ncreated: 2026-09-01\n---\n\n{body}\n",
        encoding="utf-8")
    return path


def _corpus(tmp_path):
    project = tmp_path / "proj"
    (project / ".eos").mkdir(parents=True)
    paid = notes.add_note(
        project, kind="finding", title="Wallet line reports what was paid",
        body="Not the face value untaxed; the ticket example is illustrative.")
    chain = notes.add_note(
        project, kind="finding", title="Command chain is data, not Java",
        body="cmd_config rows decide the order, cached for 24h.")
    return project, paid.name, chain.name


def _golden(tmp_path, *lines):
    path = tmp_path / "golden.tsv"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# --- the file ------------------------------------------------------------------


def test_comments_and_blank_lines_are_not_queries(tmp_path):
    path = _golden(tmp_path, "# a header", "", "   ", "a question\tsome-note.md")
    entries = retrieval.parse_golden(path)
    assert [(e.query, e.expected) for e in entries] == [("a question", "some-note.md")]
    assert entries[0].line == 4


def test_a_line_without_a_tab_is_refused_by_name(tmp_path):
    path = _golden(tmp_path, "a question some-note.md")
    with pytest.raises(retrieval.GoldenError) as excinfo:
        retrieval.parse_golden(path)
    assert ":1:" in str(excinfo.value)


def test_an_empty_golden_file_is_refused(tmp_path):
    path = _golden(tmp_path, "# only a comment")
    with pytest.raises(retrieval.GoldenError):
        retrieval.parse_golden(path)


# --- the measurement -----------------------------------------------------------


def test_a_found_answer_scores_and_a_missed_one_does_not(tmp_path):
    project, paid, chain = _corpus(tmp_path)
    path = _golden(
        tmp_path,
        f"wallet line reports paid\t{paid}",
        f"zzzunfindable words\t{chain}",
    )

    report = retrieval.evaluate(project, retrieval.parse_golden(path))

    assert report["queries"] == 2
    assert report["recall"][1] == 0.5
    assert report["mrr"] == 0.5
    ranks = {r["expected"]: r["rank"] for r in report["results"]}
    assert ranks[paid] == 1
    assert ranks[chain] is None


def test_a_golden_line_naming_a_note_that_does_not_exist_is_broken_not_missed(tmp_path):
    """The failure this whole split exists for.

    Counted as a miss it is indistinguishable from bad ranking, and a note
    renamed once lowers the score of every run afterwards with nothing saying
    why. It is reported separately and excluded from the denominator.
    """
    project, paid, _ = _corpus(tmp_path)
    path = _golden(
        tmp_path,
        f"wallet line reports paid\t{paid}",
        "a question\t20260101-a-note-that-was-renamed.md",
    )

    report = retrieval.evaluate(project, retrieval.parse_golden(path))

    assert report["queries"] == 1, "the broken line must not be scored"
    assert report["recall"][1] == 1.0, "and must not drag the score down"
    assert [b["expected"] for b in report["broken"]] == [
        "20260101-a-note-that-was-renamed.md"]
    assert report["broken"][0]["line"] == 2


def test_rank_is_reported_not_just_whether_it_was_found(tmp_path):
    project, paid, chain = _corpus(tmp_path)
    # Both notes carry "reports"/"rows" weakly; the query names the other one's
    # title words, so the chain note must come first and the wallet note after.
    path = _golden(tmp_path, f"command chain data java\t{chain}")

    report = retrieval.evaluate(project, retrieval.parse_golden(path))

    assert report["results"][0]["rank"] == 1
    assert report["results"][0]["top"][0] == chain


def test_render_names_every_query_whose_answer_was_not_first(tmp_path):
    project, paid, chain = _corpus(tmp_path)
    path = _golden(
        tmp_path,
        f"wallet line reports paid\t{paid}",
        f"zzzunfindable words\t{chain}",
    )

    text = retrieval.render(retrieval.evaluate(project, retrieval.parse_golden(path)))

    assert "recall@1 0.5" in text
    assert "zzzunfindable words" in text
    assert "wallet line reports paid" not in text, "a query that worked needs no report"


# --- what the weighting is for ---------------------------------------------------


def test_a_filler_heavy_question_still_finds_the_note_that_answers_it(tmp_path):
    """The measurement that justified word weighting.

    Before it, every word of a query counted the same, so a question wrapped in
    ordinary English was decided by the ordinary English. Measured on a real
    106-note corpus with a 20-question golden set: recall@1 0.70 -> 0.95,
    MRR 0.78 -> 0.95.
    """
    project = tmp_path / "proj"
    (project / ".eos").mkdir(parents=True)
    _write_note(project, "20260901-wallet-balance.md",
                "Wallet balance returns 400 from i2i",
                "The registry points WLT_BLNC at the wrong host.")
    # Twenty notes carrying the question's ordinary words and nothing else.
    for n in range(20):
        _write_note(project, f"20260902-ordinary-{n}.md",
                    f"Which order does the customer see in state {n}",
                    f"The order of the steps the customer does see, and what it returns ({n}).")

    ranked = notes.search_notes(project, "which endpoint does the wallet balance use")

    assert ranked, "a question in a sentence must not return nothing"
    assert ranked[0].path.name == "20260901-wallet-balance.md"


def test_a_word_in_every_note_is_worth_nothing(tmp_path):
    project = tmp_path / "proj"
    (project / ".eos").mkdir(parents=True)
    for n in range(5):
        _write_note(project, f"20260901-subject-{n}.md", f"The subject number {n}",
                    f"The body mentions the same words, case {n}.")

    weights = notes.word_weights(notes.load_notes(project), {"the", "body", "wallet"})

    assert weights["the"] == 0.0, "in every note: no vote"
    assert weights["body"] == 0.0, "same"
    assert weights["wallet"] == 0.0, "in no note: also no vote, an unmatched word is not evidence"


def test_a_query_of_only_ubiquitous_words_still_answers(tmp_path):
    """Weighting must not turn a weak query into no query.

    Every word worth zero means the weighting has nothing to say, not that
    nothing matches -- the previous behaviour is the right fallback.
    """
    project = tmp_path / "proj"
    (project / ".eos").mkdir(parents=True)
    for n in range(4):
        _write_note(project, f"20260901-wallet-{n}.md", f"Wallet balance in state {n}",
                    f"Every note here says wallet and balance, entry {n}.")

    assert notes.search_notes(project, "wallet balance")
