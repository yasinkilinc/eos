"""Procedure commands.

Moved out of core/eos.py (2.x roadmap F4), unchanged.
"""
from __future__ import annotations

import argparse
import json
import sys
from core import notes
from core.cli.common import _split_csv


def cmd_procedure_list(args: argparse.Namespace) -> int:
    from core import executions

    # A listing is browsed by a person deciding what to run; an expired one
    # still belongs in it so its state is visible, not silently gone (N8).
    found = notes.procedures(args.path, include_expired=True)
    if args.tool:
        found = [n for n in found if args.tool in notes.procedure_tools(n)]
    if args.target:
        ran_against = {r.procedure for r in executions.load(args.path) if r.target == args.target}
        found = [n for n in found if n.procedure in ran_against]
    if args.format == "json":
        print(json.dumps([{"procedure": n.procedure, "title": n.title, "runs_ok": n.runs_ok or 0,
                           "runs_failed": n.runs_failed or 0, "last_verified": n.last_verified,
                           "tools": notes.procedure_tools(n), "path": str(n.path)} for n in found],
                         indent=2, ensure_ascii=False))
        return 0
    if not found:
        print(f"No procedure recorded here ({notes.notes_dir(args.path)}). "
              "`eos procedure new --title \"…\" --step \"…\"` writes the first.")
        return 0
    for n in found:
        print(f"{n.procedure}\t{n.runs_ok or 0} ok / {n.runs_failed or 0} failed\t"
              f"{(n.last_verified or 'never')[:10]}\t{notes.procedure_confidence(n)}\t{n.title}")
    return 0


def _last_run_steps(note, runs) -> list[dict]:
    from core import steps

    finished = [r for r in runs if r.outcome and r.events]
    return steps.attribute(steps.parse(note.body), finished[-1].events) if finished else []


def cmd_procedure_show(args: argparse.Namespace) -> int:
    from core import executions, workspace

    try:
        note = notes.find_procedure(args.path, args.procedure)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    every = executions.of_procedure(args.path, note.procedure or "")
    runs = every[-3:]
    # 2.x M2 (decided 2026-09-27): the counter moves on every ok run; how many of those
    # were verified or claimed is shown beside it, not enforced.
    verified = sum(1 for r in every if r.outcome == "ok" and r.outcome_source == "verified")
    claimed = sum(1 for r in every if r.outcome == "ok" and r.outcome_source == "claimed")
    # C5c's open point: the header names the store (this project), not where a
    # step's relative paths actually resolve from -- stated only when the
    # project belongs to a workspace (`[workspace] root`, listed back by that
    # workspace's own projects.toml); None otherwise, the store IS where.
    home = workspace.of(args.path)
    if args.format == "json":
        print(json.dumps({"procedure": note.procedure, "title": note.title,
                          "steps": notes.procedure_steps(note),
                          "last_run_steps": _last_run_steps(note, runs),
                          "prerequisites": notes.procedure_prerequisites(note),
                          "success": notes.procedure_success(note),
                          "known_failures": notes.procedure_known_failures(note),
                          "runs_ok": note.runs_ok or 0, "runs_failed": note.runs_failed or 0,
                          "runs_verified": verified, "runs_claimed": claimed,
                          "last_verified": note.last_verified,
                          "recent": [r.to_dict() for r in reversed(runs)],
                          "steps_run_from": str(home) if home is not None else None,
                          "path": str(note.path)}, indent=2, ensure_ascii=False))
        return 0
    print(f"{note.title}   [{note.procedure}]")
    if home is not None:
        print(f"  steps run from the workspace root: {home}")
    print(f"  runs      {note.runs_ok or 0} ok / {note.runs_failed or 0} failed   "
          f"of the ok: verified {verified}, claimed {claimed}   "
          f"last verified {note.last_verified or 'never'}   "
          f"confidence {notes.procedure_confidence(note)}")
    for label, items in (("prerequisites", notes.procedure_prerequisites(note)),
                         ("success", notes.procedure_success(note))):
        for i, item in enumerate(items):
            print(f"  {label if i == 0 else '':<13} {item}")
    from core import steps as typed

    parsed = typed.parse(note.body)
    finished = [r for r in runs if r.outcome and r.events]
    rows = typed.attribute(parsed, finished[-1].events) if finished and any(s.tool for s in parsed) else []
    print("  steps" + (f"          (last run {finished[-1].id})" if rows else ""))
    for step in parsed:
        status = rows[step.number - 1]["status"] if rows else ""
        status = "-" if status == "not seen" else status
        print(f"    {step.number:>2}. {status:<7} {step.raw}" if rows else f"    {step.number:>2}. {step.raw}")
    avoid = notes._items(notes.section_in(note.body, "When not to use this"))
    for i, item in enumerate(avoid):
        print(f"  {'not for' if i == 0 else '':<13} {item}")
    failures = notes.procedure_known_failures(note)
    if failures:
        print("  known failures")
        for item in failures[-5:]:
            print(f"    - {item}")
    if runs:
        print("  recent runs")
        for r in reversed(runs):
            print(f"    {(r.finished_at or r.started_at or '')[:16]}  {r.outcome or 'open':<9} "
                  f"{r.target or '-':<10} {r.id}")
    print(f"  file      {note.path}")
    return 0


def cmd_procedure_new(args: argparse.Namespace) -> int:
    steps = list(args.step or [])
    if args.steps == "-":
        steps += [line.strip() for line in sys.stdin.read().splitlines() if line.strip()]
    sections = ["## Steps", ""] + [f"{n}. {s}" for n, s in enumerate(steps, start=1)]
    for heading, items in (("Prerequisites", args.prerequisite), ("Success", args.success)):
        if items:
            sections += ["", f"## {heading}", ""] + [f"- {item}" for item in items]
    if args.body:
        sections = [args.body.strip(), ""] + sections
    try:
        path = notes.add_note(args.path, kind="procedure", title=args.title,
                              body="\n".join(sections), tags=_split_csv(args.tags),
                              scope=_split_csv(args.scope), source=args.source,
                              session=args.session, procedure=args.slug)
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    note = notes.parse_note(path)
    print(f"{note.procedure}\t{path}")
    return 0


def cmd_procedure_lint(args: argparse.Namespace) -> int:
    """Steps naming a tool nobody can find, and procedures with no success (2.x roadmap E2)."""
    from core import procedure_lint

    reports = procedure_lint.lint(args.path)
    if args.format == "json":
        print(json.dumps(reports, indent=2, ensure_ascii=False))
    else:
        print(procedure_lint.render(reports))
    return 0


def cmd_procedure_audit(args: argparse.Namespace) -> int:
    from core import executions

    report = executions.audit_procedures(args.path)
    if args.format == "json":
        print(json.dumps(report, indent=2, ensure_ascii=False))
    elif not report:
        print("No procedure recorded here; nothing to audit.")
    else:
        for entry in report:
            state = "ok" if not entry["problems"] else "; ".join(entry["problems"])
            print(f"{entry['procedure']}\t{entry['runs_ok']} ok / {entry['runs_failed']} failed\t{state}")
    # Exit 1 only for the one problem that is an integrity failure: counters
    # the ledger does not support. The rest are observations for a person.
    return 1 if any(entry["mismatch"] for entry in report) else 0


def cmd_procedure(args: argparse.Namespace) -> int:
    return {
        "list": cmd_procedure_list,
        "show": cmd_procedure_show,
        "new": cmd_procedure_new,
        "audit": cmd_procedure_audit,
        "lint": cmd_procedure_lint,
    }[args.procedure_command](args)
