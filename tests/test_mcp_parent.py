"""MCP contract for get_parent_implementation -- same stdio JSON-RPC harness
as test_mcp_notes.py."""
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _run(args, **kw):
    return subprocess.run(
        [sys.executable, str(REPO / "core" / "eos.py"), *args],
        capture_output=True, text=True, **kw,
    )


def _mcp(project, requests):
    payload = "\n".join(json.dumps(r) for r in requests) + "\n"
    result = subprocess.run(
        [sys.executable, str(REPO / "core" / "eos.py"), "mcp", str(project)],
        input=payload, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    return [json.loads(line) for line in result.stdout.splitlines()]


def test_get_parent_implementation_tool_is_advertised_and_works(tmp_path):
    proj = tmp_path / "acme-orders"
    proj.mkdir()
    (proj / "Overlay.java").write_text("class Overlay {}\n", encoding="utf-8")
    parent = tmp_path / "upstream-orders"
    parent.mkdir()
    (parent / "Base.java").write_text(
        "public class Base {\n    public void baseMethod() {}\n}\n", encoding="utf-8",
    )
    assert _run(["init", str(proj), "--link-parent", str(parent)]).returncode == 0
    assert _run(["scan", str(proj), "--full", "--with-parents"]).returncode == 0

    responses = _mcp(proj, [
        {"jsonrpc": "2.0", "id": 0, "method": "tools/list", "params": {}},
        {
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "get_parent_implementation", "arguments": {"symbol": "baseMethod"}},
        },
    ])

    tool_names = {tool["name"] for tool in responses[0]["result"]["tools"]}
    assert "get_parent_implementation" in tool_names

    result = json.loads(responses[1]["result"]["content"][0]["text"])
    assert len(result["matches"]) == 1
    assert result["matches"][0]["name"] == "baseMethod"
