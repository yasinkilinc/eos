"""The brief reads a note through the project's synonym groups, as search does."""
import subprocess
import sys
from pathlib import Path

from core import brief, notes

EOS = [sys.executable, str(Path(__file__).resolve().parents[1] / "core" / "eos.py")]
SYNONYMS = '[notes]\nsynonyms = [["bildirim", "notification"]]\n'


def _project(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    assert subprocess.run(EOS + ["init", str(root), "--no-ai"], capture_output=True).returncode == 0
    path = root / ".eos" / "config.toml"
    path.write_text(path.read_text(encoding="utf-8") + "\n" + SYNONYMS, encoding="utf-8")
    return root


def _add(root, kind, title, body, tags):
    done = subprocess.run(EOS + ["note", "add", str(root), "--kind", kind, "--title", title,
                                 "--tags", tags, "--body", body], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


def test_best_procedure_matches_through_a_synonym(tmp_path):
    root = _project(tmp_path)
    _add(root, "procedure", "Send a notification", "1. Do it.\n\n## Steps\n\n1. Send it.", "notify,test")
    _add(root, "procedure", "Rotate the keys", "1. Do it.\n\n## Steps\n\n1. Rotate.", "keys")
    found = brief.best_procedure(root, "bildirim gönder test")
    assert found is not None and found.title == "Send a notification"


def test_related_lists_a_note_reachable_only_through_a_synonym(tmp_path):
    root = _project(tmp_path)
    _add(root, "finding", "Notification retry is capped", "The retry stops after three tries.", "queue")
    _add(root, "finding", "Unrelated cache note", "Nothing here.", "cache")
    sections, _found = brief._task_sections(root, "bildirim neden duruyor")
    text = "\n".join("\n".join(section) for section in sections)
    assert "Notification retry is capped" in text
