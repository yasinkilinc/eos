"""Minimal stdio MCP server for EOS project intelligence."""
from __future__ import annotations

import json
import sys
from typing import Any

from core import inspector
from core import notes


def _explicit_scope(args: dict) -> list | None:
    """The `scope` an add_note call asked for, refusing an empty one.

    A client that sends `"scope": []` named a scope that came to nothing, and
    passing that through writes a note with neither `scope:` nor
    `scope_hashes:` -- un-monitorable from birth, the same silent data loss
    `eos note add --scope ,` was closed for. Omitting the key entirely stays
    legal and is the normal case: most notes have no scope, and `add_note`'s
    own `scope=[]` must keep meaning that for the generators that pass it.
    This boundary, like the CLI's, is the only layer that can tell the two
    apart.

    Emptiness is a property of the ENTRIES, not of the list: `[""]`, `["   "]`
    and `["  src/X.java  "]` all passed the list-level check and were written
    with `scope_hashes: [null]` on exit 0 -- un-monitorable for life, and the
    padded one looks scoped in the file. Entries are stripped and blanks
    dropped, the same rule `eos note add` applies through `_split_csv`, so
    what survives is either a real entry or nothing.

    A non-string entry is REFUSED, not coerced. `str(entry)` stringified it
    instead: `[{"a": 1}]` was written as the scope entry `{'a': 1}` and
    `[None]` as `None`, each with `scope_hashes: [null]` on exit 0 -- the
    un-monitorable-for-life note this guard exists to prevent, reached
    through the guard itself. The declared inputSchema already says
    `items: string`, so this needs a client that ignores it; that is what a
    validation boundary is for.
    """
    if "scope" not in args:
        return None
    scope = args["scope"]
    if isinstance(scope, list):
        for entry in scope:
            if not isinstance(entry, str):
                raise ValueError(
                    f"scope entries must be strings; got {type(entry).__name__} "
                    f"({entry!r}). Name the file or class this note is about, "
                    "rather than writing a scope entry that can never be "
                    "resolved against the code."
                )
        scope = [entry.strip() for entry in scope if entry.strip()]
    if not scope:
        raise ValueError(
            "scope was given but names nothing; omit it if this note is not "
            "about specific files, rather than writing a note that can never "
            "be checked against the code."
        )
    return scope


class McpServer:
    """Serve read-only EOS tools over newline-delimited JSON-RPC."""

    def __init__(self, project_root: str, version: str) -> None:
        self.project_root = project_root
        self.version = version
        self.tools: dict[str, dict[str, Any]] = {
            "get_project": {
                "description": "Return EOS project metadata and latest scan information.",
                "inputSchema": {"type": "object", "properties": {}},
                "handler": lambda _: inspector.project_summary(project_root),
            },
            "get_structure": {
                "description": "Return a bounded list of project files.",
                "inputSchema": {
                    "type": "object",
                    "properties": {"max_entries": {"type": "integer", "minimum": 1, "maximum": 2000}},
                },
                "handler": lambda args: {"files": inspector.structure(project_root, int(args.get("max_entries", 500)))},
            },
            "get_context": {
                "description": "Return the generated AI-oriented project context.",
                "inputSchema": {
                    "type": "object",
                    "properties": {"budget": {"type": "integer", "minimum": 1, "maximum": 100000}},
                },
                "handler": lambda args: {"context": inspector.build_context(project_root, int(args.get("budget", 12000)))},
            },
            "get_file": {
                "description": "Read a project-relative source file.",
                "inputSchema": {
                    "type": "object",
                    "required": ["path"],
                    "properties": {
                        "path": {"type": "string"},
                        "max_chars": {"type": "integer", "minimum": 1, "maximum": 100000},
                    },
                },
                "handler": lambda args: inspector.read_file(project_root, args["path"], int(args.get("max_chars", 20000))),
            },
            "find_symbol": {
                "description": "Find parsed symbols by name.",
                "inputSchema": {
                    "type": "object",
                    "required": ["query"],
                    "properties": {
                        "query": {"type": "string"},
                        "max_results": {"type": "integer", "minimum": 1, "maximum": 500},
                    },
                },
                "handler": lambda args: {
                    "matches": inspector.find_symbols(project_root, args["query"], int(args.get("max_results", 100)))
                },
            },
            "compose": {
                "description": "Compose focused project context for a task and optional target file.",
                "inputSchema": {
                    "type": "object",
                    "required": ["task"],
                    "properties": {
                        "task": {"type": "string"},
                        "target": {"type": "string"},
                        "budget": {"type": "integer", "minimum": 1, "maximum": 100000},
                    },
                },
                "handler": lambda args: {
                    "context": inspector.compose(
                        project_root,
                        args["task"],
                        args.get("target"),
                        int(args.get("budget", 12000)),
                    )
                },
            },
            "impact_analysis": {
                "description": "Return direct import dependencies and dependents for a file.",
                "inputSchema": {
                    "type": "object",
                    "required": ["path"],
                    "properties": {"path": {"type": "string"}},
                },
                "handler": lambda args: inspector.impact(project_root, args["path"]),
            },
            "get_graph": {
                "description": "Return the generated project graph.",
                "inputSchema": {"type": "object", "properties": {}},
                "handler": lambda _: inspector.load_graph(project_root),
            },
            "get_history": {
                "description": "Return EOS runtime, scan and backup history.",
                "inputSchema": {"type": "object", "properties": {}},
                "handler": lambda _: inspector.history(project_root),
            },
            "get_parent_implementation": {
                "description": (
                    "Search the linked parent project's symbols and return the real "
                    "source around each match. Requires a prior 'eos scan --with-parents'."
                ),
                "inputSchema": {
                    "type": "object",
                    "required": ["symbol"],
                    "properties": {
                        "symbol": {"type": "string"},
                        "max_results": {"type": "integer", "minimum": 1, "maximum": 50},
                    },
                },
                "handler": lambda args: inspector.get_parent_implementation(
                    project_root, args["symbol"], int(args.get("max_results", 10))
                ),
            },
            "add_note": {
                "description": (
                    "Record an authored note (a decision, root cause, or finding) so it "
                    "survives the next scan. This is the only tool that writes anything."
                ),
                "inputSchema": {
                    "type": "object",
                    "required": ["kind", "title"],
                    "properties": {
                        "kind": {"type": "string", "enum": list(notes.KINDS)},
                        "title": {"type": "string"},
                        "body": {"type": "string", "description": "Required for kind=finding"},
                        "tags": {"type": "array", "items": {"type": "string"}},
                        "scope": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Files or classes this note is about",
                        },
                        "source": {"type": "string", "description": "issue or PR reference, e.g. TICKET-123"},
                        "cause": {"type": "string", "description": "Required for kind=defect"},
                        "solution": {"type": "string", "description": "Required for kind=defect"},
                        "metric": {"type": "string", "description": "Required for kind=defect"},
                    },
                },
                "handler": lambda args: {
                    "path": str(
                        notes.add_note(
                            project_root,
                            kind=args["kind"],
                            title=args["title"],
                            body=args.get("body"),
                            tags=args.get("tags"),
                            scope=_explicit_scope(args),
                            source=args.get("source"),
                            cause=args.get("cause"),
                            solution=args.get("solution"),
                            metric=args.get("metric"),
                        )
                    )
                },
            },
            "search_notes": {
                "description": "Return previously recorded notes relevant to a query, most relevant first.",
                "inputSchema": {
                    "type": "object",
                    "required": ["query"],
                    "properties": {
                        "query": {"type": "string"},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                    },
                },
                "handler": lambda args: {
                    "notes": [
                        {
                            "title": note.title,
                            "kind": note.kind,
                            "tags": note.tags,
                            "scope": note.scope,
                            "source": note.source,
                            "body": note.body,
                        }
                        for note in notes.search_notes(
                            project_root, args["query"], limit=args.get("limit")
                        )
                    ]
                },
            },
        }

    def run(self) -> int:
        for line in sys.stdin:
            if not line.strip():
                continue
            try:
                request = json.loads(line)
                response = self._handle(request)
            except Exception as exc:
                response = self._error(None, -32603, str(exc))
            if response is not None:
                sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
                sys.stdout.flush()
        return 0

    def _handle(self, request: dict[str, Any]) -> dict[str, Any] | None:
        request_id = request.get("id")
        method = request.get("method")
        params = request.get("params") or {}

        if method == "initialize":
            return self._result(
                request_id,
                {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "eos", "version": self.version},
                },
            )
        if method in {"notifications/initialized", "notifications/cancelled"}:
            return None
        if method == "ping":
            return self._result(request_id, {})
        if method == "tools/list":
            tools = [
                {key: value for key, value in definition.items() if key != "handler"}
                | {"name": name}
                for name, definition in self.tools.items()
            ]
            return self._result(request_id, {"tools": tools})
        if method == "tools/call":
            name = params.get("name")
            definition = self.tools.get(name)
            if definition is None:
                return self._error(request_id, -32602, f"Unknown tool: {name}")
            try:
                value = definition["handler"](params.get("arguments") or {})
                text = json.dumps(value, ensure_ascii=False, indent=2)
                return self._result(request_id, {"content": [{"type": "text", "text": text}]})
            except Exception as exc:
                return self._result(
                    request_id,
                    {"content": [{"type": "text", "text": str(exc)}], "isError": True},
                )
        return self._error(request_id, -32601, f"Method not found: {method}")

    @staticmethod
    def _result(request_id: Any, result: Any) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    @staticmethod
    def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def serve(project_root: str, version: str) -> int:
    return McpServer(project_root, version).run()
