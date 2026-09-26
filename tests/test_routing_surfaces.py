"""Where the decision reaches an agent: the brief, the MCP context, the templates.

The property that matters most is the one nobody would notice breaking: a
project that never asked for routing sees exactly the brief it saw before.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

import core.routing as routing
from core import brief, notes
from core.ai import writer
from core.mcp_server import McpServer
from core.routing import adapters

TASK = "refactor the auth flow and update tests"
REPO = Path(__file__).resolve().parents[1]
EOS = [sys.executable, str(REPO / "core" / "eos.py")]


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    (root / ".eos").mkdir(parents=True)
    monkeypatch.setenv("EOS_STATE_DIR", str(tmp_path / "state"))
    for name in ("EOS_EXECUTION", "EOS_EXECUTION_LEDGER", routing.MODEL_ENV, routing.EFFORT_ENV):
        monkeypatch.delenv(name, raising=False)
    return root


def _configure(root, table):
    config = root / ".eos" / "config.toml"
    kept = config.read_text(encoding="utf-8") + "\n" if config.is_file() else ""
    config.write_text(kept + table, encoding="utf-8")


def _scanned(root):
    (root / "main.py").write_text("import os\n", encoding="utf-8")
    for args in (["init", str(root), "--no-ai"], ["scan", str(root), "--full"]):
        done = subprocess.run(EOS + args, capture_output=True, text=True, encoding="utf-8")
        assert done.returncode == 0, done.stderr
    return root


def _with_a_note(root):
    notes.add_note(root, kind="finding", title="Auth flow refactor keeps the session cookie",
                   body="The auth flow refactor must keep the session cookie name.", tags=["auth"])


def test_without_a_routing_table_the_brief_is_byte_identical(project, monkeypatch):
    _with_a_note(project)
    before_task = brief.build(project, task=TASK)
    before_plain = brief.build(project)
    before_hook = brief.build(project, task=TASK, task_only=True)

    def exploding(*args, **kwargs):
        raise AssertionError("the router must not run for a project without [model_routing]")

    monkeypatch.setattr(routing, "route", exploding)
    assert brief.build(project, task=TASK) == before_task
    assert brief.build(project) == before_plain
    assert brief.build(project, task=TASK, task_only=True) == before_hook
    assert "ROUTE" not in before_task


def test_with_routing_on_the_task_brief_carries_the_route_line(project):
    _configure(project, "[model_routing]\nenabled = true\n")
    _with_a_note(project)
    text = brief.build(project, task=TASK, agent="claude")

    route = [line for line in text.splitlines() if line.startswith("ROUTE  ")]
    assert len(route) == 1
    assert "refactoring HIGH → sonnet/high" in route[0]
    assert ('  Apply: Agent({model: "sonnet"}) for subagents; effort high is set at session start '
            '(claude --effort high)') in text


def test_the_route_line_says_when_an_environment_variable_pins_the_effort(project, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_EFFORT_LEVEL", "low")
    _configure(project, "[model_routing]\nenabled = true\n")
    _with_a_note(project)
    text = brief.build(project, task=TASK, agent="claude")
    assert "effort high cannot apply while CLAUDE_CODE_EFFORT_LEVEL=low pins it" in text


def test_another_agent_gets_the_decision_as_plain_words(project):
    _configure(project, "[model_routing]\n")
    _with_a_note(project)
    text = brief.build(project, task=TASK, agent="devin")
    assert "  Apply: model sonnet at effort high" in text
    assert "Agent({" not in text


def test_brief_never_suppresses_the_line(project):
    _configure(project, '[model_routing]\nbrief = "never"\n')
    _with_a_note(project)
    assert "ROUTE" not in brief.build(project, task=TASK)


def test_routing_disabled_suppresses_the_line(project):
    _configure(project, "[model_routing]\nenabled = false\n")
    _with_a_note(project)
    assert "ROUTE" not in brief.build(project, task=TASK)


def test_the_prompt_hook_brief_still_costs_nothing_when_nothing_is_recorded(project):
    _configure(project, "[model_routing]\n")
    assert brief.build(project, task=TASK, task_only=True) == ""


def test_always_makes_the_route_line_enough_on_its_own(project):
    _configure(project, '[model_routing]\nbrief = "always"\n')
    text = brief.build(project, task=TASK, task_only=True)
    assert text.splitlines()[0].startswith("EOS brief for this task")
    assert any(line.startswith("ROUTE  ") for line in text.splitlines())


def test_the_route_line_survives_a_tiny_budget(project):
    _configure(project, "[model_routing]\n")
    topics = ("session cookie", "token rotation", "login throttling", "password hashing",
              "remember-me flag", "logout redirect")
    for topic in topics:
        notes.add_note(project, kind="finding", title=f"Auth flow refactor and the {topic}",
                       body=f"The auth flow refactor touches the {topic}. " + f"{topic} " * 40,
                       tags=["auth"])
    text = brief.build(project, task=TASK, budget=60)
    assert "…trimmed" in text
    assert any(line.startswith("ROUTE  ") for line in text.splitlines())
    assert any(line.startswith("  Apply: ") for line in text.splitlines())


def test_a_broken_routing_config_costs_the_line_never_the_brief(project):
    _configure(project, '[model_routing]\ndefault_effort = "extreme"\n')
    _with_a_note(project)
    text = brief.build(project, task=TASK)
    assert "ROUTE" not in text and text.startswith("EOS brief for this task")


def test_the_brief_records_nothing(project):
    _configure(project, "[model_routing]\n")
    _with_a_note(project)
    brief.build(project, task=TASK)
    assert not (project / ".eos" / "data" / "routing.jsonl").exists()


def test_the_mcp_context_carries_the_decision_when_asked(project):
    server = McpServer(str(_scanned(project)), "test")
    answer = server.tools["get_context"]["handler"]({"task": TASK, "route": True, "budget": 50})
    assert answer["route"]["model"] == "sonnet" and answer["route"]["effort"] == "high"
    plain = server.tools["get_context"]["handler"]({"task": TASK, "budget": 50})
    assert "route" not in plain
    assert not (project / ".eos" / "data" / "routing.jsonl").exists(), "the server is read-only"


def test_the_mcp_context_says_when_routing_is_off(project):
    _scanned(project)
    _configure(project, "[model_routing]\nenabled = false\n")
    server = McpServer(str(project), "test")
    answer = server.tools["get_context"]["handler"]({"task": TASK, "route": True, "budget": 50})
    assert "disabled" in answer["route"]


def test_the_mcp_roster_did_not_grow(project):
    listed = McpServer(str(project), "test")._handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    names = {tool["name"] for tool in listed["result"]["tools"]}
    assert "route" not in names
    schema = next(t for t in listed["result"]["tools"] if t["name"] == "get_context")["inputSchema"]
    assert {"route", "files", "model", "effort"} <= set(schema["properties"])


def test_the_claude_adapter_marks_effort_advisory(project):
    decision = routing.route(project, TASK, record=False)
    rendered = adapters.render(decision, "claude")
    assert rendered["subagent"] == {"model": "sonnet"}
    assert rendered["effort"]["applies"] == "advisory"
    assert set(adapters.render(decision, None)) == {"line"}
    assert json.dumps(rendered)  # serialisable for a hook


def test_the_rendered_agent_surfaces_mention_eos_route(tmp_path):
    root = tmp_path / "surf"
    root.mkdir()
    writer.write_all(root, "9.9.9")
    skill = (root / ".claude" / "skills" / "eos" / "SKILL.md").read_text(encoding="utf-8")
    agents = (root / "AGENTS.md").read_text(encoding="utf-8")
    assert "eos route" in skill and "ROUTE" in skill
    assert "eos route" in agents and "ROUTE" in agents


def test_a_reading_task_is_told_where_its_reads_cost_nothing_later():
    import dataclasses

    from core.routing import adapters
    from core.routing.types import Decision

    base = Decision(task_type="investigation", level="MEDIUM", score=0.4, model="sonnet", effort="medium",
                    reason="r", confidence=0.7, override_source="auto")
    claude = adapters.brief_lines(base, "claude")
    assert len(claude) == 3 and claude[2].startswith(adapters.CONTEXT_MARK)
    assert 'Agent({subagent_type: "Explore", model: "sonnet"})' in claude[2]
    assert "a subagent" in adapters.brief_lines(base, "devin")[2]
    assert len(adapters.brief_lines(dataclasses.replace(base, level="LOW"), "claude")) == 2
    assert len(adapters.brief_lines(dataclasses.replace(base, task_type="debugging"), "claude")) == 2
