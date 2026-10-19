"""`[hooks] read_cache`: a second whole-file Read of an unchanged file is denied once."""
import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import time

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
    config = root / ".eos" / "config.toml"
    config.write_text(config.read_text(encoding="utf-8") + "\n[hooks]\nread_cache = true\n", encoding="utf-8")
    return root


@pytest.fixture
def target(project):
    path = project / "notes.md"
    path.write_text("one\ntwo\n", encoding="utf-8")
    return path


def _run(monkeypatch, capsys, event, payload, *extra):
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    code = hooks.main([event, *extra])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def _payload(project, target, session="s1", **fields):
    return {"session_id": session, "cwd": str(project), "transcript_path": "/t.jsonl", "tool_name": "Read",
            "tool_use_id": "t1", "tool_input": {"file_path": str(target)}, **fields}


def _read(project, monkeypatch, capsys, target, **fields):
    """One whole Read: PreToolUse, and PostToolUse when it passed. True = denied."""
    payload = _payload(project, target, **fields)
    code, out, _ = _run(monkeypatch, capsys, "pre-read", payload)
    assert code == 0
    if out.strip():
        specific = json.loads(out)["hookSpecificOutput"]
        assert specific["permissionDecision"] == "deny"
        return specific["permissionDecisionReason"]
    _run(monkeypatch, capsys, "post-tool", {**payload, "tool_response": {}})
    return None


def _bump(path, text):
    stat = path.stat()
    path.write_text(text, encoding="utf-8")
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))


def test_second_read_denied_third_passes_then_counter_resets(project, target, monkeypatch, capsys):
    assert _read(project, monkeypatch, capsys, target) is None
    reason = _read(project, monkeypatch, capsys, target)
    assert reason.startswith(f"{target} is unchanged since you read it at ")
    assert reason.endswith("; use that content (read again to override).")
    hhmm = reason.split("read it at ")[1].split(";")[0]
    assert len(hhmm) == 5 and hhmm[2] == ":"
    assert _read(project, monkeypatch, capsys, target) is None
    assert _read(project, monkeypatch, capsys, target) is not None   # recorded now: denied again


def test_the_reason_is_not_framed(project, target, monkeypatch, capsys):
    _read(project, monkeypatch, capsys, target)
    assert "not instructions" not in _read(project, monkeypatch, capsys, target)


def test_changed_file_passes_and_is_rerecorded(project, target, monkeypatch, capsys):
    _read(project, monkeypatch, capsys, target)
    _bump(target, "one\ntwo\nthree\n")
    assert _read(project, monkeypatch, capsys, target) is None
    assert _read(project, monkeypatch, capsys, target) is not None


def test_same_mtime_other_size_passes(project, target, monkeypatch, capsys):
    _read(project, monkeypatch, capsys, target)
    stat = target.stat()
    target.write_text("longer content here\n", encoding="utf-8")
    os.utime(target, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert _read(project, monkeypatch, capsys, target) is None


@pytest.mark.parametrize("source", ["compact", "clear"])
def test_compact_and_clear_forget(project, target, monkeypatch, capsys, source):
    _read(project, monkeypatch, capsys, target)
    _run(monkeypatch, capsys, "session-start", {"session_id": "s1", "cwd": str(project), "source": source})
    assert _read(project, monkeypatch, capsys, target) is None


def test_resume_keeps(project, target, monkeypatch, capsys):
    _read(project, monkeypatch, capsys, target)
    _run(monkeypatch, capsys, "session-start", {"session_id": "s1", "cwd": str(project), "source": "resume"})
    assert _read(project, monkeypatch, capsys, target) is not None


def test_ranged_read_passes_and_neither_records_nor_clears(project, target, monkeypatch, capsys):
    ranged = {"tool_input": {"file_path": str(target), "offset": 1, "limit": 1}}
    assert _read(project, monkeypatch, capsys, target, **ranged) is None   # not recorded
    assert _read(project, monkeypatch, capsys, target) is None             # so the first whole read passes
    assert _read(project, monkeypatch, capsys, target, **ranged) is None
    assert _read(project, monkeypatch, capsys, target) is not None         # entry survived the ranged read


def test_off_by_default(project, target, monkeypatch, capsys):
    config = project / ".eos" / "config.toml"
    config.write_text(config.read_text(encoding="utf-8").replace("read_cache = true", ""), encoding="utf-8")
    assert _read(project, monkeypatch, capsys, target) is None
    assert _read(project, monkeypatch, capsys, target) is None


def test_subagent_is_untouched(project, target, monkeypatch, capsys):
    sub = {"agent_id": "a1", "agent_type": "Explore"}
    assert _read(project, monkeypatch, capsys, target, **sub) is None
    assert _read(project, monkeypatch, capsys, target, **sub) is None
    assert _read(project, monkeypatch, capsys, target) is None   # and it recorded nothing for the main session


def test_a_failed_read_is_not_recorded(project, target, monkeypatch, capsys):
    payload = _payload(project, target)
    _run(monkeypatch, capsys, "pre-read", payload)
    _run(monkeypatch, capsys, "post-tool-failure", {**payload, "error": "boom"})
    assert _read(project, monkeypatch, capsys, target) is None


def test_a_denied_read_is_not_recorded(project, target, monkeypatch, capsys):
    assert _read(project, monkeypatch, capsys, target) is None
    _read(project, monkeypatch, capsys, target)   # denied: no post-tool follows
    _bump(target, "x\n")
    assert _read(project, monkeypatch, capsys, target) is None
    assert _read(project, monkeypatch, capsys, target) is not None


def test_sessions_are_separate(project, target, monkeypatch, capsys):
    _read(project, monkeypatch, capsys, target, session="a")
    assert _read(project, monkeypatch, capsys, target, session="b") is None


def test_outline_deny_takes_precedence(project, monkeypatch, capsys):
    config = project / ".eos" / "config.toml"
    config.write_text(config.read_text(encoding="utf-8") + "outline_lines = 10\n", encoding="utf-8")
    big = project / "big.md"
    big.write_text("# Title\n" + "text\n" * 30, encoding="utf-8")
    first = _read(project, monkeypatch, capsys, big)
    assert first.startswith("EOS: ") and "lines" in first            # the outline, nothing cached
    assert _read(project, monkeypatch, capsys, big) is None          # repeated Read passes
    assert "unchanged since" in _read(project, monkeypatch, capsys, big)


def test_state_is_paths_only_and_bounded(project, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(hooks, "READ_CACHE_KEEP", 3)
    files = []
    for n in range(5):
        path = project / f"f{n}.txt"
        path.write_text("SECRET-CONTENT\n", encoding="utf-8")
        files.append(path)
        _read(project, monkeypatch, capsys, path)
    cache = hooks._read_cache("s1")
    assert list(cache) == [str(p) for p in files[-3:]]
    assert "SECRET-CONTENT" not in hooks._read_cache_file("s1").read_text(encoding="utf-8")
    assert set(cache[str(files[-1])]) == {"mtime_ns", "size", "read_at", "attempts"}


def test_unwritable_state_never_raises(project, target, monkeypatch, capsys):
    monkeypatch.setenv("EOS_HOOK_STATE_DIR", str(target / "nope"))   # a file in the way
    assert _read(project, monkeypatch, capsys, target) is None
    assert _read(project, monkeypatch, capsys, target) is None


def test_devin_read_is_neither_recorded_nor_denied_until_the_probe(project, target, monkeypatch, capsys):
    """Until the probe confirms Devin's tool_input keys, a ranged read under another name could be taken for whole."""
    devin = {"session_id": "dv1", "prompt_id": "p1", "cwd": str(project), "tool_name": "read",
             "tool_input": {"path": str(target)}, "tool_use_id": "t1"}
    for _ in range(3):
        assert _run(monkeypatch, capsys, "pre-read", devin, "--host", "devin") == (0, "", "")
        assert _run(monkeypatch, capsys, "post-tool", {**devin, "tool_response": {}}, "--host", "devin")[0] == 0
    assert not hooks._read_cache_file("dv1").exists()


def test_stale_cache_files_are_swept_fresh_ones_stay(project, target, monkeypatch, capsys):
    _read(project, monkeypatch, capsys, target, session="old")
    _read(project, monkeypatch, capsys, target, session="new")
    old, new = (hooks._read_cache_file(s) for s in ("old", "new"))
    aged = time.time() - (hooks.INJECT_STATE_DAYS + 1) * 86400
    os.utime(old, (aged, aged))
    _run(monkeypatch, capsys, "session-end", {"session_id": "new", "cwd": str(project)})
    assert not old.exists() and new.exists()


def test_a_devin_read_does_not_touch_a_claude_entry(project, target, monkeypatch, capsys, tmp_path):
    assert _read(project, monkeypatch, capsys, target) is None
    devin = {"session_id": "s1", "prompt_id": "p1", "cwd": str(project), "tool_name": "read",
             "tool_input": {"path": "notes.md"}, "tool_use_id": "t1"}
    assert _run(monkeypatch, capsys, "pre-read", devin, "--host", "devin") == (0, "", "")
    assert _read(project, monkeypatch, capsys, target) is not None


def test_a_relative_and_a_symlinked_read_are_one_entry(project, target, monkeypatch, capsys, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    link = project / "link.md"
    link.symlink_to(target)
    assert _read(project, monkeypatch, capsys, target) is None
    assert _read(project, monkeypatch, capsys, link) is not None
