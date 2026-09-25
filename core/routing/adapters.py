"""A decision, in the form a harness can act on (ADR-025).

EOS never configures a provider. What it can do is put the decision where
the harness already reads -- the brief, a hook's output -- in the harness's
own terms. For Claude Code that is a subagent `model` argument, which a
caller or a `PreToolUse` hook can set per call, and an effort that the
harness takes only per session or per agent definition, so it is named as
the command that sets it and marked advisory. Any other harness gets the
same decision as a sentence.

Registry ids are what `model` receives. Mapping an id to a harness argument
happens here and nowhere else, so a harness that renames its aliases is a
change to this file.
"""
from __future__ import annotations

from core.routing.types import Decision

CLAUDE = "claude"
ROUTE_MARK = "ROUTE  "
APPLY_MARK = "  Apply: "
REASON_CHARS = 120


def render(decision: Decision, agent: str | None) -> dict:
    line = headline(decision)
    if (agent or "").lower() == CLAUDE:
        return {
            "subagent": {"model": decision.model},
            "effort": {"value": decision.effort, "apply": f"/effort {decision.effort}",
                       "applies": "advisory"},
            "line": line,
        }
    return {"line": line}


def headline(decision: Decision) -> str:
    reason = decision.reason
    if len(reason) > REASON_CHARS:
        reason = reason[: REASON_CHARS - 1].rstrip() + "…"
    return (f"{ROUTE_MARK}{decision.task_type} {decision.level} → {decision.model}/{decision.effort} "
            f"(conf {decision.confidence:.2f}) — {reason}")


def brief_lines(decision: Decision, agent: str | None) -> list[str]:
    """The two lines the task brief carries."""
    if (agent or "").lower() == CLAUDE:
        apply = (f'{APPLY_MARK}Task({{model: "{decision.model}"}}) for subagents; '
                 f"/effort {decision.effort} for this session; "
                 f'eos route . "<task>" for the factors')
    else:
        apply = (f"{APPLY_MARK}model {decision.model} at effort {decision.effort}; "
                 f'eos route . "<task>" for the factors')
    return [headline(decision), apply]
