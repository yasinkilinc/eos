"""Routing and capability commands.

Moved out of core/eos.py (2.x roadmap F4), unchanged.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from pathlib import Path
from core.cli.common import _print_advised_vs_used


def cmd_route(args: argparse.Namespace) -> int:
    """Which model and how much effort this task deserves, and why (ADR-025).

    Advice for the harness, not an action: EOS calls no model. A refused
    explicit choice -- an unknown model, an unknown effort, a malformed
    `[model_routing]` table -- exits 2 with the reason, never a substitute.
    """
    import core.routing as routing
    from core.routing import trace, usage

    if args.usage_from:
        # Called from a Stop hook with the harness transcript: always exit 0.
        if usage.record(args.path, args.session, args.usage_from):
            print(f"usage recorded for session {args.session}")
        return 0

    if args.stats:
        recorded, sessions = len(trace.load(args.path)), len(usage.load(args.path))
        rows = trace.stats(args.path)
        if not rows:
            print(f"No decisions recorded in runs yet ({recorded} recorded outside runs, "
                  f"model usage for {sessions} session(s)). A decision made between "
                  "`eos run start` and `eos run finish` is joined to that run's outcome.")
            _print_advised_vs_used(args.path)
            return 0
        total = sum(sum(counts.values()) for _, counts in rows)
        print(f"Routing decisions joined to runs: {total} of {recorded} recorded; "
              f"model usage for {sessions} session(s)")
        print()
        for (kind, level, model, effort), counts in rows:
            tally = "  ".join(f"{word} {counts[word]}" for word in ("ok", "failed", "abandoned", "open")
                              if counts[word])
            target = f"{model}/{effort}" if effort else model
            print(f"  {kind:<24} {level:<8} {target:<14} {tally}")
        _print_advised_vs_used(args.path)
        return 0

    if args.eval:
        from core.routing import evaluate

        try:
            result = evaluate.run(args.path, evaluate.load(args.eval), split=args.split)
        except (OSError, ValueError, routing.OverrideError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        if args.split == "test":
            result["earlier_test_runs_other_config"] = evaluate.record_test(args.path, args.eval, result)
        print(json.dumps(result, indent=2, ensure_ascii=False) if args.json else evaluate.render(result))
        if args.split == "test" and result["earlier_test_runs_other_config"]:
            print(f"warning: this test set was measured {result['earlier_test_runs_other_config']} time(s) "
                  "before under a different configuration -- tune on dev, not on test", file=sys.stderr)
        # 0 PASS, 1 FAIL: the gate is what a CI step reads.
        return 0 if result["gate"]["pass"] else 1

    task = " ".join(args.task or ()).strip()
    if not task:
        hint = "" if Path(args.path).is_dir() else f" ({args.path!r} is not a project directory)"
        print(f'error: a task is required: eos route <path> "<task>"{hint}', file=sys.stderr)
        return 2
    try:
        decision = routing.route(args.path, task, files=tuple(args.file or ()), session=args.session,
                                 model=args.model, effort=args.effort, record=not args.no_record,
                                 fresh=args.fresh)
    except (routing.OverrideError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(decision.to_dict(), indent=2, ensure_ascii=False))
        return 0

    from core.routing.score import WEIGHTS

    factors = ", ".join(f"{name} {decision.factors[name]:.2f}"
                        for name, _ in WEIGHTS if name in decision.factors)
    lines = [
        f"Task: {task}",
        "",
        f"Type:        {decision.task_type}",
        f"Complexity:  {decision.level}  (score {decision.score:.2f})",
        f"Model:       {decision.model}",
        f"Effort:      {decision.effort or '(none: the model takes no effort setting)'}",
        f"Confidence:  {decision.confidence:.2f}",
        f"Verify:      depth {decision.verify_depth}",
        f"Source:      {decision.override_source}" + ("  (reused)" if decision.reused else ""),
        "",
        "Reason:",
        decision.reason,
    ]
    if factors:
        lines += ["", f"Factors: {factors}"]
    lines.append(f"Alternatives: {', '.join(decision.alternatives) or 'none'}")
    print("\n".join(lines))
    return 0


def cmd_capabilities(args: argparse.Namespace) -> int:
    """What to run instead of a raw command (ADR-026).

    The list a session needs before it reaches for `curl` or a database client:
    each wrapper, what it answers, and -- with --command -- whether a command
    line is the raw form one of them covers.
    """
    from core import capabilities

    try:
        declared = capabilities.load(args.path)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if getattr(args, "status", None) is not None:
        found = capabilities.truth(args.path, args.status)
        if args.format == "json":
            print(json.dumps(found.to_dict() if found else None, indent=2, ensure_ascii=False))
        elif found is None:
            print(f"{args.status}: not catalogued (no such capability)")
        else:
            print(f"{found.name}: {found.state}  (health: {found.health})"
                  + (f"\n  {found.detail}" if found.detail else ""))
        return 0
    if args.raw is not None:
        found = capabilities.match(args.raw, declared)
        wrapper = capabilities.wrapper_in(args.raw, declared)
        if args.format == "json":
            print(json.dumps({"wrapper": wrapper.name if wrapper else None,
                              "covered_by": found.capability.name if found else None,
                              "tier": found.tier if found else None,
                              "use": found.capability.run if found else None}, indent=2))
        elif wrapper is not None:
            print(f"already a wrapper: {wrapper.line()}")
        elif found is not None:
            print(f"{found.tier}: {capabilities.remedy(found)}")
        else:
            print("no capability covers this command")
        return 0
    shown = capabilities.for_task(args.task, declared) if args.task else declared
    if args.format == "json":
        print(json.dumps([dataclasses.asdict(c) for c in shown], indent=2, ensure_ascii=False))
        return 0
    if not declared:
        print(f"No capabilities declared ({capabilities.path_for(args.path)}). A project that routes "
              "its external calls through wrappers lists them there, one [[capability]] each.")
        return 0
    if not shown:
        print(f"No capability matches that task; {len(declared)} declared: "
              + ", ".join(c.name for c in declared))
        return 0
    for capability in shown:
        print(capability.line())
    return 0
