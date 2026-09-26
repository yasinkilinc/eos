"""Ledger discipline (2.x roadmap F5): a version on every line, rotation with a
fold that reads every file, and no finish for a run that was never started."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from core import executions

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    (root / ".eos").mkdir(parents=True)
    monkeypatch.setenv("EOS_STATE_DIR", str(tmp_path / "state"))
    return root


def _lines(project):
    return [json.loads(line) for line in executions.path_for(project).read_text().splitlines()]


def test_every_line_carries_the_ledger_version(project, tmp_path):
    run = executions.start(project, "Deploy", session="s1")
    executions.event(project, run.id, kind="ran", tool="git")
    subprocess.run(["/bin/sh", str(REPO / "bin" / "eos-event"), "--kind", "called", "--tool", "jenkins"],
                   env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path),
                        "EOS_STATE_DIR": os.environ["EOS_STATE_DIR"], "CLAUDE_CODE_SESSION_ID": "s1"})
    executions.finish(project, run.id, outcome="ok")
    lines = _lines(project)
    assert len(lines) == 4 and all(line.get("v") == executions.LEDGER_VERSION for line in lines), lines


def test_a_full_ledger_rotates_and_the_fold_reads_every_file(project, monkeypatch):
    monkeypatch.setattr(executions, "MAX_LEDGER_BYTES", 400)
    first = executions.start(project, "a run that spans a rotation", session="s1")
    for n in range(12):
        executions.event(project, first.id, kind="ran", tool=f"tool{n}")
    executions.finish(project, first.id, outcome="ok")
    ledger = executions.path_for(project)
    rotated = sorted(ledger.parent.glob("executions.*.jsonl"))
    assert 1 <= len(rotated) <= executions.KEEP_ROTATED, rotated
    [record] = executions.load_path(ledger)
    assert record.outcome == "ok" and len(record.events) >= 1


def test_rotation_keeps_at_most_four_old_files(project, monkeypatch):
    monkeypatch.setattr(executions, "MAX_LEDGER_BYTES", 200)
    for n in range(40):
        executions.start(project, f"run {n}", session=f"s{n}")
    ledger = executions.path_for(project)
    assert len(list(ledger.parent.glob("executions.*.jsonl"))) == executions.KEEP_ROTATED


def test_a_run_that_was_never_started_cannot_be_finished(project):
    with pytest.raises(ValueError, match="never started"):
        executions.finish(project, "x-nothing-0000", outcome="ok")
    assert not executions.path_for(project).exists()
