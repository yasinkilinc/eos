"""Minimal stdio MCP server for EOS project intelligence.

The exception surface, not the default (ADR-026): a project reaches EOS
through its CLI and hooks unless it chose `--surface mcp`. So the roster is
kept to what a CLI call would be clumsier for, and it only shrinks: `get_graph`
returned the whole generated graph (33.8 MB on one service; `eos graph
--output` exports it) and `compose` was an alias of `get_context` "kept for
one release" for eight. Two channels cost no roster at all, because a client
lists them only when a person asks: resources (`eos://brief`,
`eos://capabilities`, attached as `@eos:eos://brief`) and the `brief` prompt.
"""
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


def _impact_with(project_root, args: dict) -> dict:
    """impact_analysis, plus whatever `include` asks for.

    Folded into one tool rather than three. This workspace retired MCP servers
    over roster cost -- 18 servers came to 2,194 tokens of tool list per
    session -- so a thirteenth tool is paid for on every request, while a
    slightly broader one costs only at call time.
    """
    answer = inspector.impact(project_root, args["path"], depth=int(args.get("depth", 1)))
    include = [item for item in (args.get("include") or []) if isinstance(item, str)]
    if not include:
        return answer
    detail = inspector.why(project_root, args["path"]) if {"facts", "coverage"} & set(include) else {}
    if "facts" in include:
        answer["facts"] = detail.get("facts", [])
        answer["inbound_facts"] = detail.get("inbound", [])
    if "coverage" in include:
        answer["coverage"] = detail.get("coverage", [])
    if "history" in include:
        answer["history"] = inspector.file_history(project_root, args["path"])
    return answer


def _search_index(project_root, args: dict) -> dict:
    from core import index as _index

    database = _index.db_path(project_root)
    if not database.is_file():
        return {"results": [], "note": "No index yet. Run 'eos index' or 'eos scan'."}
    columns, rows = _index.search(database, args["query"], int(args.get("limit", 20)))
    return {"results": [dict(zip(columns, row)) for row in rows]}


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
                "description": (
                    "Return AI-oriented project context, budgeted in approximate tokens. "
                    "Pass `task` to rank the accumulated notes against it and `target` to "
                    "anchor on one file -- without a task the notes are ordered by recency, "
                    "which is the next best signal but rarely the right one. With `task` and "
                    "`route: true` the answer also carries `route`: the model and effort this "
                    "project's policy picks for the task, with its reason (ADR-025); pass a "
                    "small `budget` when the decision is all you want. `files`, `model` and "
                    "`effort` feed and override that decision."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "budget": {"type": "integer", "minimum": 1, "maximum": 100000},
                        "task": {"type": "string"},
                        "target": {"type": "string"},
                        "route": {"type": "boolean"},
                        "files": {"type": "array", "items": {"type": "string"}},
                        "model": {"type": "string"},
                        "effort": {"type": "string"},
                    },
                },
                "handler": self._context,
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
            "impact_analysis": {
                "description": (
                    "What a file reaches and what reaches it, out to `depth` hops. "
                    "`include` adds, per file: 'facts' (where each fact came from -- "
                    "detector, origin, confidence, path:line), 'coverage' (which "
                    "detectors looked at this project and what they found, so "
                    "'found nothing' is distinguishable from 'never looked'), and "
                    "'history' (the commits that touched this file)."
                ),
                "inputSchema": {
                    "type": "object",
                    "required": ["path"],
                    "properties": {
                        "path": {"type": "string"},
                        "depth": {"type": "integer", "minimum": 1, "maximum": 5},
                        "include": {
                            "type": "array",
                            "items": {"enum": ["facts", "coverage", "history"]},
                        },
                    },
                },
                "handler": lambda args: _impact_with(project_root, args),
            },
            "search_index": {
                "description": (
                    "Full-text search across everything indexed: authored notes, the "
                    "brain documents, and whatever this project's index extensions "
                    "contribute. Returns (source, ref, title, snippet) -- use get_file "
                    "or search_notes to read a hit in full."
                ),
                "inputSchema": {
                    "type": "object",
                    "required": ["query"],
                    "properties": {
                        "query": {"type": "string"},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                    },
                },
                "handler": lambda args: _search_index(project_root, args),
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

    RESOURCES = (
        {"uri": "eos://brief", "name": "brief", "mimeType": "text/plain",
         "description": "What is in flight here, runs left open, and the notes matching this branch."},
        {"uri": "eos://capabilities", "name": "capabilities", "mimeType": "text/plain",
         "description": "The wrappers this project offers instead of raw commands (capabilities.toml)."},
    )
    PROMPTS = (
        {"name": "brief",
         "description": "The EOS brief for a task: its recorded procedure, last runs and lesson, "
                        "the wrappers it calls for.",
         "arguments": [{"name": "task", "description": "The task, as you would type it",
                        "required": True}]},
    )

    def _resource(self, uri: str) -> str:
        if uri == "eos://brief":
            from core import brief

            return brief.build(self.project_root) or "Nothing in flight here and no notes match this branch."
        if uri == "eos://capabilities":
            from core import capabilities

            declared = capabilities.load(self.project_root)
            return "\n".join(c.line() for c in declared) or "No capabilities declared."
        raise ValueError(f"unknown resource: {uri}")

    def _prompt(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name != "brief":
            raise ValueError(f"unknown prompt: {name}")
        task = str(arguments.get("task") or "").strip()
        if not task:
            raise ValueError("the brief prompt needs a task")
        from core import brief

        text = brief.build(self.project_root, task=task) or "EOS has nothing recorded for this task."
        return {"description": "EOS brief for the task",
                "messages": [{"role": "user", "content": {"type": "text", "text": text}}]}

    def _context(self, args: dict[str, Any]) -> dict[str, Any]:
        """`get_context`, widened with the routing decision rather than a new tool:
        the roster's size is paid on every request (test_mcp_index_tools)."""
        answer: dict[str, Any] = {"context": inspector.build_context(
            self.project_root, int(args.get("budget", 12000)),
            task=args.get("task"), target=args.get("target"))}
        if args.get("route") and args.get("task"):
            import core.routing as routing
            from core.routing import config

            if not config.load(self.project_root).enabled:
                answer["route"] = {"disabled": "[model_routing] enabled = false in this project"}
            else:
                # record=False: the server is read-only.
                answer["route"] = routing.route(
                    self.project_root, args["task"], files=tuple(args.get("files") or ()),
                    model=args.get("model"), effort=args.get("effort"), record=False).to_dict()
        return answer

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
                    "capabilities": {"tools": {"listChanged": False},
                                     "resources": {"listChanged": False},
                                     "prompts": {"listChanged": False}},
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
        if method == "resources/list":
            return self._result(request_id, {"resources": list(self.RESOURCES)})
        if method == "resources/read":
            uri = str(params.get("uri") or "")
            try:
                text = self._resource(uri)
            except ValueError as exc:
                return self._error(request_id, -32602, str(exc))
            return self._result(request_id, {"contents": [{"uri": uri, "mimeType": "text/plain",
                                                           "text": text}]})
        if method == "prompts/list":
            return self._result(request_id, {"prompts": list(self.PROMPTS)})
        if method == "prompts/get":
            try:
                value = self._prompt(str(params.get("name") or ""), params.get("arguments") or {})
            except ValueError as exc:
                return self._error(request_id, -32602, str(exc))
            return self._result(request_id, value)
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
