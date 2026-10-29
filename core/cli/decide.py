"""`eos decide`: record a decision with its evidence, resolve an assumption, list the open ones.

Approval is not here on purpose: only the prompt hook, on the user's own words, approves.
"""
from __future__ import annotations

import argparse
import sys


def _print(decision) -> None:
    print(f"decision={decision.id} state={decision.state}")
    for reason in decision.reasons:
        code, _, cited = reason.partition(" ")
        print(f"reason={code} evidence={cited or '-'}")


def cmd_decide(args: argparse.Namespace) -> int:
    from core import decisions

    try:
        if args.open:
            run, _, _ = decisions._open_run(args.path, args.session)
            for item in decisions.open_assumptions(args.path, work_item=run.work_item,
                                                   execution=run.id, session=args.session):
                print(f"{item.id} {item.claim} check={item.check or '-'}")
            return 0
        if args.resolve:
            decision = decisions.resolve(args.path, args.resolve, args.evidence,
                                         session=args.session)
            _print(decision)
            return 0 if decision.state == "resolved" else 3
        decision = decisions.record(args.path, args.claim, args.evidence, check=args.check,
                                    session=args.session)
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    _print(decision)
    return 0
