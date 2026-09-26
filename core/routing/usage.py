"""Which models a session actually ran, and what they spent (ADR-025).

A routing decision is advice; whether it was followed, and at what cost, is
only known to the harness. Claude Code keeps it in the session's transcript
(one JSON line per event, with `message.model` and `message.usage` on every
assistant message), and deletes transcripts after a retention period -- 30
days unless configured. A later analysis of "was the advice right" needs the
numbers to outlive that, so this folds a transcript into one small line per
session: per model, the message count, the four token counts and how many
messages ran at each effort (the transcript writes it beside each message). No content,
no prompt, no tool input -- only numbers and model ids (ADR-019).

The transcript format is the harness's, read here the way telemetry reads
`CLAUDE_CODE_SESSION_ID`: a harness-specific input behind one function. A
line that does not parse, or has no model, is skipped.

Two facts about the format shape the fold. An assistant message is written
once per content block, with the same id and the same usage each time, so
messages are counted by id. A subagent's messages live beside the transcript
in `<session>/subagents/*.jsonl`; they are summed separately, because what a
subagent ran is a different question from what the session ran.
"""
from __future__ import annotations

import json
from pathlib import Path

FILENAME = "routing-usage.jsonl"
MAX_SESSIONS = 2000
TOKEN_FIELDS = ("input_tokens", "output_tokens", "cache_read_input_tokens",
                "cache_creation_input_tokens")


def path_for(project_root: str | Path) -> Path:
    return Path(project_root).expanduser().resolve() / ".eos" / "data" / FILENAME


def summarize(transcript: str | Path) -> dict:
    """{"models": {...}, "subagent_models": {...}} for one transcript."""
    path = Path(transcript).expanduser()
    subagents_dir = path.with_suffix("") / "subagents"
    subagents = sorted(subagents_dir.glob("*.jsonl")) if subagents_dir.is_dir() else []
    return {"models": _tally([path]), "subagent_models": _tally(subagents)}


def _tally(paths) -> dict:
    seen: set = set()
    totals: dict = {}
    for path in paths:
        try:
            handle = open(path, encoding="utf-8", errors="replace")
        except OSError:
            continue
        with handle:
            for line in handle:
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                message = entry.get("message") if isinstance(entry, dict) else None
                if not isinstance(message, dict) or not message.get("model"):
                    continue
                key = (path.name, message.get("id") or entry.get("uuid"))
                if key in seen:
                    continue
                seen.add(key)
                row = totals.setdefault(str(message["model"]),
                                        {"messages": 0, **{field: 0 for field in TOKEN_FIELDS},
                                         "efforts": {}})
                row["messages"] += 1
                # The effort each message actually ran at, as the harness wrote
                # it beside the message -- what a recommendation is read against.
                effort = str(entry.get("effort") or "none")[:16]
                row["efforts"][effort] = row["efforts"].get(effort, 0) + 1
                usage = message.get("usage") or {}
                for field in TOKEN_FIELDS:
                    value = usage.get(field)
                    if isinstance(value, int) and not isinstance(value, bool):
                        row[field] += value
    return totals


def record(project_root: str | Path, session: str | None, transcript: str | Path) -> bool:
    """Replace this session's line with the transcript's current totals. Never raises."""
    try:
        root = Path(project_root).expanduser().resolve()
        if not session or not (root / ".eos").is_dir():
            return False
        summary = summarize(transcript)
        if not summary["models"] and not summary["subagent_models"]:
            return False
        from core.knowledge.evidence import utc_now

        entry = {"at": utc_now(), "session": session[:64], **summary}
        target = path_for(root)
        kept = [line for line in load(root) if line.get("session") != entry["session"]]
        kept = kept[-(MAX_SESSIONS - 1):] + [entry]
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".tmp")
        temporary.write_text("".join(json.dumps(line, ensure_ascii=False) + "\n" for line in kept),
                             encoding="utf-8")
        temporary.replace(target)
        return True
    except Exception:  # a statistic is never worth a failed hook
        return False


def load(project_root: str | Path) -> list[dict]:
    try:
        text = path_for(project_root).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    found = []
    for line in text.splitlines():
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            found.append(value)
    return found
