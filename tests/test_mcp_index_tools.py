"""The MCP surface reads the index, and the roster does not grow.

Before this, all twelve tools read file_cache.json, graph.json, brain markdown
or the notes directory, and nothing reached .eos/data/eos.db -- so journey
tables, full-text search and git history were invisible to an agent and
queryable only by typing `eos query` in a shell.

The roster size is a hard constraint, not a preference. This workspace retired
MCP servers on token cost: eighteen of them came to 2,194 tokens of tool list
per session, paid on every request. So capability arrives by widening tools,
never by adding them.
"""
import shutil
import subprocess
import sys
from pathlib import Path

from core.mcp_server import McpServer

REPO = Path(__file__).resolve().parents[1]
EOS = [sys.executable, str(REPO / "core" / "eos.py")]
FIXTURE = Path(__file__).parent / "fixtures" / "polyglot_project"

# 12 until 1.3.0: get_graph (the whole graph, which `eos graph --output`
# exports) and compose (a deprecated alias of get_context) left (ADR-026).
ROSTER = 10


def _run(args, **kw):
    return subprocess.run(EOS + args, capture_output=True, text=True, encoding="utf-8", **kw)


def _scanned(tmp_path: Path) -> Path:
    root = tmp_path / "polyglot"
    shutil.copytree(FIXTURE, root)
    assert _run(["init", str(root), "--no-ai"]).returncode == 0
    assert _run(["scan", str(root), "--full"]).returncode == 0
    return root


def _server(root: Path) -> McpServer:
    return McpServer(root, "test")


def test_tool_roster_did_not_grow(tmp_path):
    server = _server(_scanned(tmp_path))

    assert len(server.tools) == ROSTER, (
        f"roster grew to {len(server.tools)}: {sorted(server.tools)}. "
        "Widen an existing tool instead -- every entry is paid for on every request."
    )


def test_mcp_server_starts_without_an_eos_directory(tmp_path):
    """cmd_mcp deliberately skips require_project(); reading the index must not
    reintroduce a dependency on having scanned first."""
    server = _server(tmp_path / "never-scanned")

    listed = server._handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})

    assert "result" in listed, listed
    assert len(listed["result"]["tools"]) == ROSTER
    called = server._handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                             "params": {"name": "search_index", "arguments": {"query": "anything"}}})
    assert "result" in called, f"a tool must answer, not kill the server: {called}"


def test_impact_analysis_returns_provenance_and_coverage_when_asked(tmp_path):
    server = _server(_scanned(tmp_path))

    answer = server.tools["impact_analysis"]["handler"]({
        "path": "web/src/index.ts", "depth": 2, "include": ["facts", "coverage"]})

    assert answer["facts"], answer
    assert {"origin", "confidence", "detector", "source_ref"} <= set(answer["facts"][0])
    assert answer["coverage"], (
        "coverage is what separates 'found nothing' from 'never looked'; it must reach the agent"
    )


def test_impact_analysis_without_include_stays_small(tmp_path):
    server = _server(_scanned(tmp_path))

    answer = server.tools["impact_analysis"]["handler"]({"path": "web/src/index.ts"})

    assert "facts" not in answer and "coverage" not in answer and "history" not in answer, (
        f"the default answer must not carry what nobody asked for: {sorted(answer)}"
    )


def test_get_context_accepts_task_and_target(tmp_path):
    root = _scanned(tmp_path)
    assert _run(["note", "add", str(root), "--kind", "finding",
                 "--title", "Button theme regression on retry",
                 "--body", "The retry path re-renders the button with the wrong theme."]).returncode == 0
    server = _server(root)

    focused = server.tools["get_context"]["handler"](
        {"task": "button theme regression", "target": "web/src/index.ts"})["context"]

    assert "## Task" in focused, focused[:400]
    assert "Button theme regression on retry" in focused, (
        "a task must reach the note ranking; without it notes are ordered by recency"
    )


def test_search_index_finds_a_note_through_full_text(tmp_path):
    root = _scanned(tmp_path)
    assert _run(["note", "add", str(root), "--kind", "finding",
                 "--title", "Pool exhaustion under retry storms",
                 "--body", "Connections are never returned when the retry budget runs out."]).returncode == 0
    assert _run(["index", str(root)]).returncode == 0
    server = _server(root)

    found = server.tools["search_index"]["handler"]({"query": "retry storms"})["results"]

    assert found, "full-text search over the index returned nothing"
    assert any(hit["source"] == "note" for hit in found), found


def test_search_index_says_so_when_there_is_no_index(tmp_path):
    server = _server(tmp_path / "bare")

    answer = server.tools["search_index"]["handler"]({"query": "anything"})

    assert answer["results"] == []
    assert "eos index" in answer["note"], answer
