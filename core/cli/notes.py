"""Note commands: add, show, search, amend, skip, eval, audit, findings.

Moved out of core/eos.py (2.x roadmap F4), unchanged.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from core import notes
from core.cli.common import _PLACEHOLDER_WARNING, _no_notes_here, _split_csv


def cmd_findings(args: argparse.Namespace) -> int:
    """Recorded runs, newest first, and what they were judged to be."""
    from core import verification

    records = verification.load(args.path)
    if args.failed_only:
        records = [entry for entry in records if entry.outcome != verification.PASSED]
    if args.format == "json":
        print(json.dumps([entry.to_dict() for entry in records], indent=2, ensure_ascii=False))
        return 0
    if not records:
        print("No run has been recorded here. `eos verify` records one; "
              "`eos draft-test` drafts a skeleton to start the test from.")
        return 0
    for entry in reversed(records):
        verdict = entry.verdict or ("-" if entry.outcome == verification.PASSED else "no verdict yet")
        print(f"{entry.recorded_at}  {entry.outcome:<8} {entry.code:<40} {verdict}")
        print(f"    {entry.command}")
        if entry.log:
            print(f"    log {entry.log}")
        if entry.note:
            print(f"    {entry.note}")
    summary = verification.summary(args.path)
    print(f"\n{summary['runs']} run(s) over {summary['codes']} code(s): "
          f"{summary['outcomes']}" + (f", verdicts {summary['verdicts']}" if summary["verdicts"] else ""))
    return 0


def cmd_note_add(args: argparse.Namespace) -> int:
    # `--scope ,` (likewise `""`, `" "`, `",,"`) splits to nothing, and
    # passing that through as "no scope" wrote a note with neither `scope:`
    # nor `scope_hashes:` -- un-monitorable for the rest of its life, on
    # exit 0, from a typo, on the command that wrote every note in the
    # corpus. The same silent data loss `amend` refuses. Checked here rather
    # than in `add_note` because only the CLI can tell "the caller typed
    # --scope and it named nothing" from "there is no scope", which is the
    # normal case for most notes and stays legal: `scope=[]` reaching
    # `add_note` from a generator means the latter.
    scope = _split_csv(args.scope)
    if args.scope is not None and not scope:
        print(
            "error: --scope was given but names nothing; drop the flag if this "
            "note is not about specific files, rather than writing a note that "
            "can never be checked against the code.",
            file=sys.stderr,
        )
        return 1
    try:
        path = notes.add_note(
            args.path,
            kind=args.kind,
            title=args.title,
            body=args.body,
            tags=_split_csv(args.tags),
            scope=scope,
            source=args.source,
            cause=args.cause,
            solution=args.solution,
            metric=args.metric,
            session=args.session,
            procedure=args.procedure,
            execution=args.execution,
        )
    except (ValueError, FileExistsError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"Note written to {path}")
    return 0


def cmd_note_list(args: argparse.Namespace) -> int:
    recorded = notes.load_notes(args.path)
    matches = [note for note in recorded if args.tag in note.tags] if args.tag else recorded
    for note in matches:
        print(f"{note.path.name}\t{note.kind}\t{note.title}")
    if matches:
        return 0
    if not recorded:
        print(_no_notes_here(args.path))
        return 0
    tags = sorted({tag for note in recorded for tag in note.tags})
    print(f"{len(recorded)} note(s) recorded, none tagged {args.tag!r}. "
          + (f"Tags in use: {', '.join(tags)}." if tags else "No note carries a tag."))
    return 0


def cmd_note_show(args: argparse.Namespace) -> int:
    """Print one note in full, by file name or by part of its title.

    `note search` and `note list` print titles, and until now nothing printed
    a body: the only way to read what an earlier session wrote was to phrase a
    `compose` task narrow enough to rank that note into the top few. Two
    surfaces sent readers the wrong way about it -- compose's own truncation
    footer told them to use `note search`, which returns titles -- and an eval
    session tested that instruction against a near-verbatim title, got 29
    loose matches and no body, and concluded the tool was contradicting
    itself. It was.
    """
    recorded = notes.load_notes(args.path)
    if not recorded:
        print(_no_notes_here(args.path))
        return 0
    needle = args.name.casefold()
    exact = [note for note in recorded if note.path.name.casefold() == needle]
    matches = exact or [note for note in recorded
                        if needle in note.path.name.casefold() or needle in note.title.casefold()]
    if not matches:
        print(f"No note matches {args.name!r} among {len(recorded)} recorded here. "
              f"`eos note search {args.path} \"{args.name}\"` searches their contents.",
              file=sys.stderr)
        return 1
    if len(matches) > 1:
        # Printing the first would be a coin toss presented as an answer.
        print(f"{len(matches)} notes match {args.name!r}; name one of them:", file=sys.stderr)
        for note in matches[:10]:
            print(f"  {note.path.name}\t{note.title}", file=sys.stderr)
        return 1
    print(matches[0].path.read_text(encoding="utf-8"))
    return 0


def cmd_note_search(args: argparse.Namespace) -> int:
    matches = notes.search_notes(args.path, args.query, limit=args.limit)
    for note in matches:
        print(f"{note.path.name}\t{note.kind}\t{note.title}")
    if matches:
        return 0
    recorded = notes.load_notes(args.path)
    if not recorded:
        print(_no_notes_here(args.path))
        return 0
    # It searched and found none, which is a finding -- and a different one
    # from having had nothing to search. A session that cannot tell those
    # apart re-derives from source what an earlier session already paid for.
    print(f"Searched {len(recorded)} note(s); none match {args.query!r}. "
          f"`eos note list {args.path}` lists every one of them.")
    return 0


def cmd_note_eval(args: argparse.Namespace) -> int:
    """Score note search against questions somebody wrote the answers for.

    Exits non-zero only when the golden file itself is broken -- unreadable, or
    naming a note that no longer exists. A low score is a measurement and must
    not fail a run, or the number stops being reported honestly.
    """
    from core import retrieval

    try:
        entries = retrieval.parse_golden(args.golden)
    except (retrieval.GoldenError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    report = retrieval.evaluate(args.path, entries, depth=args.depth)
    if args.format == "json":
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(retrieval.render(report))
    return 1 if report["broken"] else 0


def cmd_note_skip(args: argparse.Namespace) -> int:
    # `record_skip` had no refusal path until the placeholder guard, so this
    # handler was not needed and was not written -- and its absence turned an
    # instructive one-sentence refusal into a six-frame traceback with the
    # sentence buried at the bottom. Same shape as cmd_note_add's and
    # cmd_note_amend's.
    try:
        path = notes.record_skip(args.path, reason=args.reason, session=args.session)
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"Recorded: no note for this session ({args.reason}) -> {path}")
    return 0


def cmd_note_amend(args: argparse.Namespace) -> int:
    body = args.body
    if body == "-":
        body = sys.stdin.read()
    # _split_csv("") returns None, the same value it returns for --scope
    # never having been passed at all -- so a caller who typed `--scope ""`
    # (or `--scope ,`, which splits to []) would otherwise be told "amend
    # needs at least one of --body, --reaffirm, or --scope", a message that
    # contradicts what they actually typed. Normalize any explicitly-passed
    # but empty value to `[]` so amend_note's own refusal (which names
    # --scope specifically) is the one the caller sees.
    scope = None
    if args.scope is not None:
        scope = _split_csv(args.scope) or []
    try:
        path = notes.amend_note(
            args.note,
            args.path,
            body=body,
            reaffirm=args.reaffirm,
            scope=scope,
            session=args.session,
        )
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"Note amended: {path}")
    if body:
        # Reported, not refused (2.x roadmap M5): amend is the way out of other guards.
        twin = notes.paraphrase_of(notes.load_notes(args.path), body, exclude=Path(path))
        if twin is not None:
            print(f"warning: {twin.path} ({twin.title!r}) says this in other words; "
                  "consider keeping one of the two", file=sys.stderr)
    return 0


def cmd_note_audit(args: argparse.Namespace) -> int:
    """Report notes whose scoped files have moved. Always exit 0: this is a
    report, not a gate -- the Stop hook is the gate, and it blocks only on
    the hand-written half."""
    handwritten, generated = [], []
    for item in notes.stale_notes(args.path):
        (generated if item["generated"] else handwritten).append(item)

    # Resolved the same way the Stop hook resolves it, through notes_dir() --
    # never rebuilt as `<root>/.eos/knowledge/<name>`. Every FM service
    # configures [knowledge] dir outside the service itself, so a hand-built
    # path would print an `eos note amend` command naming a file that does
    # not exist, on all 18 of them.
    directory = notes.notes_dir(args.path)
    root = Path(args.path).expanduser().resolve()

    def _note_path(item):
        return directory / item["note"]

    def _title(item):
        try:
            return notes.parse_note(_note_path(item)).title
        except Exception:
            return item["note"]

    def _kind(item):
        try:
            return notes.parse_note(_note_path(item)).kind
        except Exception:
            return ""

    if handwritten:
        print("Hand-written notes that may now be wrong:")
        for item in handwritten:
            scopes = ", ".join(issue["scope"] for issue in item["issues"])
            print(f"  {_title(item)!r} ({_note_path(item)})  [{scopes}]")
        print(f"  {_PLACEHOLDER_WARNING}")
        for item in handwritten:
            note_path = _note_path(item)
            # Classified by the whole note, in the same three shapes the Stop
            # hook's `_amend_flags` uses and for the same reason: `amend_note`
            # validates EVERY scope entry, so one entry's refusal governs the
            # command whatever the others report. Two surfaces printing
            # different remedies for one state is what this replaced -- the
            # split here was two-way, so the mixed note was handed `--scope`
            # alone, which exits 1.
            gone = [i["scope"] for i in item["issues"] if i.get("reason") == "removed"]
            changed = [i["scope"] for i in item["issues"] if i.get("reason") != "removed"]
            if not gone:
                flags = "--body '<what is true now>'"
            elif not changed:
                flags = "--scope '<the new project-relative path>'"
            else:
                flags = ("--scope '<the project-relative paths this note is still "
                         "about>' --body '<what is true now>'")
            if gone:
                print(f"  {', '.join(gone)} no longer exists, so --body and "
                      "--reaffirm can only refuse (they re-hash that path).")
                print(
                    f"  {', '.join(changed)} did change, so a scope-only amend is "
                    "refused too -- one command does both:"
                    if changed else
                    "  Re-point the note at where the code moved:"
                )
            print(f"  eos note amend {note_path} {root} {flags}")
            if "--body" in flags and _kind(item) == "defect":
                print("    (a defect note's new body must keep its Root cause, "
                      "Solution and Metric sections)")
            if gone:
                if not changed:
                    print("  ...and add --body '<what is true now>' to that same "
                          "command if the move changed what the note claims.")
                continue
            print(
                f"  ...or, if the change does not affect what the note claims: "
                f"eos note amend {note_path} {root} --reaffirm '<why it still holds>'"
            )
    if generated:
        print(f"Generated notes gone stale: {len(generated)}")
        for item in generated:
            print(f"  {_title(item)!r} ({_note_path(item)})  (source: {item['source']})")
        print("  Fix: regenerate them; do not edit these by hand.")
    if not handwritten and not generated:
        # How many were examined, not just that none failed. "No stale notes."
        # reads the same whether it checked forty notes or found the knowledge
        # directory empty, and those call for opposite next actions.
        checked = len(notes.load_notes(args.path))
        print(f"Checked {checked} note(s); every scoped path still matches what "
              "the note recorded." if checked else _no_notes_here(args.path))
    return 0


def cmd_note(args: argparse.Namespace) -> int:
    return {
        "add": cmd_note_add,
        "list": cmd_note_list,
        "search": cmd_note_search,
        "show": cmd_note_show,
        "eval": cmd_note_eval,
        "skip": cmd_note_skip,
        "amend": cmd_note_amend,
        "audit": cmd_note_audit,
    }[args.note_command](args)
