"""What needs a person's attention in this project's memory (2.x roadmap L1).

A fold-only report: it reads the notes, the procedures, the run and work
ledgers, and prints what the other audits would each say -- including notes that
still cite a note another replaced -- bounded, with the
command that shows more. It never rewrites anything -- a proposal a person
accepts is how anything here changes (Ruflo's lesson: consolidation that edits
memory on its own becomes a daemon nobody trusts).
"""
from __future__ import annotations

import datetime
import itertools
from pathlib import Path

LIMIT = 5
NEAR_DUPLICATE = 0.6          # below the add guard's 0.8: pairs worth a look, not a refusal
OPEN_RUN_DAYS = 2


def touched_files(project_root: str | Path, commit_start: str | None,
                  commit_end: str | None) -> set[str] | None:
    """Repo-relative paths git says changed between two commits, or None when
    there is no real range to measure (E1, 2.x roadmap N9).

    None -- "unmeasurable", never an empty set standing in for "0 files" --
    when either commit is missing, when they are the same commit (a run that
    made no commit at all: there is no range to diff, and reporting 0 would
    read as a measurement rather than the absence of one), or when git cannot
    resolve the range at all (a rewritten or pruned history).
    """
    import shutil
    import subprocess

    if not commit_start or not commit_end or commit_start == commit_end:
        return None
    git = shutil.which("git")
    if not git:
        return None
    try:
        result = subprocess.run(
            [git, "-C", str(project_root), "diff", "--name-only", f"{commit_start}..{commit_end}"],
            capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


def _normalized_ref(ref: str, project: Path) -> str | None:
    """A `changed` event's ref as a repo-relative posix path, the shape git's
    own output takes -- so an absolute ref names the same file as a relative
    one. None when an absolute ref falls outside the project (nothing git's
    diff could have named)."""
    path = Path(ref)
    if path.is_absolute():
        try:
            path = path.relative_to(project)
        except ValueError:
            return None
    return path.as_posix()


def change_capture_for_run(project_root: str | Path, record) -> dict | None:
    """For one finished run: how many of the files its commit range touched
    were also named by a `changed` event -- the E1 acceptance measured (2.x
    roadmap N9). A pure read: nothing here writes anything.

    None ("unmeasurable") for an open run (no outcome to measure yet) or when
    `touched_files` cannot place a real range for its commits.
    """
    if record.outcome is None:
        return None
    touched = touched_files(project_root, record.commit_start, record.commit_end)
    if touched is None:
        return None
    project = Path(project_root).expanduser().resolve()
    captured_refs = {
        norm for e in record.events if e.kind == "changed" and e.ref
        for norm in [_normalized_ref(e.ref, project)] if norm is not None
    }
    captured = captured_refs & touched
    missed = touched - captured_refs
    return {"captured": len(captured), "touched": len(touched), "missed": sorted(missed)}


def change_capture(project_root: str | Path, records: list | None = None) -> dict:
    """Aggregated E1 capture over every finished run with a commit range (N9)."""
    from core import executions

    root = Path(project_root).expanduser().resolve()
    captured = touched = runs_with_commits = runs_unmeasurable = 0
    for record in (records if records is not None else executions.load(root)):
        if record.outcome is None:
            continue
        result = change_capture_for_run(root, record)
        if result is None:
            runs_unmeasurable += 1
            continue
        runs_with_commits += 1
        captured += result["captured"]
        touched += result["touched"]
    share = (captured / touched) if touched else None
    return {"captured": captured, "touched": touched, "share": share,
            "runs_with_commits": runs_with_commits, "runs_unmeasurable": runs_unmeasurable}


def report(project_root: str | Path) -> dict:
    from core import executions, notes, procedure_lint, work

    root = Path(project_root).expanduser().resolve()
    corpus = notes.load_notes(root)
    now = datetime.datetime.now(datetime.timezone.utc)

    procedures = executions.audit_procedures(root)
    failing = [p for p in procedures if any(x.startswith("the latest run failed") for x in p["problems"])]
    unrun = [p for p in procedures if p["runs_ok"] == 0 and p["runs_failed"] == 0]
    lint = [r for r in procedure_lint.lint(root) if r["problems"]]

    grams = [(note, notes._trigrams(note.body)) for note in corpus if not notes.is_bulk_index(note)]
    pairs = []
    for (a, ga), (b, gb) in itertools.combinations(grams, 2):
        if len(ga) >= notes.PARAPHRASE_MIN_TRIGRAMS and len(gb) >= notes.PARAPHRASE_MIN_TRIGRAMS:
            share = len(ga & gb) / len(ga | gb)
            if share >= NEAR_DUPLICATE:
                pairs.append((round(share, 2), a.title, b.title))
    pairs.sort(reverse=True)

    from core import note_graph

    replaced = notes.superseded(corpus)
    titles = {note.path.name: note.title for note in corpus}
    cites_replaced = sorted({(titles[e.src], titles[e.dst]) for e in note_graph.edges(corpus, None)
                             if e.kind == "cites" and e.dst in replaced and e.src not in replaced})

    expired = [f"{note.title} ({note.valid_until})" for note in corpus if notes.expired(note)]
    stale = notes.stale_notes(root)
    records = executions.load(root)
    silent = _silent_steps(root, records)
    old_runs = []
    for record in records:
        if record.open and record.started_at:
            try:
                started = datetime.datetime.fromisoformat(record.started_at)
            except ValueError:
                continue
            if (now - started).days >= OPEN_RUN_DAYS:
                old_runs.append((record.id, record.title, (now - started).days))
    stale_work = [item for item in work.items(root) if item.is_stale(now)]
    lessons_without_evidence = [n.title for n in corpus
                                if n.kind == "lesson" and not n.execution and not n.evidence]
    done = [r for r in records if r.outcome == "ok"]
    verified = sum(1 for r in done if r.outcome_source == "verified")
    claimed = sum(1 for r in done if r.outcome_source == "claimed")
    capture = change_capture(root, records=records)
    return {
        "notes": len(corpus),
        "procedures": {"total": len(procedures), "failing": [p["procedure"] for p in failing],
                       "never_run": [p["procedure"] for p in unrun],
                       "lint": {r["procedure"]: r["problems"] for r in lint},
                       "silent_steps": silent},
        "near_duplicates": pairs,
        "cites_replaced": cites_replaced,
        "stale_notes": [entry.get("note") for entry in stale],
        "expired_notes": expired,
        "open_runs_older": old_runs,
        "stale_work": [(item.id, item.title) for item in stale_work],
        "verified_rate": {"verified": verified, "claimed": claimed},
        "change_capture": capture,
        "lessons_without_evidence": lessons_without_evidence,
    }


def _silent_steps(root: Path, records) -> dict:
    """For each procedure that has run, the steps whose tool no run of it ever
    recorded: their outcome cannot be placed (ADR-031), however often it runs."""
    from core import executions, notes, steps

    silent = {}
    # Same reason as audit_procedures: consolidate reports problems, and an
    # expired procedure's silent steps are still one (N8).
    for note in notes.procedures(root, include_expired=True):
        runs = [r for r in records if r.procedure == note.procedure and r.outcome]
        if not runs:
            continue
        seen = {executions.normalize_tool(e.tool) for r in runs for e in r.events if e.tool}
        missing = [f"step {s.number} (tool: {s.tool})" for s in steps.parse(note.body)
                   if s.tool and executions.normalize_tool(s.tool) not in seen]
        if missing:
            silent[note.procedure] = missing
    return silent


def render(data: dict) -> str:
    def block(title, items, more):
        if not items:
            return []
        shown = [f"  {line}" for line in items[:LIMIT]]
        tail = [f"  … {len(items) - LIMIT} more: {more}"] if len(items) > LIMIT else [f"  ({more})"]
        return ["", f"{title} ({len(items)})"] + shown + tail

    lines = [f"EOS consolidate -- {data['notes']} notes, {data['procedures']['total']} procedures. "
             "Nothing here was changed."]
    lines += block("PROCEDURES WHOSE LATEST RUN FAILED", data["procedures"]["failing"], "eos procedure audit .")
    lines += block("PROCEDURES NEVER RUN", data["procedures"]["never_run"], "eos procedure audit .")
    lines += block("PROCEDURE LINT", [f"{slug}: {len(problems)} problem(s)"
                                      for slug, problems in data["procedures"]["lint"].items()],
                   "eos procedure lint .")
    lines += block("STEPS NO RUN RECORDED", [f"{slug}: {', '.join(found)}"
                                             for slug, found in data["procedures"].get("silent_steps", {}).items()],
                   "their tool records no event, so no run says how they went")
    lines += block("NOTES THAT READ ALIKE", [f"{share:.0%}  {a}  ~  {b}" for share, a, b in data["near_duplicates"]],
                   "keep one, or say what differs")
    lines += block("NOTES CITING A REPLACED NOTE", [f"{a}  ->  {b}" for a, b in data.get("cites_replaced", [])],
                   "point them at the replacement: eos note show . \"<replaced title>\"")
    lines += block("NOTES WHOSE FILES CHANGED", data["stale_notes"], "eos note audit .")
    lines += block("EXPIRED NOTES", data.get("expired_notes", []),
                   "past their valid_until: search no longer offers them; replace or let them go")
    lines += block("RUNS OPEN FOR DAYS", [f"{rid}  {title}  ({days}d)" for rid, title, days in data["open_runs_older"]],
                   "eos run finish . <id> --outcome ok|failed|abandoned")
    lines += block("STALE WORK", [f"{wid}  {title}" for wid, title in data["stale_work"]], "eos work list .")
    lines += block("LESSONS WITHOUT EVIDENCE", data.get("lessons_without_evidence", []),
                   "no execution and no evidence recorded; eos note show . \"<title>\" to read one")
    rate = data["verified_rate"]
    if rate["verified"] + rate["claimed"]:
        share = rate["verified"] / (rate["verified"] + rate["claimed"])
        lines += ["", f"VERIFIED RATE  {rate['verified']} verified, {rate['claimed']} claimed ({share:.0%})"]
    capture = data.get("change_capture") or {}
    if capture.get("runs_with_commits", 0) + capture.get("runs_unmeasurable", 0):
        from core.lib import honest

        lines += ["", f"CHANGE CAPTURE  {capture['captured']}/{capture['touched']} files "
                      f"({honest.show(capture['share'], spec='.0%')}) over {capture['runs_with_commits']} "
                      f"runs with commits; {capture['runs_unmeasurable']} runs unmeasurable"]
    if len(lines) == 1:
        lines.append("Nothing needs attention.")
    return "\n".join(lines)
