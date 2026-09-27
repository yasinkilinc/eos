"""What needs a person's attention in this project's memory (2.x roadmap L1).

A fold-only report: it reads the notes, the procedures, the run and work
ledgers, and prints what the other audits would each say, bounded, with the
command that shows more. It never rewrites anything -- a proposal a person
accepts is how anything here changes (Ruflo's lesson: consolidation that edits
memory on its own becomes a daemon nobody trusts).
"""
from __future__ import annotations

import datetime
import itertools
from pathlib import Path

LIMIT = 5
NEAR_DUPLICATE = 0.6          # below the add guard's 0.8: pairs worth a look, not a refusal
OPEN_RUN_DAYS = 2


def report(project_root: str | Path) -> dict:
    from core import executions, notes, procedure_lint, work

    root = Path(project_root).expanduser().resolve()
    corpus = notes.load_notes(root)
    now = datetime.datetime.now(datetime.timezone.utc)

    procedures = executions.audit_procedures(root)
    failing = [p for p in procedures if any(x.startswith("the latest run failed") for x in p["problems"])]
    unrun = [p for p in procedures if p["runs_ok"] == 0 and p["runs_failed"] == 0]
    lint = [r for r in procedure_lint.lint(root) if r["problems"]]

    grams = [(note, notes._trigrams(note.body)) for note in corpus if not notes.is_bulk_index(note)]
    pairs = []
    for (a, ga), (b, gb) in itertools.combinations(grams, 2):
        if len(ga) >= notes.PARAPHRASE_MIN_TRIGRAMS and len(gb) >= notes.PARAPHRASE_MIN_TRIGRAMS:
            share = len(ga & gb) / len(ga | gb)
            if share >= NEAR_DUPLICATE:
                pairs.append((round(share, 2), a.title, b.title))
    pairs.sort(reverse=True)

    stale = notes.stale_notes(root)
    records = executions.load(root)
    silent = _silent_steps(root, records)
    old_runs = []
    for record in records:
        if record.open and record.started_at:
            try:
                started = datetime.datetime.fromisoformat(record.started_at)
            except ValueError:
                continue
            if (now - started).days >= OPEN_RUN_DAYS:
                old_runs.append((record.id, record.title, (now - started).days))
    stale_work = [item for item in work.items(root) if item.is_stale(now)]
    done = [r for r in records if r.outcome == "ok"]
    verified = sum(1 for r in done if r.outcome_source == "verified")
    claimed = sum(1 for r in done if r.outcome_source == "claimed")
    return {
        "notes": len(corpus),
        "procedures": {"total": len(procedures), "failing": [p["procedure"] for p in failing],
                       "never_run": [p["procedure"] for p in unrun],
                       "lint": {r["procedure"]: r["problems"] for r in lint},
                       "silent_steps": silent},
        "near_duplicates": pairs,
        "stale_notes": [entry.get("note") for entry in stale],
        "open_runs_older": old_runs,
        "stale_work": [(item.id, item.title) for item in stale_work],
        "verified_rate": {"verified": verified, "claimed": claimed},
    }


def _silent_steps(root: Path, records) -> dict:
    """For each procedure that has run, the steps whose tool no run of it ever
    recorded: their outcome cannot be placed (ADR-031), however often it runs."""
    from core import executions, notes, steps

    silent = {}
    for note in notes.procedures(root):
        runs = [r for r in records if r.procedure == note.procedure and r.outcome]
        if not runs:
            continue
        seen = {executions.normalize_tool(e.tool) for r in runs for e in r.events if e.tool}
        missing = [f"step {s.number} (tool: {s.tool})" for s in steps.parse(note.body)
                   if s.tool and executions.normalize_tool(s.tool) not in seen]
        if missing:
            silent[note.procedure] = missing
    return silent


def render(data: dict) -> str:
    def block(title, items, more):
        if not items:
            return []
        shown = [f"  {line}" for line in items[:LIMIT]]
        tail = [f"  … {len(items) - LIMIT} more: {more}"] if len(items) > LIMIT else [f"  ({more})"]
        return ["", f"{title} ({len(items)})"] + shown + tail

    lines = [f"EOS consolidate -- {data['notes']} notes, {data['procedures']['total']} procedures. "
             "Nothing here was changed."]
    lines += block("PROCEDURES WHOSE LATEST RUN FAILED", data["procedures"]["failing"], "eos procedure audit .")
    lines += block("PROCEDURES NEVER RUN", data["procedures"]["never_run"], "eos procedure audit .")
    lines += block("PROCEDURE LINT", [f"{slug}: {len(problems)} problem(s)"
                                      for slug, problems in data["procedures"]["lint"].items()],
                   "eos procedure lint .")
    lines += block("STEPS NO RUN RECORDED", [f"{slug}: {', '.join(found)}"
                                             for slug, found in data["procedures"].get("silent_steps", {}).items()],
                   "their tool records no event, so no run says how they went")
    lines += block("NOTES THAT READ ALIKE", [f"{share:.0%}  {a}  ~  {b}" for share, a, b in data["near_duplicates"]],
                   "keep one, or say what differs")
    lines += block("NOTES WHOSE FILES CHANGED", data["stale_notes"], "eos note audit .")
    lines += block("RUNS OPEN FOR DAYS", [f"{rid}  {title}  ({days}d)" for rid, title, days in data["open_runs_older"]],
                   "eos run finish . <id> --outcome ok|failed|abandoned")
    lines += block("STALE WORK", [f"{wid}  {title}" for wid, title in data["stale_work"]], "eos work list .")
    rate = data["verified_rate"]
    if rate["verified"] + rate["claimed"]:
        share = rate["verified"] / (rate["verified"] + rate["claimed"])
        lines += ["", f"VERIFIED RATE  {rate['verified']} verified, {rate['claimed']} claimed ({share:.0%})"]
    if len(lines) == 1:
        lines.append("Nothing needs attention.")
    return "\n".join(lines)
