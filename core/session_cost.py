"""What EOS delivered to each session, against what the model read (2.x roadmap C7).

Three files already hold the halves, keyed by the harness's session id:

  telemetry.jsonl       every EOS answer -- the hooks' injections and the CLI/MCP
                        calls -- with its size (`chars`, and `tokens` at
                        core/context/budget.py's rate)
  routing-usage.jsonl   per session, the model's token counts, folded from the
                        harness transcript at Stop (ADR-025)
  sessions.jsonl        per session, the hooks' counters at SessionEnd

This joins them and changes nothing. Two model numbers, because they answer
different questions: `new_tokens` (input + cache creation) is what entered the
context for the first time, the fair denominator for "how much of it was EOS";
`read_tokens` adds the cache reads, which a long session pays again on every
call. The share is EOS's delivered tokens over `new_tokens`.

A source with no line for a session leaves that half None, printed as "—":
"not measured" and "nothing" are different claims, and a zero would make the
second one (2.x roadmap L4). Totals and the overall share are taken only over
sessions both sides measured.
"""
from __future__ import annotations

from pathlib import Path

HOOK_COUNTERS = ("hints", "outlined", "verify_gates", "verify_after_gate", "subagents")


def report(project_root: str | Path, *, since: str = "") -> dict:
    from core import telemetry
    from core.lib import honest
    from core.routing import usage

    root = Path(project_root).expanduser().resolve()
    rows: dict[str, dict] = {}
    unattributed = 0

    def row(session: str) -> dict:
        return rows.setdefault(session, {"session": session, "first_at": None, "last_at": None,
                                         "eos": None, "model": None, "subagents": None, "hooks": None})

    def seen(entry: dict, at) -> None:
        if isinstance(at, str) and at:
            entry["first_at"] = min(filter(None, (entry["first_at"], at)))
            entry["last_at"] = max(filter(None, (entry["last_at"], at)))

    for call in telemetry.load(root):
        session = call.get("session")
        if not session:
            unattributed += 1
            continue
        entry = row(str(session))
        seen(entry, call.get("at"))
        eos = entry["eos"] or {"calls": 0, "chars": 0, "tokens": 0, "by_command": {}}
        tokens = _int(call.get("tokens"))
        eos["calls"] += 1
        eos["chars"] += _int(call.get("chars"))
        eos["tokens"] += tokens
        name = " ".join([str(call.get("command") or "?")] + [str(f) for f in call.get("flags") or []][:1])
        eos["by_command"][name] = eos["by_command"].get(name, 0) + tokens
        entry["eos"] = eos

    for line in usage.load(root):
        session = line.get("session")
        if not session:
            continue
        entry = row(str(session))
        seen(entry, line.get("at"))
        entry["model"] = _tokens(line.get("models"))
        entry["subagents"] = _tokens(line.get("subagent_models"))

    for line in _jsonl(root / ".eos" / "data" / "sessions.jsonl"):
        session = line.get("session")
        if not session:
            continue
        entry = row(str(session))
        seen(entry, line.get("at"))
        hooks = {name: _int(line.get(name)) for name in HOOK_COUNTERS}
        hooks["events_recorded"] = sum(_int(v) for v in (line.get("events_recorded") or {}).values())
        entry["hooks"] = hooks

    sessions = [entry for entry in rows.values() if not since or (entry["last_at"] or "") >= since]
    for entry in sessions:
        entry["eos_share"] = _share(entry)
    sessions.sort(key=lambda entry: entry["last_at"] or "", reverse=True)

    both = [entry for entry in sessions if entry["eos_share"] is not None]
    eos_both = sum(entry["eos"]["tokens"] for entry in both)
    new_both = sum(entry["model"]["new_tokens"] for entry in both)
    return {
        "since": since or None,
        "sources": {"telemetry": str(telemetry.path_for(root)), "routing-usage": str(usage.path_for(root)),
                    "sessions": str(root / ".eos" / "data" / "sessions.jsonl")},
        "sessions": sessions,
        "unattributed_calls": unattributed,
        "totals": {
            "sessions": len(sessions),
            "both_measured": len(both),
            # None when no session was measured by that source: a sum over nothing is not 0 (L4).
            "eos_tokens": _total(entry["eos"]["tokens"] for entry in sessions if entry["eos"]),
            "new_tokens": _total(entry["model"]["new_tokens"] for entry in sessions if entry["model"]),
            "read_tokens": _total(entry["model"]["read_tokens"] for entry in sessions if entry["model"]),
            "eos_share": round(eos_both / new_both, 4) if new_both else None,
        },
        "provenance": {"sessions[].eos.calls": honest.MEASURED, "sessions[].eos.chars": honest.MEASURED,
                       "sessions[].eos.tokens": honest.DERIVED, "sessions[].eos.by_command": honest.DERIVED,
                       "sessions[].model": honest.MEASURED, "sessions[].subagents": honest.MEASURED,
                       "sessions[].hooks": honest.MEASURED, "sessions[].eos_share": honest.DERIVED,
                       "unattributed_calls": honest.MEASURED, "totals.sessions": honest.MEASURED,
                       "totals.both_measured": honest.MEASURED, "totals.eos_tokens": honest.DERIVED,
                       "totals.new_tokens": honest.MEASURED, "totals.read_tokens": honest.MEASURED,
                       "totals.eos_share": honest.DERIVED},
    }


def _total(values) -> int | None:
    values = list(values)
    return sum(values) if values else None


def _share(entry: dict) -> float | None:
    if not entry["eos"] or not entry["model"] or not entry["model"]["new_tokens"]:
        return None
    return round(entry["eos"]["tokens"] / entry["model"]["new_tokens"], 4)


def _tokens(models) -> dict | None:
    if not isinstance(models, dict) or not models:
        return None
    total = {"messages": 0, "input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0,
             "cache_creation_input_tokens": 0}
    for counts in models.values():
        if isinstance(counts, dict):
            for field in total:
                total[field] += _int(counts.get(field))
    total["new_tokens"] = total["input_tokens"] + total["cache_creation_input_tokens"]
    total["read_tokens"] = total["new_tokens"] + total["cache_read_input_tokens"]
    total["models"] = sorted(models)
    return total


def _int(value) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _jsonl(path: Path) -> list[dict]:
    import json

    try:
        text = path.read_text(encoding="utf-8", errors="replace")
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


def render(data: dict, limit: int = 20) -> str:
    if not data["sessions"]:
        return ("No session found in " + ", ".join(f"{name} ({path})" for name, path in data["sources"].items())
                + ". Telemetry needs [telemetry] enabled = true; routing-usage is written by the Stop hook.")
    dash = "—"
    lines = [f"{data['totals']['sessions']} session(s) since {data['since'] or 'the start'}, newest first. "
             "~ marks a derived number (EOS tokens are chars at 2.22/token); model tokens are measured by the harness; "
             f"{dash} is not measured, not zero.",
             "",
             f"{'session':<14} {'last seen':<17} {'EOS calls':>9} {'EOS tok':>11} {'new tok':>12} "
             f"{'read tok':>13} {'EOS share':>9} {'hints':>5} {'gates':>5}"]
    for entry in data["sessions"][:limit]:
        eos, model, hooks = entry["eos"] or {}, entry["model"] or {}, entry["hooks"] or {}
        share = _cell(entry["eos_share"], derived=True, spec=".1%")
        lines.append(
            f"{entry['session'][:14]:<14} {(entry['last_at'] or dash)[:16]:<17} "
            f"{_cell(eos.get('calls')):>9} {_cell(eos.get('tokens'), derived=True):>11} {_cell(model.get('new_tokens')):>12} "
            f"{_cell(model.get('read_tokens')):>13} {share:>9} {_cell(hooks.get('hints')):>5} "
            f"{_cell(hooks.get('verify_gates')):>5}")
    if len(data["sessions"]) > limit:
        lines.append(f"… {len(data['sessions']) - limit} older session(s); --format json lists all of them.")
    totals = data["totals"]
    overall = _cell(totals["eos_share"], derived=True, spec=".1%")
    lines += ["", f"EOS share of new context over the {totals['both_measured']} session(s) both sides measured: {overall}."]
    if data["unattributed_calls"]:
        lines.append(f"{data['unattributed_calls']} EOS call(s) carried no session id and are in no row.")
    return "\n".join(lines)


def _cell(value, *, derived: bool = False, spec: str = ",") -> str:
    from core.lib import honest

    return honest.show(value, honest.DERIVED if derived else honest.MEASURED, spec=spec)
