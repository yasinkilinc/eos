"""MCP contract for the note store: add_note and search_notes tools.

Same stdio JSON-RPC harness as test_distribution.py's
test_mcp_server_exposes_read_only_tools, extended to the two tools that make
this server able to write, not just read.
"""
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _init(project):
    project.mkdir()
    r = subprocess.run(
        [sys.executable, str(REPO / "core" / "eos.py"), "init", str(project)],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr


def _mcp(project, requests):
    payload = "\n".join(json.dumps(r) for r in requests) + "\n"
    result = subprocess.run(
        [sys.executable, str(REPO / "core" / "eos.py"), "mcp", str(project)],
        input=payload, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    return [json.loads(line) for line in result.stdout.splitlines()]


def _call(name, arguments, request_id=1):
    return {"jsonrpc": "2.0", "id": request_id, "method": "tools/call",
            "params": {"name": name, "arguments": arguments}}


def test_add_note_tool_is_advertised_and_writes_a_note(tmp_path):
    project = tmp_path / "project"
    _init(project)

    responses = _mcp(project, [
        {"jsonrpc": "2.0", "id": 0, "method": "tools/list", "params": {}},
        _call("add_note", {
            "kind": "finding",
            "title": "Cache warms on boot",
            "body": "The first request pays for it otherwise.",
        }),
    ])

    tool_names = {tool["name"] for tool in responses[0]["result"]["tools"]}
    assert "add_note" in tool_names

    result = responses[1]["result"]
    assert result.get("isError") is not True, result
    written = list((project / ".eos" / "knowledge").glob("*.md"))
    assert len(written) == 1


def test_search_notes_tool_finds_a_note_added_earlier(tmp_path):
    project = tmp_path / "project"
    _init(project)

    responses = _mcp(project, [
        _call("add_note", {
            "kind": "finding",
            "title": "Connection pool exhausted",
            "body": "The pool ran out of connections during deposits.",
            "tags": ["pool", "database"],
        }, request_id=1),
        _call("search_notes", {"query": "database connection pool"}, request_id=2),
    ])

    hits = json.loads(responses[1]["result"]["content"][0]["text"])["notes"]
    assert len(hits) == 1
    assert hits[0]["title"] == "Connection pool exhausted"
    assert hits[0]["tags"] == ["pool", "database"]


def test_add_note_tool_reports_a_credential_body_as_a_tool_error(tmp_path):
    # The MCP boundary must surface core.notes' guard as a normal tool error
    # (isError: true), not let the exception escape as a transport failure or,
    # worse, get swallowed and silently skip the write. Built at run time, not
    # written as a literal: .githooks/pre-commit refuses a staged line that
    # assigns a credential-shaped value, and _find_credential now requires the
    # same 12-character floor, so a short word like "hunter2" no longer
    # qualifies.
    project = tmp_path / "project"
    _init(project)
    secret = "S" * 24

    responses = _mcp(project, [
        _call("add_note", {
            "kind": "finding",
            "title": "Auth call",
            "body": f"Reproduce with password={secret} against env1.",
        }),
    ])

    result = responses[0]["result"]
    assert result.get("isError") is True
    assert "credential" in result["content"][0]["text"].lower()
    assert list((project / ".eos" / "knowledge").glob("*.md")) == []


def test_add_note_tool_refuses_an_explicitly_empty_scope(tmp_path):
    # The same trap `eos note add --scope ,` was closed for, at the other
    # boundary that can tell "the caller named a scope and it came to
    # nothing" from "there is no scope": an explicit `"scope": []` wrote a
    # note with neither scope: nor scope_hashes:, un-monitorable from birth.
    # Omitting the key entirely stays legal -- most notes have no scope, and
    # add_note's Python default must keep meaning that for the generators
    # that pass [].
    project = tmp_path / "project"
    _init(project)

    responses = _mcp(project, [
        _call("add_note", {
            "kind": "finding",
            "title": "Cache warms on boot",
            "body": "The first request pays for it otherwise.",
            "scope": [],
        }),
    ])

    result = responses[0]["result"]
    assert result.get("isError") is True
    assert "scope" in result["content"][0]["text"].lower()
    assert list((project / ".eos" / "knowledge").glob("*.md")) == []


def test_add_note_tool_refuses_a_scope_whose_entries_are_all_blank(tmp_path):
    # `_explicit_scope` checked the LIST for emptiness, not its entries, so
    # `[""]` and `["   "]` wrote a note with `scope_hashes: [null]` on exit 0
    # -- un-monitorable for life, and indistinguishable from the `[]` case
    # already refused above. The CLI strips entries via `_split_csv`; this
    # boundary must use the same rule.
    project = tmp_path / "project"
    _init(project)

    for blank in ([""], ["   "], ["", "  "]):
        responses = _mcp(project, [
            _call("add_note", {
                "kind": "finding",
                "title": f"Cache warms on boot {len(blank)}{blank[0]!r}",
                "body": "The first request pays for it otherwise.",
                "scope": blank,
            }),
        ])
        result = responses[0]["result"]
        assert result.get("isError") is True, blank
        assert "scope" in result["content"][0]["text"].lower()
    assert list((project / ".eos" / "knowledge").glob("*.md")) == []


def test_add_note_tool_refuses_a_non_string_scope_entry(tmp_path):
    # `str(entry)` coerced instead of refusing, so a malformed entry was
    # written as its own repr -- `- "{'a': 1}"` with `scope_hashes: - null`,
    # exactly the un-monitorable-for-life note this guard exists to prevent,
    # on exit 0. The declared inputSchema says `items: string`; this is the
    # boundary that has to hold when a client ignores it.
    project = tmp_path / "project"
    _init(project)

    for index, entry in enumerate(({"a": 1}, None, 7, ["src/X.java"])):
        responses = _mcp(project, [
            _call("add_note", {
                "kind": "finding",
                "title": f"Cache warms on boot {index}",
                "body": "The first request pays for it otherwise.",
                "scope": [entry],
            }),
        ])
        result = responses[0]["result"]
        assert result.get("isError") is True, entry
        assert "scope" in result["content"][0]["text"].lower(), entry
    assert list((project / ".eos" / "knowledge").glob("*.md")) == []


def test_add_note_tool_strips_a_padded_scope_entry_before_hashing(tmp_path):
    # `"  src/X.java  "` is the worst of the three: it looks scoped, and the
    # padded string resolves to no file, so the note is written with
    # `scope_hashes: [null]` and can never be flagged stale. Stripping is what
    # makes it monitored.
    project = tmp_path / "project"
    _init(project)
    (project / "src").mkdir()
    (project / "src" / "X.java").write_text("class X {}\n", encoding="utf-8")

    responses = _mcp(project, [
        _call("add_note", {
            "kind": "finding",
            "title": "X is constructed reflectively",
            "body": "The parent instantiates it by name.",
            "scope": ["  src/X.java  "],
        }),
    ])

    assert responses[0]["result"].get("isError") is not True, responses[0]
    (written,) = list((project / ".eos" / "knowledge").glob("*.md"))
    text = written.read_text(encoding="utf-8")
    assert "- src/X.java" in text, text
    assert "scope_hashes" in text and "null" not in text, (
        f"a padded entry must be hashed, not left un-monitorable:\n{text}"
    )
