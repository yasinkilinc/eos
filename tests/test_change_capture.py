"""E1 capture measured (2.x roadmap N9): for one finished run, the files its
`changed` events named against the files git says its commit range touched.
"""
import os
import subprocess
import time
from pathlib import Path

from core import consolidate, executions

_GIT_ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}


def _git(root, *args):
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True,
                   text=True, env=_GIT_ENV)


def _head(root):
    return subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                          capture_output=True, text=True).stdout.strip()


def _fake_subtree_pull(root, prefix, filename, content):
    """The two-commit shape a real `git subtree pull --squash` leaves: a
    parentless squash commit whose own tree has no prefix, merged into the
    branch at `prefix/` by a second commit -- `git log --name-only` on the
    squash commit alone names a path the outer diff never will, exactly what
    `_foreign_files`'s prefix-from-message reconstruction has to undo."""
    branch = subprocess.run(["git", "-C", str(root), "branch", "--show-current"],
                            capture_output=True, text=True).stdout.strip()
    base = _head(root)
    _git(root, "checkout", "-q", "--orphan", "tmp-squash-source")
    _git(root, "rm", "-r", "-f", "-q", ".")
    path = root / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    _git(root, "add", filename)
    _git(root, "commit", "-q", "-m", f"Squashed '{prefix}/' changes from a..b")
    squash = _head(root)
    _git(root, "checkout", "-q", branch)
    _git(root, "read-tree", f"--prefix={prefix}/", "-u", squash)
    tree = subprocess.run(["git", "-C", str(root), "write-tree"],
                          capture_output=True, text=True).stdout.strip()
    merge = subprocess.run(["git", "-C", str(root), "commit-tree", tree, "-p", base, "-p", squash,
                           "-m", "chore: subtree pull"], capture_output=True, text=True,
                           env=_GIT_ENV).stdout.strip()
    _git(root, "reset", "-q", "--hard", merge)
    return merge


def _repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    (root / "a.txt").write_text("1\n")
    _git(root, "add", "a.txt")
    _git(root, "commit", "-q", "-m", "init")
    return root


def test_touched_files_is_what_git_says_the_range_changed(tmp_path):
    root = _repo(tmp_path)
    start = subprocess.run(["git", "-C", root, "rev-parse", "HEAD"],
                           capture_output=True, text=True).stdout.strip()
    (root / "b.txt").write_text("1\n")
    (root / "c.txt").write_text("1\n")
    _git(root, "add", "b.txt", "c.txt")
    _git(root, "commit", "-q", "-m", "two files")
    end = subprocess.run(["git", "-C", root, "rev-parse", "HEAD"],
                         capture_output=True, text=True).stdout.strip()

    assert consolidate.touched_files(root, start, end) == {"b.txt", "c.txt"}


def test_touched_files_counts_a_rename_as_both_paths_regardless_of_diff_renames(tmp_path):
    # RVc/N9: without --no-renames, a pure rename (no content change) collapses
    # to just the new path whenever diff.renames is on -- a config this
    # function does not control and must not be sensitive to.
    root = _repo(tmp_path)
    start = subprocess.run(["git", "-C", root, "rev-parse", "HEAD"],
                           capture_output=True, text=True).stdout.strip()
    _git(root, "mv", "a.txt", "b.txt")
    _git(root, "commit", "-q", "-m", "rename")
    end = subprocess.run(["git", "-C", root, "rev-parse", "HEAD"],
                         capture_output=True, text=True).stdout.strip()
    _git(root, "config", "diff.renames", "true")

    assert consolidate.touched_files(root, start, end) == {"a.txt", "b.txt"}


def test_touched_files_is_unmeasurable_without_a_real_range(tmp_path):
    root = _repo(tmp_path)
    head = subprocess.run(["git", "-C", root, "rev-parse", "HEAD"],
                          capture_output=True, text=True).stdout.strip()

    assert consolidate.touched_files(root, None, head) is None
    assert consolidate.touched_files(root, head, None) is None
    assert consolidate.touched_files(root, head, head) is None  # a single commit, no range
    assert consolidate.touched_files(root, head, "0" * 40) is None  # unreachable


def test_change_capture_for_run_counts_captured_touched_and_missed(tmp_path):
    root = _repo(tmp_path)
    run = executions.start(root, "Deploy")
    (root / "b.txt").write_text("1\n")
    (root / "c.txt").write_text("1\n")
    _git(root, "add", "b.txt", "c.txt")
    _git(root, "commit", "-q", "-m", "two files")
    executions.event(root, run.id, kind="changed", ref="b.txt")
    executions.finish(root, run.id, outcome="ok")

    record = executions.load(root)[0]
    result = consolidate.change_capture_for_run(root, record)

    assert result == {"captured": 1, "touched": 2, "missed": ["c.txt"]}


def test_change_capture_for_run_normalises_an_absolute_ref(tmp_path):
    root = _repo(tmp_path)
    run = executions.start(root, "Deploy")
    (root / "b.txt").write_text("1\n")
    _git(root, "add", "b.txt")
    _git(root, "commit", "-q", "-m", "one file")
    executions.event(root, run.id, kind="changed", ref=str((root / "b.txt").resolve()))
    executions.finish(root, run.id, outcome="ok")

    record = executions.load(root)[0]
    result = consolidate.change_capture_for_run(root, record)

    assert result == {"captured": 1, "touched": 1, "missed": []}


def test_change_capture_for_run_is_none_when_no_commit_was_made(tmp_path):
    root = _repo(tmp_path)
    run = executions.start(root, "Look only")
    executions.finish(root, run.id, outcome="ok")

    record = executions.load(root)[0]
    assert consolidate.change_capture_for_run(root, record) is None


def test_change_capture_for_run_is_none_for_an_open_run(tmp_path):
    root = _repo(tmp_path)
    run = executions.start(root, "Still going")

    record = executions.load(root)[0]
    assert consolidate.change_capture_for_run(root, record) is None
    assert run.id == record.id


def test_change_capture_for_run_excludes_a_concurrent_sessions_own_commit(tmp_path):
    # N11: two sessions committing to the same branch put the other one's
    # commit inside this run's own start..end range -- its files are not this
    # run's to have missed.
    root = _repo(tmp_path)
    mine = executions.start(root, "Mine")
    other = executions.start(root, "Other session")  # both start from the same commit
    (root / "other.txt").write_text("1\n")
    _git(root, "add", "other.txt")
    _git(root, "commit", "-q", "-m", "other session's own commit")
    executions.finish(root, other.id, outcome="ok")

    (root / "mine.txt").write_text("1\n")
    _git(root, "add", "mine.txt")
    _git(root, "commit", "-q", "-m", "my own commit")
    executions.event(root, mine.id, kind="changed", ref="mine.txt")
    executions.finish(root, mine.id, outcome="ok")

    records = {r.title: r for r in executions.load(root)}
    other_finish_commits = frozenset(r.commit_end for r in records.values())

    without = consolidate.change_capture_for_run(root, records["Mine"])
    assert without == {"captured": 1, "touched": 2, "missed": ["other.txt"]}

    result = consolidate.change_capture_for_run(root, records["Mine"], other_finish_commits)
    assert result == {"captured": 1, "touched": 1, "missed": []}


def test_a_file_a_run_own_commit_also_touches_is_never_excluded(tmp_path):
    # The exclusion only ever removes a file every commit naming it is
    # foreign; touched by the run's own commit too, it stays -- real work is
    # never made to disappear by another session sharing the same file.
    root = _repo(tmp_path)
    mine = executions.start(root, "Mine")
    other = executions.start(root, "Other session")  # both start from the same commit
    (root / "shared.txt").write_text("1\n")
    _git(root, "add", "shared.txt")
    _git(root, "commit", "-q", "-m", "other session touches it first")
    executions.finish(root, other.id, outcome="ok")

    (root / "shared.txt").write_text("2\n")
    _git(root, "add", "shared.txt")
    _git(root, "commit", "-q", "-m", "my own commit touches it too")
    executions.finish(root, mine.id, outcome="ok")

    records = {r.title: r for r in executions.load(root)}
    other_finish_commits = frozenset(r.commit_end for r in records.values())

    result = consolidate.change_capture_for_run(root, records["Mine"], other_finish_commits)
    assert result == {"captured": 0, "touched": 1, "missed": ["shared.txt"]}


def test_change_capture_excludes_an_idle_runs_finish_commit_from_other_finish_commits(tmp_path):
    # RVd/N11: `commit_end` is just `git_head()` at finish time, not a commit
    # the finishing run necessarily made itself (N9's own "no commit made"
    # case: `commit_start == commit_end`). A concurrent run that finishes idle
    # still contributes whatever HEAD happens to be as its `commit_end`; if
    # that HEAD is really an *earlier, still-open* run's own intermediate
    # commit, `change_capture` must not mistake it for foreign noise once the
    # earlier run makes a further commit and finishes -- that would make the
    # earlier run's own uncaptured file vanish from `touched`, overstating its
    # capture share.
    root = _repo(tmp_path)
    (root / ".eos").mkdir(exist_ok=True)
    mine = executions.start(root, "Mine, still working")
    (root / "intermediate.txt").write_text("1\n")
    _git(root, "add", "intermediate.txt")
    _git(root, "commit", "-q", "-m", "my own intermediate commit")

    idle = executions.start(root, "Idle concurrent session")  # no commit of its own
    executions.finish(root, idle.id, outcome="ok")  # commit_end == my intermediate commit

    (root / "final.txt").write_text("1\n")
    _git(root, "add", "final.txt")
    _git(root, "commit", "-q", "-m", "my own final commit")
    executions.event(root, mine.id, kind="changed", ref="final.txt", source="hook")
    executions.finish(root, mine.id, outcome="ok")

    data = consolidate.change_capture(root)

    assert data == {"captured": 1, "touched": 2, "share": 0.5,
                    "runs_with_commits": 1, "runs_unmeasurable": 1,
                    "runs_unmeasurable_no_hook_events": 0}


def test_a_subtree_squash_commits_own_files_are_excluded(tmp_path):
    root = _repo(tmp_path)
    run = executions.start(root, "Adopt upstream")
    _fake_subtree_pull(root, "tools/eos", "core/VERSION", "1.0.0\n")
    executions.finish(root, run.id, outcome="ok")

    record = executions.load(root)[0]
    assert consolidate.touched_files(root, record.commit_start, record.commit_end) == \
        {"tools/eos/core/VERSION"}

    result = consolidate.change_capture_for_run(root, record, frozenset({record.commit_end}))

    assert result == {"captured": 0, "touched": 0, "missed": []}


def test_report_aggregates_over_finished_runs_with_commits(tmp_path):
    root = _repo(tmp_path)
    (root / ".eos").mkdir(exist_ok=True)

    measured = executions.start(root, "Deploy")
    (root / "b.txt").write_text("1\n")
    (root / "c.txt").write_text("1\n")
    _git(root, "add", "b.txt", "c.txt")
    _git(root, "commit", "-q", "-m", "two files")
    executions.event(root, measured.id, kind="changed", ref="b.txt", source="hook")
    executions.finish(root, measured.id, outcome="ok")

    unmeasured = executions.start(root, "Look only")
    executions.finish(root, unmeasured.id, outcome="ok")

    data = consolidate.report(root)

    assert data["change_capture"] == {
        "captured": 1, "touched": 2, "share": 0.5,
        "runs_with_commits": 1, "runs_unmeasurable": 1,
        "runs_unmeasurable_no_hook_events": 0,
    }
    text = consolidate.render(data)
    assert ("CHANGE CAPTURE  1/2 files (50%) over 1 runs with commits; 1 runs unmeasurable "
            "(0 without hook events)") in text


def test_change_capture_excludes_a_run_with_commits_but_no_hook_events(tmp_path):
    # N12: a run whose harness never invoked the EOS hook at all still made
    # real commits (e.g. a CLI-only backfilled session) -- it has nothing a
    # hook could have captured, so it must not drag the share to 0/t; it is
    # reported separately, under its own reason, never blended into
    # captured/touched.
    root = _repo(tmp_path)
    (root / ".eos").mkdir(exist_ok=True)
    run = executions.start(root, "CLI-only session")
    (root / "b.txt").write_text("1\n")
    (root / "c.txt").write_text("1\n")
    _git(root, "add", "b.txt", "c.txt")
    _git(root, "commit", "-q", "-m", "two files, no hook ever ran")
    executions.finish(root, run.id, outcome="ok")

    data = consolidate.change_capture(root)

    assert data == {"captured": 0, "touched": 0, "share": None,
                    "runs_with_commits": 0, "runs_unmeasurable": 1,
                    "runs_unmeasurable_no_hook_events": 1}


def test_change_capture_an_event_with_no_source_is_not_a_hook_event(tmp_path):
    # A `changed` event recorded with no `source` at all (the field predates
    # N12; only `core/hooks.py`'s `_record` ever sets `source="hook"`) is not
    # proof the hook ran -- it is treated the same as a CLI-only run.
    root = _repo(tmp_path)
    (root / ".eos").mkdir(exist_ok=True)
    run = executions.start(root, "No source on the event")
    (root / "b.txt").write_text("1\n")
    _git(root, "add", "b.txt")
    _git(root, "commit", "-q", "-m", "one file")
    executions.event(root, run.id, kind="changed", ref="b.txt")  # no source=
    executions.finish(root, run.id, outcome="ok")

    data = consolidate.change_capture(root)

    assert data == {"captured": 0, "touched": 0, "share": None,
                    "runs_with_commits": 0, "runs_unmeasurable": 1,
                    "runs_unmeasurable_no_hook_events": 1}


def test_change_capture_a_run_with_a_wrapper_or_cli_event_but_no_hook_event_is_excluded(tmp_path):
    # A run can carry real events -- a `ran` event from `run start`/`run
    # finish` themselves (source="cli") -- and still have had no hook fire for
    # it; that is still "no hook event."
    root = _repo(tmp_path)
    (root / ".eos").mkdir(exist_ok=True)
    run = executions.start(root, "CLI events only")
    executions.event(root, run.id, kind="ran", tool="git", source="cli")
    (root / "b.txt").write_text("1\n")
    _git(root, "add", "b.txt")
    _git(root, "commit", "-q", "-m", "one file")
    executions.finish(root, run.id, outcome="ok")

    data = consolidate.change_capture(root)

    assert data["runs_unmeasurable_no_hook_events"] == 1
    assert data["captured"] == 0 and data["touched"] == 0


def test_change_capture_only_folds_runs_that_did_have_a_hook_event(tmp_path):
    root = _repo(tmp_path)
    (root / ".eos").mkdir(exist_ok=True)

    with_hook = executions.start(root, "Had the hook")
    (root / "b.txt").write_text("1\n")
    _git(root, "add", "b.txt")
    _git(root, "commit", "-q", "-m", "hooked run")
    executions.event(root, with_hook.id, kind="changed", ref="b.txt", source="hook")
    executions.finish(root, with_hook.id, outcome="ok")

    without_hook = executions.start(root, "No hook at all")
    (root / "c.txt").write_text("1\n")
    _git(root, "add", "c.txt")
    _git(root, "commit", "-q", "-m", "unhooked run")
    executions.finish(root, without_hook.id, outcome="ok")

    data = consolidate.change_capture(root)

    assert data == {"captured": 1, "touched": 1, "share": 1.0,
                    "runs_with_commits": 1, "runs_unmeasurable": 1,
                    "runs_unmeasurable_no_hook_events": 1}


def test_change_capture_a_decided_only_routing_event_is_not_capture_hook_activity(tmp_path):
    # RVe: every `_record` call site in core/hooks.py opens the run through
    # `cfg["capture"]` except `_route_subagent`'s "decided" event, which
    # records a subagent routing decision regardless of the `capture` setting
    # (ADR-025 wants every routing decision kept for N5's advised-vs-used
    # report even in a project that has turned event capture off). A run
    # whose only hook-sourced event is "decided" therefore proves the routing
    # hook fired, never that the capture-relevant hook (Edit/Write/Bash
    # PostToolUse) had a chance to record anything -- it must still count as
    # no hook events, the same as a run with no hook-sourced event at all.
    root = _repo(tmp_path)
    (root / ".eos").mkdir(exist_ok=True)
    run = executions.start(root, "Routed subagent, capture off")
    (root / "b.txt").write_text("1\n")
    _git(root, "add", "b.txt")
    _git(root, "commit", "-q", "-m", "one file, only a routing decision recorded")
    executions.event(root, run.id, kind="decided", tool="route", ref="route:abc123",
                     source="hook")
    executions.finish(root, run.id, outcome="ok")

    data = consolidate.change_capture(root)

    assert data == {"captured": 0, "touched": 0, "share": None,
                    "runs_with_commits": 0, "runs_unmeasurable": 1,
                    "runs_unmeasurable_no_hook_events": 1}


def test_report_prints_the_dash_when_nothing_is_measurable(tmp_path):
    root = _repo(tmp_path)
    (root / ".eos").mkdir(exist_ok=True)

    run = executions.start(root, "Look only")
    executions.finish(root, run.id, outcome="ok")

    data = consolidate.report(root)

    assert data["change_capture"] == {
        "captured": 0, "touched": 0, "share": None,
        "runs_with_commits": 0, "runs_unmeasurable": 1,
        "runs_unmeasurable_no_hook_events": 0,
    }
    text = consolidate.render(data)
    assert ("CHANGE CAPTURE  0/0 files (—) over 0 runs with commits; 1 runs unmeasurable "
            "(0 without hook events)") in text


# --- consolidate speed (2.x roadmap Day 3, item D2): batched git subprocesses ---------


def _ledger(tmp_path, n):
    """`n` finished runs, each its own commit, on one linear branch --
    RVc's synthetic shape (one `git diff`/`git log` subprocess per run,
    measured 3.46s at n=300)."""
    root = _repo(tmp_path)
    for i in range(n):
        run = executions.start(root, f"Run {i}")
        (root / f"f{i}.txt").write_text("1\n")
        _git(root, "add", f"f{i}.txt")
        _git(root, "commit", "-q", "-m", f"run {i}")
        executions.event(root, run.id, kind="changed", ref=f"f{i}.txt", source="hook")
        executions.finish(root, run.id, outcome="ok")
    return root


def _naive_change_capture(root, records):
    """The pre-batching computation `change_capture` used to make: one
    `change_capture_for_run` call per record, summed by hand -- kept here as
    the ground truth the batched aggregate must still match exactly."""
    other_finish_commits = frozenset(r.commit_end for r in records
                                     if r.commit_end and r.commit_start != r.commit_end)
    captured = touched = runs_with_commits = runs_unmeasurable = runs_no_hook_events = 0
    for record in records:
        if record.outcome is None:
            continue
        result = consolidate.change_capture_for_run(root, record, other_finish_commits)
        if result is None:
            runs_unmeasurable += 1
            continue
        if not consolidate._has_hook_event(record):
            runs_no_hook_events += 1
            continue
        runs_with_commits += 1
        captured += result["captured"]
        touched += result["touched"]
    share = (captured / touched) if touched else None
    return {"captured": captured, "touched": touched, "share": share,
            "runs_with_commits": runs_with_commits,
            "runs_unmeasurable": runs_unmeasurable + runs_no_hook_events,
            "runs_unmeasurable_no_hook_events": runs_no_hook_events}


def test_change_capture_matches_the_naive_per_run_computation_on_a_linear_ledger(tmp_path):
    root = _ledger(tmp_path, 15)
    records = executions.load(root)

    assert consolidate.change_capture(root, records=records) == _naive_change_capture(root, records)


def test_change_capture_matches_the_naive_computation_with_concurrent_sessions_and_a_squash(tmp_path):
    # The shapes the per-run reference already covers on their own (concurrent
    # sessions sharing a branch, an idle run, a subtree squash, a rename) --
    # exercised together against the same ground truth, since the batched
    # path shares no code with `change_capture_for_run` at the git-subprocess
    # level and must still classify every commit identically.
    root = _repo(tmp_path)
    mine = executions.start(root, "Mine")
    other = executions.start(root, "Other session")
    (root / "other.txt").write_text("1\n")
    _git(root, "add", "other.txt")
    _git(root, "commit", "-q", "-m", "other session's own commit")
    executions.event(root, other.id, kind="changed", ref="other.txt", source="hook")
    executions.finish(root, other.id, outcome="ok")

    idle = executions.start(root, "Idle concurrent session")
    executions.finish(root, idle.id, outcome="ok")

    (root / "mine.txt").write_text("1\n")
    _git(root, "add", "mine.txt")
    _git(root, "commit", "-q", "-m", "my own commit")
    executions.event(root, mine.id, kind="changed", ref="mine.txt", source="hook")
    executions.finish(root, mine.id, outcome="ok")

    squash_run = executions.start(root, "Adopt upstream")
    _fake_subtree_pull(root, "tools/eos", "core/VERSION", "1.0.0\n")
    executions.event(root, squash_run.id, kind="changed", ref="tools/eos/core/VERSION", source="hook")
    executions.finish(root, squash_run.id, outcome="ok")

    still_open = executions.start(root, "Still going")

    records = executions.load(root)

    assert consolidate.change_capture(root, records=records) == _naive_change_capture(root, records)
    assert still_open.id in {r.id for r in records if r.open}


def test_batched_touched_matches_touched_files_per_pair_including_a_shared_start(tmp_path):
    root = _repo(tmp_path)
    head0 = subprocess.run(["git", "-C", root, "rev-parse", "HEAD"],
                           capture_output=True, text=True).stdout.strip()
    (root / "b.txt").write_text("1\n")
    _git(root, "add", "b.txt")
    _git(root, "commit", "-q", "-m", "b")
    head1 = subprocess.run(["git", "-C", root, "rev-parse", "HEAD"],
                           capture_output=True, text=True).stdout.strip()
    (root / "c.txt").write_text("1\n")
    _git(root, "add", "c.txt")
    _git(root, "commit", "-q", "-m", "c")
    head2 = subprocess.run(["git", "-C", root, "rev-parse", "HEAD"],
                           capture_output=True, text=True).stdout.strip()

    # head0..head1 and head0..head2 share a start -- the collision case that
    # falls back to the per-pair path instead of `git diff-tree --stdin`
    # (which prints nothing at all for an empty pair, so two pairs sharing a
    # start could not otherwise be told apart from one of them being empty).
    ranges = [(head0, head1), (head0, head2), (head1, head2), (None, head1), (head1, head1)]

    batched = consolidate._batched_touched(root, ranges)
    expected = [consolidate.touched_files(root, s, e) for s, e in ranges]

    assert batched == expected


def test_batched_foreign_matches_foreign_files_per_pair(tmp_path):
    root = _repo(tmp_path)
    head0 = subprocess.run(["git", "-C", root, "rev-parse", "HEAD"],
                           capture_output=True, text=True).stdout.strip()
    (root / "b.txt").write_text("1\n")
    _git(root, "add", "b.txt")
    _git(root, "commit", "-q", "-m", "b")
    head1 = subprocess.run(["git", "-C", root, "rev-parse", "HEAD"],
                           capture_output=True, text=True).stdout.strip()
    (root / "c.txt").write_text("1\n")
    _git(root, "add", "c.txt")
    _git(root, "commit", "-q", "-m", "c")
    head2 = subprocess.run(["git", "-C", root, "rev-parse", "HEAD"],
                           capture_output=True, text=True).stdout.strip()
    other_finish_commits = frozenset({head1})

    ranges = [(head0, head1), (head0, head2), (head1, head2), (None, head1)]
    batched = consolidate._batched_foreign(root, ranges, other_finish_commits)
    expected = [consolidate._foreign_files(root, s, e, other_finish_commits) for s, e in ranges]

    assert batched == expected


def test_change_capture_batches_git_calls_instead_of_one_pair_per_run(tmp_path, monkeypatch):
    root = _ledger(tmp_path, 20)
    records = executions.load(root)

    calls = []
    real_run = subprocess.run

    def counting_run(cmd, *args, **kwargs):
        calls.append(cmd)
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", counting_run)

    consolidate.change_capture(root, records=records)

    git_calls = [c for c in calls if c[0].endswith("git") or c[0] == "git"]
    # One run per finished record, unbatched, would be 2 subprocesses each
    # (a `git diff` for touched_files, a `git log` for _foreign_files) = 40
    # here; batched, it is a small constant number regardless of how many
    # runs there are.
    assert len(git_calls) < 10


def test_change_capture_speed_on_a_larger_synthetic_ledger_stays_well_under_a_generous_bound(tmp_path):
    root = _ledger(tmp_path, 60)
    records = executions.load(root)

    started = time.perf_counter()
    result = consolidate.change_capture(root, records=records)
    elapsed = time.perf_counter() - started

    assert result["runs_with_commits"] == 60
    # RVc measured ~3.46s at n=300 unbatched (~11.5ms/run); a generous bound
    # for n=60 that would still fail loudly if batching regressed to the old
    # per-run shape (~0.7s at that rate) without being flaky on a slow CI box.
    assert elapsed < 5.0
