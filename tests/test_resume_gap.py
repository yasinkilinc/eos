"""`[hooks] resume_gap_minutes`: resuming a large session after a long gap warns
that the prompt cache is cold (SessionStart, source `resume`)."""
import datetime
import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

from core import hooks

REPO = Path(__file__).resolve().parents[1]
EOS = [sys.executable, str(REPO / "core" / "eos.py")]


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    root.mkdir()
    assert subprocess.run(EOS + ["init", str(root), "--no-ai"], capture_output=True).returncode == 0
    monkeypatch.setenv("EOS_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("EOS_HOOK_STATE_DIR", raising=False)
    return root


def _config(project, text):
    config = project / ".eos" / "config.toml"
    config.write_text(config.read_text(encoding="utf-8") + "\n[hooks]\n" + text, encoding="utf-8")


def _transcript(tmp_path, minutes_ago, tokens, filler=0):
    stamp = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=minutes_ago))
    entry = {"type": "assistant", "timestamp": stamp.isoformat().replace("+00:00", "Z"),
             "message": {"usage": {"input_tokens": 10, "cache_read_input_tokens": tokens - 10,
                                   "cache_creation_input_tokens": 0}}}
    path = tmp_path / "t.jsonl"
    path.write_text(("x" * 1000 + "\n") * filler + json.dumps(entry) + "\n", encoding="utf-8")
    return path


def _start(project, monkeypatch, capsys, transcript, source="resume"):
    payload = {"session_id": "s1", "cwd": str(project), "source": source, "transcript_path": str(transcript)}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    assert hooks.main(["session-start"]) == 0
    return capsys.readouterr().out


def test_long_gap_and_large_context_warns(project, tmp_path, monkeypatch, capsys):
    _config(project, "resume_gap_minutes = 60\n")
    out = _start(project, monkeypatch, capsys, _transcript(tmp_path, 90, 180_000, filler=3000))
    assert "RESUME  1h30m since the last turn; the cache is cold and the next call rewrites 180k tokens." in out
    assert "/compact" in out


def test_short_gap_is_quiet(project, tmp_path, monkeypatch, capsys):
    _config(project, "resume_gap_minutes = 60\n")
    assert "RESUME" not in _start(project, monkeypatch, capsys, _transcript(tmp_path, 10, 180_000))


def test_small_context_is_quiet(project, tmp_path, monkeypatch, capsys):
    _config(project, "resume_gap_minutes = 60\n")
    assert "RESUME" not in _start(project, monkeypatch, capsys, _transcript(tmp_path, 90, 20_000))


def test_off_by_default(project, tmp_path, monkeypatch, capsys):
    assert "RESUME" not in _start(project, monkeypatch, capsys, _transcript(tmp_path, 900, 180_000))


def test_only_on_resume(project, tmp_path, monkeypatch, capsys):
    _config(project, "resume_gap_minutes = 60\n")
    out = _start(project, monkeypatch, capsys, _transcript(tmp_path, 90, 180_000), source="startup")
    assert "RESUME" not in out


def test_context_threshold_is_configurable(project, tmp_path, monkeypatch, capsys):
    _config(project, "resume_gap_minutes = 60\nresume_context_tokens = 10000\n")
    assert "RESUME" in _start(project, monkeypatch, capsys, _transcript(tmp_path, 90, 20_000))


@pytest.mark.parametrize("content", [None, "", "not json\n{garbled", '{"type":"assistant"}\n'])
def test_unreadable_transcript_is_quiet(project, tmp_path, monkeypatch, capsys, content):
    _config(project, "resume_gap_minutes = 60\n")
    path = tmp_path / "bad.jsonl"
    if content is not None:
        path.write_text(content, encoding="utf-8")
    assert "RESUME" not in _start(project, monkeypatch, capsys, path)


@pytest.mark.parametrize("minutes,text", [(45, "45m"), (90, "1h30m"), (2 * 1440 + 3 * 60, "2d3h")])
def test_gap_text(minutes, text):
    assert hooks._gap_text(minutes) == text
