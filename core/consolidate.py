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
import re
from pathlib import Path

LIMIT = 5
# `git subtree pull --squash` names itself this way: the squash commit's own
# tree holds the subtree's bare content, never the `<prefix>/` it lands at in
# the branch it merges into -- `git log --name-only` shows that commit's own
# unprefixed paths, so the prefix has to come back out of its own message to
# match anything `git diff` (a plain two-tree compare) ever names (N11).
_SQUASH = re.compile(r"^Squashed '([^']+)' changes from")
NEAR_DUPLICATE = 0.6          # below the add guard's 0.8: pairs worth a look, not a refusal
OPEN_RUN_DAYS = 2
# Proposals a person took, one line each (Phase 6: "acted on at least once per
# month" is the count of the last ACCEPTED_DAYS, read from here).
ACCEPTED = "consolidate-accepted.jsonl"
ACCEPTED_DAYS = 30


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
        # --no-renames: without it, a renamed-with-no-content-change file is
        # collapsed to its new path alone whenever the caller's (or repo's)
        # `diff.renames` is on, silently dropping the old path from `touched`
        # depending on a config this function never controls. Explicit here
        # keeps the count the same on every machine.
        result = subprocess.run(
            [git, "-C", str(project_root), "diff", "--no-renames", "--name-only",
             f"{commit_start}..{commit_end}"],
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


def _classify_commits(commits: list[str], commit_end: str, other_finish_commits: frozenset,
                      subjects: dict[str, str], files: dict[str, set[str]]) -> set[str]:
    """The own-vs-foreign-vs-squash rule `_foreign_files` and its batched
    counterpart (`_batched_foreign`) both classify every commit in a run's
    range by: `subjects`/`files` are per-commit lookups (a subtree-squash
    commit's own message and its own, unprefixed file list)."""
    own_files: set[str] = set()
    foreign_files: set[str] = set()
    for commit_hash in commits:
        subject = subjects.get(commit_hash, "")
        commit_files = files.get(commit_hash, set())
        squash = _SQUASH.match(subject)
        if squash:
            prefix = squash.group(1).strip("/")
            commit_files = {f"{prefix}/{f}" for f in commit_files}
            is_foreign = True
        else:
            is_foreign = commit_hash != commit_end and commit_hash in other_finish_commits
        (foreign_files if is_foreign else own_files).update(commit_files)
    return foreign_files - own_files


def _foreign_files(project_root: str | Path, commit_start: str | None, commit_end: str | None,
                   other_finish_commits: frozenset) -> set[str]:
    """Files inside a run's own commit range that no commit of the run's own
    made -- another run's own finish commit landing in the same range (two
    sessions committing to one branch), or a subtree-pull squash -- named
    "the dominant measurement noise" by N11's classification of the host's
    18/454 gap. Only ever removes a file every commit naming it is foreign;
    a file a run's own commit also touches is always kept, so this can only
    shrink `touched`, never make a run's own work disappear from it.

    `other_finish_commits` must already exclude a run that made no commit of
    its own (RVd: `commit_end` is only ever `git_head()` at finish time --
    idle, it names whatever HEAD already was, which can be *this* run's own
    still-open, not-yet-finished intermediate commit; the caller filters those
    out before this ever sees them, the same `commit_start == commit_end`
    signal `touched_files` already uses for "no commit made").
    """
    import shutil
    import subprocess

    if not commit_start or not commit_end or commit_start == commit_end:
        return set()
    git = shutil.which("git")
    if not git:
        return set()
    try:
        result = subprocess.run(
            [git, "-C", str(project_root), "log", "--no-renames", "--name-only",
             "--format=%x02%H%x1f%s", f"{commit_start}..{commit_end}"],
            capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return set()
    if result.returncode != 0:
        return set()
    commits: list[str] = []
    subjects: dict[str, str] = {}
    files: dict[str, set[str]] = {}
    for block in result.stdout.split("\x02"):
        if not block.strip():
            continue
        header, _, rest = block.partition("\n")
        commit_hash, _, subject = header.partition("\x1f")
        commits.append(commit_hash)
        subjects[commit_hash] = subject
        files[commit_hash] = {line.strip() for line in rest.splitlines() if line.strip()}
    return _classify_commits(commits, commit_end, other_finish_commits, subjects, files)


# --- batched: the same two computations above, over every finished run in one pass ----
# (2.x roadmap Day 3, item D2). RVc measured one `git diff` (touched_files) plus one
# `git log` (_foreign_files) subprocess per finished run at 3.46s on a synthetic
# 300-run ledger -- process spawn, not git's own work, dominates that. Batched here
# into a small, constant number of `git` invocations regardless of how many runs
# `change_capture` folds, with identical results (tests/test_change_capture.py compares
# the batched aggregate against the same per-run computation as ground truth).


def _diff_tree_stdin(project_root: str | Path, pairs: list[tuple[str, str]]) -> list[set[str] | None]:
    """One `git diff-tree --stdin` process for many two-tree comparisons --
    the exact tree compare `git diff --no-renames --name-only A..B` makes
    (`git diff-tree` given two tree-ish arguments is the same plain two-tree
    diff, not a per-commit walk; `--stdin` lets many pairs share one process).

    Every `pairs[i][0]` (the start) must be unique across the list --
    `--stdin` prints nothing at all for a pair whose diff is empty, so two
    pairs sharing a start could not otherwise be told apart from one of them
    being empty (confirmed empirically before choosing this shape); the only
    caller (`_batched_touched`) only ever sends pairs it has already checked
    are unique and falls back to `touched_files` per pair for the rest.
    `None` per pair on any git failure (no git, a timeout, a non-zero exit)."""
    import shutil
    import subprocess

    if not pairs:
        return []
    git = shutil.which("git")
    if not git:
        return [None] * len(pairs)
    stdin_text = "".join(f"{start} {end}\n" for start, end in pairs)
    try:
        result = subprocess.run(
            [git, "-C", str(project_root), "diff-tree", "--stdin", "--no-renames", "--name-only", "-r"],
            input=stdin_text, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return [None] * len(pairs)
    if result.returncode != 0:
        return [None] * len(pairs)
    lines = result.stdout.splitlines()
    starts = {start for start, _ in pairs}
    output: list[set[str]] = [set() for _ in pairs]
    li = 0
    for i, (start, _end) in enumerate(pairs):
        if li < len(lines) and lines[li] == start:
            li += 1
            found: set[str] = set()
            while li < len(lines) and lines[li] not in starts:
                found.add(lines[li])
                li += 1
            output[i] = found
        # else: git printed nothing for this pair at all -- an empty diff,
        # `output[i]` stays the empty set already there.
    return output


def _resolvable(project_root: str | Path, refs: set[str]) -> set[str]:
    """Which of `refs` name a real git object, via one `git cat-file
    --batch-check` call instead of a `git rev-parse`/`git diff` per ref.
    Empty (nothing proven resolvable) on any git failure -- a caller that
    cannot prove a ref is good must treat it as bad, the same direction
    `touched_files` already fails in for an unreachable ref."""
    import shutil
    import subprocess

    if not refs:
        return set()
    git = shutil.which("git")
    if not git:
        return set()
    ordered = sorted(refs)
    try:
        result = subprocess.run(
            [git, "-C", str(project_root), "cat-file", "--batch-check=%(objectname) %(objecttype)"],
            input="".join(f"{ref}\n" for ref in ordered), capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return set()
    if result.returncode != 0:
        return set()
    lines = result.stdout.splitlines()
    if len(lines) != len(ordered):
        return set()
    return {ref for ref, line in zip(ordered, lines) if not line.endswith(" missing")}


def _batched_touched(project_root: str | Path, ranges: list[tuple[str | None, str | None]]
                     ) -> list[set[str] | None]:
    """`touched_files` for every `(commit_start, commit_end)` pair in
    `ranges`, in as few `git` processes as possible. Same result, pair for
    pair, as calling `touched_files` once per entry.

    `git diff-tree --stdin` (the batched primitive, `_diff_tree_stdin`)
    prints nothing at all both for a pair whose diff is genuinely empty and
    for a pair naming an unresolvable ref (a rewritten or pruned commit) --
    unlike plain `git diff A..B`, which fails loudly (exit 128) for the
    latter, exactly the distinction `touched_files` turns into `None`
    ("unmeasurable") rather than an empty set. `_resolvable` checks every
    ref first so that distinction survives batching: an unresolvable pair
    is `None` here too, never silently folded into "0 files touched"."""
    results: list[set[str] | None] = [None] * len(ranges)
    candidates = [(i, s, e) for i, (s, e) in enumerate(ranges) if s and e and s != e]
    if not candidates:
        return results
    good_refs = _resolvable(project_root, {ref for _, s, e in candidates for ref in (s, e)})
    resolvable = [(i, s, e) for i, s, e in candidates if s in good_refs and e in good_refs]
    unresolvable = [i for i, s, e in candidates if not (s in good_refs and e in good_refs)]
    for i in unresolvable:
        results[i] = None
    from collections import Counter

    start_counts = Counter(s for _, s, _ in resolvable)
    batchable = [(i, s, e) for i, s, e in resolvable if start_counts[s] == 1]
    singles = [(i, s, e) for i, s, e in resolvable if start_counts[s] > 1]
    if batchable:
        diffs = _diff_tree_stdin(project_root, [(s, e) for _, s, e in batchable])
        for (i, _s, _e), files in zip(batchable, diffs):
            results[i] = files
    for i, s, e in singles:
        results[i] = touched_files(project_root, s, e)
    return results


def _commit_graph(project_root: str | Path, ends: list[str]) -> dict | None:
    """Parents, subject and own changed files (vs. first parent -- the same
    default `git log --name-only` already uses, so a merge commit's files
    stay empty without `-m`, exactly matching `_foreign_files`'s existing
    per-range behaviour) for every commit reachable from any of `ends` -- one
    walk, deduplicated, in place of one `git log` per run's own range.

    Safe to combine into a single positive-only query: combining several
    `A..B` *ranges* in one `git log` call folds every range's exclusions into
    one shared set and can silently drop a commit two ranges should each have
    kept on their own (confirmed empirically before choosing this shape); a
    plain union of positive refs with no exclusions at all has no such
    ambiguity -- it is just git's ordinary multi-ref log, deduplicated.
    `None` on any git failure -- the caller falls back to the per-range path."""
    import shutil
    import subprocess

    if not ends:
        return {"parents": {}, "subjects": {}, "files": {}}
    git = shutil.which("git")
    if not git:
        return None
    try:
        result = subprocess.run(
            [git, "-C", str(project_root), "log", "--no-renames", "--name-only",
             "--format=%x02%H%x1f%P%x1f%s", *sorted(set(ends))],
            capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    parents: dict[str, list[str]] = {}
    subjects: dict[str, str] = {}
    files: dict[str, set[str]] = {}
    for block in result.stdout.split("\x02"):
        if not block.strip():
            continue
        header, _, rest = block.partition("\n")
        commit_hash, _, tail = header.partition("\x1f")
        parent_field, _, subject = tail.partition("\x1f")
        parents[commit_hash] = parent_field.split() if parent_field else []
        subjects[commit_hash] = subject
        files[commit_hash] = {line.strip() for line in rest.splitlines() if line.strip()}
    return {"parents": parents, "subjects": subjects, "files": files}


def _range_commits(end: str, start: str | None, parents: dict[str, list[str]]) -> list[str]:
    """Commits reachable from `end`, excluding every commit reachable from
    `start` -- git's own definition of `start..end` (ancestors(end) minus
    ancestors(start)), computed over an already-fetched parent graph instead
    of a fresh `git log` per range."""
    excluded: set[str] = set()
    if start:
        stack = [start]
        while stack:
            commit_hash = stack.pop()
            if commit_hash in excluded:
                continue
            excluded.add(commit_hash)
            stack.extend(parents.get(commit_hash, ()))
    seen: set[str] = set()
    ordered: list[str] = []
    stack = [end]
    while stack:
        commit_hash = stack.pop()
        if commit_hash in seen or commit_hash in excluded:
            continue
        seen.add(commit_hash)
        ordered.append(commit_hash)
        stack.extend(parents.get(commit_hash, ()))
    return ordered


def _batched_foreign(project_root: str | Path, ranges: list[tuple[str | None, str | None]],
                     other_finish_commits: frozenset) -> list[set[str]]:
    """`_foreign_files` for every `(commit_start, commit_end)` pair in
    `ranges`: one `git log` (the parent graph, `_commit_graph`) plus a Python
    graph walk per pair, in place of one `git log` subprocess per pair."""
    results: list[set[str]] = [set() for _ in ranges]
    if not other_finish_commits:
        return results
    candidates = [(i, s, e) for i, (s, e) in enumerate(ranges) if s and e and s != e]
    if not candidates:
        return results
    graph = _commit_graph(project_root, [e for _, _, e in candidates])
    if graph is None:
        for i, s, e in candidates:
            results[i] = _foreign_files(project_root, s, e, other_finish_commits)
        return results
    for i, start, end in candidates:
        commits = _range_commits(end, start, graph["parents"])
        results[i] = _classify_commits(commits, end, other_finish_commits, graph["subjects"], graph["files"])
    return results


def change_capture_for_run(project_root: str | Path, record,
                           other_finish_commits: frozenset = frozenset()) -> dict | None:
    """For one finished run: how many of the files its commit range touched
    were also named by a `changed` event -- the E1 acceptance measured (2.x
    roadmap N9). A pure read: nothing here writes anything.

    None ("unmeasurable") for an open run (no outcome to measure yet) or when
    `touched_files` cannot place a real range for its commits.

    `other_finish_commits` -- every other run's own `commit_end`, so a commit
    that lands in this run's range only because another session committed to
    the same branch in between is not counted as this run's to have missed
    (N11); left empty (the default), nothing is excluded, exactly N9's number.
    A caller passing this in is expected to have already dropped a run whose
    own `commit_start == commit_end` (RVd) -- `change_capture` does.
    """
    if record.outcome is None:
        return None
    touched = touched_files(project_root, record.commit_start, record.commit_end)
    if touched is None:
        return None
    if other_finish_commits:
        touched = touched - _foreign_files(project_root, record.commit_start, record.commit_end,
                                           other_finish_commits)
    project = Path(project_root).expanduser().resolve()
    captured_refs = {
        norm for e in record.events if e.kind == "changed" and e.ref
        for norm in [_normalized_ref(e.ref, project)] if norm is not None
    }
    captured = captured_refs & touched
    missed = touched - captured_refs
    return {"captured": len(captured), "touched": len(touched), "missed": sorted(missed)}


# Every `_record` call site in core/hooks.py opens the run through
# `cfg["capture"]` (`_post_tool`, `_post_tool_failure`, `_subagent_start`,
# `_subagent_stop`, `_stop_failure`, `_record_passes`) except one:
# `_route_subagent`'s "decided" event always calls `_open_run` on its own,
# so a subagent routing decision is recorded whether or not `capture` is on
# (ADR-025 wants every decision kept for the roadmap's N5 advised-vs-used
# report even in a project that has turned event capture off). A "decided"
# event therefore proves only that the routing hook fired, never that the
# capture-relevant hook (Edit/Write/Bash PostToolUse) ever had a chance to
# record anything -- it must not count as evidence of real hook activity here.
_NON_CAPTURE_HOOK_KINDS = frozenset({"decided"})


def _has_hook_event(record) -> bool:
    """Whether this run's own ledger carries at least one hook-recorded event
    that a capture-gated hook path could have written.

    `core/hooks.py`'s `_record` is the one place any hook event is written,
    and it always passes `source="hook"` (CLI and wrapper calls record
    `source="cli"`/`"wrapper"` or leave it unset) -- so the field reliably
    tells a run the harness's own hook fired for at all apart from a run it
    never fired for once (N12), the same distinction the ledger already
    carries rather than a proxy over `changed`/tool events. `_NON_CAPTURE_HOOK_KINDS`
    excludes the one kind that fires independently of `capture` (RVe)."""
    return any(e.source == "hook" and e.kind not in _NON_CAPTURE_HOOK_KINDS for e in record.events)


def change_capture(project_root: str | Path, records: list | None = None) -> dict:
    """Aggregated E1 capture over every finished run with a commit range (N9),
    excluding another run's own commits from what each range "touched" (N11),
    and excluding a run with real commits but zero hook events of its own from
    the capture share entirely (N12) -- such a run never had the harness's
    hook invoked at all, so a 0-captured score would blend "the hook missed a
    file" with "the hook was never there to try," understating the measured
    gap. It is still counted, under its own reason, in `runs_unmeasurable`.

    Reproduces `change_capture_for_run`'s own per-run computation exactly
    (tests/test_change_capture.py checks the two against each other), but
    batches every run's git work into a small, constant number of `git`
    subprocesses instead of two per run (Day 3, item D2 -- RVc measured
    3.46s at 300 runs, one `git diff` plus one `git log` subprocess each)."""
    from core import executions

    root = Path(project_root).expanduser().resolve()
    records = records if records is not None else executions.load(root)
    # A run whose own commit_start == commit_end made no commit of its own
    # (touched_files' own "unmeasurable" signal); its commit_end is just
    # whatever HEAD happened to be, and could be another still-open run's own
    # intermediate commit -- excluded here so it is never mistaken for that
    # run's foreign, real work (RVd).
    other_finish_commits = frozenset(r.commit_end for r in records
                                     if r.commit_end and r.commit_start != r.commit_end)
    measurable = [r for r in records if r.outcome is not None]
    ranges = [(r.commit_start, r.commit_end) for r in measurable]
    touched_list = _batched_touched(root, ranges)
    foreign_list = _batched_foreign(root, ranges, other_finish_commits)

    captured = touched = runs_with_commits = runs_unmeasurable = runs_no_hook_events = 0
    for record, touched_set, foreign_set in zip(measurable, touched_list, foreign_list):
        if touched_set is None:
            runs_unmeasurable += 1
            continue
        touched_set = touched_set - foreign_set
        captured_refs = {
            norm for e in record.events if e.kind == "changed" and e.ref
            for norm in [_normalized_ref(e.ref, root)] if norm is not None
        }
        if not _has_hook_event(record):
            runs_no_hook_events += 1
            continue
        runs_with_commits += 1
        captured += len(captured_refs & touched_set)
        touched += len(touched_set)
    share = (captured / touched) if touched else None
    return {"captured": captured, "touched": touched, "share": share,
            "runs_with_commits": runs_with_commits,
            "runs_unmeasurable": runs_unmeasurable + runs_no_hook_events,
            "runs_unmeasurable_no_hook_events": runs_no_hook_events}


def report(project_root: str | Path, *, since: float | None = None) -> dict:
    """`since` (epoch seconds) narrows ACTION CAPTURE to the actions made after
    it, in every session written since (`action_capture.report`)."""
    from core import executions, index, notes, procedure_lint, work

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
    from core import action_capture

    actions = action_capture.report(root, records=records, since=since)
    # L1 remainder item (a), Day 3: the one generated EOS artifact under
    # `.eos/` with a declared, deterministic freshness key -- the index's own
    # `inputs_sha256` vs. a freshly computed sources digest (`index.is_stale`).
    # The brain/graph files a scan writes (graph.json, evidence.jsonl,
    # AI_SUMMARY.md) fold into that same digest as *inputs*, but declare no
    # freshness key of their own to compare against current source, so their
    # own staleness is not reported here (not defined in the codebase).
    stale_index = index.is_stale(root)
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
        "action_capture": actions,
        "lessons_without_evidence": lessons_without_evidence,
        "stale_index": stale_index,
        "priors": _priors(root, corpus, records),
        "routing": _routing(root, records),
        "accepted": accepted_counts(root, now),
    }


def _priors(root: Path, corpus, records) -> dict:
    """The priors the ledger gives today (M6, ADR-036), from the runs already read."""
    from core import priors

    fold = priors.fold_records(records, corpus)
    titles = {note.path.name: note.title for note in corpus}
    top = sorted(fold.values.items(), key=lambda item: (-item[1], item[0]))[:LIMIT]
    return {"runs": fold.runs, "notes": len(fold.values), "points": sum(fold.values.values()),
            "alpha": priors.alpha(root),
            "top": [(value, titles.get(name, name)) for name, value in top]}


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
                      f"runs with commits; {capture['runs_unmeasurable']} runs unmeasurable "
                      f"({capture.get('runs_unmeasurable_no_hook_events', 0)} without hook events)"]
    from core import action_capture

    action_line = action_capture.render_line(data.get("action_capture") or {})
    if action_line:
        lines += ["", action_line]
    prior = data.get("priors") or {}
    if prior.get("runs"):
        state = "on" if prior["alpha"] else "off"
        lines += ["", f"PRIORS  {prior['points']} points from {prior['runs']} verified "
                      f"runs over {prior['notes']} note{'s' if prior['notes'] != 1 else ''} "
                      f"(prior_alpha {prior['alpha']}: {state} in search)"]
        lines += [f"  {value}  {title}" for value, title in prior["top"]]
    if data.get("stale_index"):
        lines += ["", "STALE GENERATED ARTIFACT",
                  "  .eos/data/eos.db was built from an older snapshot of its sources",
                  "  (eos index . to rebuild)"]
    lines += _render_routing(data, block)
    if len(lines) == 1:
        lines.append("Nothing needs attention.")
    return "\n".join(lines)


# --- the routing corpus and accepted proposals (L1, ADR-025 addendum) -------------------


def _routing(root: Path, records) -> dict | None:
    """Accuracy on the configured corpus's development rows, advised vs used,
    keyword proposals; None without `[model_routing] corpus`."""
    from core.routing import corpus

    return corpus.report(root, records=records)


def _accepted_path(project_root: str | Path) -> Path:
    return Path(project_root).expanduser().resolve() / ".eos" / "data" / ACCEPTED


def _accepted(project_root: str | Path) -> list[dict]:
    """Every readable line; a torn one costs itself, never the others."""
    import json

    try:
        text = _accepted_path(project_root).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    entries = []
    for line in text.splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict) and entry.get("id"):
            entries.append(entry)
    return entries


def accepted_counts(project_root: str | Path, now: datetime.datetime | None = None) -> dict:
    now = now or datetime.datetime.now(datetime.timezone.utc)
    entries = _accepted(project_root)
    recent = 0
    for entry in entries:
        try:
            at = datetime.datetime.fromisoformat(str(entry.get("at")))
        except ValueError:
            continue
        if at.tzinfo is None:
            at = at.replace(tzinfo=datetime.timezone.utc)
        recent += int((now - at).days < ACCEPTED_DAYS)
    return {"last_30_days": recent, "total": len(entries)}


def accept(project_root: str | Path, proposal_id: str) -> dict:
    """Record that a person accepted a current proposal; writes nothing else.

    ValueError for an id no current proposal has, or one already accepted."""
    import json

    from core.knowledge.evidence import utc_now
    from core.lib import lock
    from core.routing import corpus

    if any(entry["id"] == proposal_id for entry in _accepted(project_root)):
        raise ValueError(f"{proposal_id} was already accepted")
    found = next((p for p in corpus.proposals(project_root) if p["id"] == proposal_id), None)
    if found is None:
        raise ValueError(f"{proposal_id}: no current proposal has this id (eos consolidate <path> lists "
                         "them; accept one before writing it into the config, where it stops being proposed)")
    entry = {"at": utc_now(), "id": proposal_id, "proposal": corpus.describe(found)}
    target = _accepted_path(project_root)
    target.parent.mkdir(parents=True, exist_ok=True)
    with lock.locked(target):
        with open(target, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def _render_routing(data: dict, block) -> list[str]:
    from core.routing import corpus

    found = data.get("routing")
    accepted = data.get("accepted") or {}
    lines: list[str] = []
    if found:
        lines += ["", "ROUTING VS CORPUS (development rows; the test split is never read)"]
        for entry in found["corpora"]:
            if entry["error"]:
                lines.append(f"  {entry['path']}  cannot read: {entry['error']}")
                continue
            accuracy = entry["accuracy"] or {}
            parts = [f"{column} {accuracy[column]:.1%}" for column in ("type", "level", "model", "effort")
                     if accuracy.get(column) is not None]
            lines.append(f"  {entry['path']}  {entry['prompts']} dev prompts" + "".join(f"  {p}" for p in parts))
        avu = found["advised_vs_used"]
        total = sum(avu.values())
        lines += ["", f"ADVISED VS USED  {total} routed run(s): {avu['same']} on the advised model, "
                      f"{avu['different']} on another, {avu['unknown']} unknown (eos route . --stats)"]
        proposals = [f"{p['id']}  {corpus.describe(p)}  moves {p['fixes']} mistyped dev rows to their label, "
                     f"breaks none ({p['rows']} rows hold it)" for p in found["proposals"]]
        if proposals:
            lines += block("KEYWORD PROPOSALS", proposals,
                           "for [model_routing.keywords]; nothing was written. A person who takes one: "
                           "eos consolidate . --accept <id>")
        else:
            lines += ["", f"KEYWORD PROPOSALS  none: no word moves {corpus.MIN_FIXED} mistyped dev rows "
                          "to their label without breaking one"]
    if found or accepted.get("total"):
        lines += ["", f"ACCEPTED PROPOSALS  {accepted.get('last_30_days', 0)} in the last {ACCEPTED_DAYS} days "
                      f"({accepted.get('total', 0)} in all)"]
    return lines
