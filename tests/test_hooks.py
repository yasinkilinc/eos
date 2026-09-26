"""`eos hook <event>`: one entry point for every harness hook (ADR-026).

The payloads below are the shapes Claude Code 2.1.281 was recorded sending by
a probe of every event: `agent_id`/`agent_type` only inside a subagent, a
failed Bash call as its own event with `error: "Exit code N"`, `effort` only
where the model takes one. Every path must exit 0 and stay silent on failure.
"""
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
    monkeypatch.delenv("EOS_HOOK_STATE_DIR", raising=False)
    monkeypatch.delenv("EOS_EXECUTION", raising=False)
    monkeypatch.delenv("EOS_EXECUTION_LEDGER", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "s1")
    return root


def _hook(monkeypatch, capsys, event, payload, *extra):
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload) if isinstance(payload, dict) else payload))
    assert hooks.main([event, *extra]) == 0
    return capsys.readouterr()


def _payload(root, **fields):
    return {"session_id": "s1", "cwd": str(root), **fields}


def _open_run(root):
    return executions.start(root, "a run", session="s1")


def _events(root):
    return executions.load(root)[-1].events


def test_normalize_reads_claude_and_devin_field_names():
    claude = hooks.normalize({"session_id": "s", "tool_name": "Bash", "tool_input": {"command": "ls"},
                              "tool_use_id": "t1", "agent_id": "a", "agent_type": "Explore",
                              "effort": {"level": "low"}, "duration_ms": 12})
    assert (claude.command, claude.tool_use_id, claude.agent_type, claude.effort, claude.duration_ms) == \
        ("ls", "t1", "Explore", "low", 12)
    devin = hooks.normalize({"tool": "shell", "arguments": json.dumps({"cmd": "git status"})})
    assert devin.command == "git status" and devin.tool == "shell"


def test_a_raw_command_a_capability_covers_is_a_bypass_with_one_hint_per_session(project, monkeypatch, capsys):
    _open_run(project)
    first = _hook(monkeypatch, capsys, "post-tool", _payload(
        project, tool_name="Bash", tool_input={"command": "curl -s https://tracker.example/api/1"},
        tool_use_id="t1"))
    second = _hook(monkeypatch, capsys, "post-tool", _payload(
        project, tool_name="Bash", tool_input={"command": "curl https://tracker.example/api/2"},
        tool_use_id="t2"))

    context = json.loads(first.out)["hookSpecificOutput"]
    assert context["hookEventName"] == "PostToolUse"
    assert "scripts/tracker.sh" in context["additionalContext"]
    assert second.out == "" and first.err == "" and second.err == ""
    assert [(e.tool, e.status, e.source) for e in _events(project)] == \
        [("curl", "bypass", "hook"), ("curl", "bypass", "hook")]


def test_a_subagent_gets_its_own_hint(project, monkeypatch, capsys):
    raw = {"command": "curl https://tracker.example/x"}
    _hook(monkeypatch, capsys, "post-tool", _payload(project, tool_name="Bash", tool_input=raw, tool_use_id="t1"))
    inside = _hook(monkeypatch, capsys, "post-tool", _payload(
        project, tool_name="Bash", tool_input=raw, tool_use_id="t2", agent_id="a1", agent_type="Explore"))
    assert "tracker.sh" in inside.out


def test_the_same_call_reported_twice_is_one_event(project, monkeypatch, capsys):
    _open_run(project)
    payload = _payload(project, tool_name="Bash", tool_input={"command": "cd sub && git commit -m x && ls"},
                       tool_use_id="t1")
    _hook(monkeypatch, capsys, "post-tool", payload)
    _hook(monkeypatch, capsys, "post-tool", payload)
    assert [(e.tool, e.body, e.tool_use_id) for e in _events(project)] == [("git", "commit", "t1")]


def test_read_only_programs_and_registered_wrappers_are_not_recorded(project, monkeypatch, capsys):
    _open_run(project)
    for index, command in enumerate(("ls -la && cat x | grep y", "git status", "scripts/tracker.sh issue A-1")):
        _hook(monkeypatch, capsys, "post-tool", _payload(
            project, tool_name="Bash", tool_input={"command": command}, tool_use_id=f"t{index}"))
    assert _events(project) == []


def test_nothing_is_recorded_without_an_open_run_but_the_hint_still_arrives(project, monkeypatch, capsys):
    out = _hook(monkeypatch, capsys, "post-tool", _payload(
        project, tool_name="Bash", tool_input={"command": "curl https://tracker.example/x"}, tool_use_id="t1"))
    assert "tracker.sh" in out.out
    assert executions.load(project) == []


def test_a_failed_command_carries_its_exit_code(project, monkeypatch, capsys):
    _open_run(project)
    _hook(monkeypatch, capsys, "post-tool-failure", _payload(
        project, tool_name="Bash", tool_input={"command": "python3 build.py"}, tool_use_id="t1",
        error="Exit code 3", is_interrupt=False))
    [event] = _events(project)
    assert (event.tool, event.status, event.exit_code) == ("python3", "error", 3)


def test_an_interrupted_call_is_not_a_failure(project, monkeypatch, capsys):
    _open_run(project)
    _hook(monkeypatch, capsys, "post-tool-failure", _payload(
        project, tool_name="Bash", tool_input={"command": "python3 build.py"}, tool_use_id="t1",
        error="interrupted", is_interrupt=True))
    assert _events(project) == []


def test_an_edit_is_a_changed_event_with_the_subagent_that_made_it(project, monkeypatch, capsys):
    _open_run(project)
    _hook(monkeypatch, capsys, "post-tool", _payload(
        project, tool_name="Edit", tool_input={"file_path": str(project / "src" / "a.py")},
        tool_use_id="t1", agent_id="a1", agent_type="Explore"))
    [event] = _events(project)
    assert (event.kind, event.tool, event.ref, event.agent) == ("changed", "edit", "src/a.py", "Explore")


def test_subagent_start_and_stop_are_attributed_and_the_answer_is_only_counted(project, monkeypatch, capsys):
    _open_run(project)
    secret = "the answer text itself"
    _hook(monkeypatch, capsys, "subagent-start", _payload(project, agent_id="a1", agent_type="Explore"))
    _hook(monkeypatch, capsys, "subagent-stop", _payload(
        project, agent_id="a1", agent_type="Explore",
        last_assistant_message=f"{secret}: core/x.py:12 and [y](src/y.py#L4)"))
    start, stop = _events(project)
    assert (start.kind, start.tool, start.target, start.ref, start.agent) == \
        ("called", "subagent", "explore", "agent:a1", None)
    assert (stop.kind, stop.body, stop.agent) == ("verified", "2 citation(s) in its answer", "Explore")
    assert secret not in executions.path_for(project).read_text(encoding="utf-8")


def test_instructions_loaded_are_logged_relative_to_the_project(project, monkeypatch, capsys):
    _hook(monkeypatch, capsys, "instructions-loaded", _payload(
        project, file_path=str(project / ".claude" / "rules" / "x.md"), load_reason="path_glob_match",
        memory_type="Project", trigger_file_path=str(project / "data" / "a.txt")))
    [line] = (project / ".eos" / "data" / "loaded.jsonl").read_text(encoding="utf-8").splitlines()
    entry = json.loads(line)
    assert (entry["path"], entry["load_reason"], entry["trigger"]) == \
        (".claude/rules/x.md", "path_glob_match", "data/a.txt")


def test_session_end_writes_one_summary_and_clears_the_session_state(project, monkeypatch, capsys):
    _open_run(project)
    _hook(monkeypatch, capsys, "post-tool", _payload(
        project, tool_name="Bash", tool_input={"command": "curl https://tracker.example/x"}, tool_use_id="t1"))
    _hook(monkeypatch, capsys, "session-end", _payload(project, reason="other"))
    [line] = (project / ".eos" / "data" / "sessions.jsonl").read_text(encoding="utf-8").splitlines()
    summary = json.loads(line)
    assert summary["bypass"] == {"tracker": 1} and summary["hints"] == 1 and summary["reason"] == "other"
    assert not hooks._state_file("s1").exists()


def test_a_compaction_forgets_which_task_briefs_were_delivered(project, monkeypatch, capsys):
    seen = hooks._prompted_file("s1")
    seen.parent.mkdir(parents=True, exist_ok=True)
    seen.write_text("digest\n", encoding="utf-8")
    _hook(monkeypatch, capsys, "session-start", _payload(project, source="compact"))
    assert not seen.exists()


def test_brief_off_in_config_silences_the_brief_but_not_the_capture(project, monkeypatch, capsys):
    config = project / ".eos" / "config.toml"
    config.write_text(config.read_text(encoding="utf-8") + "\n[hooks]\nbrief = false\n", encoding="utf-8")
    _open_run(project)
    work_payload = _payload(project, source="startup")
    assert _hook(monkeypatch, capsys, "session-start", work_payload).out == ""
    _hook(monkeypatch, capsys, "post-tool", _payload(
        project, tool_name="Bash", tool_input={"command": "python3 x.py"}, tool_use_id="t1"))
    assert [e.tool for e in _events(project)] == ["python3"]


def test_stop_asks_once_about_runs_left_open(project, monkeypatch, capsys):
    record = _open_run(project)
    out = _hook(monkeypatch, capsys, "stop", _payload(project))
    answer = json.loads(out.out)
    assert answer["decision"] == "block" and record.id in answer["reason"]
    again = _hook(monkeypatch, capsys, "stop", _payload(project, stop_hook_active=True))
    assert again.out == ""


def test_stop_close_off_in_config_never_blocks(project, monkeypatch, capsys):
    config = project / ".eos" / "config.toml"
    config.write_text(config.read_text(encoding="utf-8") + "\n[hooks]\nclose = false\n", encoding="utf-8")
    _open_run(project)
    assert _hook(monkeypatch, capsys, "stop", _payload(project)).out == ""


def test_the_routing_hook_records_in_dry_run_and_applies_when_live(project, monkeypatch, capsys):
    config = project / ".eos" / "config.toml"
    base = config.read_text(encoding="utf-8")
    config.write_text(base + "\n[model_routing]\nhook = true\n", encoding="utf-8")
    _open_run(project)
    call = _payload(project, tool_name="Agent", tool_use_id="t1",
                    tool_input={"prompt": "design the service boundary for billing", "subagent_type": ""})
    assert _hook(monkeypatch, capsys, "pre-agent", call).out == ""
    assert any(e.kind == "decided" and (e.body or "").startswith("dry run") for e in _events(project))

    config.write_text(base + "\n[model_routing]\nhook = true\nhook_dry_run = false\n", encoding="utf-8")
    live = json.loads(_hook(monkeypatch, capsys, "pre-agent", call).out)
    assert live["hookSpecificOutput"]["updatedInput"]["model"] in hooks.SUBAGENT_MODELS


def test_the_routing_hook_decides_on_the_first_2000_characters_only(project, monkeypatch, capsys):
    config = project / ".eos" / "config.toml"
    config.write_text(config.read_text(encoding="utf-8") + "\n[model_routing]\nhook = true\n", encoding="utf-8")
    _open_run(project)
    filler = "note " * (hooks.MAX_TASK_CHARS // 5)
    assert len(filler) == hooks.MAX_TASK_CHARS
    words = "design the service boundary for billing"
    for number, prompt in enumerate((filler, filler + words, words + " " + filler)):
        _hook(monkeypatch, capsys, "pre-agent", _payload(
            project, tool_name="Agent", tool_use_id=f"b{number}", tool_input={"prompt": prompt}))
    bodies = [e.body.split("(")[1] for e in _events(project) if e.kind == "decided"]
    assert len(bodies) == 3
    # Words past the cut do not reach the decision; the same words inside it do.
    assert bodies[1] == bodies[0] and bodies[2] != bodies[0]


def test_a_named_subagent_or_an_explicit_model_is_never_routed(project, monkeypatch, capsys):
    config = project / ".eos" / "config.toml"
    config.write_text(config.read_text(encoding="utf-8") + "\n[model_routing]\nhook = true\n"
                      "hook_dry_run = false\n", encoding="utf-8")
    for tool_input in ({"prompt": "x", "subagent_type": "Explore"}, {"prompt": "x", "model": "haiku"}):
        assert _hook(monkeypatch, capsys, "pre-agent", _payload(
            project, tool_name="Agent", tool_input=tool_input)).out == ""


@pytest.mark.parametrize("stdin", ["", "not json", "[1, 2]", '{"cwd": "/nonexistent/path"}'])
def test_anything_malformed_exits_zero_silently(project, monkeypatch, capsys, stdin):
    for event in hooks.EVENTS + ("no-such-event",):
        out = _hook(monkeypatch, capsys, event, stdin)
        assert out.out == "" and out.err == ""


def test_the_cli_dispatches_hook_before_the_heavy_imports(project, tmp_path):
    payload = json.dumps(_payload(project, tool_name="Bash", tool_input={"command": "curl https://tracker.example/"}))
    done = subprocess.run(EOS + ["hook", "post-tool"], input=payload, capture_output=True, text=True,
                          env={"EOS_STATE_DIR": str(tmp_path / "state2"), "PATH": "/usr/bin:/bin"})
    assert done.returncode == 0 and done.stderr == ""
    assert "tracker.sh" in done.stdout


# --- the context diet (ADR-027) ------------------------------------------------------


def _hooks_config(project, text):
    config = project / ".eos" / "config.toml"
    config.write_text(config.read_text(encoding="utf-8") + "\n[hooks]\n" + text, encoding="utf-8")


def test_a_long_unranged_read_gets_the_outline_once_then_passes(project, monkeypatch, capsys):
    _hooks_config(project, "outline_lines = 10\n")
    target = project / "notes.md"
    target.write_text("# Title\n" + "text\n" * 20 + "## Part two\n```\n# not a heading\n```\n" + "more\n" * 5,
                      encoding="utf-8")
    read = _payload(project, tool_name="Read", tool_input={"file_path": str(target)})
    first = json.loads(_hook(monkeypatch, capsys, "pre-read", read).out)["hookSpecificOutput"]
    assert first["permissionDecision"] == "deny"
    reason = first["permissionDecisionReason"]
    assert "1: # Title" in reason and "22: ## Part two" in reason and "not a heading" not in reason
    assert "limit: 30" in reason
    assert _hook(monkeypatch, capsys, "pre-read", read).out == ""      # asking again reads it
    ranged = _payload(project, tool_name="Read", tool_input={"file_path": str(target), "limit": 5})
    assert _hook(monkeypatch, capsys, "pre-read", ranged).out == ""
    inside = _payload(project, tool_name="Read", agent_id="a1", tool_input={"file_path": str(project / "x")})
    assert _hook(monkeypatch, capsys, "pre-read", inside).out == ""


def test_the_outline_gate_is_off_unless_configured(project, monkeypatch, capsys):
    target = project / "big.py"
    target.write_text("def f():\n    pass\n" * 500, encoding="utf-8")
    read = _payload(project, tool_name="Read", tool_input={"file_path": str(target)})
    assert _hook(monkeypatch, capsys, "pre-read", read).out == ""


def test_a_shell_rewrite_of_a_file_the_session_read_is_named_once(project, monkeypatch, capsys):
    import os

    target = project / "app.py"
    target.write_text("a = 1\n", encoding="utf-8")
    _hook(monkeypatch, capsys, "post-tool", _payload(project, tool_name="Read",
                                                     tool_input={"file_path": str(target)}))
    bash = _payload(project, tool_name="Bash", tool_input={"command": "python3 fix.py"}, tool_use_id="b1")
    assert _hook(monkeypatch, capsys, "post-tool", bash).out == ""
    os.utime(target, (target.stat().st_atime, target.stat().st_mtime + 5))
    hint = json.loads(_hook(monkeypatch, capsys, "post-tool", bash).out)["hookSpecificOutput"]["additionalContext"]
    assert "app.py" in hint and "Edit tool" in hint
    os.utime(target, (target.stat().st_atime, target.stat().st_mtime + 5))
    assert _hook(monkeypatch, capsys, "post-tool", bash).out == ""      # once per session


def test_an_edit_through_the_edit_tool_is_not_a_shell_rewrite(project, monkeypatch, capsys):
    import os

    target = project / "app.py"
    target.write_text("a = 1\n", encoding="utf-8")
    _hook(monkeypatch, capsys, "post-tool", _payload(project, tool_name="Read",
                                                     tool_input={"file_path": str(target)}))
    os.utime(target, (target.stat().st_atime, target.stat().st_mtime + 5))
    _hook(monkeypatch, capsys, "post-tool", _payload(project, tool_name="Edit",
                                                     tool_input={"file_path": str(target)}))
    bash = _payload(project, tool_name="Bash", tool_input={"command": "ls"}, tool_use_id="b2")
    assert _hook(monkeypatch, capsys, "post-tool", bash).out == ""


def _transcript(tmp_path, tokens):
    path = tmp_path / "t.jsonl"
    path.write_text(json.dumps({"type": "user", "message": {"content": "x"}}) + "\n"
                    + json.dumps({"type": "assistant", "message": {"usage": {
                        "input_tokens": 1, "cache_read_input_tokens": tokens,
                        "cache_creation_input_tokens": 0}}}) + "\n", encoding="utf-8")
    return str(path)


def test_closing_a_run_in_a_large_context_suggests_clear(project, tmp_path, monkeypatch, capsys):
    finish = {"command": "eos run finish . x-a-run-0000 --outcome ok"}
    big = _payload(project, tool_name="Bash", tool_input=finish, transcript_path=_transcript(tmp_path, 400_000))
    hint = json.loads(_hook(monkeypatch, capsys, "post-tool", big).out)["hookSpecificOutput"]["additionalContext"]
    assert "~400k tokens" in hint and "/clear" in hint
    small = _payload(project, tool_name="Bash", tool_input=finish, transcript_path=_transcript(tmp_path, 50_000))
    assert _hook(monkeypatch, capsys, "post-tool", small).out == ""
    _hooks_config(project, "clear_hint_tokens = 0\n")
    assert _hook(monkeypatch, capsys, "post-tool", big).out == ""


def test_compaction_is_told_what_to_keep_and_the_summary_is_checked(project, monkeypatch, capsys):
    run = _open_run(project)
    target = project / "service.py"
    target.write_text("x = 1\n", encoding="utf-8")
    _hook(monkeypatch, capsys, "post-tool", _payload(project, tool_name="Edit",
                                                     tool_input={"file_path": str(target)}))
    instructions = _hook(monkeypatch, capsys, "pre-compact", _payload(project, trigger="auto")).out
    assert run.id in instructions and "service.py" in instructions
    assert "Drop raw tool output" in instructions
    _hook(monkeypatch, capsys, "post-compact", _payload(project, compact_summary=f"worked on {run.id}"))
    [checked] = [line for line in hooks._state("s1") if "missing" in line]
    assert checked["missing"] == ["service.py"] and checked["expected"] == 2


def test_after_compaction_the_session_gets_back_what_it_holds(project, monkeypatch, capsys):
    _hooks_config(project, "brief = false\n")
    run = _open_run(project)
    out = _hook(monkeypatch, capsys, "session-start", _payload(project, source="compact")).out
    assert "kept across the compaction" in out and run.id in out
    assert _hook(monkeypatch, capsys, "session-start", _payload(project, source="startup")).out == ""


def test_the_session_summary_keeps_the_context_diet_evidence(project, monkeypatch, capsys):
    _hooks_config(project, "outline_lines = 5\n")
    target = project / "long.md"
    target.write_text("# A\n" + "x\n" * 10, encoding="utf-8")
    _hook(monkeypatch, capsys, "pre-read", _payload(project, tool_name="Read", tool_input={"file_path": str(target)}))
    _hook(monkeypatch, capsys, "pre-compact", _payload(project))
    _hook(monkeypatch, capsys, "post-compact", _payload(project, compact_summary="nothing"))
    _hook(monkeypatch, capsys, "session-end", _payload(project, reason="other"))
    entry = json.loads((project / ".eos" / "data" / "sessions.jsonl").read_text().splitlines()[-1])
    assert entry["outlined"] == 1 and entry["compactions"] == [{"expected": 0, "missing": []}]
