"""Every note a brief names carries its kind and age (2.x roadmap C2).

A lesson from two days ago and a finding from last quarter weigh differently;
printed as bare titles they looked the same.
"""
import subprocess
import sys
from pathlib import Path

from core import brief, notes

EOS = [sys.executable, str(Path(__file__).resolve().parents[1] / "core" / "eos.py")]


def test_related_notes_show_kind_and_age(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    assert subprocess.run(EOS + ["init", str(root), "--no-ai"], capture_output=True).returncode == 0
    notes.add_note(root, "finding", "Billing retries need the idempotency key", "Without it a retry charges twice.")
    text = brief.build(root, task="why do billing retries charge twice", task_only=True)
    assert "Billing retries need the idempotency key  [finding, " in text
    assert brief.note_label(notes.load_notes(root)[0]) == "[finding, today]"
