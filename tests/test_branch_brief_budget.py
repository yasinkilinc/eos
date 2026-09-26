"""The session-start brief has a budget (2.x roadmap F1: branch brief <= 800 tokens).

Measured on the host: 13 work items, four blocked on the same 300-character
reason repeated in full, open runs with whole session ids -- about 1,045 tokens.
"""
import subprocess
import sys
from pathlib import Path

from core import brief, executions

EOS = [sys.executable, str(Path(__file__).resolve().parents[1] / "core" / "eos.py")]
REASON = ("Prerequisite not in place: a sales channel must be defined in the identity service, "
          "probably a new user as well, and the channel configured wherever the products need it; "
          "all of these stories are built together after that, per the decision recorded last week.")


def _eos(*args):
    done = subprocess.run(EOS + list(args), capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    return done.stdout


def _crowded(tmp_path, monkeypatch):
    monkeypatch.setenv("EOS_STATE_DIR", str(tmp_path / "state"))
    root = tmp_path / "proj"
    root.mkdir()
    _eos("init", str(root), "--no-ai")
    for n in range(8):
        out = _eos("work", "add", str(root), "--title", f"Story {n}: a digital channel flow with a long title")
        item = out.split()[0] if out.split() else ""
        if n < 4 and item:
            _eos("work", "block", str(root), item, "--reason", REASON)
    for n in range(3):
        executions.start(root, f"run {n} with a title", session=f"{n}" * 36)
    return root


def test_the_branch_brief_stays_within_its_budget(tmp_path, monkeypatch):
    text = brief.build(_crowded(tmp_path, monkeypatch))
    assert len(text) <= brief.BRANCH_BUDGET * brief.CHARS_PER_TOKEN, len(text)
    assert "IN FLIGHT" in text and "RUNS OPEN" in text


def test_a_repeated_reason_is_said_once_and_details_are_clipped(tmp_path, monkeypatch):
    text = brief.build(_crowded(tmp_path, monkeypatch))
    assert text.count(REASON[:60]) <= 1
    assert "(same as above)" in text
    assert all(len(line) <= brief.DETAIL_CHARS + 20 for line in text.splitlines() if "blocked on:" in line)
    assert "1" * 36 not in text
