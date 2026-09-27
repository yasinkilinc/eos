"""Every hook event exits 0, writes nothing to stderr and prints valid JSON or
nothing, whatever it is handed (ADR-021's first rule). Measured with 420
malformed payloads before this sample was kept."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from core import hooks

EOS = [sys.executable, str(Path(__file__).resolve().parents[1] / "core" / "eos.py")]
BODIES = ["", "not json", "[1, 2]", "null", "{" * 3000,
          json.dumps({"session_id": 7, "cwd": None, "tool_name": ["Bash"], "tool_input": "make test",
                      "last_assistant_message": {"a": 1}, "stop_hook_active": "yes"}),
          json.dumps({"session_id": "fz", "tool_name": "Read", "tool_input": {"file_path": "/nonexistent/x.py"},
                      "prompt": "é" * 5000, "agent_id": "", "transcript_path": "/nonexistent.jsonl"})]


@pytest.mark.parametrize("event", sorted(hooks.HANDLERS))
def test_a_hook_never_fails_on_what_it_is_handed(event, tmp_path):
    assert subprocess.run(EOS + ["init", str(tmp_path), "--no-ai"], capture_output=True).returncode == 0
    env = {**os.environ, "EOS_STATE_DIR": str(tmp_path / "state"), "CLAUDE_CODE_SESSION_ID": "fz"}
    for body in BODIES:
        done = subprocess.run(EOS + ["hook", event], input=body, capture_output=True, text=True,
                              cwd=tmp_path, env=env, timeout=60)
        assert done.returncode == 0 and done.stderr == "", (event, body[:60], done.stderr[-300:])
        if done.stdout.strip().startswith("{"):
            json.loads(done.stdout)
