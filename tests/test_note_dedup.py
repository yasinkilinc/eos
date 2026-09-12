"""A forced writer must not fill the corpus with restatements.

Measured before this existed: 265 notes, 235 distinct bodies, 15 groups of
byte-identical bodies -- and ZERO pairs in the 0.4-0.9 similarity band. The
corpus is bimodal today. An automatic writer is exactly what introduces the
middle band, and nothing would have seen it.
"""
import pytest

from core import notes


def test_an_identical_body_is_refused(tmp_path):
    project = tmp_path / "proj"
    (project / ".eos").mkdir(parents=True)
    body = "The parent is read-only, so renaming the child's copy is the only fix."

    notes.add_note(project, kind="finding", title="First title", body=body)

    with pytest.raises(notes.DuplicateNoteError) as excinfo:
        notes.add_note(project, kind="finding", title="A different title", body=body)
    assert "First title" in str(excinfo.value)


def test_a_restated_title_warns_and_names_the_existing_note(tmp_path):
    project = tmp_path / "proj"
    (project / ".eos").mkdir(parents=True)
    notes.add_note(project, kind="finding", title="Wallet balance returns 400",
                   body="Domain-config WLT_BLNC points at i2i.")

    with pytest.raises(notes.DuplicateNoteError) as excinfo:
        notes.add_note(project, kind="finding", title="wallet, balance returns 400!",
                       body="A completely different observation about pagination.",
                       session="sess-1")
    message = str(excinfo.value)
    assert "Wallet balance returns 400" in message
    assert "sess-1" in message


def test_a_duplicate_refusal_names_the_escape_that_actually_unblocks(tmp_path):
    """Reproduced end to end before this: a previous session records a
    finding; this session meets the body-duplicate refusal, does exactly what
    it says (appends to the existing note), and the gate still exits 2. The
    only reachable exit was `eos note skip` -- precisely the wrong answer.
    What does unblock is recording THIS session against the existing note,
    and nothing said so."""
    project = tmp_path / "proj"
    (project / ".eos").mkdir(parents=True)
    body = "The parent is read-only, so renaming the child's copy is the only fix."
    notes.add_note(project, kind="finding", title="Original title", body=body)

    with pytest.raises(notes.DuplicateNoteError) as excinfo:
        notes.add_note(project, kind="finding", title="A different title",
                       body=body, session="sess-42")
    message = str(excinfo.value)
    assert "sess-42" in message
    assert "session:" in message


def test_a_duplicate_refusal_without_a_session_says_only_what_it_can(tmp_path):
    """A note written outside a gate block has no session id to record, so
    the message must not print a `session:` line with nothing after it."""
    project = tmp_path / "proj"
    (project / ".eos").mkdir(parents=True)
    body = "The parent is read-only, so renaming the child's copy is the only fix."
    notes.add_note(project, kind="finding", title="Original title", body=body)

    with pytest.raises(notes.DuplicateNoteError) as excinfo:
        notes.add_note(project, kind="finding", title="A different title", body=body)
    message = str(excinfo.value)
    assert "session:" not in message
    assert "Original title" in message


def test_a_genuinely_new_note_is_written(tmp_path):
    project = tmp_path / "proj"
    (project / ".eos").mkdir(parents=True)
    notes.add_note(project, kind="finding", title="Wallet balance returns 400",
                   body="Domain-config WLT_BLNC points at i2i.")

    path = notes.add_note(project, kind="finding", title="Pagination drops role-filtered rows",
                          body="The role filter ran on the already fetched page.")
    assert path.is_file()


def test_titles_with_no_comparable_signal_do_not_collide(tmp_path):
    # "AB" and "Q3" both reduce to an empty normalized_title_key (every token
    # is <=2 chars once digits are masked). An empty key must mean "no
    # signal", not "matches every other empty-key title".
    project = tmp_path / "proj"
    (project / ".eos").mkdir(parents=True)
    notes.add_note(project, kind="finding", title="AB", body="First unrelated observation.")

    path = notes.add_note(project, kind="finding", title="Q3", body="Second unrelated observation.")
    assert path.is_file()
