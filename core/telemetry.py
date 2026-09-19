"""What EOS costs the agent that calls it.

This whole system exists to spend fewer tokens than rediscovery would, and
that claim needs a number rather than an assertion. `eos bench` measures EOS
against baselines on demand; this records what actually happened, call by
call, so the cost of a habit is visible after the habit forms.

Three rules make it safe to leave on.

**It records what was called, never what was asked.** The command and which
flags were passed, not their values. A search query, a note body or a task
description can contain anything a person typed, and a log that captured them
would be a liability sitting in every project EOS touches -- so the writer has
no path for a value to reach the file.

**It never fails a command.** A telemetry write that raised would turn a
working `eos query` into a broken one, which is a poor trade for a statistic.
Every failure here is swallowed.

**It is off unless asked for**, because a tool that starts logging without
being told is one people turn off entirely. `[telemetry] enabled = true` in
`.eos/config.toml`.

It is deliberately not a profiler and not a tracer. Command, milliseconds,
output size, whether the index had to be rebuilt. Those are the four numbers
that decide whether an answer was cheap, and nothing else here is worth the
bytes.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from core.lib.config_io import ConfigIO

FILENAME = "telemetry.jsonl"
# Chars per token, the same rough estimate core/bench.py uses and labels as
# one. A real tokenizer is a dependency this runtime does not take.
_TOKEN_DIVISOR = 4
# Enough to see a habit, small enough that nobody has to prune it. At roughly
# 150 bytes a line this is under 1.5 MB.
MAX_LINES = 10000


def path_for(project_root: str | Path) -> Path:
    return Path(project_root).expanduser().resolve() / ".eos" / "data" / FILENAME


def enabled(project_root: str | Path) -> bool:
    config = Path(project_root).expanduser().resolve() / ".eos" / "config.toml"
    if not config.is_file():
        return False
    try:
        return bool(ConfigIO.read_toml(config).get("telemetry", {}).get("enabled", False))
    except (OSError, ValueError):
        return False


# Set by a command whose answer is not what it printed. `eos context` writes a
# file and prints one line about it, so recording the line would make the most
# expensive call in the system look like the cheapest.
_declared_size: int | None = None


def declare_answer_size(chars: int) -> None:
    """Tell the enclosing Timer how big the answer really was."""
    global _declared_size
    _declared_size = int(chars)


class Timer:
    """Time one call and record it. Never raises, never blocks the command."""

    def __init__(self, project_root: str | Path, command: str, flags: list[str] | None = None):
        self.project_root = project_root
        self.command = command
        self.flags = sorted(set(flags or ()))
        self.chars = 0
        self.rebuilt = False
        self.ok = True
        self._started = 0.0

    def __enter__(self) -> "Timer":
        global _declared_size
        _declared_size = None
        self._started = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.ok = exc_type is None
        try:
            record(self.project_root, self.command, flags=self.flags,
                   milliseconds=(time.perf_counter() - self._started) * 1000,
                   chars=_declared_size if _declared_size is not None else self.chars,
                   rebuilt=self.rebuilt, ok=self.ok)
        except Exception:  # noqa: BLE001 - a statistic may not break a command
            pass
        return False


def record(project_root: str | Path, command: str, flags: list[str] | None = None,
           milliseconds: float = 0.0, chars: int = 0, rebuilt: bool = False,
           ok: bool = True) -> None:
    if not enabled(project_root):
        return
    target = path_for(project_root)
    entry = {
        "at": _now(),
        "command": command,
        # Names only. A value here would be the first thing to leak.
        "flags": sorted(set(flags or ())),
        "ms": round(float(milliseconds), 1),
        "chars": int(chars),
        "tokens": int(chars) // _TOKEN_DIVISOR,
        "rebuilt": bool(rebuilt),
        "ok": bool(ok),
    }
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        _trim(target)
    except OSError:
        return


def _now() -> str:
    from core.knowledge.evidence import utc_now

    return utc_now()


def _trim(target: Path) -> None:
    """Keep the tail. An unbounded log in every project is a slow leak."""
    try:
        lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return
    if len(lines) <= MAX_LINES:
        return
    target.write_text("\n".join(lines[-MAX_LINES:]) + "\n", encoding="utf-8")


def load(project_root: str | Path) -> list[dict[str, Any]]:
    target = path_for(project_root)
    if not target.is_file():
        return []
    found = []
    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict):
            found.append(entry)
    return found


def summary(project_root: str | Path) -> dict[str, Any]:
    """Per command: how often, how long, how much came back.

    Median rather than mean: one cold index build is not what the next call
    will cost, and an average lets it claim otherwise.
    """
    entries = load(project_root)
    by_command: dict[str, dict[str, Any]] = {}
    for entry in entries:
        name = entry.get("command") or "?"
        bucket = by_command.setdefault(name, {"command": name, "calls": 0, "tokens": 0,
                                              "_ms": [], "rebuilt": 0, "failed": 0})
        bucket["calls"] += 1
        bucket["tokens"] += int(entry.get("tokens") or 0)
        bucket["_ms"].append(float(entry.get("ms") or 0.0))
        bucket["rebuilt"] += 1 if entry.get("rebuilt") else 0
        bucket["failed"] += 0 if entry.get("ok", True) else 1

    rows = []
    for bucket in by_command.values():
        durations = sorted(bucket.pop("_ms"))
        bucket["median_ms"] = round(durations[len(durations) // 2], 1) if durations else 0.0
        bucket["median_tokens"] = bucket["tokens"] // max(1, bucket["calls"])
        rows.append(bucket)
    rows.sort(key=lambda row: (-row["tokens"], row["command"]))
    return {
        "calls": len(entries),
        "tokens": sum(row["tokens"] for row in rows),
        "commands": rows,
        "since": entries[0]["at"] if entries else None,
    }
