"""What a project's agent sessions keep re-reading, by source (2.x roadmap C7).

Reads Claude Code's own transcripts for the project (the harness's format, so
this module is an integration, not engine logic). Every item that enters a main
session -- a tool's result, a tool input the model wrote, a hook's injection, a
harness attachment, a prompt -- is charged its tokens times the model calls it
stays in the context for, until the next compaction or the end of the session.
That total plus the first call's context is checked against the cache reads the
transcript recorded, so the table says how much of the real cost it explains.

tokens = characters / 2.22, the ratio calibrated on the host's transcripts.
"""
from __future__ import annotations

import collections
import json
import re
from pathlib import Path

CHARS_PER_TOKEN = 2.22
_SKIPPED_ATTACHMENTS = {"prompt_snapshot", "environment", "model", "date", "session_context",
                        "command_permissions", "remote_session_change", "thinking_drop"}


def transcripts_dir(project_root: str | Path) -> Path:
    """Where the harness keeps this project's sessions: its path with every
    character but letters and digits turned into '-'."""
    return Path.home() / ".claude" / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(project_root))


def _bash_source(command: str, declared) -> str:
    from core import capabilities

    return "tool: Bash (wrapper)" if capabilities.wrapper_in(command, declared) else "tool: Bash (raw)"


def _hook_source(attachment: dict) -> str:
    event = attachment.get("hookEvent") or attachment.get("hookName") or "hook"
    return f"hook: {str(event).split(':')[0]}"


def _session(path: Path, declared) -> tuple[list, list, int, int, int, str]:
    items, boundaries, seen, names = [], [], set(), {}
    calls = reads = first = 0
    day = ""
    try:
        handle = open(path, encoding="utf-8", errors="replace")
    except OSError:
        return items, boundaries, calls, reads, first, day
    with handle:
        for line in handle:
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if not isinstance(entry, dict):
                continue
            day = day or str(entry.get("timestamp") or "")[:10]
            kind, message = entry.get("type"), entry.get("message")
            message = message if isinstance(message, dict) else {}
            if kind == "system" and entry.get("subtype") == "compact_boundary":
                boundaries.append(calls)
            elif kind == "assistant":
                usage = message.get("usage")
                if isinstance(usage, dict) and message.get("id") not in seen:
                    seen.add(message.get("id"))
                    calls += 1
                    reads += int(usage.get("cache_read_input_tokens") or 0)
                    if calls == 1:
                        first = sum(int(usage.get(k) or 0) for k in
                                    ("cache_read_input_tokens", "cache_creation_input_tokens", "input_tokens"))
                for part in message.get("content") or []:
                    if not isinstance(part, dict):
                        continue
                    if part.get("type") == "text":
                        items.append(("assistant text", len(part.get("text") or ""), calls))
                    elif part.get("type") == "tool_use":
                        tool_input = part.get("input") or {}
                        names[part.get("id")] = (part.get("name") or "?", str(tool_input.get("command") or ""))
                        items.append((f"tool input: {part.get('name')}",
                                      len(json.dumps(tool_input, ensure_ascii=False)), calls))
            elif kind == "user":
                content = message.get("content")
                if isinstance(content, str):
                    items.append(("user prompt", len(content), calls))
                elif isinstance(content, list):
                    for part in content:
                        if not isinstance(part, dict):
                            continue
                        if part.get("type") == "tool_result":
                            body = part.get("content")
                            size = len(body if isinstance(body, str) else json.dumps(body, ensure_ascii=False))
                            name, command = names.get(part.get("tool_use_id"), ("?", ""))
                            source = (_bash_source(command, declared) if name == "Bash"
                                      else "tool: MCP" if name.startswith("mcp__") else f"tool: {name}")
                            items.append((source, size, calls))
                        elif part.get("type") == "text":
                            items.append(("user prompt", len(part.get("text") or ""), calls))
            elif kind == "attachment":
                attachment = entry.get("attachment") or {}
                kind_of = attachment.get("type")
                if kind_of in _SKIPPED_ATTACHMENTS:
                    continue
                if kind_of in ("hook_success", "hook_additional_context"):
                    body = attachment.get("content")
                    text = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)
                    items.append((_hook_source(attachment), len(text), calls))
                else:
                    items.append((f"attachment: {kind_of}", len(json.dumps(attachment, ensure_ascii=False)), calls))
    return items, boundaries, calls, reads, first, day


def report(project_root: str | Path, *, transcripts: str | Path | None = None, since: str = "") -> dict:
    from core import capabilities

    root = Path(project_root).expanduser().resolve()
    folder = Path(transcripts) if transcripts else transcripts_dir(root)
    try:
        declared = capabilities.load(root)
    except Exception:  # noqa: BLE001 - without a registry every Bash call is raw
        declared = []
    resident, entered = collections.Counter(), collections.Counter()
    sessions = calls_total = reads_total = base_total = 0
    for path in sorted(folder.glob("*.jsonl")):
        items, boundaries, calls, reads, first, day = _session(path, declared)
        if calls < 2 or (since and day < since):
            continue
        sessions += 1
        calls_total += calls
        reads_total += reads
        base_total += first * calls
        for source, chars, at in items:
            end = next((b for b in boundaries if b > at), calls)
            tokens = chars / CHARS_PER_TOKEN
            resident[source] += tokens * max(end - at, 0)
            entered[source] += tokens
    explained = sum(resident.values()) + base_total
    rows = [{"source": source, "resident_tokens": round(value), "entered_tokens": round(entered[source]),
             "share": round(value / explained, 4) if explained else 0.0}
            for source, value in resident.most_common()]
    return {"transcripts": str(folder), "since": since or None, "sessions": sessions, "calls": calls_total,
            "cache_reads": reads_total, "base_tokens": base_total,
            "explained": round(explained / reads_total, 3) if reads_total else None, "sources": rows}


def render(data: dict, limit: int = 15) -> str:
    if not data["sessions"]:
        return f"No main sessions with two or more model calls in {data['transcripts']}."
    lines = [f"{data['sessions']} session(s), {data['calls']} model calls since {data['since'] or 'the start'}; "
             f"cache reads {data['cache_reads'] / 1e6:.1f}M tokens, this table explains "
             f"{data['explained']:.0%} of them" if data["explained"] is not None else "",
             f"  {'source':<40}{'share':>7}{'resident':>12}{'entered':>11}"]
    base_share = data["base_tokens"] / (data["base_tokens"] + sum(r["resident_tokens"] for r in data["sources"]) or 1)
    lines.append(f"  {'first-call context (system, tools)':<40}{base_share:7.1%}{data['base_tokens'] / 1e6:11.1f}M")
    for row in data["sources"][:limit]:
        lines.append(f"  {row['source'][:40]:<40}{row['share']:7.1%}{row['resident_tokens'] / 1e6:11.1f}M"
                     f"{row['entered_tokens'] / 1e3:10.0f}k")
    return "\n".join(line for line in lines if line)
