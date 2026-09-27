"""A note may replace another (the additive part of roadmap M1, no migration).

`eos note add --supersedes <note>` writes `supersedes: <file>`; the old note
stays on disk as the record, but search and briefs stop offering it, `note
show` says what replaced it, and the note graph links the two. The rewrite is
expected to read like the note it replaces, so the duplicate guards do not
compare the two.
"""
import subprocess
import sys
from pathlib import Path

from core import note_graph, notes

EOS = [sys.executable, str(Path(__file__).resolve().parents[1] / "core" / "eos.py")]
OLD = ("The order capture service retries the payment call three times with a fixed two second "
       "delay, and a timeout on the third attempt leaves the order pending until the nightly job.")
NEW = ("The order capture service retries the payment call three times with a fixed two second "
       "delay, and a timeout on the third attempt leaves the order pending until the nightly batch.")


def _eos(*args, check=True):
    done = subprocess.run(EOS + list(args), capture_output=True, text=True)
    if check:
        assert done.returncode == 0, done.stderr
    return done


def _project(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    _eos("init", str(root), "--no-ai")
    notes.add_note(root, "finding", "Payment retries use a fixed delay", OLD)
    return root


def test_a_rewrite_that_supersedes_is_accepted_and_the_old_note_steps_back(tmp_path):
    root = _project(tmp_path)
    refused = _eos("note", "add", str(root), "--kind", "finding", "--title", "Payment retries back off",
                   "--body", NEW, check=False)
    assert refused.returncode != 0                                   # without it, a paraphrase is refused
    _eos("note", "add", str(root), "--kind", "finding", "--title", "Payment retries back off",
         "--body", NEW, "--supersedes", "Payment retries use a fixed delay")
    corpus = {n.title: n for n in notes.load_notes(root)}
    new, old = corpus["Payment retries back off"], corpus["Payment retries use a fixed delay"]
    assert new.supersedes == old.path.name
    titles = [n.title for n in notes.search_notes(root, "payment retries delay")]
    assert "Payment retries back off" in titles and "Payment retries use a fixed delay" not in titles
    shown = _eos("note", "show", str(root), old.path.name).stdout
    assert shown.startswith(f"Superseded by {new.path.name}")
    edges = note_graph.edges(list(corpus.values()), None)
    assert any(e.kind == "supersedes" and e.src == new.path.name and e.dst == old.path.name for e in edges)


def test_supersedes_must_name_one_existing_note(tmp_path):
    root = _project(tmp_path)
    done = _eos("note", "add", str(root), "--kind", "finding", "--title", "Something new entirely",
                "--body", "A different fact about notifications.", "--supersedes", "no such note", check=False)
    assert done.returncode != 0 and "supersedes" in done.stderr
