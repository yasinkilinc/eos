#!/usr/bin/env python3
"""Give a subagent the model this project's routing policy picks (ADR-025).

Written by `eos init` / `eos ai update` (engine {{VERSION}}) only for a
project with `[model_routing] hook = true`; the same command removes it when
the flag is turned off. Edit the policy in `.eos/config.toml`, not this file:
a regenerated hook overwrites it.

This is the one place a routing decision becomes an action instead of
advice. When the agent spawns a subagent without naming a model, the
subagent's prompt is routed (`eos route --json --no-record`) and the call
proceeds with the model the policy chose. Four rules keep it harmless:

  - An explicit model is never touched. A caller that named one chose it.
  - Only a subagent with no `subagent_type`, or the generic one, is routed.
    A named agent carries its own definition, often with its own model, and
    the call's `model` argument would override it.
  - Only a model the harness's subagent tool accepts is written; a project's
    own registry entries (a local model, say) are left to the agent.
  - Effort is not touched: the harness does not take it per call.
  - A decision below `[model_routing] hook_min_confidence` is not applied,
    and nothing is chosen in its place.
  - Any failure -- no `eos`, a timeout, a malformed answer -- means no output
    and exit 0, so the call goes ahead exactly as the agent made it.

The prompt is passed to one process as a query and never stored (ADR-019).
"""
import json
import os
import shutil
import subprocess
import sys

TIMEOUT_SECONDS = 10
MAX_TASK_CHARS = 2000
# The subagent tool's name has changed across harness versions.
SUBAGENT_TOOLS = ("Agent", "Task")
# What that tool's `model` argument accepts; anything else is left alone.
SUBAGENT_MODELS = ("haiku", "sonnet", "opus", "fable")
# Subagent types with no model of their own to protect.
GENERIC_TYPES = ("", "general-purpose")


def _interpreter():
    if sys.version_info >= (3, 11):
        yield sys.executable
    for name in ("python3.14", "python3.13", "python3.12", "python3.11", "python3"):
        found = shutil.which(name)
        if found:
            yield found


def _runners(root):
    found = shutil.which("eos")
    if found:
        yield [found]
    runtime = os.path.join(root, ".eos", "runtime", "eos.py")
    if os.path.isfile(runtime):
        for python in _interpreter():
            yield [python, runtime]


def _min_confidence(root):
    """`[model_routing] hook_min_confidence`: below it a decision is not applied."""
    try:
        import tomllib

        with open(os.path.join(root, ".eos", "config.toml"), "rb") as handle:
            value = tomllib.load(handle).get("model_routing", {}).get("hook_min_confidence", 0.0)
        return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0
    except Exception:
        return 0.0


def _decided_model(root, task, session):
    arguments = ["route", root, task, "--json", "--no-record"]
    if session:
        arguments += ["--session", session]
    for runner in _runners(root):
        try:
            done = subprocess.run(runner + arguments, capture_output=True, text=True,
                                  timeout=TIMEOUT_SECONDS)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if done.returncode != 0:
            continue
        try:
            decision = json.loads(done.stdout)
            model, confidence = decision.get("model"), float(decision.get("confidence") or 0.0)
        except (ValueError, AttributeError, TypeError):
            return None
        if confidence < _min_confidence(root):
            return None  # an uncertain decision is not applied, and nothing replaces it
        return model if model in SUBAGENT_MODELS else None
    return None


def main() -> int:
    try:
        raw = sys.stdin.read() if not sys.stdin.isatty() else ""
        payload = json.loads(raw) if raw.strip() else {}
    except (ValueError, OSError):
        return 0
    if not isinstance(payload, dict) or payload.get("tool_name") not in SUBAGENT_TOOLS:
        return 0
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict) or tool_input.get("model"):
        return 0
    if str(tool_input.get("subagent_type") or "").strip() not in GENERIC_TYPES:
        return 0
    task = str(tool_input.get("prompt") or tool_input.get("description") or "").strip()[:MAX_TASK_CHARS]
    if not task:
        return 0
    root = payload.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    model = _decided_model(root, task, str(payload.get("session_id") or ""))
    if not model:
        return 0
    # `updatedInput` is merged into the call's input by the harness, and needs
    # no permission decision beside it: only the changed field is sent, and
    # the normal permission flow is left alone (code.claude.com/docs/en/hooks,
    # "Using updatedInput", read 2026-09-25).
    sys.stdout.write(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "updatedInput": {"model": model},
    }}) + "\n")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:  # a hook that fails noisily blocks the call it was meant to help
        raise SystemExit(0)
