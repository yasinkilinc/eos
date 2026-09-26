"""Tests for the standalone EOS launcher and stdio MCP contract."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from core import inspector


REPO = Path(__file__).resolve().parents[1]
# Read the version rather than hardcoding it: the assertion below is about the
# launcher reporting *the version it shipped*, not about any particular number,
# and a literal here turns every release bump into a spurious test failure.
VERSION = (REPO / "core" / "VERSION").read_text(encoding="utf-8").strip()


def run_cli(binary: Path, *args: str, **kwargs):
    return subprocess.run([str(binary), *args], capture_output=True, text=True, **kwargs)


def test_install_launcher_and_cli_commands(tmp_path):
    prefix = tmp_path / "prefix"
    env = os.environ.copy()
    env["EOS_INSTALL_PREFIX"] = str(prefix)

    installed = subprocess.run(
        ["sh", str(REPO / "bin" / "install.sh")],
        capture_output=True,
        text=True,
        env=env,
    )
    assert installed.returncode == 0, installed.stderr

    binary = prefix / "bin" / "eos"
    assert binary.is_file()
    assert run_cli(binary, "--version").stdout.strip() == f"eos {VERSION}"
    # The capture helper lands beside the launcher, where a wrapper can
    # reach it by name (ADR-022).
    assert os.access(prefix / "bin" / "eos-event", os.X_OK)

    project = tmp_path / "project"
    project.mkdir()
    (project / "main.py").write_text("import helper\nhelper.run()\n", encoding="utf-8")
    (project / "helper.py").write_text("def run():\n    return 1\n", encoding="utf-8")

    assert run_cli(binary, "init", str(project)).returncode == 0
    assert run_cli(binary, "scan", str(project), "--full").returncode == 0
    assert run_cli(binary, "context", str(project), "--budget", "1000").returncode == 0

    graph = run_cli(binary, "graph", str(project), "--output", "-")
    assert graph.returncode == 0
    assert json.loads(graph.stdout)["edges"]

    compose = run_cli(binary, "compose", str(project), "understand", "main.py")
    assert compose.returncode == 0
    assert "main.py" in compose.stdout


def test_mcp_server_exposes_read_only_tools(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "main.py").write_text("import helper\nhelper.run()\n", encoding="utf-8")
    (project / "helper.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    (project / ".env").write_text("TOKEN=should-not-be-exposed\n", encoding="utf-8")

    assert subprocess.run(
        [sys.executable, str(REPO / "core" / "eos.py"), "init", str(project)],
        capture_output=True,
        text=True,
    ).returncode == 0
    assert subprocess.run(
        [sys.executable, str(REPO / "core" / "eos.py"), "scan", str(project), "--full"],
        capture_output=True,
        text=True,
    ).returncode == 0
    assert ".env" not in inspector.structure(project)
    with pytest.raises(ValueError, match="Sensitive files"):
        inspector.read_file(project, ".env")

    request = "\n".join(
        [
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
            json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}),
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {"name": "find_symbol", "arguments": {"query": "run"}},
                }
            ),
        ]
    ) + "\n"
    server = subprocess.run(
        ["python3", str(REPO / "core" / "eos.py"), "mcp", str(project)],
        input=request,
        capture_output=True,
        text=True,
    )
    assert server.returncode == 0, server.stderr
    responses = [json.loads(line) for line in server.stdout.splitlines()]
    assert responses[0]["result"]["serverInfo"]["name"] == "eos"
    tool_names = {tool["name"] for tool in responses[1]["result"]["tools"]}
    # get_graph is deliberately not asserted: it is unusable at FM scale (306k-6.47M
    # tokens, no filter) and the skills now forbid calling it. Pinning it here would
    # block removing it.
    assert {"get_context", "find_symbol", "impact_analysis"} <= tool_names
    assert "helper.py" in responses[2]["result"]["content"][0]["text"]


def test_update_keeps_no_backup_of_the_previous_runtime(tmp_path):
    """The updater used to copy the old runtime to
    `.eos/runtime.backup.<timestamp>/` and keep the last three. Those copies
    accumulated in every project's gitignored `.eos/` and were never read: the
    runtime is a copy of a canonical source tracked in git, so a rollback is
    `git checkout` plus one `eos update`. An update must now replace the
    runtime in place and leave nothing behind."""
    from core.lib.updater import Updater

    canonical = tmp_path / "core"
    canonical.mkdir()
    (canonical / "VERSION").write_text("9.9.9\n", encoding="utf-8")
    (canonical / "thing.py").write_text("VALUE = 2\n", encoding="utf-8")

    eos = tmp_path / ".eos"
    runtime = eos / "runtime"
    runtime.mkdir(parents=True)
    # A non-empty runtime is what used to trigger the backup.
    (runtime / "thing.py").write_text("VALUE = 1\n", encoding="utf-8")

    result = Updater(canonical, runtime).update()

    assert "backed_up_to" not in result
    assert [p.name for p in eos.iterdir() if p.name.startswith("runtime.backup.")] == []
    assert (runtime / "thing.py").read_text(encoding="utf-8") == "VALUE = 2\n"


def test_the_runtime_carries_the_templates_ai_update_renders(tmp_path):
    """Reported from a real workspace (2026-09-26): the updater copied `*.py`
    only, so `ai/templates/*.md` never reached `.eos/runtime/`, and `eos ai
    update` run from the project's own runtime -- the fallback the hooks use
    when the `eos` on PATH is older -- died on FileNotFoundError."""
    project = tmp_path / "project"
    project.mkdir()
    assert subprocess.run([sys.executable, str(REPO / "core" / "eos.py"), "init", str(project), "--no-ai"],
                          capture_output=True, text=True).returncode == 0
    runtime = project / ".eos" / "runtime"
    for name in ("skill.md", "agent.md", "agents_section.md"):
        assert (runtime / "ai" / "templates" / name).is_file(), name

    updated = subprocess.run([sys.executable, str(runtime / "eos.py"), "ai", "update", str(project)],
                             capture_output=True, text=True)

    assert updated.returncode == 0, updated.stderr
    assert (project / ".claude" / "skills" / "eos" / "SKILL.md").is_file()


def test_an_older_runtime_gets_the_templates_on_its_next_update(tmp_path):
    """A runtime deployed before 1.3.0 has a manifest of `.py` files only; the
    templates arrive as additions on the next update, nothing else changes."""
    from core.lib.updater import Updater

    runtime = tmp_path / ".eos" / "runtime"
    first = Updater(REPO / "core", runtime)
    manifest = {k: v for k, v in first.compute_manifest(REPO / "core").items() if k.endswith(".py")}
    first.update()
    (runtime / "manifest.json").write_text(json.dumps({"version": "1.2.2", "files": manifest}), encoding="utf-8")
    for template in (runtime / "ai" / "templates").glob("*.md"):
        template.unlink()

    result = Updater(REPO / "core", runtime).update()

    assert sorted(result["added"]) == sorted(k for k in first.compute_manifest(REPO / "core") if k.endswith(".md"))
    assert result["changed"] == [] and result["removed"] == []
    assert (runtime / "ai" / "templates" / "skill.md").is_file()
