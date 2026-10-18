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


def test_a_lesson_without_execution_or_evidence_is_marked_in_its_label(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    assert subprocess.run(EOS + ["init", str(root), "--no-ai"], capture_output=True).returncode == 0
    notes.add_note(root, kind="lesson", title="Registry was down",
                   body="## What went wrong\nRegistry down.\n\n## What was learned\ny\n\n## Next time\nz\n")
    notes.add_note(root, kind="lesson", title="Build broke on main",
                   body="## What went wrong\nBuild broke.\n\n## What was learned\ny\n\n## Next time\nz\n",
                   evidence="automation/jenkins.sh log build-42")

    by_title = {n.title: n for n in notes.load_notes(root)}
    assert brief.note_label(by_title["Registry was down"]) == "[lesson, today] [no evidence]"
    assert brief.note_label(by_title["Build broke on main"]) == "[lesson, today]"
