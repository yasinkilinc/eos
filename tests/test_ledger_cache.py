"""The rotated ledger files are folded once, not on every prompt.

A full ledger is five files; the four rotated ones change only at the next
rotation, yet every brief decoded all of them again (0.8 s of a 1.6 s prompt
hook at 113k events, 2026-11-04). The fold of the rotated files is kept in
the state directory and only the live file is decoded on top of it.
"""
import json

import pytest

from core import executions


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    (root / ".eos").mkdir(parents=True)
    monkeypatch.setenv("EOS_STATE_DIR", str(tmp_path / "state"))
    # Rotates a dozen events into old files without dropping the oldest one.
    monkeypatch.setattr(executions, "MAX_LEDGER_BYTES", 1500)
    return root


def _fill(project, title="a run that spans a rotation", events=12, finish=True):
    run = executions.start(project, title, session="s1")
    for n in range(events):
        executions.event(project, run.id, kind="ran", tool=f"tool{n}")
    if finish:
        executions.finish(project, run.id, outcome="ok")
    return run


def _uncached(ledger, tmp_path, monkeypatch, name):
    monkeypatch.setenv("EOS_STATE_DIR", str(tmp_path / name))
    held = dict(executions._held)
    executions._held.clear()
    try:
        return executions.load_path(ledger)
    finally:
        executions._held.clear()
        executions._held.update(held)
        monkeypatch.setenv("EOS_STATE_DIR", str(tmp_path / "state"))


def _decodes(monkeypatch):
    calls = []
    real = json.loads

    def counting(text, *args, **kwargs):
        calls.append(1)
        return real(text, *args, **kwargs)

    monkeypatch.setattr(executions.json, "loads", counting)
    return calls


def test_a_second_load_decodes_only_the_live_file(project, monkeypatch):
    run = _fill(project, finish=False)
    ledger = executions.path_for(project)
    assert executions.rotated(ledger, 1).is_file()
    executions.load_path(ledger)
    executions.finish(project, run.id, outcome="ok")
    live = len(ledger.read_text().splitlines())

    calls = _decodes(monkeypatch)
    [record] = executions.load_path(ledger)

    assert len(calls) <= live
    assert record.outcome == "ok" and len(record.events) == 12


def test_the_cached_fold_equals_a_fold_from_scratch(project, tmp_path, monkeypatch):
    first = _fill(project, finish=False)
    ledger = executions.path_for(project)
    executions.load_path(ledger)
    executions.event(project, first.id, kind="ran", tool="after-the-cache")
    executions.finish(project, first.id, outcome="failed", lesson="spans the cache")

    assert executions.load_path(ledger) == _uncached(ledger, tmp_path, monkeypatch, "fresh")


def test_a_new_rotation_is_read_rather_than_the_old_fold(project, tmp_path, monkeypatch):
    _fill(project)
    ledger = executions.path_for(project)
    executions.load_path(ledger)
    second = _fill(project, title="a second run after the cache", events=20)

    loaded = executions.load_path(ledger)

    assert loaded == _uncached(ledger, tmp_path, monkeypatch, "fresh")
    assert second.id in {r.id for r in loaded}


def test_a_damaged_cache_is_folded_again(project, tmp_path, monkeypatch):
    _fill(project)
    ledger = executions.path_for(project)
    executions.load_path(ledger)
    for cached in (tmp_path / "state").rglob("*"):
        if cached.is_file() and "ledger" in cached.parent.name:
            cached.write_bytes(b"not a fold")

    assert executions.load_path(ledger) == _uncached(ledger, tmp_path, monkeypatch, "fresh")


def test_the_cache_is_not_written_into_the_project(project):
    _fill(project)
    ledger = executions.path_for(project)
    before = sorted(p for p in project.rglob("*") if p.is_file())

    executions.load_path(ledger)

    assert sorted(p for p in project.rglob("*") if p.is_file()) == before
