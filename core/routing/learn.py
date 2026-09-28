"""`eos route --learn`: proposals from what a decision was joined to (A5).

Print-only, never writes `.eos/config.toml` -- a person reads the proposals
and decides. Built from the same join `eos route --stats` already makes
(`trace.stats`, `trace.advised_vs_used_outcome_stats`, N5/RVb): recorded
decisions against their runs' outcomes, and the advised model against the
model a session's transcript shows it actually ran.

What this does NOT propose, and why: "keywords whose routed runs failed" (one
of the shapes this row's own task named) needs to know which keyword fired
for a given decision, and ADR-019 never keeps that -- `routing.jsonl` keeps a
hash of the task text and the classifier's generic factor names (`risk`,
`architectural`, ...), never the matched word. Answering it honestly would
need a new field recording keywords, which is a privacy decision this pass
does not make on its own (see 2x-progress.md, "Decided without asking").
What IS built from what already exists: a (type, level) whose finished
routed runs fail or are abandoned at a high rate, and a level whose advised
model is consistently overridden by the model actually used -- both already
joinable, and both honest about a small sample (`too few runs`, never a
guessed rate) per `core/lib/honest.py`'s own rule.
"""
from __future__ import annotations

from collections import defaultdict

# Below this many finished (or usage-known) runs, a rate is not printed -- an
# honest "too few runs" instead of a percentage computed from a handful of
# runs that could flip with the next one.
MIN_RUNS = 5
# A rate this high or higher is worth a person's look; chosen conservatively
# (most decisions are expected to be "ok" and "same"), not tuned on any one
# project's data.
FAILURE_RATE_FLAG = 0.5
OVERRIDE_RATE_FLAG = 0.7


def propose(project_root) -> list[str]:
    """Every proposal this pass can make, honestly. Never empty: says so when
    nothing crosses a threshold, and separately what has too few runs."""
    from core.routing import trace

    lines: list[str] = []
    lines += _failure_proposals(trace.stats(project_root))
    lines += _override_proposals(trace.advised_vs_used_outcome_stats(project_root))
    if not lines:
        lines.append("No proposal: nothing crossed the sample-size or rate thresholds yet.")
    return lines


def _failure_proposals(stats) -> list[str]:
    proposals = []
    too_few = 0
    for (kind, level, model, effort), counts in stats:
        finished = counts["ok"] + counts["failed"] + counts["abandoned"]
        if finished == 0:
            continue
        if finished < MIN_RUNS:
            too_few += 1
            continue
        bad = counts["failed"] + counts["abandoned"]
        rate = bad / finished
        if rate >= FAILURE_RATE_FLAG:
            target = f"{model}/{effort}" if effort else model
            proposals.append(f"{kind} routed to {level} ({target}): {bad}/{finished} finished routed "
                             f"runs failed or were abandoned ({rate:.0%}) -- review the keywords or "
                             f"level for this type.")
    if too_few:
        proposals.append(f"{too_few} (type, level, model) group(s) have fewer than {MIN_RUNS} finished "
                         "routed runs -- too few runs for a rate.")
    return proposals


def _override_proposals(groups: dict) -> list[str]:
    by_level: dict[str, dict[str, int]] = defaultdict(lambda: {"same": 0, "different": 0})
    for row in groups.get("same", ()):
        by_level[row.get("advised_level") or "?"]["same"] += 1
    for row in groups.get("different", ()):
        by_level[row.get("advised_level") or "?"]["different"] += 1
    proposals = []
    too_few = 0
    for level, counts in sorted(by_level.items()):
        total = counts["same"] + counts["different"]
        if total < MIN_RUNS:
            too_few += 1
            continue
        rate = counts["different"] / total
        if rate >= OVERRIDE_RATE_FLAG:
            proposals.append(f"{level}: the model actually used differed from the one advised in "
                             f"{counts['different']}/{total} routed runs ({rate:.0%}) -- review this "
                             f"level's model choice.")
    if too_few:
        proposals.append(f"{too_few} level(s) have fewer than {MIN_RUNS} runs with a known used model "
                         "-- too few runs for a rate.")
    return proposals
