"""The run (execution) ledger commands.

Moved out of core/eos.py (2.x roadmap F4), unchanged.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from core.cli.common import _route_run, _run_line


def _work_from_branch(path) -> str | None:
    """The one open work item whose ticket the current branch names (2.x roadmap E4)."""
    from core import brief, work

    _, branch = work.git_head(path)
    keys = set(brief.ticket_keys(path, branch or ""))
    if not keys:
        return None
    matches = [item for item in work.items(path)
               if item.ticket in keys and item.status in (work.ACTIVE, work.BLOCKED)]
    return matches[0].id if len(matches) == 1 else None


def _named_project(args: argparse.Namespace):
    """(root to record in, the line saying why) -- the caller's own path when the
    workspace names no project for this run (2.x roadmap C5, part 2)."""
    from core import workspace

    projects = workspace.load(args.path)
    if args.project:
        if not projects:
            raise ValueError(f"--project needs {workspace.path_for(args.path)}, which names no project")
        chosen = workspace.find(projects, args.project)
        if chosen is None:
            raise ValueError(f"no project {args.project!r} in {workspace.path_for(args.path)}; "
                             f"known: {', '.join(p.name for p in projects)}")
        return chosen.root, f"recorded in {chosen.name}'s ledger (--project)"
    if not projects or args.here:
        return args.path, None
    named = workspace.named_in(projects, args.title)
    if len(named) == 1:
        chosen, words = named[0]
        return chosen.root, (f"recorded in {chosen.name}'s ledger (the title names {words!r}); "
                             f"--here keeps a run in this one")
    if named:
        return args.path, (f"recorded here: the title names {', '.join(p.name for p, _ in named)}; "
                           f"--project picks one")
    near = workspace.near_in(projects, args.title)
    if near:
        return args.path, ("recorded here; the title almost names "
                           + ", ".join(f"{p.name} ({words!r})" for p, words in near[:3])
                           + " -- --project records it there")
    return args.path, None


def cmd_run_start(args: argparse.Namespace) -> int:
    from core import executions

    linked = None if args.work else _work_from_branch(args.path)
    try:
        root, why = _named_project(args)
        record = executions.start(root, args.title, procedure=args.procedure,
                                  work_item=args.work or linked, session=args.session,
                                  agent=args.agent, target=args.target)
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(record.id)
    if why:
        print(why, file=sys.stderr)
    if linked:
        print(f"linked to work item {linked} (its ticket is on this branch); --work names another")
    if not record.session:
        # Nothing to key the pointer on, so wrappers cannot find this run on
        # their own. Say how to reach it rather than let capture go quiet.
        print(f"No session id found; wrappers will not attach events on their own. "
              f"Export {executions.EXECUTION_ENV}={record.id} and "
              f"{executions.LEDGER_ENV}={executions.path_for(root)}, "
              f"or pass --session.", file=sys.stderr)
    _route_run(args.path, record)
    return 0


def cmd_run_event(args: argparse.Namespace) -> int:
    from core import executions

    try:
        entry = executions.event(args.path, args.execution, kind=args.kind, tool=args.tool,
                                 target=args.target, ref=args.ref, exit_code=args.exit,
                                 ms=args.ms, body=args.body, session=args.session,
                                 source="cli")
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"{entry.execution}\t{entry.kind}\t{entry.tool or '-'}\t{entry.target or '-'}")
    return 0


def cmd_run_finish(args: argparse.Namespace) -> int:
    from core import executions

    try:
        record = executions.finish(args.path, args.execution, outcome=args.outcome,
                                   lesson=args.lesson, session=args.session,
                                   next_time=args.next_time)
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(_run_line(record))
    if record.outcome_source == "claimed":
        _, left = executions.outcome_source(args.path, record)
        success = [item.split(": ", 1)[1] for item in left if item.startswith("success: ")]
        changed = [item for item in left if not item.startswith("success: ")]
        if changed:
            print(f"claimed: {', '.join(changed)} changed after its last passing check")
        if success:
            print(f"claimed: the procedure's Success check(s) never ran: {', '.join(success)}")
    return 0


def cmd_run_list(args: argparse.Namespace) -> int:
    from core import executions

    found = executions.load(args.path)
    if args.session:
        found = [r for r in found if r.session == args.session
                 or any(e.session == args.session for e in r.events)]
    if args.procedure:
        found = [r for r in found if r.procedure == args.procedure]
    if args.target:
        found = [r for r in found if r.target == args.target]
    if args.outcome:
        wanted = None if args.outcome == "open" else args.outcome
        found = [r for r in found if r.outcome == wanted]
    if args.since:
        found = [r for r in found if (r.started_at or "") >= args.since]
    if args.stats:
        done = [r for r in found if r.outcome == "ok"]
        verified = sum(1 for r in done if r.outcome_source == "verified")
        claimed = sum(1 for r in done if r.outcome_source == "claimed")
        rate = f"{verified / (verified + claimed):.0%}" if verified + claimed else "n/a"
        print(f"ok runs {len(done)}: verified {verified}, claimed {claimed}, "
              f"nothing to verify {len(done) - verified - claimed}; verified rate {rate}")
        return 0
    found = found[-args.limit:] if args.limit else found
    if args.format == "json":
        print(json.dumps([r.to_dict() for r in found], indent=2, ensure_ascii=False))
        return 0
    if not found:
        total = len(executions.load(args.path))
        print(f"No execution matches ({total} recorded in {executions.path_for(args.path)}). "
              "`eos run start` opens one.")
        return 0
    for record in reversed(found):
        print(_run_line(record))
    return 0


def _failed_step(root, record) -> str | None:
    """`<n>: <step as written>` for a run of a procedure whose steps name tools."""
    if not record.procedure or record.outcome != "failed":
        return None
    from core import notes, steps

    try:
        note = notes.find_procedure(root, record.procedure)
    except ValueError:
        return None
    parsed = steps.parse(note.body)
    number = steps.failed_step(parsed, record.events)
    return f"{number}: {parsed[number - 1].raw}" if number else None


def cmd_run_show(args: argparse.Namespace) -> int:
    from core import executions

    try:
        record = executions.resolve(executions.load(args.path), args.execution)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if args.format == "json":
        print(json.dumps(record.to_dict(), indent=2, ensure_ascii=False))
        return 0
    print(f"{record.id}  {record.title}")
    print(f"  outcome   {record.outcome or 'open'}")
    print(f"  started   {record.started_at or '-'}   finished {record.finished_at or '-'}")
    print(f"  session   {record.session or '-'}   agent {record.agent or '-'}")
    for label, value in (("procedure", record.procedure), ("work", record.work_item),
                         ("target", record.target), ("branch", record.branch),
                         ("commits", " .. ".join(c[:9] for c in (record.commit_start, record.commit_end) if c)),
                         ("lesson", record.lesson)):
        if value:
            print(f"  {label:<9} {value}")
    from core import verification
    checks = [v for v in verification.load(args.path) if v.execution == record.id]
    for check in checks:
        print(f"  verified  {check.code} {check.outcome} (exit {check.exit_code}) — {check.command}")
    sources = {}
    for e in record.events:
        sources[e.source or "wrapper/cli"] = sources.get(e.source or "wrapper/cli", 0) + 1
    by_source = ", ".join(f"{name} {count}" for name, count in sorted(sources.items()))
    print(f"  events    {len(record.events)}" + (f"  ({by_source})" if record.events else ""))
    failed = _failed_step(args.path, record)
    if failed:
        print(f"  failed at step {failed}")
    for e in record.events:
        tail = " ".join(part for part in (
            f"exit={e.exit_code}" if e.exit_code is not None else "",
            f"{e.ms}ms" if e.ms is not None else "",
            f"[{e.status}]" if e.status and e.status != "ok" else "",
            f"by {e.agent}" if e.agent else "", e.ref or "") if part)
        print(f"    {e.ord:>3}  {e.at}  {e.kind:<8} {e.tool or '-':<12} {e.target or '-':<10} {tail}")
    return 0


def cmd_run_tools(args: argparse.Namespace) -> int:
    from core import executions

    used = executions.tools(args.path, procedure=args.procedure, target=args.target)
    if args.format == "json":
        print(json.dumps([dataclasses.asdict(u) for u in used], indent=2, ensure_ascii=False))
        return 0
    if not used:
        print("No tool use recorded" + (f" for {args.procedure}" if args.procedure else "")
              + (f" against {args.target}" if args.target else "")
              + ". Wrappers record it with eos-event / eos run event inside an open run.")
        return 0
    for u in used:
        print(f"{u.tool:<16} {u.count:>4} call(s) in {u.runs} run(s)   {u.failures} non-zero   "
              f"last run {u.last_outcome or 'open'} {(u.last_at or '')[:10]}   {', '.join(u.targets) or '-'}")
    return 0


def cmd_run_diff(args: argparse.Namespace) -> int:
    from core import executions

    try:
        delta = executions.diff(args.path, args.execution)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if args.format == "json":
        print(json.dumps(delta, indent=2, ensure_ascii=False))
        return 0
    start, end = delta["commit_start"], delta["commit_end"]
    print(f"{delta['execution']}   commits {(start or '?')[:9]} .. {(end or 'open')[:9]}")
    print(f"  changed (recorded)  {len(delta['paths'])}")
    for path in delta["paths"]:
        print(f"    {path}")
    if delta["commits"]:
        print(f"  commits in range    {len(delta['commits'])}")
        for sha, subject in delta["commits"][:20]:
            print(f"    {sha[:9]}  {subject}")
        print(f"  files in range      {len(delta['committed_paths'])}")
        for path in delta["committed_paths"][:40]:
            print(f"    {path}")
    elif start and end and start == end:
        print("  no commit made during the run")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    return {
        "start": cmd_run_start,
        "event": cmd_run_event,
        "finish": cmd_run_finish,
        "list": cmd_run_list,
        "show": cmd_run_show,
        "tools": cmd_run_tools,
        "diff": cmd_run_diff,
    }[args.run_command](args)
