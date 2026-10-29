"""`eos gate`: what the ledger says about an action, for a hook to read as JSON."""
from __future__ import annotations

import argparse
import dataclasses
import json
import sys


def _answer(current: str, refusals: list, error: str | None = None) -> None:
    payload = {"mode": current, "refusals": [dataclasses.asdict(r) for r in refusals]}
    if error:
        payload["error"] = error
    print(json.dumps(payload, ensure_ascii=False))


def cmd_gate(args: argparse.Namespace) -> int:
    from core import gates

    if args.gate_command == "mode":
        print(gates.mode(args.path))
        return 0
    # A hook reads this line, not the exit code: whatever goes wrong, one JSON line, exit 0.
    try:
        current = gates.mode(args.path)
    except Exception:  # noqa: BLE001
        current = "off"
    if args.action not in gates.ACTIONS:
        _answer(current, [], "unknown-action")
        return 0
    try:
        refusals = gates.check(args.path, session=args.session, action=args.action,
                               path=args.file, tool=args.tool)
        if current != "off":
            gates.record_refusals(args.path, refusals, session=args.session, action=args.action,
                                  target=args.file)
    except Exception as exc:  # noqa: BLE001
        print(f"error: {exc}", file=sys.stderr)
        _answer(current, [], type(exc).__name__)
        return 0
    _answer(current, refusals)
    return 0
