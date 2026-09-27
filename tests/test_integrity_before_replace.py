"""Nothing replaces a file before what replaces it is checked (2.x roadmap F2)."""
import pytest

from core import index, notes
from core.lib import atomic


def test_a_temp_file_that_does_not_hold_what_was_written_replaces_nothing(tmp_path, monkeypatch):
    target = tmp_path / "counter.txt"
    target.write_text("old\n", encoding="utf-8")
    monkeypatch.setattr(atomic, "_read_back", lambda name: b"ol")   # a short write the disk accepted
    with pytest.raises(atomic.IntegrityError):
        atomic.write_text(target, "new\n")
    assert target.read_text(encoding="utf-8") == "old\n"
    assert list(tmp_path.glob(".counter.txt.*.tmp")) == []


def test_a_good_write_still_replaces(tmp_path):
    target = tmp_path / "counter.txt"
    atomic.write_text(target, "ünïcode\r\nline\n")
    assert target.read_bytes() == "ünïcode\r\nline\n".encode("utf-8")


def test_an_index_that_fails_its_check_keeps_the_previous_one(tmp_path, monkeypatch):
    (tmp_path / ".eos").mkdir()
    notes.add_note(tmp_path, kind="finding", title="Kept index", body="The first build is fine.")
    index.build(tmp_path)
    before = index.db_path(tmp_path).read_bytes()
    notes.add_note(tmp_path, kind="finding", title="Second note", body="This build fails its check.")
    monkeypatch.setattr(index, "_quick_check", lambda path: "row 3 missing from index note_by_path")
    with pytest.raises(index.IndexBuildError, match="quick_check"):
        index.build(tmp_path)
    assert index.db_path(tmp_path).read_bytes() == before
    assert list(index.db_path(tmp_path).parent.glob("eos.db.*.tmp")) == []


def test_an_authored_note_that_cannot_be_read_is_never_rewritten(tmp_path):
    path = notes.add_note(tmp_path, kind="finding", title="Bytes kept", body="A body.")
    raw = path.read_bytes() + b"\n\xff\xfe not utf-8\n"
    path.write_bytes(raw)
    with pytest.raises(UnicodeDecodeError):
        notes.append_to_note_section(path, "Seen again", "- 2026-09-27 again")
    assert path.read_bytes() == raw
