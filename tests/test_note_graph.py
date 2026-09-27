"""Notes linked to notes (2.x: the note graph).

Measured on the host: 1 of 238 notes cites another by name, but 40 share a
ticket key with another. The edges are derived, deterministically, on every
build: a citation (`note:<stem>` or the file's name), a lesson naming its
procedure, a shared scope file, a shared ticket key. A key or file shared by
more than MAX_SHARED notes links nothing -- it says nothing about any two.
"""
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

from core import brief, index, note_graph, notes

EOS = [sys.executable, str(Path(__file__).resolve().parents[1] / "core" / "eos.py")]
KEY = re.compile(r"\bFM-[0-9]+\b")


def _project(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    assert subprocess.run(EOS + ["init", str(root), "--no-ai"], capture_output=True).returncode == 0
    (root / "src").mkdir()
    (root / "src" / "pay.py").write_text("x = 1\n", encoding="utf-8")
    return root


def _file(root, title):
    return next(n.path.name for n in notes.load_notes(root) if n.title == title)


def _seed(root):
    notes.add_note(root, "finding", "Payment retries leave orders pending",
                   "The FM-12 flow retries the payment call three times.", scope=["src/pay.py"])
    notes.add_note(root, "finding", "Retries stay at three for the gateway",
                   "Decided while fixing FM-12: three retries, a fixed delay.")
    notes.add_note(root, "finding", "The pay module reads its timeout from config",
                   "Found reading the module.", scope=["src/pay.py"])
    pending = _file(root, "Payment retries leave orders pending")
    notes.add_note(root, "finding", "Cancelling a pending order releases the number",
                   f"Follows from note:{pending[:-3]} -- a cancel releases what the retry held.")
    notes.add_note(root, "finding", "An unrelated note about notifications", "Templates render at send time for FM-99.")


def test_edges_are_citations_lessons_scopes_and_tickets(tmp_path):
    root = _project(tmp_path)
    _seed(root)
    found = note_graph.edges(notes.load_notes(root), KEY)
    pending = _file(root, "Payment retries leave orders pending")
    kinds = {(e.kind, e.via) for e in found if pending in (e.src, e.dst)}
    assert ("ticket", "FM-12") in kinds and ("scope", "src/pay.py") in kinds and ("cites", None) in kinds
    unrelated = _file(root, "An unrelated note about notifications")
    assert not any(unrelated in (e.src, e.dst) for e in found)


def test_a_key_shared_by_too_many_notes_links_nothing(tmp_path):
    root = _project(tmp_path)
    words = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel", "india", "juliet"]
    for word in words[:note_graph.MAX_SHARED + 1]:
        notes.add_note(root, "finding", f"The {word} service behaves oddly", f"Seen while on FM-7 in {word}.")
    assert note_graph.edges(notes.load_notes(root), KEY) == []


def test_related_ranks_strong_links_first_and_says_why(tmp_path):
    root = _project(tmp_path)
    _seed(root)
    corpus = notes.load_notes(root)
    pending = _file(root, "Payment retries leave orders pending")
    found = note_graph.related(corpus, pending, KEY, limit=5)
    assert found[0][0].title == "Cancelling a pending order releases the number"   # a citation outranks
    reasons = {note.title: why for note, why in found}
    assert reasons["Retries stay at three for the gateway"] == "ticket FM-12"
    assert reasons["The pay module reads its timeout from config"] == "scope src/pay.py"


def test_a_lesson_is_linked_to_its_procedure(tmp_path):
    root = _project(tmp_path)
    slug = subprocess.run(EOS + ["procedure", "new", str(root), "--title", "Ship the service", "--steps", "-",
                                 "--success", "it runs"], input="Build it\n", capture_output=True,
                          text=True).stdout.split("\t")[0]
    notes.add_note(root, "lesson", "The build needs the cache warmed",
                   "## What went wrong\nCold cache.\n## What was learned\nWarm it.\n## Next time\nWarm first.",
                   procedure=slug)
    corpus = notes.load_notes(root)
    procedure = next(n for n in corpus if n.kind == "procedure")
    assert [(n.title, why) for n, why in note_graph.related(corpus, procedure.path.name, KEY)] == [
        ("The build needs the cache warmed", "lesson of this procedure")]


def test_the_index_holds_the_edges(tmp_path):
    root = _project(tmp_path)
    _seed(root)
    index.build(root)
    conn = sqlite3.connect(index.db_path(root))
    kinds = {row[0] for row in conn.execute("SELECT kind FROM note_edge")}
    assert {"cites", "scope", "ticket"} <= kinds


def test_eos_note_related_prints_the_links(tmp_path):
    root = _project(tmp_path)
    _seed(root)
    done = subprocess.run(EOS + ["note", "related", str(root), "Payment retries leave orders pending"],
                          capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    assert "Cancelling a pending order releases the number" in done.stdout and "cites" in done.stdout
    assert "An unrelated note" not in done.stdout


def test_the_task_brief_names_notes_linked_to_what_it_found(tmp_path):
    root = _project(tmp_path)
    _seed(root)
    text = brief.build(root, task="payment retries pending", task_only=True)
    assert "LINKED" in text and "Cancelling a pending order releases the number" in text


def test_one_file_written_three_ways_is_one_scope(tmp_path):
    """Review 13: `src/pay.py`, `./src/pay.py` and the absolute path linked nothing."""
    root = _project(tmp_path)
    for title, entry in (("The pay module retries", "src/pay.py"), ("The pay module logs", "./src/pay.py"),
                         ("The pay module times out", str(root / "src" / "pay.py"))):
        notes.add_note(root, "finding", title, f"About {title.split()[-1]}.", scope=[entry])
    scoped = [e for e in note_graph.edges(notes.load_notes(root), KEY, root) if e.kind == "scope"]
    assert len(scoped) == 3


def test_a_key_quoted_in_a_code_block_links_nothing(tmp_path):
    root = _project(tmp_path)
    notes.add_note(root, "finding", "How ticket keys look in logs", "Example:\n\n```\nticket=FM-500 status=open\n```\n")
    notes.add_note(root, "finding", "The FM-500 export drops rows", "The export for FM-500 skips the last page.")
    assert not [e for e in note_graph.edges(notes.load_notes(root), KEY, root) if e.kind == "ticket"]


def test_the_graph_exports_in_node_link_form(tmp_path):
    """For Graphify's community and visual views (`graphify cluster-only`)."""
    import json

    root = _project(tmp_path)
    _seed(root)
    out = tmp_path / "notes-graph.json"
    done = subprocess.run(EOS + ["note", "graph", str(root), "--output", str(out)], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["directed"] is False and data["multigraph"] is False
    ids = {node["id"] for node in data["nodes"]}
    assert len(ids) == 5 and all(node["file_type"] == "note" for node in data["nodes"])
    assert all(link["source"] in ids and link["target"] in ids and link["relation"] for link in data["links"])
    assert "5 notes" in done.stdout and "edge" in done.stdout
