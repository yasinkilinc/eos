"""`eos gate`: what the ledger says about an action, for a hook to read as JSON."""
from __future__ import annotations

import argparse
import dataclasses
import json
import sys


def cmd_gate(args: argparse.Namespace) -> int:
    from core import gates

    if args.gate_command == "mode":
        print(gates.mode(args.path))
        return 0
    current = gates.mode(args.path)
    try:
        refusals = gates.check(args.path, session=args.session, action=args.action,
                               path=args.file, tool=args.tool)
        if current != "off":
            gates.record_refusals(args.path, refusals, session=args.session, action=args.action,
                                  target=args.file)
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        refusals = []
    print(json.dumps({"mode": current,
                      "refusals": [dataclasses.asdict(r) for r in refusals]}, ensure_ascii=False))
    return 0
