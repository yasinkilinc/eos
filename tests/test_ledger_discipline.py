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


def test_a_run_started_in_another_projects_ledger_is_finished_there(tmp_path, monkeypatch):
    """Branch review finding 5: in a workspace a run started in a service's
    ledger is finished by id from the workspace root."""
    monkeypatch.setenv("EOS_STATE_DIR", str(tmp_path / "state"))
    hub, svc = tmp_path / "hub", tmp_path / "svc"
    for root in (hub, svc):
        (root / ".eos").mkdir(parents=True)
    run = executions.start(svc, "Deploy the service", session="s1")
    record = executions.finish(hub, run.id, outcome="ok", session="s1")
    assert record.outcome == "ok"
    assert executions.load(svc)[0].outcome == "ok" and executions.load(hub) == []


def test_finish_prefers_the_callers_ledger_when_it_holds_the_run(tmp_path, monkeypatch):
    """Worktrees share committed ledgers: the same id can be open in two of them."""
    monkeypatch.setenv("EOS_STATE_DIR", str(tmp_path / "state"))
    one, two = tmp_path / "one", tmp_path / "two"
    for root in (one, two):
        (root / ".eos").mkdir(parents=True)
    run = executions.start(one, "Shared run", session="s1")
    two_ledger = executions.path_for(two)
    two_ledger.parent.mkdir(parents=True, exist_ok=True)
    two_ledger.write_text(executions.path_for(one).read_text(), encoding="utf-8")
    executions.finish(two, run.id, outcome="ok", session="s1")
    assert executions.load(two)[0].outcome == "ok" and executions.load(one)[0].outcome is None


def test_finish_survives_an_unreadable_config_above_the_project(tmp_path, monkeypatch):
    monkeypatch.setenv("EOS_STATE_DIR", str(tmp_path / "state"))
    (tmp_path / ".eos").mkdir()
    (tmp_path / ".eos" / "config.toml").write_text("not [valid toml", encoding="utf-8")
    project = tmp_path / "project"
    (project / ".eos").mkdir(parents=True)
    shared = tmp_path / "shared" / "notes"
    (project / ".eos" / "config.toml").write_text(f'[knowledge]\ndir = "{shared}"\n', encoding="utf-8")
    run = executions.start(project, "Redirected notes", session="s1")
    assert executions.finish(project, run.id, outcome="ok", session="s1").outcome == "ok"


def test_a_reader_during_a_rotation_still_sees_every_run(tmp_path, monkeypatch):
    """Whole-branch review: between the rename and the next append a lock-free
    reader could find neither file (about 1% of reads in a tight loop)."""
    import os
    import threading

    monkeypatch.setenv("EOS_STATE_DIR", str(tmp_path / "state"))
    (tmp_path / ".eos").mkdir()
    run = executions.start(tmp_path, "Kept across rotations", session="s1")
    ledger = executions.path_for(tmp_path)
    first = executions.rotated(ledger, 1)
    stop, misses = threading.Event(), []

    from core.lib import lock

    def flip():  # the rename a rotation does, and back, under the writers' lock as _append holds it
        while not stop.is_set():
            with lock.locked(ledger):
                os.replace(ledger, first)
                os.replace(first, ledger)

    worker = threading.Thread(target=flip)
    worker.start()
    try:
        for _ in range(2000):
            if run.id not in {r.id for r in executions.load_path(ledger)}:
                misses.append(1)
    finally:
        stop.set()
        worker.join()
    assert misses == []


def test_a_torn_look_at_a_rotating_ledger_is_not_read_as_empty(tmp_path, monkeypatch):
    """Review of the flaky rotation test (1 in 5 runs): a reader that looked at
    `.1` before the rename and at the ledger after it saw no file at all, and an
    absent ledger read as an empty one. A ledger that was ever written is read
    again under the lock instead."""
    monkeypatch.setenv("EOS_STATE_DIR", str(tmp_path / "state"))
    (tmp_path / ".eos").mkdir()
    run = executions.start(tmp_path, "Kept through a torn look", session="s1")
    ledger = executions.path_for(tmp_path)

    real_read = type(ledger).read_text
    torn = {"left": 1}   # the one read that fell between the rename and the next append

    def read_text(self, *args, **kwargs):
        if torn["left"] > 0 and self.name == "executions.jsonl":
            torn["left"] -= 1
            raise FileNotFoundError(self)
        return real_read(self, *args, **kwargs)

    from core.lib import lock
    real_locked = lock.locked

    def locked(*args, **kwargs):   # holding the writers' lock, the rotation is over
        torn["left"] = 0
        return real_locked(*args, **kwargs)

    monkeypatch.setattr(lock, "locked", locked)
    monkeypatch.setattr(executions, "_identities", lambda parts: tuple(None for _ in parts))
    monkeypatch.setattr(type(ledger), "read_text", read_text)
    assert run.id in {r.id for r in executions.load_path(ledger)}


def test_a_ledger_never_written_is_empty_at_once(tmp_path, monkeypatch):
    monkeypatch.setenv("EOS_STATE_DIR", str(tmp_path / "state"))
    import time
    ledger = tmp_path / "nowhere" / "executions.jsonl"
    started = time.monotonic()
    assert executions.load_path(ledger) == []
    assert time.monotonic() - started < 0.05


def test_without_fcntl_a_torn_look_is_read_under_the_lock(tmp_path, monkeypatch):
    """Review of the race fix: the O_EXCL fallback deletes its marker on release,
    so "a lock file exists" says nothing there; the all-absent look is read under
    the lock instead."""
    from core.lib import lock

    monkeypatch.setattr(lock, "fcntl", None)
    monkeypatch.setenv("EOS_STATE_DIR", str(tmp_path / "state"))
    (tmp_path / ".eos").mkdir()
    run = executions.start(tmp_path, "Kept without fcntl", session="s1")
    ledger = executions.path_for(tmp_path)
    monkeypatch.setattr(executions, "_identities", lambda parts: tuple(None for _ in parts))
    assert run.id in {r.id for r in executions.load_path(ledger)}
