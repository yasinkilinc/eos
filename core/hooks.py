"""Harness hooks, handled in one place (ADR-026).

`eos hook <event>` reads the event a harness hands its hooks on stdin and does
the one thing EOS does for it. A harness plugin registers each event once and
names the engine, instead of copying a script per event into every project --
the copies were written into thirteen service repositories of one workspace
and, sessions starting one directory up, never ran in any of them.

    session-start        the brief; after a compaction or /clear, forget which
                         task briefs were delivered so they can arrive again
    user-prompt          the task brief, once per distinct text per session
    post-tool            one event on the open run per action worth reading
                         (source: hook, deduplicated by the call id); a raw
                         command a capability covers is recorded as a bypass
                         and answered, once per session, with the wrapper
    post-tool-failure    the same, status: error, exit code from the harness
    subagent-start/stop  who did the work: the subagent's type and id, and how
                         many references its final answer cited
    instructions-loaded  which instruction file a session loaded, and why
                         (.eos/data/loaded.jsonl) -- proof a scoped rule arrived
    stop                 fold the transcript's model/effort/token counts into
                         routing usage (detached; where routing is configured),
                         and ask once about work items and runs this session
                         left open -- every honest answer closes the question
    stop-failure         a run cut short by the harness (rate limit, overload)
    session-end          one summary line per session (.eos/data/sessions.jsonl)
    pre-agent            the routing decision for an untyped subagent (ADR-025):
                         recorded only while `hook_dry_run`, applied otherwise

Four rules, from ADR-021 and the ledger's own:

- **It always exits 0 and never writes to stderr.** A hook that fails noisily
  is a hook somebody deletes, and the loop goes with it.
- **The payload's field names are read in one function** (`normalize`). What
  a harness calls a session, a subagent or a tool call is its business; what
  the engine records is the same whichever harness sent it.
- **Nothing a person typed is stored.** A prompt is a query; a command line
  is reduced to program names (`capabilities.programs`); a subagent's answer
  to a count of the references in it (ADR-019).
- **No run open, nothing recorded** -- except the per-session counters the
  summary line is built from, which live in a state directory, not the ledger.

`[hooks]` in `.eos/config.toml` turns each part off; all are on by default.
A host that delivers its own brief (a workspace briefing several projects at
once) sets `brief = false` and keeps the rest.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

# Where per-session hook state lives when the harness gives a plugin its own
# directory (the plugin exports it); otherwise beside the run pointers.
STATE_ENV = "EOS_HOOK_STATE_DIR"
SETTINGS = ("brief", "capture", "hints", "subagents", "loaded", "sessions", "close", "usage")
MAX_TASK_CHARS = 2000
# The harness delivers its own notices through the prompt event.
_NOTICE_MARKS = ("[SYSTEM NOTIFICATION", "<task-notification>", "<system-reminder>",
                 "<local-command-caveat>", "<command-name>")
_EXIT = re.compile(r"exit code (-?\d+)", re.I)
# A reference a subagent's answer can be checked against: path:line, a
# markdown link to a line, a note, a run id.
_CITATION = re.compile(r"[\w./-]+\.\w{1,6}:\d+|\]\([^)\s]+#L\d+|\bnote:\S+|\bx-[a-z0-9]+-[0-9a-f]{4}\b")
SUBAGENT_TOOLS = ("Agent", "Task")
SUBAGENT_MODELS = ("haiku", "sonnet", "opus", "fable")
GENERIC_TYPES = ("", "general-purpose")
EDIT_TOOLS = {"Edit": "edit", "Write": "write", "NotebookEdit": "notebook", "MultiEdit": "edit"}
MAX_PROGRAMS = 3
LOADED_FILE = "loaded.jsonl"
SESSIONS_FILE = "sessions.jsonl"
MAX_LOG_BYTES = 2 * 1024 * 1024


@dataclasses.dataclass
class Hook:
    event: str = ""
    session: str = ""
    cwd: str = ""
    agent_id: str = ""
    agent_type: str = ""
    tool: str = ""
    tool_input: dict = dataclasses.field(default_factory=dict)
    tool_use_id: str = ""
    error: str = ""
    interrupted: bool = False
    duration_ms: int | None = None
    source: str = ""          # session-start: startup | resume | clear | compact
    reason: str = ""          # session-end
    load_reason: str = ""
    file_path: str = ""
    memory_type: str = ""
    trigger: str = ""
    last_message: str = ""
    error_type: str = ""
    prompt: str = ""
    effort: str = ""
    transcript: str = ""
    stop_active: bool = False

    @property
    def command(self) -> str:
        for key in ("command", "cmd", "script"):
            value = self.tool_input.get(key)
            if isinstance(value, str):
                return value
        return ""

    @property
    def actor(self) -> str:
        return self.agent_id or "main"


def normalize(payload: dict) -> Hook:
    """A harness's hook payload, in the engine's terms.

    Claude Code 2.1.281, as recorded by a probe of every event: `session_id`,
    `cwd`, `agent_id`/`agent_type` only inside a subagent, `tool_input`,
    `tool_use_id`, `duration_ms`; a failed tool call arrives as its own event
    with `error: "Exit code N"`; `effort: {level}` only on models that take one.
    Devin's shell hook may send `arguments` (sometimes JSON text) and `cmd`.
    """
    def text(*keys) -> str:
        for key in keys:
            value = payload.get(key)
            if isinstance(value, (str, int, float)) and str(value).strip():
                return str(value)
        return ""

    tool_input = payload.get("tool_input", payload.get("arguments", {}))
    if isinstance(tool_input, str):
        try:
            tool_input = json.loads(tool_input)
        except ValueError:
            tool_input = {"command": tool_input}
    effort = payload.get("effort")
    duration = payload.get("duration_ms")
    return Hook(
        event=text("hook_event_name"), session=text("session_id", "sessionId")[:64],
        cwd=text("cwd"), agent_id=text("agent_id"), agent_type=text("agent_type"),
        tool=text("tool_name", "tool"), tool_input=tool_input if isinstance(tool_input, dict) else {},
        tool_use_id=text("tool_use_id"), error=text("error"),
        interrupted=bool(payload.get("is_interrupt")),
        duration_ms=duration if isinstance(duration, int) and not isinstance(duration, bool) else None,
        source=text("source"), reason=text("reason"), load_reason=text("load_reason"),
        file_path=text("file_path"), memory_type=text("memory_type"),
        trigger=text("trigger_file_path"), last_message=text("last_assistant_message"),
        error_type=text("error_type", "error"), prompt=text("prompt", "user_input"),
        effort=str(effort.get("level") or "") if isinstance(effort, dict) else "",
        transcript=text("transcript_path"), stop_active=bool(payload.get("stop_hook_active")),
    )


# --- entry point ---------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """`eos hook <event> [--agent NAME] [--project PATH]`, the event JSON on stdin."""
    started = time.perf_counter()
    args = list(argv or [])
    event = args.pop(0) if args else ""
    agent, project = None, None
    while args:
        flag = args.pop(0)
        if flag == "--agent" and args:
            agent = args.pop(0)
        elif flag == "--project" and args:
            project = args.pop(0)
    root, hook, output = None, Hook(), ""
    try:
        raw = sys.stdin.read() if not sys.stdin.isatty() else ""
        payload = json.loads(raw) if raw.strip() else {}
        hook = normalize(payload if isinstance(payload, dict) else {})
        root = project_root(project or hook.cwd or os.environ.get("EOS_PROJECT_DIR") or os.getcwd())
        handler = HANDLERS.get(event)
        if root is not None and handler is not None:
            output = handler(root, hook, settings(root), agent) or ""
    except Exception:  # noqa: BLE001 - a hook never disturbs the session it serves
        output = ""
    if output:
        try:
            sys.stdout.write(output if output.endswith("\n") else output + "\n")
        except Exception:  # noqa: BLE001
            pass
    if root is not None:
        _telemetry(root, event, started, len(output), hook.session)
    return 0


def project_root(start: str | os.PathLike | None) -> Path | None:
    """The nearest directory at or above `start` holding `.eos/`."""
    if not start:
        return None
    try:
        path = Path(start).expanduser().resolve()
    except OSError:
        return None
    for candidate in (path, *path.parents):
        if (candidate / ".eos").is_dir():
            return candidate
    return None


def settings(root: Path) -> dict:
    values = {name: True for name in SETTINGS}
    try:
        from core.lib.config_io import ConfigIO

        table = ConfigIO.read_toml(root / ".eos" / "config.toml").get("hooks") or {}
    except Exception:  # noqa: BLE001 - a malformed table leaves the defaults
        return values
    for name in SETTINGS:
        if isinstance(table.get(name), bool):
            values[name] = table[name]
    return values


def _telemetry(root: Path, event: str, started: float, chars: int, session: str) -> None:
    try:
        from core import telemetry

        telemetry.record(root, "hook", [event or "?"], (time.perf_counter() - started) * 1000,
                         chars=chars, session=session or None,
                         session_from="hook" if session else None)
    except Exception:  # noqa: BLE001
        pass


def _context(event_name: str, text: str) -> str:
    return json.dumps({"hookSpecificOutput": {"hookEventName": event_name,
                                              "additionalContext": text}}, ensure_ascii=False)


# --- per-session state: append-only, folded on read ----------------------------------


def _state_dir() -> Path:
    configured = os.environ.get(STATE_ENV)
    if configured:
        return Path(configured).expanduser()
    from core import executions

    return executions._state_dir() / "hooks"


def _state_file(session: str) -> Path:
    safe = "".join(ch for ch in session if ch.isalnum() or ch in "-_.")[:64] or "unnamed"
    return _state_dir() / f"{safe}.jsonl"


def _note_state(session: str, **fields) -> None:
    if not session:
        return
    path = _state_file(session)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(fields, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _state(session: str) -> list[dict]:
    if not session:
        return []
    try:
        text = _state_file(session).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    lines = []
    for line in text.splitlines():
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            lines.append(value)
    return lines


def _prompted_file(session: str) -> Path:
    from core import executions

    safe = "".join(ch for ch in session if ch.isalnum() or ch in "-_.")[:64] or "unnamed"
    return executions._state_dir() / "prompted" / safe


# --- events on the open run ------------------------------------------------------------


def _open_run(root: Path, session: str):
    from core import executions

    if not session:
        return None
    try:
        return executions.current(root, session)
    except Exception:  # noqa: BLE001
        return None


def _record(root: Path, hook: Hook, found, **fields) -> bool:
    """Append to the run the session's pointer names, in the ledger it names --
    which, in a workspace, can be another project's (a run opened in a service
    while the session stands one directory up)."""
    from core import executions

    execution, ledger = found
    previous = os.environ.get(executions.EXECUTION_ENV), os.environ.get(executions.LEDGER_ENV)
    os.environ[executions.EXECUTION_ENV], os.environ[executions.LEDGER_ENV] = execution, str(ledger)
    try:
        entry = executions.event(root, None, session=hook.session, source="hook",
                                 agent=hook.agent_type or None, **fields)
    except Exception:  # noqa: BLE001 - capture never fails the session
        entry = None
    finally:
        for name, value in zip((executions.EXECUTION_ENV, executions.LEDGER_ENV), previous):
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
    if entry is not None:
        _note_state(hook.session, recorded=fields.get("kind"), status=fields.get("status") or "ok")
    return entry is not None


def _relative(root: Path, path: str) -> str | None:
    """A path the ledger may hold: project-relative, or absolute outside any temp dir."""
    if not path:
        return None
    try:
        resolved = Path(path).expanduser().resolve()
    except OSError:
        return None
    try:
        return str(resolved.relative_to(root))
    except ValueError:
        pass
    temp_roots = [Path(p).resolve() for p in {os.environ.get("TMPDIR", ""), "/tmp", "/private/tmp",
                                              "/var/folders", "/private/var/folders"} if p]
    if any(resolved == t or t in resolved.parents for t in temp_roots):
        return None
    return str(resolved)


# --- handlers -------------------------------------------------------------------------


def _session_start(root: Path, hook: Hook, cfg: dict, agent: str | None) -> str:
    if hook.source in ("compact", "clear") and hook.session:
        # The context that held the delivered briefs is gone; the dedup that
        # kept them from repeating would now keep them from arriving at all.
        try:
            _prompted_file(hook.session).unlink()
        except OSError:
            pass
        _note_state(hook.session, reset=hook.source)
    if not cfg["brief"]:
        return ""
    from core import brief

    return brief.build(root, session=hook.session or None, agent=agent) or ""


def _user_prompt(root: Path, hook: Hook, cfg: dict, agent: str | None) -> str:
    prompt = hook.prompt.strip()[:MAX_TASK_CHARS]
    if not cfg["brief"] or not prompt or any(mark in prompt.lstrip()[:200] for mark in _NOTICE_MARKS):
        return ""
    from core import brief

    text = (brief.build(root, session=hook.session or None, agent=agent, task=prompt,
                        task_only=True) or "").strip()
    if not text:
        return ""
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    seen = _prompted_file(hook.session) if hook.session else None
    if seen is not None:
        try:
            if digest in {line.strip() for line in seen.read_text(encoding="utf-8").splitlines()}:
                return ""
        except OSError:
            pass
        try:
            seen.parent.mkdir(parents=True, exist_ok=True)
            with open(seen, "a", encoding="utf-8") as handle:
                handle.write(digest + "\n")
        except OSError:
            pass
    return text


def _post_tool(root: Path, hook: Hook, cfg: dict, agent: str | None, failed: bool = False) -> str:
    exit_code = None
    if failed:
        found_exit = _EXIT.search(hook.error or "")
        exit_code = int(found_exit.group(1)) if found_exit else None
    status = "error" if failed else "ok"
    event_name = "PostToolUseFailure" if failed else "PostToolUse"
    run = _open_run(root, hook.session) if cfg["capture"] else None

    if hook.tool in EDIT_TOOLS:
        if run is not None:
            ref = _relative(root, str(hook.tool_input.get("file_path") or hook.tool_input.get("notebook_path") or ""))
            if ref:
                _record(root, hook, run, kind="changed", tool=EDIT_TOOLS[hook.tool], ref=ref,
                        status=status, tool_use_id=hook.tool_use_id or None, ms=hook.duration_ms)
        return ""
    if hook.tool != "Bash":
        if run is not None and failed and hook.tool:
            _record(root, hook, run, kind="called", tool=hook.tool.lower(), status=status,
                    tool_use_id=hook.tool_use_id or None, ms=hook.duration_ms)
        return ""

    from core import capabilities

    command = hook.command
    try:
        declared = capabilities.load(root)
    except Exception:  # noqa: BLE001 - a broken registry costs the hint, never the call
        declared = []
    found = capabilities.match(command, declared) if declared else None
    output = ""
    if found is not None:
        status = "bypass"
        name = found.capability.name
        _note_state(hook.session, bypass=name, tier=found.tier, actor=hook.actor)
        if cfg["hints"] and hook.session:
            hinted = {(line.get("hint"), line.get("actor")) for line in _state(hook.session)}
            if (name, hook.actor) not in hinted:
                _note_state(hook.session, hint=name, actor=hook.actor)
                output = _context(event_name, "EOS: " + capabilities.remedy(found)
                                  + " (`eos capabilities .` lists every wrapper here.)")
    if run is None or capabilities.wrapper_in(command, declared) is not None:
        # A registered wrapper records its own call (eos-event); counting it
        # here as well would make every wrapper call look like two.
        return output
    recordable = [(program, verb) for program, verb in capabilities.programs(command)
                  if capabilities.worth_recording(program, verb)]
    if not recordable and (failed or status == "bypass"):
        head = capabilities.programs(command)
        recordable = head[:1] or [("bash", None)]
    for index, (program, verb) in enumerate(recordable[:MAX_PROGRAMS]):
        key = hook.tool_use_id if index == 0 else (f"{hook.tool_use_id}#{index}" if hook.tool_use_id else "")
        _record(root, hook, run, kind="ran", tool=program, body=verb if program == "git" else None,
                status=status, exit_code=exit_code if index == len(recordable[:MAX_PROGRAMS]) - 1 else None,
                tool_use_id=key or None, ms=hook.duration_ms if index == 0 else None)
    return output


def _post_tool_failure(root: Path, hook: Hook, cfg: dict, agent: str | None) -> str:
    if hook.interrupted:
        return ""
    return _post_tool(root, hook, cfg, agent, failed=True)


def _subagent_start(root: Path, hook: Hook, cfg: dict, agent: str | None) -> str:
    if not (cfg["subagents"] and cfg["capture"]) or not hook.agent_id:
        return ""
    run = _open_run(root, hook.session)
    _note_state(hook.session, subagent=hook.agent_type or "?", started=hook.agent_id)
    if run is not None:
        parent = dataclasses.replace(hook, agent_type="")  # the parent starts it
        _record(root, parent, run, kind="called", tool="subagent", target=hook.agent_type or None,
                ref=f"agent:{hook.agent_id}", tool_use_id=f"start:{hook.agent_id}")
    return ""


def _subagent_stop(root: Path, hook: Hook, cfg: dict, agent: str | None) -> str:
    if not (cfg["subagents"] and cfg["capture"]) or not hook.agent_id:
        return ""
    citations = len(_CITATION.findall(hook.last_message or ""))
    _note_state(hook.session, subagent=hook.agent_type or "?", stopped=hook.agent_id, citations=citations)
    run = _open_run(root, hook.session)
    if run is not None:
        _record(root, hook, run, kind="verified", tool="subagent", target=hook.agent_type or None,
                ref=f"agent:{hook.agent_id}", body=f"{citations} citation(s) in its answer",
                tool_use_id=f"stop:{hook.agent_id}")
    return ""


def _stop_failure(root: Path, hook: Hook, cfg: dict, agent: str | None) -> str:
    kind = re.sub(r"[^a-z_]", "", (hook.error_type or "unknown").lower())[:32] or "unknown"
    _note_state(hook.session, stop_failure=kind)
    run = _open_run(root, hook.session) if cfg["capture"] else None
    if run is not None:
        _record(root, hook, run, kind="noted", tool="harness", status="error",
                body=f"stopped by the harness: {kind}")
    return ""


def _append_log(root: Path, name: str, entry: dict) -> None:
    path = root / ".eos" / "data" / name
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_file() and path.stat().st_size > MAX_LOG_BYTES:
            kept = path.read_text(encoding="utf-8", errors="replace").splitlines()[-2000:]
            path.write_text("\n".join(kept) + "\n", encoding="utf-8")
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _utc_now() -> str:
    from core.knowledge.evidence import utc_now

    return utc_now()


def _instructions_loaded(root: Path, hook: Hook, cfg: dict, agent: str | None) -> str:
    if not cfg["loaded"] or not hook.file_path:
        return ""
    home = str(Path.home())
    path = _relative(root, hook.file_path) or hook.file_path
    if path.startswith(home):
        path = "~" + path[len(home):]
    entry = {"at": _utc_now(), "session": hook.session or None, "path": path,
             "load_reason": hook.load_reason or None, "memory_type": hook.memory_type or None,
             "trigger": _relative(root, hook.trigger) if hook.trigger else None,
             "agent": hook.agent_type or None}
    _append_log(root, LOADED_FILE, entry)
    return ""


def _session_end(root: Path, hook: Hook, cfg: dict, agent: str | None) -> str:
    """One line per session, from the counters the other hooks left. O(state)."""
    lines = _state(hook.session)
    if cfg["sessions"] and hook.session:
        bypass: dict[str, int] = {}
        recorded: dict[str, int] = {}
        for line in lines:
            if line.get("bypass"):
                bypass[line["bypass"]] = bypass.get(line["bypass"], 0) + 1
            if line.get("recorded"):
                key = f"{line['recorded']}:{line.get('status', 'ok')}"
                recorded[key] = recorded.get(key, 0) + 1
        entry = {"at": _utc_now(), "session": hook.session, "reason": hook.reason or None,
                 "events_recorded": recorded, "bypass": bypass,
                 "hints": sum(1 for line in lines if line.get("hint")),
                 "subagents": sum(1 for line in lines if line.get("started")),
                 "resets": [line["reset"] for line in lines if line.get("reset")],
                 "stop_failures": [line["stop_failure"] for line in lines if line.get("stop_failure")]}
        _append_log(root, SESSIONS_FILE, entry)
    for stale in (_state_file(hook.session), _prompted_file(hook.session)) if hook.session else ():
        try:
            stale.unlink()
        except OSError:
            pass
    return ""


def _pre_agent(root: Path, hook: Hook, cfg: dict, agent: str | None) -> str:
    """Route an untyped subagent call (ADR-025), or only record what would be routed.

    The same four rules as the per-project hook it replaces: an explicit model
    is never touched, a named subagent keeps its own definition, only a model
    the harness's subagent tool takes is written, and effort is left alone.
    """
    if hook.tool not in SUBAGENT_TOOLS:
        return ""
    from core.routing import config as routing_config

    routing = routing_config.load(root)
    if not (routing.configured and routing.enabled and routing.hook):
        return ""
    if hook.tool_input.get("model") or str(hook.tool_input.get("subagent_type") or "").strip() not in GENERIC_TYPES:
        return ""
    task = str(hook.tool_input.get("prompt") or hook.tool_input.get("description") or "").strip()[:MAX_TASK_CHARS]
    if not task:
        return ""
    import core.routing as route_module

    # A subagent's prompt is its own task: classified on its own, while the
    # run's decision stays the run's (ADR-025 reuses it for the run's task).
    decision = route_module.route(root, task, session=hook.session or None, record=False, fresh=True)
    if decision.model not in SUBAGENT_MODELS:
        return ""
    if routing.hook_dry_run:
        _note_state(hook.session, would_route=decision.model, level=decision.level)
        run = _open_run(root, hook.session)
        if run is not None:
            _record(root, hook, run, kind="decided", tool="route", ref=f"route:{decision.task_hash}",
                    body=f"dry run: a subagent would get {decision.model} ({decision.task_type} {decision.level})",
                    tool_use_id=f"route:{hook.tool_use_id}" if hook.tool_use_id else None)
        return ""
    return json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                              "updatedInput": {"model": decision.model}}})


def _stop(root: Path, hook: Hook, cfg: dict, agent: str | None) -> str:
    if cfg["usage"] and hook.transcript and hook.session:
        _fold_usage(root, hook)
    if not cfg["close"] or hook.stop_active or not hook.session or hook.agent_id:
        return ""
    from core import executions, work

    held = [item for item in work.items(root) if item.status == work.ACTIVE
            and any(holder.get("session") == hook.session for holder in item.holders)]
    runs = [run for run in executions.load(root) if run.open and run.session == hook.session]
    _note_state(hook.session, held=len(held), open_runs=len(runs))
    if not held and not runs:
        return ""
    lines = []
    if held:
        lines.append(f"This session still holds {len(held)} work item(s); the next session reads them "
                     "as work in progress:")
        lines += [f"  {item.id}  {item.title}" for item in held[:5]]
        lines.append(f'Close each the way that is true: eos work done|block|drop {root} <id> --note/--reason "…"')
    if runs:
        lines.append(f"This session left {len(runs)} run(s) open; a failed one leaves no lesson until finished:")
        lines += [f"  {run.id}  {run.title}" for run in runs[:5]]
        lines.append(f"Finish each: eos run finish {root} <id> --outcome ok|failed|abandoned "
                     '(failed needs --lesson "…")')
    # A Stop hook blocks through its JSON answer, so this one still exits 0;
    # the harness sets stop_hook_active on the next stop and it lets go.
    return json.dumps({"decision": "block", "reason": "\n".join(lines)}, ensure_ascii=False)


def _fold_usage(root: Path, hook: Hook) -> None:
    """Keep what the session ran, before the harness deletes its transcript --
    detached, because a transcript can be tens of megabytes (ADR-025)."""
    try:
        from core.routing import config as routing_config

        if not routing_config.load(root).configured:
            return
        import subprocess

        subprocess.Popen([sys.executable, str(Path(__file__).resolve().parent / "eos.py"), "route", str(root),
                          "--usage-from", hook.transcript, "--session", hook.session],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
    except Exception:  # noqa: BLE001 - a statistic is never worth a failed stop
        return


HANDLERS = {
    "session-start": _session_start,
    "user-prompt": _user_prompt,
    "post-tool": _post_tool,
    "post-tool-failure": _post_tool_failure,
    "subagent-start": _subagent_start,
    "subagent-stop": _subagent_stop,
    "stop": _stop,
    "stop-failure": _stop_failure,
    "instructions-loaded": _instructions_loaded,
    "session-end": _session_end,
    "pre-agent": _pre_agent,
}
EVENTS = tuple(HANDLERS)
