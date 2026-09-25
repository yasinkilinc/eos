"""A decision is remembered without the task, and bound to the run it was made in.

The trace is the minimal record ADR-025 asks for; the `decided` event is what
lets a later reader put a decision next to the run's outcome; reuse is what
keeps one task from being classified again and again inside its own run.
"""
import json

import pytest

import core.routing as routing
from core import executions
from core.routing import trace

SENTINEL = "zanzibarquux"


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    (root / ".eos").mkdir(parents=True)
    monkeypatch.setenv("EOS_STATE_DIR", str(tmp_path / "state"))
    for name in ("EOS_EXECUTION", "EOS_EXECUTION_LEDGER", routing.MODEL_ENV, routing.EFFORT_ENV):
        monkeypatch.delenv(name, raising=False)
    return root


def _eos_text(root) -> str:
    return "\n".join(p.read_text(encoding="utf-8", errors="replace")
                     for p in (root / ".eos").rglob("*") if p.is_file())


def test_a_recorded_decision_keeps_a_hash_and_never_the_task(project):
    decision = routing.route(project, f"refactor the {SENTINEL} adapter")

    lines = trace.load(project)
    assert len(lines) == 1
    assert lines[0]["task_hash"] == decision.task_hash
    assert lines[0]["model"] == decision.model and lines[0]["effort"] == decision.effort
    assert SENTINEL not in _eos_text(project)


def test_record_false_writes_nothing(project):
    routing.route(project, "fix the typo", record=False)
    assert trace.load(project) == []
    assert not trace.path_for(project).exists()


def test_a_directory_without_eos_gets_no_trace(tmp_path):
    bare = tmp_path / "bare"
    bare.mkdir()
    routing.route(bare, "fix the typo")
    assert not (bare / ".eos").exists()


def test_the_trace_keeps_only_its_tail(project, monkeypatch):
    monkeypatch.setattr(trace, "MAX_LINES", 3)
    for _ in range(5):
        routing.route(project, "fix the typo")
    assert len(trace.load(project)) == 3


def test_a_write_failure_does_not_break_routing(project, monkeypatch):
    import builtins

    real_open = builtins.open

    def refusing(path, *args, **kwargs):
        if str(path).endswith(trace.FILENAME):
            raise OSError("disk full")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", refusing)
    decision = routing.route(project, "fix the typo")
    assert decision.model == "haiku"


def test_inside_an_open_run_the_decision_is_an_event_and_is_reused(project):
    run = executions.start(project, "some task", session="s1")

    first = routing.route(project, "refactor the auth flow and update tests", session="s1")
    again = routing.route(project, "fix the typo", session="s1")

    events = [e for e in executions.load(project)[0].events if e.kind == "decided"]
    assert len(events) == 1
    assert events[0].tool == "route" and events[0].ref == f"route:{first.task_hash}"
    assert first.execution == run.id and not first.reused
    assert again.reused and again.override_source == "run"
    assert (again.model, again.effort, again.level, again.task_type) == \
           (first.model, first.effort, first.level, first.task_type)
    assert len(trace.load(project)) == 1, "a reused decision records nothing"


def test_fresh_decides_again_and_records_again(project):
    executions.start(project, "some task", session="s1")
    routing.route(project, "refactor the auth flow and update tests", session="s1")
    fresh = routing.route(project, "fix the typo", session="s1", fresh=True)

    assert not fresh.reused and fresh.model == "haiku"
    events = [e for e in executions.load(project)[0].events if e.kind == "decided"]
    assert len(events) == 2


def test_an_explicit_choice_is_not_answered_with_the_reused_decision(project):
    executions.start(project, "some task", session="s1")
    routing.route(project, "fix the typo", session="s1")
    explicit = routing.route(project, "fix the typo", session="s1", model="opus")
    assert explicit.model == "opus" and not explicit.reused


def test_without_an_open_run_there_is_no_event_but_there_is_a_trace(project):
    decision = routing.route(project, "fix the typo", session="s1")
    assert decision.execution is None
    assert executions.load(project) == []
    assert len(trace.load(project)) == 1


def test_a_finished_run_is_not_reused(project):
    run = executions.start(project, "some task", session="s1")
    routing.route(project, "refactor the auth flow and update tests", session="s1")
    executions.finish(project, run.id, outcome="ok", session="s1")

    later = routing.route(project, "fix the typo", session="s1")
    assert not later.reused and later.model == "haiku"


def test_outcomes_join_a_decision_to_its_run(project):
    run = executions.start(project, "some task", session="s1")
    decision = routing.route(project, "refactor the auth flow and update tests", session="s1")
    executions.finish(project, run.id, outcome="ok", session="s1")
    routing.route(project, "fix the typo")  # outside any run: nothing to join

    assert trace.outcomes(project) == [(decision.task_type, decision.level, decision.model,
                                        decision.effort, "ok")]
    [(key, counts)] = trace.stats(project)
    assert counts["ok"] == 1


def test_trace_lines_are_json_with_the_documented_fields(project):
    routing.route(project, "fix the typo", session="s9")
    line = json.loads(trace.path_for(project).read_text(encoding="utf-8").splitlines()[0])
    assert set(line) == {"at", "session", "execution", "work_item", "task_hash", "type", "level",
                         "model", "effort", "reason", "confidence", "override_source", "reused",
                         "score", "factors", "effort_in_use"}


def test_the_trace_keeps_the_numbers_and_the_effort_in_use(project, monkeypatch):
    monkeypatch.setenv("CLAUDE_EFFORT", "xhigh")
    decision = routing.route(project, "refactor the auth flow and update tests")
    [line] = trace.load(project)
    assert line["score"] == decision.score
    assert set(line["factors"]) == {name for name, _ in routing.score.WEIGHTS}
    assert line["effort_in_use"] == "xhigh"
    monkeypatch.delenv("CLAUDE_EFFORT")
    monkeypatch.setenv("EOS_EFFORT", "Low")
    routing.route(project, "fix the typo")
    assert trace.load(project)[-1]["effort_in_use"] == "low"
