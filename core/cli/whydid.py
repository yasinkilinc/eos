"""`eos why-did`: the decisions of a run, work item or ticket, each with the evidence that proved it.

Read-only: nothing is written to any ledger. Times are local; the ledger holds
them in UTC. An evidence time carries its date when its local day is not the
decision's.
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path


def _local(at: str):
    """The local time of a readable stamp; None for one that is not (never converted)."""
    from core import decisions

    try:
        return decisions._parse(at).astimezone()
    except (ValueError, OverflowError):
        return None


def _day(at: str):
    found = _local(at)
    return found.date() if found else None


def _stamp(at: str, date: bool) -> str:
    found = _local(at)
    if found is None:
        return "unreadable"
    return found.strftime("%Y-%m-%d %H:%M" if date else "%H:%M")


def _output(entry) -> str:
    if not entry.output_sha256 or not entry.output_path:
        return "output: -"
    path = Path(entry.output_path)
    if not path.is_file():
        return "output: gone"
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(1 << 20):
                digest.update(chunk)
    except OSError:
        return "output: unreadable"
    same = digest.hexdigest() == entry.output_sha256
    return f"output: present ({'sha ok' if same else 'sha differs'}) {path}"


def _evidence_line(index, cited: str, day) -> str:
    found = index.get(cited)
    if found is None:
        return f"    {cited}  missing"
    entry = found[1]
    what = " ".join(part for part in (entry.tool, entry.target, entry.ref) if part)
    exit_part = f"exit {entry.exit_code}  " if entry.exit_code is not None else ""
    when = _stamp(entry.at, _day(entry.at) != day)
    return f"    {cited}  {what}  {exit_part}{when}  {_output(entry)}"


def _scope(root, value: str, records):
    """(work_item, execution) pairs for_scope should read."""
    from core import decisions, work

    if any(r.id == value for r in records):
        return [(None, value)]
    found = [i for r in decisions.project_roots(root) for i in work.items(r, status="all")]
    if any(r.work_item == value for r in records) or any(i.id == value for i in found):
        return [(value, None)]
    wanted = value.casefold()
    tickets = [i.id for i in found if i.ticket and i.ticket.casefold() == wanted]
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
        records = decisions.all_records(root, args.session)
        scopes = _scope(root, words[0], records)
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    found: dict[str, decisions.Decision] = {}
    for work_item, execution in scopes:
        for item in decisions.for_scope(root, work_item=work_item, execution=execution,
                                        records=records):
            found.setdefault(item.id, item)
    wanted = [w.casefold() for w in (args.grep or "").split()]
    shown = [d for d in found.values() if all(w in d.claim.casefold() for w in wanted)]
    if not shown:
        print(f"no decisions recorded for {words[0]}" + (f" matching {args.grep!r}" if wanted else ""))
        return 0
    first: dict[str, str] = {}
    refused: dict[str, list] = {}
    for record in records:
        for entry in record.events:
            if entry.tool != decisions.TOOL or not entry.ref:
                continue
            if entry.kind == "noted":
                refused.setdefault(entry.ref, []).append(entry)
            elif entry.kind == "decided":
                if entry.ref not in first or decisions._when(entry.at) < decisions._when(first[entry.ref]):
                    first[entry.ref] = entry.at
    index = decisions.event_index(records)
    for item in sorted(shown, key=lambda d: decisions._when(first.get(d.id, d.at))):
        made = first.get(item.id, item.at)
        print(f"{_stamp(made, True)}  {item.id}  {item.state}  {item.claim}")
        if item.check:
            print(f"    check={item.check}")
        for reason in item.reasons:
            print(f"    {decisions.reason_line(reason)}")
        for cited in item.evidence:
            print(_evidence_line(index, cited, _day(made)))
        for entry in sorted(refused.get(item.id, []), key=lambda e: decisions._when(e.at)):
            print(f"    resolve refused {_stamp(entry.at, _day(entry.at) != _day(made))}")
            for reason in entry.reasons or ():
                print(f"      {decisions.reason_line(reason)}")
        if item.state == "resolved":
            when = _stamp(item.at, _day(item.at) != _day(made))
            print(f"    resolved later: {' '.join((when, *item.evidence))}")
        elif item.state == "approved":
            print(f"    approved later: {_stamp(item.at, _day(item.at) != _day(made))}")
    return 0
