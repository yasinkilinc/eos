"""Code intelligence commands: rules, why, trace, impact, query, parents, verify, cost, brief.

Moved out of core/eos.py (2.x roadmap F4), unchanged.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from core import index
from core import inspector
from core import links
from core.cli.common import _escape_what_stdout_cannot_encode, _print_fact, _query_cell, _read_git_ref


def cmd_query(args: argparse.Namespace) -> int:
    _escape_what_stdout_cannot_encode()
    database = index.db_path(args.path)
    if not database.is_file():
        root = Path(args.path).expanduser().resolve()
        print(f"error: no index at {database}; build it with `eos index {root}`", file=sys.stderr)
        return 1
    if (args.sql is None) == (args.search is None):
        print("error: give either one SQL statement or --search, not both", file=sys.stderr)
        return 1
    try:
        rebuilt = index.refresh(args.path)
    except (index.IndexBuildError, OSError, ValueError, sqlite3.Error) as exc:
        # ValueError: a .eos/config.toml that no longer parses.
        try:
            built_at = index.run_query(database, "SELECT value FROM meta WHERE key = 'built_at'")[1][0][0]
        except (sqlite3.Error, IndexError):
            built_at = "unknown"
        print(f"warning: answering from a possibly stale index (built {built_at}); "
              f"could not bring it up to date: {exc}", file=sys.stderr)
    else:
        if rebuilt is not None:
            print(f"Index rebuilt before answering: its sources changed since it was built ({rebuilt.seconds:.2f}s)",
                  file=sys.stderr)
    try:
        if args.search is not None:
            columns, rows = index.search(database, args.search, limit=args.limit)
        else:
            columns, rows = index.run_query(database, args.sql)
    except sqlite3.Error as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if columns:
        print("\t".join(columns))
    for row in rows:
        print("\t".join(_query_cell(value) for value in row))
    return 0


def cmd_impact(args: argparse.Namespace) -> int:
    if args.include:
        # Provenance lives in the index, so make sure it is not older than the
        # scan it describes. Plain `eos impact` deliberately does not: it is
        # also reached through an MCP call, where a rebuild is the wrong
        # amount of work to do inside a request.
        try:
            index.refresh(args.path)
        except (index.IndexBuildError, OSError, ValueError, sqlite3.Error) as exc:
            print(f"warning: index may be stale ({type(exc).__name__}: {exc})", file=sys.stderr)

    answer = inspector.impact(args.path, args.file, depth=args.depth)
    if args.include:
        try:
            detail = inspector.why(args.path, args.file)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        if "facts" in args.include:
            answer["facts"] = detail["facts"]
            answer["inbound_facts"] = detail["inbound"]
        if "coverage" in args.include:
            answer["coverage"] = detail["coverage"]
        if "history" in args.include:
            answer["history"] = inspector.file_history(args.path, args.file)
    print(json.dumps(answer, indent=2, ensure_ascii=False))
    return 0


def cmd_why(args: argparse.Namespace) -> int:
    """Print where a fact came from, and what nothing looked for."""
    try:
        rebuilt = index.refresh(args.path)
    except (index.IndexBuildError, OSError, ValueError, sqlite3.Error) as exc:
        print(f"warning: index may be stale ({type(exc).__name__}: {exc})", file=sys.stderr)
    else:
        if rebuilt is not None:
            print("note: index rebuilt from changed sources", file=sys.stderr)

    try:
        answer = inspector.why(args.path, args.subject, args.predicate, args.min_confidence)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.format == "json":
        print(json.dumps(answer, indent=2, ensure_ascii=False))
        return 0

    if answer["subject"]:
        print(answer["subject"])
        for entry in answer["facts"]:
            _print_fact(entry, "  ")
        for entry in answer["inbound"]:
            _print_fact(entry, "  <- ")
        if not answer["facts"] and not answer["inbound"]:
            print("  (no facts recorded)")
    # Always: the question "did you not find it, or did you not look" has to be
    # answerable at the moment it is asked, not by reading a separate table.
    for entry in answer["coverage"]:
        found = f"{entry['hits']} in {entry['files_with_hits']} file(s)" if entry["hits"] else "nothing"
        line = (f"  coverage: {entry['detector']} looked at {entry['files_eligible']} file(s) "
                f"for {entry['predicate']}, found {found}")
        # The whole question is about the one file that was asked about, and a
        # project-wide count does not answer it. Say what happened here.
        verdict = entry.get("applies_here")
        if verdict == "yes" and entry.get("hits_here"):
            line += f"; {entry['hits_here']} here"
        elif verdict == "yes":
            line += "; examined this file, found nothing here"
        elif verdict == "no":
            line += "; does not read files like this one"
        elif verdict == "structural":
            line += "; records structure, not file contents, so it has no per-file answer"
        elif verdict == "unknown":
            line += "; produced nothing anywhere, so whether it reads this file is not derivable"
        print(line)
    return 0


def cmd_rules(args: argparse.Namespace) -> int:
    """List the behaviour identifiers this project throws, and what names them."""
    try:
        index.refresh(args.path)
    except (index.IndexBuildError, OSError, ValueError, sqlite3.Error) as exc:
        print(f"warning: index may be stale ({type(exc).__name__}: {exc})", file=sys.stderr)
    try:
        answer = inspector.rules(args.path, untested_only=args.untested)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.format == "json":
        print(json.dumps(answer, indent=2, ensure_ascii=False))
        return 0

    if not answer["codes"]:
        if not answer.get("detector_ran") and not answer.get("detector_applies"):
            print("Nothing here raises a refusal identified by a constant: the detector "
                  "reads Java, and this project has none indexed.")
            return 0
        if not answer.get("detector_ran"):
            # Actionable, not a pointer. The scan output this index was built
            # from predates behaviour-code extraction, and rebuilding the index
            # cannot invent facts a scan never wrote.
            print("This project's scan output predates behaviour-code extraction, so "
                  "nothing looked for them. Run `eos scan` (not `eos index`: rebuilding "
                  "the index re-reads the same scan output).", file=sys.stderr)
            return 1
        if args.untested and answer.get("total"):
            # The filter emptied the list, not the detector -- and saying "found
            # no behaviour codes" when 70 are indexed is the same silence-versus-
            # absence failure one level up. A session hit exactly this: it read
            # the answer as the command being broken for this project, re-ran it
            # four ways, and only settled it by running the unflagged form and
            # finding the codes it had just been told did not exist.
            tally = answer.get("coverage", {})
            graded = ", ".join(f"{count} {grade}" for grade, count in tally.items() if count)
            print(f"All {answer['total']} behaviour code(s) here are named by at least "
                  f"one test, so --untested matches none of them ({graded}). "
                  "Drop --untested to see them, or read the grades: only `none` means "
                  "no test names the code at all.")
            return 0
        print("The detector ran and found no behaviour codes: nothing here raises a "
              "refusal identified by a constant. `eos why` shows what it examined.")
        return 0
    marks = {"none": "!", "named": "~", "reachable": "-", "asserted": " ", "verified": "+"}
    # Bounded by default, and the bound is not a style choice. Telemetry on its
    # first day measured the unbounded listing at 37,874 tokens on one service
    # -- for a command the skill tells agents to run before writing a test.
    # A habit that costs a third of a context window is not a habit anyone
    # keeps. The tally below is the part worth reading; the list is a sample of
    # the worst, and --format json is still complete for a machine.
    shown = answer["codes"] if args.limit <= 0 else answer["codes"][:args.limit]
    for entry in shown:
        where = ", ".join(entry["where"][:2]) or "?"
        print(f"{marks[entry['coverage']]} {entry['code']:<40} {entry['coverage']:<10} {where}")
        for reference in entry["thrown_at"][:3]:
            print(f"    thrown at {reference}")
        if entry["reached_by"]:
            print(f"    reached by {len(entry['reached_by'])} test file(s): "
                  f"{', '.join(Path(p).name for p in entry['reached_by'][:2])}")
        if entry["tests"]:
            print(f"    named by {len(entry['tests'])} test file(s): "
                  f"{', '.join(Path(p).name for p in entry['tests'][:2])}")
        run = entry.get("verification")
        if run:
            print(f"    last run {run['outcome']} (exit {run['exit_code']}) at {run['recorded_at']}"
                  + (f", verdict {run['verdict']}" if run.get("verdict") else ""))
        if not entry["reached_by"] and not entry["tests"] and not run:
            print("    no test reaches the class or names the code")

    hidden = len(answer["codes"]) - len(shown)
    if hidden:
        print(f"\n… and {hidden} more, least covered first. `--limit 0` for all of them, "
              "`--format json` for every field.")

    tally = answer["coverage"]
    print(f"\n{answer['total']} code(s):")
    print(f"  verified   {tally['verified']:>4}  a recorded run passed (the only rung that is not analysis)")
    print(f"  asserted   {tally['asserted']:>4}  a test reaches the class and names the code")
    print(f"  reachable  {tally['reachable']:>4}  the class is exercised, this refusal is not asserted")
    print(f"  named      {tally['named']:>4}  the code is named, nothing touches the class")
    print(f"  none       {tally['none']:>4}  no test reaches the class or names the code")
    print("\nStill a floor: reaching a class is not the same as exercising the branch "
          "that raises the code.")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    """Record what an adapter ran for one behaviour code, and what happened.

    EOS does not run the test. Executing one needs a build tool, an
    environment and minutes, and core/ is stdlib-only by design; the workspace
    already has a wrapper that keeps the log and prints a digest. This records
    the evidence that wrapper produced.
    """
    from core import verification

    output = None
    if args.output == "-":
        output = sys.stdin.read()
    elif args.output:
        try:
            output = Path(args.output).read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            print(f"error: cannot read {args.output} ({exc})", file=sys.stderr)
            return 1
    # ADR-024: inside an open run, the check belongs to it.
    from core import executions, telemetry
    session = getattr(args, "session", None) or telemetry.detect_session(args.path)[0]
    current = executions.current(args.path, session)
    execution = current[0] if current else None
    try:
        entry = verification.record(
            args.path, args.code, args.outcome, args.ran, exit_code=args.exit_code,
            log=args.log, output=output, verdict=args.verdict, note=args.note,
            session=session, execution=execution)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if execution:
        try:
            executions.event(args.path, execution, kind="verified", tool="eos verify",
                             ref=f"{entry.code}:{entry.outcome}", exit_code=entry.exit_code,
                             session=session)
        except (ValueError, OSError):
            pass  # the verification is recorded; the timeline line is a convenience

    print(f"recorded {entry.outcome} for {entry.code} at {entry.recorded_at}")
    print(f"  {entry.command}")
    if entry.commit:
        print(f"  commit {entry.commit[:12]}")
    if entry.verdict is None and entry.outcome != verification.PASSED:
        # Said once, here, rather than inferred anywhere: an exit code does not
        # separate "the rule is not enforced" from "the test is wrong" from
        # "the environment was".
        print("  no verdict recorded. A failing run does not say which of a wrong rule, "
              f"a wrong test or a bad environment it was -- pass --verdict when you know "
              f"({', '.join(verification.VERDICTS)}).", file=sys.stderr)
    return 0


def cmd_cost(args: argparse.Namespace) -> int:
    """What EOS has cost this project, per command."""
    from core import telemetry

    if not telemetry.enabled(args.path):
        print("Telemetry is off. Turn it on with [telemetry] enabled = true in "
              f"{Path(args.path) / '.eos' / 'config.toml'} — it records the command, "
              "the flag names, the milliseconds and the size of the answer, never "
              "what was asked.")
        return 0

    report = telemetry.summary(args.path)
    if args.format == "json":
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0
    if not report["calls"]:
        print("Telemetry is on, and nothing has been recorded yet.")
        return 0

    print(f"{report['calls']} call(s) since {report['since']}, "
          f"~{report['tokens']} token(s) returned in total (estimated at 4 chars each)")
    print(f"{'command':<16}{'calls':>7}{'median ms':>11}{'median tok':>12}{'rebuilds':>10}{'failed':>8}")
    for row in report["commands"]:
        print(f"{row['command']:<16}{row['calls']:>7}{row['median_ms']:>11}"
              f"{row['median_tokens']:>12}{row['rebuilt']:>10}{row['failed']:>8}")

    # Calls are what this file holds; sessions are what the question was
    # always about. A tool called twelve times by one session and never by
    # eleven others is not a tool anyone adopted, and the per-command table
    # above cannot tell that apart from steady use.
    sessions = report["sessions"]
    if sessions["sessions"]:
        share = round(100 * sessions["beyond_opening"] / sessions["sessions"])
        print(f"\n{sessions['sessions']} session(s) identified themselves; "
              f"{sessions['beyond_opening']} of them ({share}%) called EOS for something "
              f"beyond the opening brief.")
        print(f"Calls per session: median {sessions['median_calls']}, "
              f"most {sessions['max_calls']}.")
    if sessions["closed_sessions"]:
        # The outcome number, not a usage one: it should fall as sessions
        # learn to close what they took, and a rise is worth acting on.
        print(f"{sessions['closed_sessions']} session(s) were asked what happened to "
              f"their claims on the way out; {sessions['left_work_open']} of them still "
              "held work at that point.")
    if sessions["failed_openings"]:
        # The number that makes the one above trustworthy. From a percentage
        # alone, a hook that cannot run EOS at all is indistinguishable from a
        # project where everything is working.
        print(f"{sessions['failed_openings']} session(s) could not produce the opening "
              "brief: the hook ran and EOS could not be reached. Check that `eos` is on "
              f"PATH, and that `eos update {args.path}` has refreshed this project's "
              "runtime copy.")
    if sessions["attributed_by"]:
        # Which variable did the attributing, because "the harness told us"
        # and "somebody passed a flag by hand" are different levels of trust
        # in the same number.
        named = ", ".join(f"{name} ({count})" for name, count
                          in sorted(sessions["attributed_by"].items()))
        print(f"Sessions identified by: {named}.")
    if sessions["unattributed_calls"]:
        print(f"{sessions['unattributed_calls']} call(s) carried no session id and are "
              "counted above but belong to no session. Claude Code is read from "
              "$CLAUDE_CODE_SESSION_ID automatically; another harness exports "
              "$EOS_SESSION, or names its own variable in [telemetry] session_env.")
    return 0


def cmd_draft_test(args: argparse.Namespace) -> int:
    """Draft a test for a refusal the suite does not assert."""
    from core import testgen

    try:
        index.refresh(args.path)
    except (index.IndexBuildError, OSError, ValueError, sqlite3.Error) as exc:
        print(f"warning: index may be stale ({type(exc).__name__}: {exc})", file=sys.stderr)
    try:
        draft = testgen.draft_for(args.path, args.code)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.format == "json":
        print(json.dumps(draft.to_dict(), indent=2, ensure_ascii=False))
        return 0 if draft.status == testgen.STATUS_DRAFT else 1

    if draft.status != testgen.STATUS_DRAFT:
        print(f"refused: {draft.refused}", file=sys.stderr)
        return 1

    where = "joins" if draft.test_exists else "would create"
    print(f"# draft for {draft.code}")
    print(f"# {where}: {draft.test_path}")
    print(f"# thrown by:  {draft.source_ref}")
    print(f"# style read from: {draft.style.get('source')}")
    for fact in draft.grounded:
        print(f"# grounded:  {fact}")
    print()
    print(draft.body)
    if args.write:
        target = testgen.write_draft(args.path, draft)
        print(f"\nWritten to {target}", file=sys.stderr)
    print("\n# Not compiled, not run, not reviewed. Make it fail for the right "
          "reason before making it pass.", file=sys.stderr)
    return 0


def cmd_ask(args: argparse.Namespace) -> int:
    """Run a question this project's index extensions provide."""
    try:
        available = inspector.questions(args.path)
    except Exception as exc:  # noqa: BLE001 - a misconfigured extension, reported in one line
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if not args.name:
        if not available:
            print("No extension provides a question here. `eos why` reports "
                  "which extensions ran at all.")
            return 0
        for name in sorted(available):
            entry = available[name]
            print(f"{name:<24} {entry['help'] or entry['sql'].split(chr(10))[0]}")
            print(f"{'':<24} from {entry['extension']}")
        return 0

    try:
        index.refresh(args.path)
    except (index.IndexBuildError, OSError, ValueError, sqlite3.Error) as exc:
        print(f"warning: index may be stale ({type(exc).__name__}: {exc})", file=sys.stderr)
    try:
        columns, rows = inspector.ask(args.path, args.name, args.argument)
    except (ValueError, sqlite3.Error) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.format == "json":
        print(json.dumps([dict(zip(columns, row)) for row in rows], indent=2, ensure_ascii=False))
        return 0
    print("\t".join(columns))
    for row in rows:
        print("\t".join("" if value is None else str(value) for value in row))
    return 0


def cmd_trace(args: argparse.Namespace) -> int:
    """What an entry point reaches, and what the call graph cannot see from it."""
    try:
        index.refresh(args.path)
    except (index.IndexBuildError, OSError, ValueError, sqlite3.Error) as exc:
        print(f"warning: index may be stale ({type(exc).__name__}: {exc})", file=sys.stderr)
    try:
        answer = inspector.trace(args.path, args.file, depth=args.depth)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.format == "json":
        print(json.dumps(answer, indent=2, ensure_ascii=False))
        return 0

    print(answer["file"])
    for route in answer["endpoints"]:
        print(f"  serves {route}")
    print(f"  reaches {len(answer['reaches'])} file(s) within {answer['depth']} hop(s)"
          + (" (truncated)" if answer["truncated"] else ""))
    for entry in answer["reaches"][:10]:
        print(f"    {entry['depth']}  {entry['path']}")
    if len(answer["reaches"]) > 10:
        print(f"    … and {len(answer['reaches']) - 10} more")

    if answer["codes"]:
        print(f"  can refuse with {len(answer['codes'])} behaviour code(s): "
              f"{', '.join(c['code'] for c in answer['codes'][:6])}")
    else:
        print("  no behaviour code is reachable by call edges from here")

    # Printed every time, not only when it is inconvenient. A trace that
    # reported only what it could follow would be a confident, incomplete
    # answer on any system whose components are looked up by name.
    wired = answer["runtime_wired"]
    if wired["uncalled"]:
        print(f"\n  {wired['uncalled']} of {wired['total']} named component(s) in this project "
              "have no caller at all: they are resolved at run time, so no call-graph "
              "answer -- including this one -- can follow the chain through them.")
        for example in wired["examples"][:3]:
            print(f"    {example['bean']}  {example['path']}")
    return 0


def cmd_brief(args: argparse.Namespace) -> int:
    """What a session needs before it starts, in one call it did not choose.

    Every other command answers a question somebody asked. This one answers
    the question nobody thinks to ask -- "is someone already on this, and was
    this already learned here" -- which is why it is wired to a hook rather
    than offered in a list.
    """
    from core import brief

    if getattr(args, "resume", False):
        from core import telemetry

        session = args.session or telemetry.detect_session(args.path)[0]
        if not session:
            print("error: --resume needs a session: --session <id> or the harness's variable", file=sys.stderr)
            return 1
        print(brief.resume(args.path, session), end="")
        return 0
    text = brief.build(args.path, session=args.session, agent=args.agent,
                       task=args.task, budget=args.budget, task_only=args.task_only)
    if text:
        print(text, end="" if text.endswith("\n") else "\n")
    return 0


def cmd_parent(args: argparse.Namespace) -> int:
    """Real parent source for a symbol, from the command line.

    This existed only as an MCP tool, and on an overlay codebase it is the
    tool most often needed: the class that decides the behaviour is in the
    parent, under a different name, and the project holds an override. Two
    eval sessions ran out of road here -- one could not check whether a fraud
    step enforces a limit, the other could not see what the dispatcher does
    with the flags the controller sets -- and both said so as the point at
    which the investigation stopped.
    """
    root = Path(args.path).resolve()
    if not links.read_links(root):
        print(f"No linked projects configured for {root.name}. "
              f"Link one with: eos init {root} --link-parent /path/to/parent-project",
              file=sys.stderr)
        return 1

    answer = inspector.get_parent_implementation(root, args.symbol, max_results=max(args.limit, 1))
    if args.format == "json":
        print(json.dumps(answer, indent=2, ensure_ascii=False))
        return 0

    matches = answer["matches"]
    if not matches:
        # Which of the two it is matters: a symbol that is not there and a
        # parent that was never indexed are different problems with different
        # fixes, and the same empty list.
        print(f"No parent symbol matches {args.symbol!r}. If the parent has never been "
              f"scanned here, run: eos scan {root} --with-parents")
        return 0

    for match in matches:
        print(f"{match['name']}  ({match['kind']})  {match['path']}:{match['line']}")
        if match["source"]:
            for line in match["source"].splitlines():
                print(f"    {line}")
        else:
            print("    (source not readable: the linked parent is missing on disk)")
        print()
    return 0


def cmd_parents(args: argparse.Namespace) -> int:
    root = Path(args.path).resolve()
    configured = links.read_links(root)
    if not configured:
        print(f"No linked projects configured for {root.name}.")
        print()
        print("  To link a parent project:")
        print(f"    eos init {root} --link-parent /path/to/parent-project")
        return 0

    for label, link in configured.items():
        resolved = links.resolve_link_path(root, link)
        exists = resolved.exists()
        print(f"[{label}] {link.role} ({'found' if exists else 'MISSING'})")
        print(f"  path: {resolved}")
        if link.ref:
            print(f"  product.version: {link.ref}")
        if exists:
            ref = _read_git_ref(resolved)
            if ref:
                print(f"  git: {ref}")
                if link.ref and link.ref not in ref:
                    print(f"  Note: ref may not match -- expected to see '{link.ref}' in '{ref}'")
    return 0
