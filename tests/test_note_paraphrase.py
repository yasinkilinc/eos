"""The paraphrase guard (2.x roadmap M5).

The exact-body and normalised-title guards miss a note that says the same thing
in slightly different words. A new body whose word trigrams overlap an existing
note's by PARAPHRASE_JACCARD or more is refused on add; amend only reports it.
Measured before the threshold was set: no pair in the host's 20 stores reaches
0.6, so the guard refuses nothing that exists.
"""
import subprocess
import sys
from pathlib import Path

import pytest

from core import notes

EOS = [sys.executable, str(Path(__file__).resolve().parents[1] / "core" / "eos.py")]
BODY = ("The order capture service retries the payment call three times with a fixed two second "
        "delay, and a timeout on the third attempt leaves the order in the pending state until the "
        "nightly reconciliation job moves it to failed and releases the reserved number.")


def _project(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    assert subprocess.run(EOS + ["init", str(root), "--no-ai"], capture_output=True).returncode == 0
    notes.add_note(root, "finding", "Payment retries leave orders pending", BODY)
    return root


def test_a_reworded_copy_is_refused(tmp_path):
    root = _project(tmp_path)
    reworded = BODY.replace("nightly", "overnight")          # one word: 87% of trigrams shared
    with pytest.raises(notes.DuplicateNoteError, match="already says"):
        notes.add_note(root, "finding", "Pending orders after payment retries", reworded)


def test_a_different_note_on_the_same_subject_is_accepted(tmp_path):
    root = _project(tmp_path)
    other = ("Reconciliation releases the reserved number only when the order is failed; a cancelled "
             "order keeps the number reserved for fifteen minutes, which the activation scenario "
             "has to wait out before it can reuse the same test number.")
    assert notes.add_note(root, "finding", "Reserved numbers after cancellation", other).is_file()


def test_the_overlap_measure(tmp_path):
    assert notes.trigram_jaccard(BODY, BODY) == 1.0
    assert notes.trigram_jaccard(BODY, "entirely unrelated words about something else here") == 0.0


def test_amend_reports_a_paraphrase_without_refusing(tmp_path):
    root = _project(tmp_path)
    other = notes.add_note(root, "finding", "Reserved numbers after cancellation",
                           "A cancelled order keeps its number reserved for fifteen minutes before release, "
                           "which any scenario reusing the number must wait for; seen on the test environment.")
    done = subprocess.run(EOS + ["note", "amend", str(other), "--path", str(root), "--body",
                                 BODY.replace("nightly", "overnight")], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    assert "says this in other words" in done.stderr


def test_a_shared_quoted_log_does_not_make_two_notes_the_same():
    """Whole-branch review: two defects quoting the same 60-line log scored 0.92."""
    log = "\n".join(f"2026-09-27 04:{n:02d} ERROR order {n} payment timeout after 30000 ms retry {n % 3}"
                    for n in range(60))
    first = f"Root cause: the gateway pool was exhausted.\n\n```\n{log}\n```\n"
    second = f"Root cause: a proxy dropped idle connections.\n\n```\n{log}\n```\n"
    assert notes.trigram_jaccard(first, second) < notes.PARAPHRASE_JACCARD
    quoted = "\n".join(f"> {line}" for line in log.splitlines())
    share = notes.trigram_jaccard(f"Cause A differs here.\n{quoted}", f"Cause B is another.\n{quoted}")
    assert share < notes.PARAPHRASE_JACCARD


def test_an_unterminated_or_indented_fence_is_still_evidence():
    log = "\n".join(f"2026-09-27 04:{n:02d} ERROR order {n} payment timeout retry {n % 3}" for n in range(60))
    open_fence = ("Root cause: pool exhausted.\n```\n" + log, "Root cause: proxy idle drop.\n```\n" + log)
    indented = tuple(f"{cause}\n\n- evidence:\n  ```\n" + "\n".join(f"  {l}" for l in log.splitlines()) + "\n  ```\n"
                     for cause in ("Root cause: pool exhausted.", "Root cause: proxy idle drop."))
    for first, second in (open_fence, indented):
        assert notes.trigram_jaccard(first, second) < notes.PARAPHRASE_JACCARD
