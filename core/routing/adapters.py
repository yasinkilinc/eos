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


# Claude Code reads this before anything else and it overrides every other
# effort source, so while it is set an effort recommendation cannot take effect.
EFFORT_PIN_ENV = "CLAUDE_CODE_EFFORT_LEVEL"


def _pinned_effort() -> str | None:
    import os

    value = (os.environ.get(EFFORT_PIN_ENV) or "").strip()
    return value or None


def render(decision: Decision, agent: str | None) -> dict:
    line = headline(decision)
    if (agent or "").lower() == CLAUDE:
        effort = None
        if decision.effort:
            pinned = _pinned_effort()
            effort = {"value": decision.effort, "apply": f"claude --effort {decision.effort}",
                      "applies": "blocked" if pinned else "advisory",
                      "when": "session start"}
            if pinned:
                effort["blocked_by"] = f"{EFFORT_PIN_ENV}={pinned}"
        return {"subagent": {"model": decision.model}, "effort": effort, "line": line}
    return {"line": line}


def headline(decision: Decision) -> str:
    reason = decision.reason
    if len(reason) > REASON_CHARS:
        reason = reason[: REASON_CHARS - 1].rstrip() + "…"
    target = f"{decision.model}/{decision.effort}" if decision.effort else decision.model
    return (f"{ROUTE_MARK}{decision.task_type} {decision.level} → {target} "
            f"(conf {decision.confidence:.2f}) — {reason}")


def brief_lines(decision: Decision, agent: str | None) -> list[str]:
    """The two lines the task brief carries.

    Effort is advice for the start of a session: Claude Code takes it per
    session or per agent definition, not per call, and changing it partway
    through can cost the prompt cache -- so the line names the flag that sets
    it up front rather than a mid-session command (claude plan D6).
    """
    if (agent or "").lower() == CLAUDE:
        if not decision.effort:
            effort = f"{decision.model} takes no effort setting"
        elif _pinned_effort():
            effort = f"effort {decision.effort} cannot apply while {EFFORT_PIN_ENV}={_pinned_effort()} pins it"
        else:
            effort = f"effort {decision.effort} is set at session start (claude --effort {decision.effort})"
        apply = (f'{APPLY_MARK}Agent({{model: "{decision.model}"}}) for subagents; {effort}; '
                 f'eos route . "<task>" for the factors')
    else:
        effort = f" at effort {decision.effort}" if decision.effort else ""
        apply = f'{APPLY_MARK}model {decision.model}{effort}; eos route . "<task>" for the factors'
    return [headline(decision), apply]
