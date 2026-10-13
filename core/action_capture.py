"""How many of a project's editor and wrapper actions reached the execution
ledger as events (2.x roadmap E1, measured as its acceptance defines it).

E1's gate is ">= 95% of wrapper and editor actions in 20 sessions appear as
events". `consolidate.change_capture` measures a proxy -- files a run's commits
touched against files its `changed` events named -- whose misses are mostly
files programs wrote, which no editor hook can see. This counts the actions.

Read from the harness's own transcripts (`context_cost.transcripts_dir`, the
folder `eos cost --context` reads), so like that module this is an
integration with the harness's format, not engine logic:

  session         a main transcript (`<folder>/<id>.jsonl`) with the subagent
                  transcripts it spawned (`<folder>/<id>/subagents/*.jsonl`);
                  the newest `sessions` of them by modification time
  editor action   an Edit, Write, MultiEdit or NotebookEdit tool call; captured
                  when any event read carries its `tool_use_id` (the hooks'
                  `changed` events do, `core/hooks.py::_post_tool`)
  wrapper action  a Bash call that runs a capability's program as a command of
                  its own (`capabilities.invoked`, not a mere mention of its
                  file); captured when an event that wrapper recorded -- same
                  tool, same session, not written by a hook -- falls between
                  the call and its result, `SLACK` seconds either side. A
                  wrapper records through `eos-event`, which never learns the
                  harness's call id, so time is the only key; each event
                  answers one call
  outside any run no run of the session was open in any ledger read when the
                  call was made: no writer had a run to record into. Counted
                  apart and left out of the share, so the share says what the
                  hooks and wrappers could have captured
  never ran       the harness refused the call before it ran (a tool-use
                  error, a PreToolUse hook's block, a permission denial): no
                  action happened. Counted apart, out of the share
  scratch         an editor action on a temp-directory file, which the hook
                  leaves out on purpose (`hooks._relative`). Counted apart

  share = captured / measured, measured = actions minus the three apart;
  None (printed `—`, `core/lib/honest.py`) when nothing was measured.

The ledgers read are the project's own and, in a workspace, the workspace's
and every listed project's: a run started from the workspace root can live in
a project's ledger (C5b). Nothing here writes anything.
"""
from __future__ import annotations

import collections
import datetime
import json
import re
from pathlib import Path

SESSIONS = 20
SLACK = 3
EDITOR_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")
# What the harness writes as the result of a call it never ran.
_NEVER_RAN = re.compile(r"^(?:<tool_use_error>|PreToolUse:\S+ hook error|Permission for this action was denied"
                        r"|The user doesn't want to proceed)")
# Structural keys only: inside a JSON string a quote is escaped, so a tool input
# that merely mentions "tool_result" does not match these.
_USE = re.compile(r'"type":\s?"tool_use"')
_RESULT = re.compile(r'"type":\s?"tool_result"')
_IS_ERROR = re.compile(r'"is_error":\s?true')
_RESULT_ID = re.compile(r'"tool_use_id":\s?"([^"]+)"')
_TIMESTAMP = re.compile(r'"timestamp":\s?"([^"]+)"')


def _seconds(text) -> float | None:
    if not isinstance(text, str) or not text:
        return None
    try:
        return datetime.datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _result_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(part.get("text") or "" for part in content if isinstance(part, dict))
    return ""


def _calls(path: Path) -> tuple[dict, dict]:
    """(tool calls by id, results by id) of one transcript. A result line that
    is no error is not decoded: its id and time are all that is needed, and
    those lines carry every tool's output -- most of a transcript's bytes."""
    uses: dict[str, dict] = {}
    results: dict[str, tuple[float | None, bool]] = {}
    try:
        handle = open(path, encoding="utf-8", errors="replace")
    except OSError:
        return uses, results
    with handle:
        for line in handle:
            if "tool_" not in line:
                continue
            if not _USE.search(line):
                if not _RESULT.search(line):
                    continue
                if not _IS_ERROR.search(line):
                    stamp = _TIMESTAMP.search(line)
                    at = _seconds(stamp.group(1)) if stamp else None
                    for tool_use_id in _RESULT_ID.findall(line):
                        results.setdefault(tool_use_id, (at, False))
                    continue
                # An error is decoded below: what it says decides "never ran".
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if not isinstance(entry, dict):
                continue
            message = entry.get("message")
            content = message.get("content") if isinstance(message, dict) else None
            if not isinstance(content, list):
                continue
            at = _seconds(entry.get("timestamp"))
            for part in content:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "tool_use" and part.get("id"):
                    tool_input = part.get("input")
                    uses[part["id"]] = {"name": part.get("name") or "", "at": at,
                                        "input": tool_input if isinstance(tool_input, dict) else {},
                                        "session": str(entry.get("sessionId") or path.stem)}
                elif part.get("type") == "tool_result" and part.get("tool_use_id"):
                    never = bool(part.get("is_error")) and bool(_NEVER_RAN.match(_result_text(part.get("content"))))
                    results[part["tool_use_id"]] = (at, never)
    return uses, results


def _ledger_records(root: Path, own: list | None) -> list:
    """The project's own records (`own` when the caller already folded them)
    and every other ledger of its workspace."""
    from core import executions, workspace

    hub = workspace.of(root) or root
    mine = executions.path_for(root).resolve()
    ledgers = {executions.path_for(hub).resolve()} | {executions.path_for(p.root).resolve()
                                                      for p in workspace.load_quiet(hub)}
    records = list(own) if own is not None else executions.load_path(mine)
    for ledger in sorted(ledgers - {mine}):
        if ledger.is_file():
            records.extend(executions.load_path(ledger))
    return records


def _half() -> dict:
    return {"actions": 0, "captured": 0, "measured": 0, "outside_run": 0, "never_ran": 0, "scratch": 0,
            "share": None}


def report(project_root: str | Path, *, transcripts: str | Path | None = None, sessions: int = SESSIONS,
           records: list | None = None) -> dict:
    """`records`: the project's own ledger, already folded by the caller; the
    workspace's other ledgers are read here either way."""
    from core import capabilities, context_cost, executions, hooks
    from core.lib import honest

    root = Path(project_root).expanduser().resolve()
    folder = Path(transcripts) if transcripts else context_cost.transcripts_dir(root)
    try:
        declared = capabilities.load(root)
    except Exception:  # noqa: BLE001 - without a registry no Bash call is a wrapper's
        declared = []
    try:
        mains = sorted(folder.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)[:max(sessions, 0)]
    except OSError:
        mains = []

    # A command that names no wrapper at all cannot run one: the parse is paid
    # only by those that do.
    named = re.compile("|".join(re.escape(c.program) for c in declared if c.program)) if any(
        c.program for c in declared) else None
    actions = []
    for main in mains:
        for part in [main, *sorted((main.parent / main.stem / "subagents").glob("*.jsonl"))]:
            uses, results = _calls(part)
            for tool_use_id, use in uses.items():
                end, never = results.get(tool_use_id, (None, False))
                if use["name"] in EDITOR_TOOLS:
                    path = str(use["input"].get("file_path") or use["input"].get("notebook_path") or "")
                    actions.append({"kind": "editor", "id": tool_use_id, "at": use["at"], "end": end,
                                    "never": never, "session": use["session"],
                                    "scratch": bool(path) and hooks._relative(root, path) is None})
                elif use["name"] == "Bash" and named is not None:
                    command = str(use["input"].get("command") or "")
                    found = capabilities.invoked(command, declared) if named.search(command) else []
                    if found:
                        actions.append({"kind": "wrapper", "id": tool_use_id, "at": use["at"], "end": end,
                                        "never": never, "session": use["session"], "scratch": False,
                                        "background": bool(use["input"].get("run_in_background")),
                                        "tools": {executions.normalize_tool(c.program) for c in found}})

    records = _ledger_records(root, records)
    ids: set[str] = set()
    wrapper_events: dict[str, list[list]] = collections.defaultdict(list)
    runs: dict[str, list[tuple[float, float]]] = collections.defaultdict(list)
    for record in records:
        if record.session:
            start = _seconds(record.started_at)
            if start is not None:
                runs[record.session].append((start, _seconds(record.finished_at) or float("inf")))
        for event in record.events:
            if event.tool_use_id:
                ids.add(event.tool_use_id)
            if event.source != "hook" and event.tool and event.session:
                at = _seconds(event.at)
                if at is not None:
                    wrapper_events[event.session].append([at, event.tool, False])
    for found in wrapper_events.values():
        found.sort(key=lambda item: item[0])

    halves = {"editor": _half(), "wrapper": _half()}
    missed_by_tool: collections.Counter = collections.Counter()
    for action in sorted(actions, key=lambda a: a["at"] or 0.0):
        half = halves[action["kind"]]
        half["actions"] += 1
        at = action["at"]
        if action["never"]:
            half["never_ran"] += 1
            continue
        if action["scratch"]:
            half["scratch"] += 1
            continue
        if at is None or not any(start - 1 <= at <= end + 1 for start, end in runs.get(action["session"], ())):
            half["outside_run"] += 1
            continue
        half["measured"] += 1
        if action["kind"] == "editor":
            captured = action["id"] in ids
        else:
            until = float("inf") if action["background"] or action["end"] is None else action["end"] + SLACK
            captured = False
            for candidate in wrapper_events.get(action["session"], ()):
                if candidate[0] > until:
                    break
                if not candidate[2] and candidate[1] in action["tools"] and candidate[0] >= at - SLACK:
                    candidate[2] = captured = True
                    break
            if not captured:
                missed_by_tool.update(sorted(action["tools"])[:1])
        half["captured"] += captured
    for half in halves.values():
        half["share"] = round(half["captured"] / half["measured"], 4) if half["measured"] else None
    halves["wrapper"]["missed_by_tool"] = dict(missed_by_tool.most_common())
    captured = sum(h["captured"] for h in halves.values())
    measured = sum(h["measured"] for h in halves.values())
    return {"transcripts": str(folder), "sessions": len(mains), **halves,
            "captured": captured, "measured": measured,
            "share": round(captured / measured, 4) if measured else None,
            **{key: sum(h[key] for h in halves.values()) for key in ("outside_run", "never_ran", "scratch")},
            "provenance": {"sessions": honest.MEASURED, "editor": honest.MEASURED, "captured": honest.MEASURED,
                           "measured": honest.MEASURED, "share": honest.MEASURED,
                           # matched by time, not by an id the wrapper never had (SLACK)
                           "wrapper": honest.DERIVED}}


def render_line(data: dict) -> str | None:
    """`ACTION CAPTURE  c/m (share) over N sessions: editor a/b, wrapper c/d; ...`,
    None when no session was read."""
    from core.lib import honest

    if not data or not data.get("sessions"):
        return None

    def half(name: str) -> str:
        part = data[name]
        return f"{name} {part['captured']}/{part['measured']}" if part["measured"] else f"{name} {honest.NOT_MEASURED}"

    total = (f"{data['captured']}/{data['measured']} ({honest.show(data['share'], spec='.0%')})"
             if data["measured"] else honest.NOT_MEASURED)
    line = (f"ACTION CAPTURE  {total} over {data['sessions']} sessions: {half('editor')}, {half('wrapper')}; "
            f"{data['outside_run']} outside any run")
    apart = data["never_ran"] + data["scratch"]
    if apart:
        line += f", {apart} not recordable ({data['never_ran']} never ran, {data['scratch']} scratch files)"
    missed = data["wrapper"].get("missed_by_tool") or {}
    if missed:
        line += "\n  wrapper calls with no event: " + ", ".join(f"{tool} {n}" for tool, n in list(missed.items())[:8])
    return line
