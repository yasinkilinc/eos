"""`eos route --learn` (A5): proposals from the outcome join `--stats` already
makes, printed only -- config is never written.
"""
import json

import pytest

import core.routing as routing
from core import executions
from core.routing import learn, trace, usage


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    (root / ".eos").mkdir(parents=True)
    monkeypatch.setenv("EOS_STATE_DIR", str(tmp_path / "state"))
    for name in ("EOS_EXECUTION", "EOS_EXECUTION_LEDGER", routing.MODEL_ENV, routing.EFFORT_ENV):
        monkeypatch.delenv(name, raising=False)
    return root


def _write_usage(project, session, *, subagent_models=None):
    path = usage.path_for(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {"at": "2026-09-27T00:00:00+00:00", "session": session,
             "models": {}, "subagent_models": subagent_models or {}}
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry) + "\n")


def _routed_run(project, index, *, outcome, model="haiku", session=None):
    session = session or f"s{index}"
    run = executions.start(project, f"task {index}", session=session)
    routing.route(project, "fix a small typo", session=session, model=model)
    lesson = f"lesson number {index} about a distinct failure mode" if outcome == "failed" else None
    executions.finish(project, run.id, outcome=outcome, lesson=lesson, session=session)
    return run


def test_nothing_recorded_says_so(project):
    assert learn.propose(project) == ["No proposal: nothing crossed the sample-size or rate thresholds yet."]


def test_a_group_with_only_open_runs_is_too_few_not_silently_dropped(project):
    """RVj (Day 4 review): a group whose routed runs are all still open has
    `finished == 0` -- no outcome signal, but it must still be counted as
    "too few runs", never silently left out of the proposals entirely."""
    for i in range(3):
        session = f"open{i}"
        executions.start(project, f"task {i}", session=session)
        routing.route(project, "fix a small typo", session=session, model="haiku")
    lines = learn.propose(project)
    assert any("too few runs" in line for line in lines)


def test_a_handful_of_runs_is_too_few_for_a_rate(project):
    for i in range(3):
        _routed_run(project, i, outcome="failed")
    lines = learn.propose(project)
    assert any("too few runs" in line for line in lines)
    assert not any("review the keywords or level" in line for line in lines)


def test_a_high_failure_rate_at_a_level_is_proposed(project):
    for i in range(6):
        _routed_run(project, i, outcome="failed" if i < 4 else "ok")
    lines = learn.propose(project)
    assert any("4/6 finished routed runs failed or were abandoned (67%)" in line for line in lines)


def test_a_low_failure_rate_proposes_nothing_for_that_group(project):
    for i in range(6):
        _routed_run(project, i, outcome="failed" if i < 1 else "ok")
    lines = learn.propose(project)
    assert not any("review the keywords or level" in line for line in lines)


def test_a_level_consistently_overridden_is_proposed(project):
    for i in range(6):
        session = f"o{i}"
        run = executions.start(project, f"task {i}", session=session)
        routing.route(project, "fix a small typo", session=session, model="haiku")
        executions.finish(project, run.id, outcome="ok", session=session)
        used = "opus" if i < 5 else "haiku"
        _write_usage(project, session, subagent_models={used: {"messages": 1}})
    lines = learn.propose(project)
    assert any("the model actually used differed from the one advised in 5/6" in line for line in lines)


def test_never_writes_config(project):
    config = project / ".eos" / "config.toml"
    before = config.read_text(encoding="utf-8") if config.exists() else None
    for i in range(6):
        _routed_run(project, i, outcome="failed" if i < 4 else "ok")
    learn.propose(project)
    after = config.read_text(encoding="utf-8") if config.exists() else None
    assert before == after
