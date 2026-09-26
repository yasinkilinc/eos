"""`eos brief --resume` (2.x roadmap M7): what one session left open, in one block."""
import subprocess
import sys
from pathlib import Path

from core import brief, executions

EOS = [sys.executable, str(Path(__file__).resolve().parents[1] / "core" / "eos.py")]


def _project(tmp_path, monkeypatch):
    monkeypatch.setenv("EOS_STATE_DIR", str(tmp_path / "state"))
    root = tmp_path / "proj"
    root.mkdir()
    assert subprocess.run(EOS + ["init", str(root), "--no-ai"], capture_output=True).returncode == 0
    return root


def test_resume_names_the_sessions_open_runs_failures_and_held_work(tmp_path, monkeypatch):
    root = _project(tmp_path, monkeypatch)
    mine = executions.start(root, "Deploy billing to staging", session="me")
    executions.event(root, mine.id, kind="ran", tool="build", exit_code=0)
    failed = executions.start(root, "Rotate the api key", session="me")
    executions.finish(root, failed.id, outcome="failed", lesson="the vault path moved to secrets/v2")
    executions.start(root, "Someone else's run", session="other")
    added = subprocess.run(EOS + ["work", "add", str(root), "--title", "Billing retry story", "--claim",
                                  "--session", "me"], capture_output=True, text=True)
    assert added.returncode == 0, added.stderr

    text = brief.resume(root, "me")
    assert "Deploy billing to staging" in text and "ran build" in text
    assert "Rotate the api key" in text and "vault path moved" in text
    assert "Billing retry story" in text
    assert "Someone else's run" not in text


def test_resume_says_so_when_nothing_is_open(tmp_path, monkeypatch):
    root = _project(tmp_path, monkeypatch)
    assert "Nothing open" in brief.resume(root, "nobody")
    done = subprocess.run(EOS + ["brief", str(root), "--resume", "--session", "nobody"],
                          capture_output=True, text=True)
    assert done.returncode == 0 and "Nothing open" in done.stdout
