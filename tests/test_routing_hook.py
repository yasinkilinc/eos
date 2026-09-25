"""The optional subagent-model hook (ADR-025, M9): opt-in, polite, and silent on failure.

It is the one surface where a routing decision changes what the agent does,
so the properties tested are the ones that keep that safe: an explicit model
is never overridden, a model the harness cannot take is never written, any
failure lets the call through untouched, and installing or removing it never
disturbs another hook.
"""
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

from core.ai import writer

REPO = Path(__file__).resolve().parents[1]
EOS = [sys.executable, str(REPO / "core" / "eos.py")]


def _hook_script(tmp_path) -> Path:
    root = tmp_path / "proj"
    root.mkdir(parents=True, exist_ok=True)
    writer.write_all(root, "9.9.9", route_hook=True)
    return root / writer.ROUTE_HOOK_FILE


def _fake_eos(tmp_path, answer: str, code: int = 0) -> Path:
    """An `eos` on PATH that answers `route --json` with a fixed body."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    script = bin_dir / "eos"
    script.write_text(f"#!/bin/sh\ncat <<'EOF'\n{answer}\nEOF\nexit {code}\n", encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return bin_dir


def _call(hook: Path, payload: dict, bin_dir: Path | None):
    env = dict(os.environ)
    env["PATH"] = (str(bin_dir) + os.pathsep if bin_dir else "") + "/usr/bin:/bin"
    return subprocess.run([sys.executable, str(hook)], input=json.dumps(payload),
                          capture_output=True, text=True, env=env)


def _payload(tmp_path, **tool_input):
    return {"tool_name": "Agent", "session_id": "s1", "cwd": str(tmp_path / "proj"),
            "tool_input": {"description": "d", "prompt": "fix the typo in the readme", **tool_input}}


def test_an_unset_model_is_filled_from_the_decision(tmp_path):
    hook = _hook_script(tmp_path)
    done = _call(hook, _payload(tmp_path), _fake_eos(tmp_path, '{"model": "haiku", "effort": "low"}'))

    assert done.returncode == 0 and done.stderr == ""
    out = json.loads(done.stdout)["hookSpecificOutput"]
    assert out["hookEventName"] == "PreToolUse"
    assert out["updatedInput"] == {"model": "haiku"}, "only the changed field; the harness merges"


def test_the_older_tool_name_is_handled_too(tmp_path):
    hook = _hook_script(tmp_path)
    payload = _payload(tmp_path)
    payload["tool_name"] = "Task"
    done = _call(hook, payload, _fake_eos(tmp_path, '{"model": "sonnet"}'))
    assert json.loads(done.stdout)["hookSpecificOutput"]["updatedInput"] == {"model": "sonnet"}


def test_an_explicit_model_is_left_alone(tmp_path):
    hook = _hook_script(tmp_path)
    done = _call(hook, _payload(tmp_path, model="opus"), _fake_eos(tmp_path, '{"model": "haiku"}'))
    assert done.returncode == 0 and done.stdout == ""


def test_a_model_the_harness_cannot_take_is_not_written(tmp_path):
    hook = _hook_script(tmp_path)
    done = _call(hook, _payload(tmp_path), _fake_eos(tmp_path, '{"model": "local-coder"}'))
    assert done.returncode == 0 and done.stdout == ""


def test_other_tools_are_ignored(tmp_path):
    hook = _hook_script(tmp_path)
    payload = _payload(tmp_path)
    payload["tool_name"] = "Bash"
    assert _call(hook, payload, _fake_eos(tmp_path, '{"model": "haiku"}')).stdout == ""


def test_a_failing_eos_lets_the_call_through_silently(tmp_path):
    hook = _hook_script(tmp_path)
    for bin_dir in (_fake_eos(tmp_path, "boom", code=2), _fake_eos(tmp_path, "not json"), None):
        done = _call(hook, _payload(tmp_path), bin_dir)
        assert done.returncode == 0 and done.stdout == "" and done.stderr == ""


def test_garbage_on_stdin_is_silent(tmp_path):
    hook = _hook_script(tmp_path)
    done = subprocess.run([sys.executable, str(hook)], input="{not json", capture_output=True, text=True)
    assert done.returncode == 0 and done.stdout == "" and done.stderr == ""


def test_the_hook_against_the_real_engine(tmp_path):
    """No stub: the hook finds the project's deployed runtime and routes for real."""
    root = tmp_path / "proj"
    root.mkdir()
    assert subprocess.run(EOS + ["init", str(root)], capture_output=True, text=True).returncode == 0
    writer.write_all(root, "9.9.9", route_hook=True)
    done = _call(root / writer.ROUTE_HOOK_FILE, _payload(tmp_path), None)
    assert json.loads(done.stdout)["hookSpecificOutput"]["updatedInput"] == {"model": "haiku"}


def _settings(root: Path) -> dict:
    return json.loads((root / ".claude" / "settings.json").read_text(encoding="utf-8"))


def test_the_entry_appears_only_with_the_flag_and_leaves_foreign_hooks_alone(tmp_path):
    root = tmp_path / "proj"
    (root / ".claude").mkdir(parents=True)
    foreign = {"matcher": "Bash", "hooks": [{"type": "command", "command": "echo mine"}]}
    (root / ".claude" / "settings.json").write_text(
        json.dumps({"hooks": {"PreToolUse": [foreign]}}), encoding="utf-8")

    writer.write_all(root, "9.9.9")
    assert _settings(root)["hooks"]["PreToolUse"] == [foreign]
    assert not (root / writer.ROUTE_HOOK_FILE).exists()

    writer.write_all(root, "9.9.9", route_hook=True)
    writer.write_all(root, "9.9.9", route_hook=True)
    entries = _settings(root)["hooks"]["PreToolUse"]
    assert entries[0] == foreign
    ours = [e for e in entries if writer.ROUTE_HOOK_FILE in e["hooks"][0]["command"]]
    assert len(ours) == 1 and ours[0]["matcher"] == writer.ROUTE_HOOK_MATCHER

    writer.write_all(root, "9.9.9")
    assert _settings(root)["hooks"]["PreToolUse"] == [foreign]
    assert not (root / writer.ROUTE_HOOK_FILE).exists()


def test_turning_the_flag_off_removes_an_empty_event(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    writer.write_all(root, "9.9.9", route_hook=True)
    writer.write_all(root, "9.9.9")
    assert "PreToolUse" not in _settings(root)["hooks"]


def test_ai_update_reads_the_flag_from_config(tmp_path):
    from core import eos as eos_cli

    root = tmp_path / "proj"
    root.mkdir()
    assert eos_cli.main(["init", str(root)]) == 0
    assert not (root / writer.ROUTE_HOOK_FILE).exists()
    config = root / ".eos" / "config.toml"
    config.write_text(config.read_text(encoding="utf-8") + "\n[model_routing]\nhook = true\n",
                      encoding="utf-8")
    assert eos_cli.main(["ai", "update", "--path", str(root)]) == 0
    assert (root / writer.ROUTE_HOOK_FILE).exists()
