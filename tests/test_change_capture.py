"""E1 capture measured (2.x roadmap N9): for one finished run, the files its
`changed` events named against the files git says its commit range touched.
"""
import os
import subprocess
from pathlib import Path

from core import consolidate, executions

_GIT_ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}


def _git(root, *args):
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True,
                   text=True, env=_GIT_ENV)


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


def test_report_aggregates_over_finished_runs_with_commits(tmp_path):
    root = _repo(tmp_path)
    (root / ".eos").mkdir(exist_ok=True)

    measured = executions.start(root, "Deploy")
    (root / "b.txt").write_text("1\n")
    (root / "c.txt").write_text("1\n")
    _git(root, "add", "b.txt", "c.txt")
    _git(root, "commit", "-q", "-m", "two files")
    executions.event(root, measured.id, kind="changed", ref="b.txt")
    executions.finish(root, measured.id, outcome="ok")

    unmeasured = executions.start(root, "Look only")
    executions.finish(root, unmeasured.id, outcome="ok")

    data = consolidate.report(root)

    assert data["change_capture"] == {
        "captured": 1, "touched": 2, "share": 0.5,
        "runs_with_commits": 1, "runs_unmeasurable": 1,
    }
    text = consolidate.render(data)
    assert "CHANGE CAPTURE  1/2 files (50%) over 1 runs with commits; 1 runs unmeasurable" in text


def test_report_prints_the_dash_when_nothing_is_measurable(tmp_path):
    root = _repo(tmp_path)
    (root / ".eos").mkdir(exist_ok=True)

    run = executions.start(root, "Look only")
    executions.finish(root, run.id, outcome="ok")

    data = consolidate.report(root)

    assert data["change_capture"] == {
        "captured": 0, "touched": 0, "share": None,
        "runs_with_commits": 0, "runs_unmeasurable": 1,
    }
    text = consolidate.render(data)
    assert "CHANGE CAPTURE  0/0 files (—) over 0 runs with commits; 1 runs unmeasurable" in text
