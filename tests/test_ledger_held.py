"""A process folds a ledger once while it stays the same.

One prompt hook loaded the 113k-event ledger three times: the brief's open
runs, its task sections and the report, ~250 ms each from the cached fold
(2026-11-05). The process keeps the last fold of each rotated ledger and hands
out copies; the live file is compared by its whole text, so any write to it,
of any size, is folded again.
"""
import pytest

from core import executions


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    (root / ".eos").mkdir(parents=True)
    monkeypatch.setattr(executions, "MAX_LEDGER_BYTES", 1500)
    return root


def _fill(project, events=12):
    run = executions.start(project, "a run that spans a rotation", session="s1")
    for n in range(events):
        executions.event(project, run.id, kind="ran", tool=f"tool{n}")
    return run


def _counting(monkeypatch):
    calls = []
    real_read, real_loads = executions._read_cache, executions.json.loads

    def read(*args, **kwargs):
        calls.append("cache")
        return real_read(*args, **kwargs)

    def loads(text, *args, **kwargs):
        calls.append("line")
        return real_loads(text, *args, **kwargs)

    monkeypatch.setattr(executions, "_read_cache", read)
    monkeypatch.setattr(executions.json, "loads", loads)
    return calls


def test_a_second_load_of_an_unchanged_ledger_reads_no_fold_and_decodes_nothing(project, monkeypatch):
    _fill(project)
    ledger = executions.path_for(project)
    assert executions.rotated(ledger, 1).is_file()
    first = executions.load_path(ledger)

    calls = _counting(monkeypatch)
    second = executions.load_path(ledger)

    assert calls == []
    assert second == first


def test_a_caller_changing_what_it_got_does_not_change_the_next_load(project):
    _fill(project)
    ledger = executions.path_for(project)
    [first] = executions.load_path(ledger)
    first.title = "changed by a caller"
    first.events.clear()

    [second] = executions.load_path(ledger)

    assert second.title == "a run that spans a rotation"
    assert len(second.events) == 12


def test_a_write_to_the_live_file_is_read(project):
    run = _fill(project)
    ledger = executions.path_for(project)
    executions.load_path(ledger)

    executions.finish(project, run.id, outcome="ok")

    [record] = executions.load_path(ledger)
    assert record.outcome == "ok"


def test_a_same_size_rewrite_of_the_live_file_is_read(project):
    run = _fill(project)
    ledger = executions.path_for(project)
    executions.finish(project, run.id, outcome="ok")
    executions.load_path(ledger)
    text = ledger.read_text(encoding="utf-8")
    assert '"outcome": "ok"' in text
    ledger.write_text(text.replace('"outcome": "ok"', '"outcome": "no"'), encoding="utf-8")

    [record] = executions.load_path(ledger)
    assert record.outcome == "no"


def test_a_new_rotation_is_read(project):
    _fill(project)
    ledger = executions.path_for(project)
    executions.load_path(ledger)
    second = _fill(project, events=20)

    assert second.id in {r.id for r in executions.load_path(ledger)}
