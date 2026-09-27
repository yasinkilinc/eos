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


# --- review 14 ---------------------------------------------------------------------------


def _supersede(root, title, body, old):
    _eos("note", "add", str(root), "--kind", "finding", "--title", title, "--body", body, "--supersedes", old)


def test_an_amend_keeps_what_a_note_supersedes(tmp_path):
    root = _project(tmp_path)
    _supersede(root, "Payment retries back off", NEW, "Payment retries use a fixed delay")
    new = next(n for n in notes.load_notes(root) if n.title == "Payment retries back off")
    _eos("note", "amend", str(new.path), str(root), "--reaffirm", "checked again today")
    again = next(n for n in notes.load_notes(root) if n.title == "Payment retries back off")
    assert again.supersedes == new.supersedes
    assert "Payment retries use a fixed delay" not in [n.title for n in notes.search_notes(root, "payment retries")]


def test_a_superseded_procedure_is_not_offered_and_cannot_start_a_run(tmp_path, monkeypatch):
    monkeypatch.setenv("EOS_STATE_DIR", str(tmp_path / "state"))
    root = _project(tmp_path)
    done = subprocess.run(EOS + ["procedure", "new", str(root), "--title", "Ship the billing service", "--steps", "-",
                                 "--success", "it runs"], input="Build it by hand\n", capture_output=True, text=True)
    old = done.stdout.split("\t")[0]
    _eos("note", "add", str(root), "--kind", "procedure", "--title", "Ship the billing service with the pipeline",
         "--body", "## Steps\n\n1. Run the pipeline\n\n## Success\n\n- it runs", "--procedure", "ship-billing-pipeline",
         "--supersedes", "Ship the billing service")
    assert [n.procedure for n in notes.procedures(root)] == ["ship-billing-pipeline"]
    refused = _eos("run", "start", str(root), "--title", "Ship once", "--procedure", old, check=False)
    assert refused.returncode != 0 and "ship-billing-pipeline" in refused.stderr
    from core import brief

    assert "ship-billing-pipeline" in brief.build(root, task="ship the billing service", task_only=True)


def test_two_replacements_are_both_named_and_a_cycle_hides_nothing(tmp_path):
    root = _project(tmp_path)
    _supersede(root, "Payment retries back off", NEW, "Payment retries use a fixed delay")
    _supersede(root, "Payment retries are capped", "At most three payment attempts are made per order, then it waits.",
               "Payment retries use a fixed delay")
    old = next(n for n in notes.load_notes(root) if n.title == "Payment retries use a fixed delay")
    shown = _eos("note", "show", str(root), old.path.name).stdout.splitlines()[0]
    assert "back-off" in shown and "capped" in shown
    first, second = sorted(notes.load_notes(root), key=lambda n: n.title)[:2]
    for note, other in ((first, second), (second, first)):
        text = note.path.read_text(encoding="utf-8").replace("---\n", f"---\nsupersedes: {other.path.name}\n", 1)
        note.path.write_text(text, encoding="utf-8")
    replaced = notes.superseded(notes.load_notes(root))
    assert first.path.name not in replaced and second.path.name not in replaced


def test_the_index_records_what_a_note_supersedes(tmp_path):
    import sqlite3

    from core import index

    root = _project(tmp_path)
    _supersede(root, "Payment retries back off", NEW, "Payment retries use a fixed delay")
    index.build(root)
    rows = sqlite3.connect(index.db_path(root)).execute(
        "SELECT title FROM note WHERE supersedes IS NOT NULL").fetchall()
    assert rows == [("Payment retries back off",)]
