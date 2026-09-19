import json
from pathlib import Path

from core.ai import writer


def test_writes_all_four_surfaces(tmp_path):
    written = writer.write_all(tmp_path, version="0.10.0")
    names = {p.relative_to(tmp_path).as_posix() for p in written}
    assert names == {
        ".claude/skills/eos/SKILL.md",
        ".claude/agents/eos-researcher.md",
        ".mcp.json",
        "AGENTS.md",
    }


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
    writer.write_all(tmp_path, version="0.10.0")
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
    assert (tmp_path / ".mcp.json").exists()
    assert (tmp_path / "AGENTS.md").exists()


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
    assert "0." in (tmp_path / ".claude" / "skills" / "eos" / "SKILL.md").read_text(encoding="utf-8")
