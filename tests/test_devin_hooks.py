"""Devin CLI hook parity: the same handlers serve Devin's payloads (tool names
`exec`/`read`/..., input keys `cmd`/`path`/`agent`, `prompt_id`, exit 2 to block)."""
import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

from core import executions, hooks

REPO = Path(__file__).resolve().parents[1]
EOS = [sys.executable, str(REPO / "core" / "eos.py")]
REGISTRY = """
[[capability]]
name = "tracker"
run = "scripts/tracker.sh"
does = "issue|search <KEY>"
words = ["tracker", "ticket"]
hint = ['curl\\s[^|;&]*tracker\\.example']
"""


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    root.mkdir()
    assert subprocess.run(EOS + ["init", str(root), "--no-ai"], capture_output=True).returncode == 0
    knowledge = root / ".eos" / "knowledge"
    knowledge.mkdir(parents=True, exist_ok=True)
    (knowledge / "capabilities.toml").write_text(REGISTRY, encoding="utf-8")
    monkeypatch.setenv("EOS_STATE_DIR", str(tmp_path / "state"))
    for name in ("EOS_HOOK_STATE_DIR", "EOS_EXECUTION", "EOS_EXECUTION_LEDGER", "EOS_SESSION",
                 "CLAUDE_CODE_SESSION_ID"):
        monkeypatch.delenv(name, raising=False)
    return root


def _run(monkeypatch, capsys, event, payload, *extra):
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    code = hooks.main([event, *extra])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def _devin(root, **fields):
    return {"session_id": "dv1", "prompt_id": "p1", "cwd": str(root), **fields}


def _config(project, text):
    config = project / ".eos" / "config.toml"
    config.write_text(config.read_text(encoding="utf-8") + "\n[hooks]\n" + text, encoding="utf-8")


def test_normalize_maps_devin_tools_to_the_canonical_names():
    names = {"exec": "Bash", "read": "Read", "write": "Write", "edit": "Edit", "apply_patch": "Edit",
             "grep": "Grep", "glob": "Glob", "run_subagent": "Agent", "webfetch": "WebFetch", "skill": "Skill"}
    for raw, canonical in names.items():
        hook = hooks.normalize({"tool_name": raw})
        assert (hook.tool, hook.raw_tool) == (canonical, raw)
    claude = hooks.normalize({"tool_name": "Bash"})
    assert (claude.tool, claude.raw_tool) == ("Bash", "Bash")


def test_normalize_reads_devin_input_keys_and_detects_the_host():
    devin = hooks.normalize({"session_id": "s", "prompt_id": "p", "tool_name": "read",
                             "tool_input": {"path": "/a/b.py"}})
    assert devin.host == "devin" and devin.target_path == "/a/b.py"
    claude = hooks.normalize({"session_id": "s", "prompt_id": "p", "transcript_path": "/t.jsonl",
                              "tool_name": "Read", "tool_input": {"file_path": "/a/b.py"}})
    assert claude.host == "" and claude.target_path == "/a/b.py"
    agent = hooks.normalize({"tool_name": "run_subagent", "tool_input": {"agent": "explore"}})
    assert agent.subagent_kind == "explore"


def test_devin_exec_cmd_reaches_the_wrapper_bypass_hint(project, monkeypatch, capsys):
    executions.start(project, "a run", session="dv1")
    code, out, err = _run(monkeypatch, capsys, "post-tool", _devin(
        project, tool_name="exec", tool_input={"cmd": "curl -s https://tracker.example/api/1"}, tool_use_id="t1"))
    assert code == 0 and err == ""
    assert "scripts/tracker.sh" in json.loads(out)["hookSpecificOutput"]["additionalContext"]
    events = executions.load(project)[-1].events
    assert [(e.tool, e.status, e.agent) for e in events] == [("curl", "bypass", "devin")]


def test_devin_read_reaches_pre_read_and_a_deny_is_stderr_with_exit_2(project, monkeypatch, capsys):
    _config(project, "outline_lines = 10\n")
    target = project / "notes.md"
    target.write_text("# Title\n" + "text\n" * 30, encoding="utf-8")
    read = _devin(project, tool_name="read", tool_input={"path": str(target)})
    code, out, err = _run(monkeypatch, capsys, "pre-read", read, "--host", "devin")
    assert (code, out) == (2, "")
    assert err.startswith("EOS: ") and not err.startswith("EOS: EOS") and "1: # Title" in err \
        and "permissionDecision" not in err
    assert _run(monkeypatch, capsys, "pre-read", read, "--host", "devin") == (0, "", "")   # asking again reads it


def test_the_host_is_detected_from_prompt_id_without_the_flag(project, monkeypatch, capsys):
    _config(project, "outline_lines = 10\n")
    target = project / "notes.md"
    target.write_text("# Title\n" + "text\n" * 30, encoding="utf-8")
    code, out, err = _run(monkeypatch, capsys, "pre-read",
                          _devin(project, tool_name="read", tool_input={"path": str(target)}))
    assert (code, out) == (2, "") and "# Title" in err


def test_a_claude_deny_is_unchanged(project, monkeypatch, capsys):
    _config(project, "outline_lines = 10\n")
    target = project / "notes.md"
    target.write_text("# Title\n" + "text\n" * 30, encoding="utf-8")
    code, out, err = _run(monkeypatch, capsys, "pre-read", {
        "session_id": "s1", "cwd": str(project), "transcript_path": "/t.jsonl",
        "tool_name": "Read", "tool_input": {"file_path": str(target)}})
    assert code == 0 and err == ""
    assert json.loads(out)["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_run_subagent_reaches_the_subagent_path_and_keeps_devins_keys(project, monkeypatch, capsys):
    _config(project, 'subagent_suffix = "Report in Turkish."\n')
    code, out, err = _run(monkeypatch, capsys, "pre-agent", _devin(
        project, tool_name="run_subagent", tool_input={"agent": "explore", "prompt": "find it"}))
    assert code == 0
    updated = json.loads(out)["hookSpecificOutput"]["updatedInput"]
    assert updated == {"agent": "explore", "prompt": "find it\n\nReport in Turkish."}


def test_devin_session_id_records_on_the_run_with_agent_devin(project, monkeypatch, capsys):
    executions.start(project, "a run", session="dv1")
    target = project / "a.py"
    target.write_text("x = 1\n", encoding="utf-8")
    code, out, err = _run(monkeypatch, capsys, "post-tool", _devin(
        project, tool_name="edit", tool_input={"path": str(target)}, tool_use_id="t9"))
    assert code == 0
    events = executions.load(project)[-1].events
    assert [(e.kind, e.tool, e.ref, e.agent) for e in events] == [("changed", "edit", "a.py", "devin")]


def test_devin_exec_output_is_never_clipped(project, monkeypatch, capsys):
    _config(project, "bash_clip_chars = 100\n")
    code, out, err = _run(monkeypatch, capsys, "post-tool", _devin(
        project, tool_name="exec", tool_input={"cmd": "seq 1 5000"},
        tool_response={"stdout": "x\n" * 5000, "stderr": ""}))
    assert (code, out) == (0, "")


def _probe(project):
    path = project / ".eos" / "data" / "hook-probe.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def test_probe_is_off_by_default(project, monkeypatch, capsys):
    _run(monkeypatch, capsys, "post-tool", _devin(project, tool_name="exec", tool_input={"cmd": "ls"}))
    assert _probe(project) == []


def test_probe_writes_key_names_never_values(project, monkeypatch, capsys):
    _config(project, "probe = true\n")
    secret = "SECRET-VALUE-42"
    _run(monkeypatch, capsys, "post-tool", _devin(
        project, tool_name="exec", tool_input={"cmd": secret, "timeout": 5}, tool_use_id=secret))
    (line,) = _probe(project)
    assert line["event"] == "post-tool"
    assert {"session_id", "prompt_id", "cwd", "tool_name", "tool_input"} <= set(line["keys"])
    assert line["input_keys"] == ["cmd", "timeout"]
    assert secret not in (project / ".eos" / "data" / "hook-probe.jsonl").read_text(encoding="utf-8")


def test_a_probe_write_failure_never_raises(project, monkeypatch, capsys):
    _config(project, "probe = true\n")
    (project / ".eos" / "data").mkdir(parents=True, exist_ok=True)
    (project / ".eos" / "data" / "hook-probe.jsonl").mkdir()
    code, out, err = _run(monkeypatch, capsys, "post-tool", _devin(
        project, tool_name="exec", tool_input={"cmd": "ls"}))
    assert (code, err) == (0, "")


HOOK = REPO / "plugin" / "hooks" / "eos-hook"


def _entry(tmp_path, fake_body, *args):
    tmp_path.mkdir(exist_ok=True)
    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir()
    fake = bin_dir / "eos"
    fake.write_text("#!/bin/sh\necho \"$@\" > \"$FAKE_ARGS\"\n" + fake_body, encoding="utf-8")
    fake.chmod(0o755)
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "FAKE_ARGS": str(tmp_path / "args"), "PWD": str(tmp_path)}
    done = subprocess.run(["/bin/sh", str(HOOK), *args], input="{}", capture_output=True, text=True,
                          env=env, cwd=tmp_path)
    args_seen = (tmp_path / "args").read_text(encoding="utf-8") if (tmp_path / "args").exists() else ""
    return done, args_seen


def test_entry_lets_a_devin_deny_through(tmp_path):
    done, seen = _entry(tmp_path, 'echo "EOS: denied" >&2\nexit 2\n', "pre-read", "--host", "devin")
    assert (done.returncode, done.stderr) == (2, "EOS: denied\n")
    assert "--agent devin" in seen and "--host devin" in seen


def test_entry_ignores_a_missing_script_or_usage_error(tmp_path):
    done, _ = _entry(tmp_path, 'echo "python3: cannot open file" >&2\nexit 2\n', "pre-read", "--host", "devin")
    assert (done.returncode, done.stderr) == (0, "")
    done, _ = _entry(tmp_path / "u", 'echo "usage: eos ..." >&2\nexit 2\n', "pre-read", "--host", "devin")
    assert (done.returncode, done.stderr) == (0, "")


def test_entry_swallows_any_other_devin_failure(tmp_path):
    done, _ = _entry(tmp_path, 'echo boom >&2\nexit 1\n', "pre-read", "--host", "devin")
    assert (done.returncode, done.stderr) == (0, "")


def test_entry_without_host_is_todays_silent_exit_0(tmp_path):
    done, seen = _entry(tmp_path, 'echo "denied: x" >&2\nexit 2\n', "pre-read")
    assert (done.returncode, done.stderr) == (0, "")
    assert "--agent claude" in seen


def test_a_stop_block_under_devin_is_stderr_with_exit_2(project, monkeypatch, capsys):
    record = executions.start(project, "a run", session="dv1")
    code, out, err = _run(monkeypatch, capsys, "stop", _devin(project))
    assert (code, out) == (2, "")
    assert err.startswith("EOS: ") and not err.startswith("EOS: EOS") and record.id in err


def test_a_subagent_stop_block_under_devin_is_stderr_with_exit_2(project, monkeypatch, capsys):
    executions.start(project, "a run", session="dv1")
    source = project / "svc.py"
    source.write_text("x = 1\n", encoding="utf-8")
    code, out, err = _run(monkeypatch, capsys, "subagent-stop", _devin(
        project, agent_id="a1", agent_type="Explore", last_assistant_message=f"set at {source}:120."))
    assert (code, out) == (2, "")
    assert err.startswith("EOS: ") and "cannot be right" in err


def test_the_probe_log_is_bounded(project, monkeypatch, capsys):
    _config(project, "probe = true\n")
    log = project / ".eos" / "data" / "hook-probe.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text('{"event": "x"}\n' * 300_000, encoding="utf-8")
    assert log.stat().st_size > hooks.MAX_LOG_BYTES
    _run(monkeypatch, capsys, "post-tool", _devin(project, tool_name="exec", tool_input={"cmd": "ls"}))
    assert log.stat().st_size < hooks.MAX_LOG_BYTES


def test_devin_read_of_a_note_is_a_read_event(project, monkeypatch, capsys):
    store = project / ".eos" / "knowledge"
    store.mkdir(parents=True, exist_ok=True)
    note = store / "20260901-a-note.md"
    note.write_text("---\nkind: finding\ntitle: A note\ncreated: 2026-09-01\n---\n\nBody.\n", encoding="utf-8")
    executions.start(project, "a run", session="dv1")
    _run(monkeypatch, capsys, "post-tool", _devin(
        project, tool_name="read", tool_input={"path": str(note)}, tool_use_id="t1"))
    [event] = executions.load(project)[-1].events
    assert (event.kind, event.tool, event.ref, event.agent) == ("read", None, f"note:{note.name}", "devin")


def test_devin_read_reaches_the_post_batch_note_injection(project, monkeypatch, capsys):
    store = project / ".eos" / "knowledge"
    store.mkdir(parents=True, exist_ok=True)
    (project / "src").mkdir()
    target = project / "src" / "Foo.py"
    target.write_text("x = 1\n", encoding="utf-8")
    (store / "20260901-foo.md").write_text(
        "---\nkind: finding\ntitle: About Foo\ncreated: 2026-09-01\nscope:\n  - src/Foo.py\n---\n\nBody.\n",
        encoding="utf-8")

    def batch(host_payload, tool, key):
        call = {"tool_name": tool, "tool_input": {key: str(target)}}
        return _run(monkeypatch, capsys, "post-batch", {**host_payload, "tool_calls": [call]})

    devin = batch(_devin(project), "read", "path")
    claude = batch({"session_id": "c1", "cwd": str(project), "transcript_path": "/t.jsonl"}, "Read", "file_path")
    assert devin[0] == 0 and "About Foo" in json.loads(devin[1])["hookSpecificOutput"]["additionalContext"]
    assert "About Foo" in json.loads(claude[1])["hookSpecificOutput"]["additionalContext"]


def test_devin_run_subagent_is_never_routed_until_the_probe(project, monkeypatch, capsys):
    """Devin's `model` key is unconfirmed: the decision is recorded, nothing is written."""
    config = project / ".eos" / "config.toml"
    config.write_text(config.read_text(encoding="utf-8") + "\n[model_routing]\nhook = true\nhook_dry_run = false\n",
                      encoding="utf-8")
    executions.start(project, "a run", session="dv1")
    prompt = "design the service boundary for billing"
    code, out, _ = _run(monkeypatch, capsys, "pre-agent", _devin(
        project, tool_name="run_subagent", tool_use_id="r1", tool_input={"prompt": prompt}))
    assert code == 0 and out == ""
    assert any(e.kind == "decided" for e in executions.load(project)[-1].events)
    claude = {"session_id": "dv1", "cwd": str(project), "transcript_path": "/t.jsonl", "tool_name": "Agent",
              "tool_use_id": "r2", "tool_input": {"prompt": prompt}}
    _, out, _ = _run(monkeypatch, capsys, "pre-agent", claude)
    assert json.loads(out)["hookSpecificOutput"]["updatedInput"]["model"] in hooks.SUBAGENT_MODELS
