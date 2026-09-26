"""The policy: cheapest sufficient model, clamped effort, explicit choices kept (ADR-025).

Each test is named for the requirement it proves. The sweep at the end is the
guarantee the whole design leans on: whatever the level, the model and the
requested effort, the pair that comes back is one the registry says exists.
"""
import dataclasses

import pytest

import core.routing as routing
from core.routing import policy, registry
from core.routing.defaults import DEFAULT_MODELS
from core.routing.types import EFFORTS, LEVELS, Complexity, ModelSpec, TaskClass


@pytest.fixture(autouse=True)
def _no_route_env(monkeypatch):
    monkeypatch.delenv(routing.MODEL_ENV, raising=False)
    monkeypatch.delenv(routing.EFFORT_ENV, raising=False)


def _project(tmp_path, table=""):
    root = tmp_path / "proj"
    (root / ".eos").mkdir(parents=True, exist_ok=True)
    if table:
        (root / ".eos" / "config.toml").write_text(table, encoding="utf-8")
    return root


def _route(tmp_path, task, table="", **kw):
    return routing.route(_project(tmp_path, table), task, record=False, **kw)


def _spec(model_id, reasoning, coding, cost, efforts=EFFORTS):
    return ModelSpec(id=model_id, provider="p", reasoning=reasoning, coding=coding,
                     context_window=0, cost=cost, efforts=tuple(efforts))


def test_a_trivial_task_gets_the_cheapest_model_and_no_effort_it_would_refuse(tmp_path):
    """Haiku 4.5 takes no effort setting; the decision carries none rather than
    one its provider would reject (claude plan 3.1)."""
    decision = _route(tmp_path, "fix the typo in the readme")
    assert (decision.level, decision.model, decision.effort) == ("LOW", "haiku", "")
    assert "takes no effort setting" in decision.reason


def test_a_model_given_efforts_in_config_gets_its_effort_back(tmp_path):
    table = '[model_routing.models.haiku]\nefforts = ["low", "medium"]\n'
    decision = _route(tmp_path, "fix the typo in the readme", table)
    assert (decision.model, decision.effort) == ("haiku", "low")


def test_a_normal_coding_task_gets_the_medium_tier(tmp_path):
    decision = _route(tmp_path, "add pagination to the orders endpoint and update the client")
    assert (decision.level, decision.model, decision.effort) == ("MEDIUM", "sonnet", "medium")


def test_debugging_is_routed_past_the_cheapest_model(tmp_path):
    decision = _route(tmp_path, "fix the crash when saving a draft")
    assert decision.task_type == "debugging"
    assert decision.model == "sonnet"


def test_an_architecture_task_is_at_least_high(tmp_path):
    decision = _route(tmp_path, "design the service boundaries for a new billing domain")
    assert decision.level in ("HIGH", "CRITICAL")
    assert decision.effort in ("high", "xhigh", "max")


def test_a_repository_wide_change_is_high_or_critical(tmp_path):
    decision = _route(tmp_path, "rename the logger field across the codebase")
    assert decision.task_type == "repository_wide_change"
    assert decision.level in ("HIGH", "CRITICAL")


def test_a_capability_mismatch_falls_back_to_the_strongest_and_says_so():
    weak = registry.Registry([_spec("small", 2, 2, 1.0), _spec("medium", 3, 3, 2.0)])
    decision = policy.decide(TaskClass("architecture", 0.9), Complexity(0.9, "CRITICAL", {}), weak)
    assert decision.model == "medium"
    assert "no registered model meets CRITICAL" in decision.reason


def test_a_type_adjustment_is_relaxed_before_giving_up():
    reg = registry.Registry([_spec("coder", 4, 4, 1.0)])
    decision = policy.decide(TaskClass("code_review", 0.9), Complexity(0.5, "HIGH", {}), reg)
    assert decision.model == "coder"
    assert "relaxed" in decision.reason


def test_an_effort_the_model_lacks_is_clamped_and_never_returned(tmp_path):
    table = '[model_routing.models.sonnet]\nefforts = ["low", "medium", "high"]\n'
    decision = _route(tmp_path, "refactor the auth flow and update tests", table,
                      model="sonnet", effort="max")
    assert decision.effort == "high"
    assert "clamped" in decision.reason


def test_the_clamp_walks_down_then_up():
    spec = _spec("x", 3, 3, 1.0, efforts=("medium", "high"))
    assert policy.clamp(spec, "high") == "high"
    assert policy.clamp(spec, "max") == "high"
    assert policy.clamp(spec, "low") == "medium"


def test_an_explicit_model_is_respected(tmp_path):
    decision = _route(tmp_path, "fix the typo in the readme", model="opus")
    assert decision.model == "opus"
    assert decision.override_source == "flag"
    assert decision.effort in ("low", "medium"), "the level's band still sets the effort"


def test_an_explicit_effort_is_respected_when_supported(tmp_path):
    decision = _route(tmp_path, "fix the typo in the readme", effort="high")
    # haiku takes no effort at all, so the cheapest model that honours "high" wins
    assert (decision.model, decision.effort) == ("sonnet", "high")


def test_an_explicit_effort_moves_the_model_only_when_it_must(tmp_path):
    table = '[model_routing.models.haiku]\nefforts = ["low", "medium"]\n'
    decision = _route(tmp_path, "fix the typo in the readme", table, effort="max")
    assert (decision.model, decision.effort) == ("sonnet", "max")


def test_model_auto_is_the_policy(tmp_path):
    assert _route(tmp_path, "fix the typo", model="auto").model == "haiku"


def test_effort_auto_is_the_policy(tmp_path):
    decision = _route(tmp_path, "add pagination to the orders endpoint and update the client", effort="auto")
    assert decision.effort == "medium" and decision.override_source == "auto"


def test_an_unknown_model_is_refused_with_the_available_ids(tmp_path):
    with pytest.raises(routing.OverrideError, match="haiku, sonnet, opus"):
        _route(tmp_path, "fix the typo", model="nope")


def test_an_unavailable_model_is_refused_not_substituted(tmp_path):
    table = '[model_routing.models.opus]\nstatus = "unavailable"\n'
    with pytest.raises(routing.OverrideError, match="unavailable"):
        _route(tmp_path, "fix the typo", table, model="opus")
    assert _route(tmp_path, "design the service boundaries for billing", table).model != "opus"


def test_an_unknown_effort_is_refused(tmp_path):
    with pytest.raises(routing.OverrideError, match="extreme"):
        _route(tmp_path, "fix the typo", effort="extreme")


def test_two_registries_with_different_effort_lists_clamp_differently():
    task, level = TaskClass("refactoring", 0.9), Complexity(0.45, "HIGH", {})
    full = registry.Registry(DEFAULT_MODELS)
    narrow = registry.Registry([dataclasses.replace(s, efforts=("low", "medium")) if s.id == "sonnet" else s
                                for s in DEFAULT_MODELS])
    assert policy.decide(task, level, full).effort == "high"
    assert policy.decide(task, level, narrow).effort == "medium"


def test_the_environment_overrides_config_and_a_flag_overrides_both(tmp_path, monkeypatch):
    table = '[model_routing]\ndefault_model = "sonnet"\n'
    assert _route(tmp_path, "fix the typo", table).override_source == "config"
    monkeypatch.setenv(routing.MODEL_ENV, "opus")
    env = _route(tmp_path, "fix the typo", table)
    assert (env.model, env.override_source) == ("opus", "env")
    flag = _route(tmp_path, "fix the typo", table, model="haiku")
    assert (flag.model, flag.override_source) == ("haiku", "flag")
    assert _route(tmp_path, "fix the typo", table, model="auto").override_source == "auto"


def test_identical_inputs_give_identical_decisions(tmp_path):
    root = _project(tmp_path)
    first = routing.route(root, "refactor the auth flow and update tests", record=False)
    for _ in range(50):
        assert routing.route(root, "refactor the auth flow and update tests", record=False) == first


def test_the_decision_carries_a_hash_not_the_task(tmp_path):
    decision = _route(tmp_path, "refactor the zanzibar adapter")
    assert "zanzibar" not in repr(decision.to_dict()["task_hash"])
    assert len(decision.task_hash) == 8


def test_the_history_seam_changes_nothing_today(tmp_path):
    decision = _route(tmp_path, "fix the typo")
    assert policy.adjust_for_history(decision, tmp_path) is decision


MIXED = registry.Registry([
    _spec("tiny", 1, 2, 0.5, efforts=("low",)),
    _spec("mid", 3, 4, 1.0, efforts=("medium", "high")),
    _spec("big", 5, 5, 4.0, efforts=("high", "xhigh")),
    _spec("max-only", 5, 5, 9.0, efforts=("max",)),
])


@pytest.mark.parametrize("reg", [registry.Registry(DEFAULT_MODELS), MIXED], ids=["defaults", "mixed"])
@pytest.mark.parametrize("level", LEVELS)
@pytest.mark.parametrize("effort", ("auto",) + EFFORTS)
def test_every_returned_pair_exists_in_the_registry(reg, level, effort):
    task = TaskClass("normal_implementation", 0.5)
    complexity = Complexity(0.5, level, {})
    for model in ["auto"] + [spec.id for spec in reg]:
        decision = policy.decide(task, complexity, reg, model=model, effort=effort)
        spec = reg.get(decision.model)
        assert spec is not None and spec.available
        assert decision.effort in spec.efforts or (not spec.efforts and decision.effort == ""), \
            (model, effort, decision)
