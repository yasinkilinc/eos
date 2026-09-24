import json
from pathlib import Path

from core.ai import writer


def test_the_cli_surface_writes_no_mcp_registration(tmp_path):
    """An MCP roster is charged to every request whether or not a tool is
    called -- 2,194 tokens per session on an 18-service workspace, which is
    what got 18 registrations removed by hand there. The default must not
    quietly re-add one."""
    written = writer.write_all(tmp_path, version="0.10.0")
    names = {p.relative_to(tmp_path).as_posix() for p in written}
    assert names == {
        ".claude/skills/eos/SKILL.md",
        ".claude/agents/eos-researcher.md",
        ".claude/hooks/eos-brief.py",
        ".claude/hooks/eos-prompt.py",
        ".claude/hooks/eos-close.py",
        ".claude/settings.json",
        "AGENTS.md",
    }
    assert not (tmp_path / ".mcp.json").exists()


def test_the_mcp_surface_adds_the_registration_and_the_tool_table(tmp_path):
    written = writer.write_all(tmp_path, version="0.10.0", surface="both")
    names = {p.relative_to(tmp_path).as_posix() for p in written}

    assert ".mcp.json" in names
    skill = (tmp_path / ".claude" / "skills" / "eos" / "SKILL.md").read_text(encoding="utf-8")
    assert "get_parent_implementation" in skill
    assert "eos:mcp-only" not in skill, "the fence markers leaked into the rendered skill"


def test_the_cli_skill_does_not_describe_tools_the_project_does_not_have(tmp_path):
    """One surface per project, and the document says only what is true here:
    a skill listing twelve MCP tools in a project with no MCP server sends an
    agent to call something that is not there."""
    writer.write_all(tmp_path, version="0.10.0")

    skill = (tmp_path / ".claude" / "skills" / "eos" / "SKILL.md").read_text(encoding="utf-8")

    assert "get_parent_implementation" not in skill
    assert "eos:mcp-only" not in skill
    assert "eos brief" in skill


def test_each_hook_is_registered_once_however_often_it_is_written(tmp_path):
    writer.write_all(tmp_path, version="0.10.0")
    writer.write_all(tmp_path, version="0.11.0")

    settings = json.loads((tmp_path / ".claude" / "settings.json").read_text(encoding="utf-8"))

    assert len(settings["hooks"]["SessionStart"]) == 1
    assert writer.HOOK_FILE in settings["hooks"]["SessionStart"][0]["hooks"][0]["command"]
    assert len(settings["hooks"]["Stop"]) == 1
    assert writer.STOP_HOOK_FILE in settings["hooks"]["Stop"][0]["hooks"][0]["command"]


def test_registering_the_hook_keeps_hooks_the_project_already_had(tmp_path):
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({
        "hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "echo mine"}]}],
                  "Stop": [{"hooks": [{"type": "command", "command": "echo bye"}]}]},
        "env": {"KEEP": "1"},
    }), encoding="utf-8")

    writer.write_all(tmp_path, version="0.10.0")

    data = json.loads(settings.read_text(encoding="utf-8"))
    assert data["env"] == {"KEEP": "1"}
    assert data["hooks"]["Stop"][0]["hooks"][0]["command"] == "echo bye"
    commands = [item["command"] for entry in data["hooks"]["SessionStart"]
                for item in entry["hooks"]]
    assert "echo mine" in commands
    assert any(writer.HOOK_FILE in command for command in commands)


def test_preserves_existing_agents_content(tmp_path):
    agents = tmp_path / "AGENTS.md"
    agents.write_text("# House rules\n\nAlways run the linter.\n", encoding="utf-8")
    writer.write_all(tmp_path, version="0.10.0")
    text = agents.read_text(encoding="utf-8")
    assert "Always run the linter." in text
    assert writer.BEGIN in text and writer.END in text


def test_rerun_is_idempotent(tmp_path):
    writer.write_all(tmp_path, version="0.10.0")
    first = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    writer.write_all(tmp_path, version="0.10.0")
    assert (tmp_path / "AGENTS.md").read_text(encoding="utf-8") == first


def test_rerun_replaces_only_the_eos_block(tmp_path):
    writer.write_all(tmp_path, version="0.10.0")
    writer.write_all(tmp_path, version="0.11.0")
    text = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert text.count(writer.BEGIN) == 1
    assert "0.11.0" in text and "0.10.0" not in text


def test_preserves_other_mcp_servers(tmp_path):
    mcp = tmp_path / ".mcp.json"
    mcp.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}}), encoding="utf-8")
    writer.write_all(tmp_path, version="0.10.0", surface="mcp")
    data = json.loads(mcp.read_text(encoding="utf-8"))
    assert "other" in data["mcpServers"]
    assert data["mcpServers"]["eos"]["command"] == "eos"
    assert data["mcpServers"]["eos"]["args"] == ["mcp", "."]


def test_skill_points_at_bench_report(tmp_path):
    writer.write_all(tmp_path, version="0.10.0")
    skill = (tmp_path / ".claude" / "skills" / "eos" / "SKILL.md").read_text(encoding="utf-8")
    assert ".eos/data/bench.md" in skill


def test_ai_update_command_dispatches_without_keyerror(tmp_path, capsys):
    """`ai` must be registered in main()'s commands dict, or this raises
    KeyError before cmd_ai ever runs.

    `--help` cannot stand in for this: argparse's help action exits during
    parsing, before `commands[args.command](args)` is reached, so a missing
    dict entry would never surface through it."""
    from core import eos as eos_cli

    rc = eos_cli.main(["ai", "update", "--path", str(tmp_path)])

    assert rc == 0
    assert (tmp_path / ".claude" / "hooks" / "eos-brief.py").exists()
    assert (tmp_path / "AGENTS.md").exists()


def test_ai_update_keeps_the_surface_init_recorded(tmp_path):
    """An upgrade run without the flag must not re-add a registration the
    project removed on purpose: the cost of that is paid on every request of
    every session afterwards, which is exactly the kind nobody notices."""
    from core import eos as eos_cli

    assert eos_cli.main(["init", str(tmp_path), "--surface", "mcp"]) == 0
    (tmp_path / ".mcp.json").unlink()

    assert eos_cli.main(["ai", "update", "--path", str(tmp_path)]) == 0
    assert (tmp_path / ".mcp.json").exists(), "the recorded surface was not honoured"

    assert eos_cli.main(["ai", "update", "--path", str(tmp_path), "--surface", "cli"]) == 0
    (tmp_path / ".mcp.json").unlink()
    assert eos_cli.main(["ai", "update", "--path", str(tmp_path)]) == 0
    assert not (tmp_path / ".mcp.json").exists(), "the new surface was not remembered"


def test_no_agents_md_refreshes_the_skill_without_touching_a_tracked_file(tmp_path):
    """The skill has to be refreshable on its own.

    Where AGENTS.md is tracked and governed elsewhere, an engine upgrade that
    can only refresh the skill by also modifying a committed file will not be
    run -- and the skill then sits at whatever version it was generated under
    while the engine moves on. Observed: a generated skill two engine versions
    behind, describing a command in words the current engine had corrected.
    """
    from core import eos as eos_cli

    house = tmp_path / "AGENTS.md"
    house.write_text("# House rules\n", encoding="utf-8")

    rc = eos_cli.main(["ai", "update", "--path", str(tmp_path), "--no-agents-md"])

    assert rc == 0
    assert house.read_text(encoding="utf-8") == "# House rules\n", "AGENTS.md was modified"
    # The engine version it was generated under, not a digit that happened to
    # be in every 0.x and 1.0.0 version string and in no 1.1.x one.
    version = (Path(__file__).resolve().parents[1] / "core" / "VERSION").read_text(encoding="utf-8").strip()
    assert f"engine {version}" in (tmp_path / ".claude" / "skills" / "eos" / "SKILL.md").read_text(encoding="utf-8")
