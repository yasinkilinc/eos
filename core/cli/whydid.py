"""`eos why-did`: the decisions of a run, work item or ticket, each with the evidence that proved it.

Read-only: nothing is written to any ledger. Times are UTC, as the ledger holds them.
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path


def _stamp(at: str, date: bool) -> str:
    return f"{at[:10]} {at[11:16]}" if date else at[11:16]


def _output(entry) -> str:
    if not entry.output_sha256 or not entry.output_path:
        return "output: -"
    path = Path(entry.output_path)
    if not path.is_file():
        return "output: gone"
    same = hashlib.sha256(path.read_bytes()).hexdigest() == entry.output_sha256
    return f"output: present ({'sha ok' if same else 'sha differs'}) {path}"


def _evidence_line(root, cited: str, session) -> str:
    from core import decisions

    found = decisions.find_evidence(root, cited, session)
    if found is None:
        return f"    {cited}  missing"
    entry = found[1]
    what = " ".join(part for part in (entry.tool, entry.target) if part)
    exit_part = f"exit {entry.exit_code}  " if entry.exit_code is not None else ""
    return f"    {cited}  {what}  {exit_part}{_stamp(entry.at, False)}  {_output(entry)}"


def _scope(root, value: str, session):
    """(work_item, execution) pairs for_scope should read."""
    from core import decisions, work

    records = decisions._all_records(root, session)
    if any(r.id == value for r in records):
        return [(None, value)]
    found = work.items(root, status="all")
    if any(r.work_item == value for r in records) or any(i.id == value for i in found):
        return [(value, None)]
    tickets = [i.id for i in found if i.ticket == value]
    if tickets:
        return [(i, None) for i in tickets]
    raise ValueError(f"{value!r} is no execution id, work item id or ticket key here")


def cmd_whydid(args: argparse.Namespace) -> int:
    from core import decisions

    words = list(args.words)
    root = args.path or (words.pop(0) if len(words) > 1 else ".")
    if len(words) != 1:
        print("error: give one run id, work item id or ticket key", file=sys.stderr)
        return 2
    try:
        scopes = _scope(root, words[0], args.session)
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    found: dict[str, decisions.Decision] = {}
    for work_item, execution in scopes:
        for item in decisions.for_scope(root, work_item=work_item, execution=execution,
                                        session=args.session):
            found.setdefault(item.id, item)
    wanted = [w.casefold() for w in (args.grep or "").split()]
    shown = [d for d in found.values() if all(w in d.claim.casefold() for w in wanted)]
    if not shown:
        print(f"no decisions recorded for {words[0]}" + (f" matching {args.grep!r}" if wanted else ""))
        return 0
    first: dict[str, str] = {}
    for record in decisions._all_records(root, args.session):
        for entry in record.events:
            if entry.kind == "decided" and entry.tool == decisions.TOOL and entry.ref:
                if entry.ref not in first or entry.at < first[entry.ref]:
                    first[entry.ref] = entry.at
    for item in sorted(shown, key=lambda d: first.get(d.id, d.at)):
        print(f"{_stamp(first.get(item.id, item.at), True)}  {item.id}  {item.state}  {item.claim}")
        if item.check:
            print(f"    check={item.check}")
        for reason in item.reasons:
            code, _, cited = reason.partition(" ")
            print(f"    reason={code} evidence={cited or '-'}")
        for cited in item.evidence:
            print(_evidence_line(root, cited, args.session))
    return 0
