"""`eos report`: done with evidence, waiting with its condition, questions for the user."""
from __future__ import annotations

import argparse
import json
import sys


def cmd_report(args: argparse.Namespace) -> int:
    from core import report

    try:
        since = report.parse_since(args.since) if args.since else report.last_prompt(args.path)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    found = report.build(args.path, since=since)
    if args.format == "json":
        print(json.dumps(found, indent=2, ensure_ascii=False))
    else:
        print(report.render(found))
    return 0
