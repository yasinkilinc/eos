"""Regression guards for the scan/clean <-> notes contract (design section 5.6).

Notes live outside .eos/data by construction (notes_dir defaults to
.eos/knowledge, scan/clean only ever touch .eos/data), so these do not drive
new behaviour — they pin the invariant down so a future change to scan or
clean cannot silently start sweeping notes up with everything else derived.
"""
import subprocess
import sys
from pathlib import Path

from core import notes

REPO = Path(__file__).resolve().parents[1]


def _run(args, **kw):
    return subprocess.run(
        [sys.executable, str(REPO / "core" / "eos.py"), *args],
        capture_output=True, text=True, **kw,
    )


def _project_with_a_note(tmp_path) -> Path:
    proj = tmp_path / "demo"
    proj.mkdir()
    (proj / "main.py").write_text("import utils\nutils.hi()\n", encoding="utf-8")
    (proj / "utils.py").write_text("def hi():\n    print('hi')\n", encoding="utf-8")
    assert _run(["init", str(proj)]).returncode == 0
    notes.add_note(proj, kind="finding", title="Pre-existing note", body="Must survive.")
    return proj


def test_full_rescan_leaves_notes_byte_identical(tmp_path):
    proj = _project_with_a_note(tmp_path)
    [note_path] = notes.notes_dir(proj).glob("*.md")
    before = note_path.read_bytes()

    r = _run(["scan", str(proj), "--full"])

    assert r.returncode == 0, r.stderr
    assert note_path.read_bytes() == before
    assert [p.name for p in notes.notes_dir(proj).glob("*.md")] == [note_path.name]


def test_clean_does_not_remove_notes(tmp_path):
    proj = _project_with_a_note(tmp_path)
    assert _run(["scan", str(proj), "--full"]).returncode == 0

    r = _run(["clean", str(proj)])

    assert r.returncode == 0, r.stderr
    assert len(notes.load_notes(proj)) == 1


def test_doctor_warns_when_notes_are_gitignored_by_default(tmp_path):
    proj = tmp_path / "demo"
    proj.mkdir()
    (proj / ".gitignore").write_text(".eos/\n", encoding="utf-8")
    assert _run(["init", str(proj)]).returncode == 0

    r = _run(["doctor", str(proj)])

    assert r.returncode == 0, "an ignored notes dir is a warning, not a broken instance"
    assert "knowledge" in r.stdout.lower() and "gitignored" in r.stdout.lower()


def test_doctor_is_silent_about_notes_when_not_gitignored(tmp_path):
    proj = tmp_path / "demo"
    proj.mkdir()
    assert _run(["init", str(proj)]).returncode == 0

    r = _run(["doctor", str(proj)])

    assert r.returncode == 0
    assert "gitignored" not in r.stdout.lower()
