"""The work ledger commands.

Moved out of core/eos.py (2.x roadmap F4), unchanged.
"""
from __future__ import annotations

import argparse
import json
import sys
from core.cli.common import _hours, _no_work_here, _record_work, _resolve_work, _split_csv, _work_line


def cmd_work_add(args: argparse.Namespace) -> int:
    from core import work

    try:
        entry = work.open_item(
            args.path, args.title, body=args.body, ticket=args.ticket,
            scope=_split_csv(args.scope), session=args.session,
            agent=args.agent, claim=args.claim,
        )
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    state = work.sync_state(args.path)
    print(f"{entry.id}\t{'claimed' if args.claim else 'open'}\t{args.title}")
    print(work.sync_sentence(state))
    return 0


def cmd_work_claim(args: argparse.Namespace) -> int:
    return _record_work(args, "claim", body=args.note)


def cmd_work_log(args: argparse.Namespace) -> int:
    return _record_work(args, "log", body=args.body)


def cmd_work_block(args: argparse.Namespace) -> int:
    return _record_work(args, "block", body=args.reason)


def cmd_work_unblock(args: argparse.Namespace) -> int:
    return _record_work(args, "unblock", body=args.note)


def cmd_work_done(args: argparse.Namespace) -> int:
    return _record_work(args, "done", body=args.note)


def cmd_work_drop(args: argparse.Namespace) -> int:
    return _record_work(args, "drop", body=args.reason)


def cmd_work_list(args: argparse.Namespace) -> int:
    """What is in flight, and whether anyone else can see it.

    The sync sentence is printed on every run rather than only when something
    is wrong: this ledger's entire purpose is that a second session reads what
    the first one wrote, and "committed but not pushed" is the state in which
    it silently is not.
    """
    import datetime

    from core import work

    now = datetime.datetime.now(datetime.timezone.utc)
    status = None if args.status == "live" else args.status

    if args.across:
        ledgers = work.across_ledgers(args.path)
        payload = []
        for label, ledger in ledgers:
            found = work.fold(work.load_path(ledger))
            if status != "all":
                keep = work.LIVE if status is None else (status,)
                found = [item for item in found if item.status in keep]
            payload.append((label, work.sort_items(found)))
        if args.format == "json":
            print(json.dumps([{"project": label, "items": [item.to_dict() for item in found]}
                              for label, found in payload], indent=2, ensure_ascii=False))
            return 0
        for label, found in payload:
            if not found:
                continue
            print(f"== {label}")
            for item in found:
                for line in _work_line(item, now):
                    print(line)
        total = sum(len(found) for _, found in payload)
        if not total:
            print(f"Nothing is in flight in any of {len(ledgers)} ledger(s) under "
                  f"{work.path_for(args.path).parent.parent}.")
        if len(ledgers) == 1:
            # --across that silently reads one directory looks like a broken
            # flag. It is the right answer for a project whose knowledge
            # directory is its own; say which of the two happened.
            print(f"One ledger only: no sibling ledgers under "
                  f"{work.path_for(args.path).parent.parent}. `[knowledge] dir` is "
                  "what puts several projects under one root.")
        print(work.sync_sentence(work.sync_state(args.path)))
        return 0

    found = work.items(args.path, status=status)
    if args.session:
        found = [item for item in found
                 if any(holder.get("session") == args.session for holder in item.holders)]
    if args.format == "json":
        print(json.dumps([item.to_dict() for item in found], indent=2, ensure_ascii=False))
        return 0
    for item in found:
        for line in _work_line(item, now):
            print(line)
    if not found:
        every = work.fold(work.load(args.path))
        if not every:
            print(_no_work_here(args.path))
        else:
            print(f"{len(every)} item(s) recorded here, none {args.status}. "
                  f"`eos work list {args.path} --status all` lists every one of them.")
    else:
        stale = [item for item in found if item.is_stale(now)]
        print(f"\n{len(found)} item(s) in flight"
              + (f", {len(stale)} stale (no event for over {work.STALE_AFTER_HOURS}h)"
                 if stale else "") + ".")
    print(work.sync_sentence(work.sync_state(args.path)))
    return 0


def cmd_work_show(args: argparse.Namespace) -> int:
    """One item in full: its events, and what git says about its ticket."""
    import datetime

    from core import work

    item = _resolve_work(args)
    if item is None:
        return 1
    history = [entry for entry in work.load(args.path) if entry.id == item.id]
    evidence = work.ticket_commits(args.path, item.ticket) if item.ticket else None

    if args.format == "json":
        print(json.dumps({"item": item.to_dict(),
                          "events": [entry.to_dict() for entry in history],
                          "ticket_commits": evidence}, indent=2, ensure_ascii=False))
        return 0

    now = datetime.datetime.now(datetime.timezone.utc)
    for line in _work_line(item, now):
        print(line)
    print()
    for entry in history:
        who = work.who(entry.session, entry.agent)
        where = f"  {entry.branch}@{entry.commit[:7]}" if entry.commit else ""
        print(f"{entry.at}  {entry.event:<8} {who}{where}")
        if entry.body:
            print(f"    {entry.body}")
    if evidence is None:
        print("\nNo ticket on this item, so there is nothing to check it against.")
    elif not evidence["indexed"]:
        print(f"\nWhether any commit names {item.ticket} is unknown here: there is no "
              f"index at .eos/data/eos.db. `eos index {args.path}` builds one.")
    elif not evidence["commits"]:
        print(f"\nThe index holds no commit naming {item.ticket}. That is either work "
              "that is not committed yet, or a claim that outran it -- this cannot "
              "tell those apart.")
    else:
        print(f"\nCommits naming {item.ticket}:")
        for commit in evidence["commits"]:
            print(f"  {commit['sha'][:9]}  {commit['at']}  {commit['subject']}")
    return 0


def cmd_work_stats(args: argparse.Namespace) -> int:
    """What happened here, as opposed to how often a command was called.

    `eos cost` says whether the engine was reached for; this says whether
    reaching for it changed anything. Every number below should fall if the
    ledger is doing its job, and a rising one is the report doing its job.
    """
    from core import work

    report = work.statistics(args.path, since=args.since)
    if args.format == "json":
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0
    if not report.get("events"):
        window = f" since {args.since}" if args.since else ""
        print(f"No work has been recorded here{window} ({work.path_for(args.path)}). "
              "`eos work add` records the first item; there is nothing yet to measure.")
        return 0

    window = f" since {args.since}" if args.since else ""
    print(f"{report['items']} item(s) over {report['events']} event(s){window}, "
          f"{report['claimed']} of them claimed by a session.")
    print()
    # Each of these is a cost somebody already paid, not a score.
    print(f"Collisions        {report['contested']:>4}  item(s) claimed by two sessions at once")
    print(f"Went quiet        {report['went_quiet']:>4}  item(s) held with no event for over "
          f"{work.STALE_AFTER_HOURS}h")
    print(f"Closed            {report['closed']:>4}  ({report['done']} done, "
          f"{report['dropped']} dropped; {report['blocked']} block(s) recorded)")
    print(f"Open now          {report['open_now']:>4}  ({report['stale_now']} stale, "
          f"{report['contested_now']} contested)")
    if report["median_hours_to_close"] is not None:
        print(f"Claim to close      {_hours(report['median_hours_to_close'])} median, "
              f"{_hours(report['longest_hours_to_close'])} longest")
    print()
    print("These count what sessions recorded. Work done without closing an item, and "
          "an item closed without the work, are indistinguishable from here.")
    return 0


def cmd_work(args: argparse.Namespace) -> int:
    return {
        "add": cmd_work_add,
        "claim": cmd_work_claim,
        "log": cmd_work_log,
        "block": cmd_work_block,
        "unblock": cmd_work_unblock,
        "done": cmd_work_done,
        "drop": cmd_work_drop,
        "list": cmd_work_list,
        "show": cmd_work_show,
        "stats": cmd_work_stats,
    }[args.work_command](args)
