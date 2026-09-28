"""Generated is not memory (2.x roadmap M3, ADR-028).

What a generator writes -- an endpoint table, an imported AGENTS.md section --
lives in the store's `generated/` directory: searchable when asked for, never
loaded as a note of the project's memory (briefs, injection, audit, dedup).
"""
import hashlib
import subprocess
import sys
from pathlib import Path

import pytest

from core import notes

EOS = [sys.executable, str(Path(__file__).resolve().parents[1] / "core" / "eos.py")]


def _project(tmp_path):
    project = tmp_path / "proj"
    (project / ".eos").mkdir(parents=True)
    return project


def _raw(project, name, title, extra="", body="Wallet body."):
    directory = notes.notes_dir(project)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(f"---\nkind: finding\ntitle: {title}\ncreated: 2026-09-01\n{extra}---\n\n{body}\n",
                    encoding="utf-8")
    return path


def test_a_generated_note_is_written_outside_the_memory(tmp_path):
    project = _project(tmp_path)
    path = notes.add_note(project, kind="finding", title="svc API map", body="| GET | /a |",
                          source="api-inventory", provenance="generated")
    assert path.parent == notes.generated_dir(project) == notes.notes_dir(project) / "generated"
    assert notes.load_notes(project) == []
    assert [n.title for n in notes.load_generated(project)] == ["svc API map"]


def test_a_generated_body_is_compared_with_generated_notes_only(tmp_path):
    project = _project(tmp_path)
    _raw(project, "20260901-authored.md", "Wallet endpoints", body="| GET | /wallet |")
    notes.add_note(project, kind="finding", title="Wallet API map", body="| GET | /wallet |",
                   source="api-inventory", provenance="generated")
    with pytest.raises(notes.DuplicateNoteError):
        notes.add_note(project, kind="finding", title="Wallet API map, again", body="| GET | /wallet |",
                       source="api-inventory", provenance="generated")


def test_search_offers_generated_notes_only_when_asked(tmp_path):
    project = _project(tmp_path)
    _raw(project, "20260901-wallet.md", "Wallet cache warms on boot")
    notes.add_note(project, kind="finding", title="Wallet API map", body="| GET | /wallet |",
                   source="api-inventory", provenance="generated")
    assert [n.title for n in notes.search_notes(project, "wallet")] == ["Wallet cache warms on boot"]
    found = {n.title for n in notes.search_notes(project, "wallet", generated=True)}
    assert found == {"Wallet cache warms on boot", "Wallet API map"}


def test_a_generated_note_can_be_amended(tmp_path):
    project = _project(tmp_path)
    path = notes.add_note(project, kind="finding", title="Wallet API map", body="| GET | /wallet |",
                          source="api-inventory", provenance="generated")
    notes.amend_note(path, project, body="| GET | /wallet |\n| POST | /wallet |")
    assert "POST" in notes.parse_note(path).body


def test_the_audit_does_not_read_generated_notes(tmp_path):
    project = _project(tmp_path)
    (project / "Api.java").write_text("class Api {}\n", encoding="utf-8")
    notes.add_note(project, kind="finding", title="Wallet API map", body="| GET | /wallet |",
                   source="api-inventory", provenance="generated", scope=["Api.java"])
    (project / "Api.java").write_text("class Api { int x; }\n", encoding="utf-8")
    assert notes.stale_notes(project) == []


def _store(tmp_path):
    project = _project(tmp_path)
    _raw(project, "20260901-api.md", "svc API map", "source: api-inventory\nprovenance: generated\n")
    _raw(project, "20260902-guide.md", "svc AGENTS.md: Rules", "source: agents-md\nprovenance: generated\n",
         body="Rules body.")
    _raw(project, "20260903-tree.md", "svc layout", "source: repo-topology\nprovenance: agent\n",
         body="Tree body.")
    _raw(project, "20260904-journey.md", "Order journey", "source: journey-map\nprovenance: agent\n",
         body="Journey body.")
    _raw(project, "20260905-ticket.md", "Refund retries", "source: FM-1\n", body="Ticket body.")
    return project


def test_the_move_lists_what_it_would_move_and_moves_nothing(tmp_path):
    project = _store(tmp_path)
    moves = notes.move_generated(project)
    assert sorted(old.name for old, _ in moves) == ["20260901-api.md", "20260902-guide.md", "20260903-tree.md"]
    assert len(notes.load_notes(project)) == 5 and not notes.generated_dir(project).exists()


def test_the_move_keeps_every_byte_and_writes_a_mapping(tmp_path):
    project = _store(tmp_path)
    before = {p.name: p.read_bytes() for p in notes.notes_dir(project).glob("*.md")}
    notes.move_generated(project, apply=True)
    assert [n.path.name for n in notes.load_notes(project)] == ["20260904-journey.md", "20260905-ticket.md"]
    moved = {n.path.name for n in notes.load_generated(project)}
    assert moved == {"20260901-api.md", "20260902-guide.md", "20260903-tree.md"}
    for name in moved:
        assert (notes.generated_dir(project) / name).read_bytes() == before[name]
    rows = (notes.generated_dir(project) / notes.MOVED_FILE).read_text(encoding="utf-8").splitlines()
    assert rows[0] == "from\tto\tsha256"
    assert f"20260901-api.md\tgenerated/20260901-api.md\t{hashlib.sha256(before['20260901-api.md']).hexdigest()}" \
        in rows
    assert notes.move_generated(project, apply=True) == []


def test_the_move_refuses_to_overwrite(tmp_path):
    project = _store(tmp_path)
    notes.generated_dir(project).mkdir(parents=True)
    (notes.generated_dir(project) / "20260901-api.md").write_text("other\n", encoding="utf-8")
    with pytest.raises(FileExistsError):
        notes.move_generated(project, apply=True)
    assert (notes.notes_dir(project) / "20260902-guide.md").exists()


def test_the_index_holds_generated_notes_flagged(tmp_path):
    from core import index

    project = _store(tmp_path)
    notes.move_generated(project, apply=True)
    index.build(project)
    conn = index.connect_read_only(index.db_path(project))
    try:
        rows = dict(conn.execute("SELECT file, generated FROM note").fetchall())
    finally:
        conn.close()
    # journey-map was already `generated` in the index (`is_generated`: re-run
    # its generator, do not hand-edit); it stays in the memory store all the same.
    assert rows == {"generated/20260901-api.md": 1, "generated/20260902-guide.md": 1,
                    "generated/20260903-tree.md": 1, "20260904-journey.md": 1, "20260905-ticket.md": 0}


def test_a_generated_note_never_takes_a_memory_note_s_name(tmp_path):
    # Before a store is moved, its generator re-runs into generated/: the same
    # day and title would give two files one name.
    project = _project(tmp_path)
    notes.add_note(project, kind="finding", title="svc API map", body="| GET | /old |", source="api-inventory")
    with pytest.raises(FileExistsError):
        notes.add_note(project, kind="finding", title="svc API map", body="| GET | /new |",
                       source="api-inventory", provenance="generated")


def test_the_index_keeps_two_notes_of_one_name_apart(tmp_path):
    from core import index

    project = _project(tmp_path)
    _raw(project, "20260901-api.md", "svc API map", "source: api-inventory\n")
    notes.generated_dir(project).mkdir(parents=True)
    (notes.generated_dir(project) / "20260901-api.md").write_text(
        "---\nkind: finding\ntitle: svc API map\ncreated: 2026-09-01\n---\n\nNew body.\n", encoding="utf-8")
    index.build(project)
    conn = index.connect_read_only(index.db_path(project))
    try:
        files = sorted(row[0] for row in conn.execute("SELECT file FROM note"))
    finally:
        conn.close()
    assert files == ["20260901-api.md", "generated/20260901-api.md"]


def test_a_move_cut_short_records_what_it_moved(tmp_path, monkeypatch):
    project = _store(tmp_path)
    real, calls = Path.rename, []

    def rename(self, target):
        calls.append(self.name)
        if len(calls) == 2:
            raise OSError("disk went away")
        return real(self, target)

    monkeypatch.setattr(Path, "rename", rename)
    with pytest.raises(OSError):
        notes.move_generated(project, apply=True)
    rows = (notes.generated_dir(project) / notes.MOVED_FILE).read_text(encoding="utf-8").splitlines()
    assert [row.split("\t")[0] for row in rows[1:]] == [calls[0]]


def test_an_amended_generated_note_is_compared_with_generated_notes(tmp_path):
    project = _project(tmp_path)
    body = ("Wallet endpoints list the deposit call, the withdrawal call, the balance call, the statement call, "
            "the transfer call and the refund call for each account type the service knows about, "
            "and every one of them answers with the same envelope and the same error codes.")
    _raw(project, "20260901-wallet.md", "Wallet endpoints", body=body)
    one = notes.add_note(project, kind="finding", title="Wallet API map", body="| GET | /a |",
                         source="api-inventory", provenance="generated")
    notes.add_note(project, kind="finding", title="Deposit API map", body=body,
                   source="api-inventory", provenance="generated")
    done = subprocess.run(EOS + ["note", "amend", str(one), str(project), "--body", body.replace("each", "every")],
                          capture_output=True, text=True)
    assert done.returncode == 0 and "Deposit API map" in done.stderr and "Wallet endpoints" not in done.stderr


def test_the_cli_moves_and_searches_generated(tmp_path):
    project = _store(tmp_path)
    dry = subprocess.run(EOS + ["note", "move-generated", str(project)], capture_output=True, text=True)
    assert dry.returncode == 0 and "3 note(s) would move" in dry.stdout and "--apply" in dry.stdout
    done = subprocess.run(EOS + ["note", "move-generated", str(project), "--apply"], capture_output=True, text=True)
    assert done.returncode == 0 and "moved 3 note(s)" in done.stdout
    plain = subprocess.run(EOS + ["note", "search", str(project), "svc"], capture_output=True, text=True).stdout
    asked = subprocess.run(EOS + ["note", "search", str(project), "svc", "--generated"],
                           capture_output=True, text=True).stdout
    assert "20260901-api.md" not in plain and "20260901-api.md" in asked
    shown = subprocess.run(EOS + ["note", "show", str(project), "20260901-api.md"], capture_output=True, text=True)
    assert shown.returncode == 0 and "Wallet body." in shown.stdout
