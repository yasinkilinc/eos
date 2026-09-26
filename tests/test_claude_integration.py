"""The Claude Code integration's engine side (ADR-026): ledger attribution,
the plugin package, `--claude plugin`, the MCP roster's zero-cost channels and
the routing corpus gate."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from core import executions
from core.ai import writer
from core.mcp_server import McpServer

REPO = Path(__file__).resolve().parents[1]
EOS = [sys.executable, str(REPO / "core" / "eos.py")]
PLUGIN = REPO / "plugin"


def _init(tmp_path, *extra) -> Path:
    root = tmp_path / "proj"
    root.mkdir(exist_ok=True)
    done = subprocess.run(EOS + ["init", str(root), *extra], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    return root


# --- ledger ---------------------------------------------------------------------------


def test_events_carry_attribution_only_when_there_is_some(tmp_path, monkeypatch):
    monkeypatch.setenv("EOS_STATE_DIR", str(tmp_path / "state"))
    root = _init(tmp_path, "--no-ai")
    record = executions.start(root, "a run", session="s1")
    executions.event(root, record.id, kind="ran", tool="build", session="s1")
    executions.event(root, record.id, kind="changed", tool="edit", ref="a.py", session="s1",
                     agent="Explore", source="hook", status="ok", tool_use_id="t1")
    lines = [json.loads(line) for line in executions.path_for(root).read_text().splitlines()]
    assert "agent" not in lines[1] and "tool_use_id" not in lines[1]
    assert (lines[2]["agent"], lines[2]["source"], lines[2]["tool_use_id"]) == ("Explore", "hook", "t1")
    plain, attributed = executions.load(root)[0].events
    assert (plain.source, attributed.agent, attributed.status) == (None, "Explore", "ok")


def test_a_call_id_already_in_the_ledger_is_not_appended_again(tmp_path, monkeypatch):
    monkeypatch.setenv("EOS_STATE_DIR", str(tmp_path / "state"))
    root = _init(tmp_path, "--no-ai")
    record = executions.start(root, "a run", session="s1")
    assert executions.event(root, record.id, kind="ran", tool="x", tool_use_id="t1", session="s1") is not None
    assert executions.event(root, record.id, kind="ran", tool="x", tool_use_id="t1", session="s1") is None
    assert len(executions.load(root)[0].events) == 1


def test_an_unknown_source_or_status_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("EOS_STATE_DIR", str(tmp_path / "state"))
    root = _init(tmp_path, "--no-ai")
    record = executions.start(root, "a run", session="s1")
    with pytest.raises(ValueError, match="source"):
        executions.event(root, record.id, kind="ran", source="robot")
    with pytest.raises(ValueError, match="status"):
        executions.event(root, record.id, kind="ran", status="maybe")


def test_the_shell_helper_marks_its_lines_as_the_wrappers(tmp_path):
    root = _init(tmp_path, "--no-ai")
    env = {**os.environ, "EOS_STATE_DIR": str(tmp_path / "state"), "EOS_SESSION": "s1"}
    started = subprocess.run(EOS + ["run", "start", str(root), "--title", "t"], capture_output=True,
                             text=True, env=env)
    assert started.returncode == 0
    subprocess.run(["sh", str(REPO / "bin" / "eos-event"), "--kind", "ran", "--tool", "build"], env=env, check=True)
    subprocess.run(EOS + ["run", "event", str(root), "--kind", "noted", "--tool", "person"], env=env, check=True)
    [by_wrapper, by_cli] = executions.load(root)[0].events
    assert (by_wrapper.source, by_cli.source) == ("wrapper", "cli")


# --- plugin package -------------------------------------------------------------------


def test_the_plugin_version_is_the_engine_version():
    manifest = json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert manifest["name"] == "eos"
    assert manifest["version"] == (REPO / "core" / "VERSION").read_text(encoding="utf-8").strip()


def test_every_plugin_hook_names_an_event_the_engine_handles():
    from core import hooks

    registered = json.loads((PLUGIN / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]
    named = set()
    for entries in registered.values():
        for entry in entries:
            for hook in entry["hooks"]:
                assert hook["command"].startswith('"${CLAUDE_PLUGIN_ROOT}/hooks/eos-hook" ')
                named.add(hook["command"].split()[-1])
    assert named == set(hooks.EVENTS)


def test_the_plugin_copies_are_rendered_from_the_templates():
    done = subprocess.run([sys.executable, str(REPO / "tools" / "build-plugin.py"), "--check"],
                          capture_output=True, text=True)
    assert done.returncode == 0, "run tools/build-plugin.py: " + done.stdout


def test_the_hook_entry_exits_zero_with_no_eos_anywhere(tmp_path):
    done = subprocess.run(["sh", str(PLUGIN / "hooks" / "eos-hook"), "post-tool"], input="{}",
                          capture_output=True, text=True,
                          env={"PATH": "/usr/bin:/bin", "CLAUDE_PROJECT_DIR": str(tmp_path)})
    assert (done.returncode, done.stdout, done.stderr) == (0, "", "")


def test_the_marketplace_lists_the_plugin():
    market = json.loads((REPO / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
    assert market["plugins"][0]["name"] == "eos" and market["plugins"][0]["source"] == "./plugin"


@pytest.mark.skipif(shutil.which("claude") is None, reason="Claude Code CLI not installed")
def test_claude_validates_the_plugin_and_the_marketplace():
    for target in (PLUGIN, REPO):
        done = subprocess.run(["claude", "plugin", "validate", str(target)], capture_output=True, text=True,
                              timeout=120)
        assert done.returncode == 0 and "Validation passed" in done.stdout, done.stdout + done.stderr


# --- `--claude plugin` ------------------------------------------------------------------


def test_plugin_mode_removes_only_what_files_mode_wrote(tmp_path):
    root = _init(tmp_path)
    settings = root / ".claude" / "settings.json"
    data = json.loads(settings.read_text(encoding="utf-8"))
    data["hooks"]["Stop"].append({"hooks": [{"type": "command", "command": "mine.sh"}]})
    data["permissions"] = {"allow": ["Bash(ls)"]}
    settings.write_text(json.dumps(data), encoding="utf-8")
    (root / ".claude" / "skills" / "other").mkdir(parents=True)
    (root / ".claude" / "skills" / "other" / "SKILL.md").write_text("---\nname: other\n---\n", encoding="utf-8")

    done = subprocess.run(EOS + ["ai", "update", str(root), "--claude", "plugin"], capture_output=True, text=True)

    assert done.returncode == 0, done.stderr
    assert not (root / ".claude" / "hooks").exists()
    assert not (root / ".claude" / "agents" / "eos-researcher.md").exists()
    assert not (root / ".claude" / "skills" / "eos").exists()
    assert (root / ".claude" / "skills" / "other" / "SKILL.md").exists()
    kept = json.loads(settings.read_text(encoding="utf-8"))
    assert kept["permissions"] == {"allow": ["Bash(ls)"]}
    assert kept["hooks"] == {"Stop": [{"hooks": [{"type": "command", "command": "mine.sh"}]}]}
    assert 'claude = "plugin"' in (root / ".eos" / "config.toml").read_text(encoding="utf-8")
    again = subprocess.run(EOS + ["ai", "update", str(root)], capture_output=True, text=True)
    assert again.returncode == 0 and not (root / ".claude" / "hooks").exists(), "the choice is kept"


def test_a_settings_file_left_empty_is_deleted(tmp_path):
    root = _init(tmp_path)
    writer.remove_claude_files(root)
    assert not (root / ".claude").exists()


def test_an_unchanged_surface_is_not_rewritten(tmp_path):
    root = _init(tmp_path)
    skill = root / ".claude" / "skills" / "eos" / "SKILL.md"
    before = skill.stat().st_mtime_ns
    os.utime(skill, ns=(before - 10_000_000_000, before - 10_000_000_000))
    stamped = skill.stat().st_mtime_ns
    assert subprocess.run(EOS + ["ai", "update", str(root)], capture_output=True).returncode == 0
    assert skill.stat().st_mtime_ns == stamped


# --- MCP ----------------------------------------------------------------------------------


def test_resources_and_the_brief_prompt_cost_no_tools(tmp_path):
    root = _init(tmp_path, "--no-ai")
    server = McpServer(str(root), "t")
    listed = server._handle({"jsonrpc": "2.0", "id": 1, "method": "resources/list"})["result"]["resources"]
    assert {r["uri"] for r in listed} == {"eos://brief", "eos://capabilities"}
    read = server._handle({"jsonrpc": "2.0", "id": 2, "method": "resources/read",
                           "params": {"uri": "eos://capabilities"}})["result"]["contents"][0]
    assert read["text"] == "No capabilities declared."
    prompt = server._handle({"jsonrpc": "2.0", "id": 3, "method": "prompts/get",
                             "params": {"name": "brief", "arguments": {"task": "deploy"}}})["result"]
    assert prompt["messages"][0]["role"] == "user"
    missing = server._handle({"jsonrpc": "2.0", "id": 4, "method": "prompts/get",
                              "params": {"name": "brief", "arguments": {}}})
    assert missing["error"]["code"] == -32602
    assert "get_graph" not in server.tools and "compose" not in server.tools


# --- routing corpus -----------------------------------------------------------------------


def test_the_routing_corpus_is_scored_without_recording_anything(tmp_path):
    root = _init(tmp_path, "--no-ai")
    corpus = tmp_path / "corpus.tsv"
    corpus.write_text("prompt\ttype\tlevel\tmodel\n"
                      "fix the typo in the readme\ttrivial_edit\tLOW\thaiku\n"
                      "design the service boundary for billing across all repositories\t*\t"
                      "HIGH|CRITICAL\topus\n"
                      "rename the variable\trefactoring\t\t\n", encoding="utf-8")
    done = subprocess.run(EOS + ["route", str(root), "--eval", str(corpus), "--json"], capture_output=True,
                          text=True)
    assert done.returncode == 0, done.stderr
    result = json.loads(done.stdout)
    assert result["prompts"] == 3 and result["scored"]["model"] == 2
    assert result["invalid_effort"] == 0
    assert not (root / ".eos" / "data" / "routing.jsonl").exists()
