"""The prompt hook stays silent on conversation (2.x roadmap F1).

"ok, continue" put a note titled "... Save & Continue ..." in front of a session
that asked for nothing. A prompt made only of conversational filler gets no task
brief; a real task that uses one of those words still matches on every word.
"""
import subprocess
import sys
from pathlib import Path

from core import brief

EOS = [sys.executable, str(Path(__file__).resolve().parents[1] / "core" / "eos.py")]


def _project(tmp_path, config=""):
    root = tmp_path / "proj"
    root.mkdir()
    assert subprocess.run(EOS + ["init", str(root), "--no-ai"], capture_output=True).returncode == 0
    if config:
        path = root / ".eos" / "config.toml"
        path.write_text(path.read_text(encoding="utf-8") + "\n" + config, encoding="utf-8")
    added = subprocess.run(EOS + ["note", "add", str(root), "--kind", "finding",
                                  "--title", "Save and Continue stays disabled on the payment form",
                                  "--body", "The button waits for a card token."], capture_output=True, text=True)
    assert added.returncode == 0, added.stderr
    return root


def test_conversation_gets_no_task_brief(tmp_path):
    root = _project(tmp_path)
    for chat in ("ok, continue", "Okay, please continue", "thanks, go ahead", "yes"):
        assert brief.build(root, task=chat, task_only=True) == "", chat


def test_a_real_task_still_matches_on_the_filler_word(tmp_path):
    root = _project(tmp_path)
    text = brief.build(root, task="why is Save and Continue disabled on the payment form", task_only=True)
    assert "Save and Continue stays disabled" in text


def test_a_project_adds_the_filler_of_its_own_language(tmp_path):
    root = _project(tmp_path, '[brief]\nfiller = ["tamam", "devam", "Continue"]\n')
    assert brief.build(root, task="tamam devam continue", task_only=True) == ""
    assert brief.build(root, task="continue", task_only=False) != ""     # asked for explicitly
