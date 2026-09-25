"""The registry is where model names live, so the policy never has to (ADR-025).

What these hold: the defaults are valid on their own, config corrects them by
id without replacing what it does not name, nothing unvalidated gets in, and
the question the policy asks -- "who meets this, cheapest first?" -- has one
deterministic answer.
"""
import pytest

from core.routing import config, registry
from core.routing.defaults import DEFAULT_MODELS
from core.routing.types import EFFORTS, TASK_TYPES, ModelSpec, task_hash


def _project(tmp_path, table: str = ""):
    root = tmp_path / "proj"
    (root / ".eos").mkdir(parents=True)
    if table:
        (root / ".eos" / "config.toml").write_text(table, encoding="utf-8")
    return root


def test_the_defaults_load_and_validate_without_any_config(tmp_path):
    reg = registry.load(_project(tmp_path))

    assert [spec.id for spec in reg] == [spec.id for spec in DEFAULT_MODELS]
    for spec in reg:
        assert spec.efforts == EFFORTS
        assert all(t in TASK_TYPES for t in spec.task_types)


def test_a_missing_table_is_the_defaults_and_says_it_was_not_configured(tmp_path):
    cfg = config.load(_project(tmp_path))

    assert cfg.configured is False
    assert cfg.enabled is True and cfg.default_model == "auto" and cfg.default_effort == "auto"


def test_a_config_entry_adds_a_model(tmp_path):
    root = _project(tmp_path, """
[model_routing.models.local-coder]
provider = "openai-compatible"
reasoning = 3
coding = 4
cost = 0.5
efforts = ["low", "medium", "high"]
""")
    spec = registry.load(root).get("local-coder")

    assert spec is not None
    assert spec.efforts == ("low", "medium", "high")
    assert spec.cost == 0.5 and spec.status == "available"


def test_overriding_one_field_of_a_default_changes_only_that_field(tmp_path):
    root = _project(tmp_path, """
[model_routing.models.sonnet]
efforts = ["high", "low", "medium"]
""")
    before = next(spec for spec in DEFAULT_MODELS if spec.id == "sonnet")
    after = registry.load(root).get("sonnet")

    assert after.efforts == ("low", "medium", "high"), "stored ascending whatever order config used"
    assert (after.reasoning, after.coding, after.cost, after.provider) == \
           (before.reasoning, before.coding, before.cost, before.provider)


def test_an_unknown_effort_in_config_is_refused_with_its_name(tmp_path):
    root = _project(tmp_path, """
[model_routing.models.sonnet]
efforts = ["low", "hgih"]
""")
    with pytest.raises(ValueError, match="hgih"):
        registry.load(root)


def test_an_unknown_key_in_a_model_entry_is_refused_with_its_name(tmp_path):
    root = _project(tmp_path, """
[model_routing.models.sonnet]
reasonning = 5
""")
    with pytest.raises(ValueError, match="reasonning"):
        registry.load(root)


def test_a_new_model_must_state_what_it_can_do(tmp_path):
    root = _project(tmp_path, """
[model_routing.models.mystery]
provider = "somewhere"
""")
    with pytest.raises(ValueError, match="reasoning"):
        registry.load(root)


def test_capabilities_outside_one_to_five_are_refused():
    bad = ModelSpec(id="x", provider="p", reasoning=6, coding=3, context_window=0,
                    cost=1.0, efforts=("low",))
    with pytest.raises(ValueError, match="reasoning"):
        registry.Registry([bad])


def test_a_name_used_twice_after_aliases_is_refused():
    one = ModelSpec(id="a", provider="p", reasoning=2, coding=2, context_window=0,
                    cost=1.0, efforts=("low",), aliases=("fast",))
    two = ModelSpec(id="b", provider="p", reasoning=2, coding=2, context_window=0,
                    cost=2.0, efforts=("low",), aliases=("FAST",))
    with pytest.raises(ValueError, match="(?i)fast"):
        registry.Registry([one, two])


def test_alias_lookup_is_case_insensitive(tmp_path):
    root = _project(tmp_path, """
[model_routing.models.sonnet]
aliases = ["Balanced"]
""")
    reg = registry.load(root)

    assert reg.get("balanced").id == "sonnet"
    assert reg.get("SONNET").id == "sonnet"
    assert reg.get("nope") is None


def test_candidates_are_ordered_by_cost_then_reasoning():
    specs = [
        ModelSpec(id="dear", provider="p", reasoning=5, coding=5, context_window=0, cost=9.0, efforts=EFFORTS),
        ModelSpec(id="cheap-weak", provider="p", reasoning=3, coding=4, context_window=0, cost=1.0, efforts=EFFORTS),
        ModelSpec(id="cheap-strong", provider="p", reasoning=4, coding=4, context_window=0, cost=1.0, efforts=EFFORTS),
    ]
    reg = registry.Registry(specs)

    assert [s.id for s in reg.candidates(min_reasoning=3, min_coding=3)] == ["cheap-strong", "cheap-weak", "dear"]
    assert [s.id for s in reg.candidates(min_reasoning=5, min_coding=3)] == ["dear"]


def test_candidates_can_require_an_effort():
    specs = [
        ModelSpec(id="no-max", provider="p", reasoning=4, coding=4, context_window=0, cost=1.0,
                  efforts=("low", "medium", "high")),
        ModelSpec(id="with-max", provider="p", reasoning=4, coding=4, context_window=0, cost=2.0,
                  efforts=EFFORTS),
    ]
    reg = registry.Registry(specs)

    assert [s.id for s in reg.candidates(min_reasoning=1, min_coding=1, effort="max")] == ["with-max"]


def test_an_unavailable_model_is_never_a_candidate(tmp_path):
    root = _project(tmp_path, """
[model_routing.models.haiku]
status = "unavailable"
""")
    reg = registry.load(root)

    assert "haiku" not in reg.ids()
    assert all(s.id != "haiku" for s in reg.candidates(min_reasoning=1, min_coding=1))
    assert reg.get("haiku") is not None, "still known, so an explicit request can be refused by name"


def test_a_malformed_routing_table_is_refused_with_the_key_named(tmp_path):
    for table, word in (('[model_routing]\ndefault_effort = "extreme"\n', "default_effort"),
                        ('[model_routing]\nbrief = "sometimes"\n', "brief"),
                        ('[model_routing]\nenable = true\n', "enable"),
                        ('[model_routing.keywords]\nfixing = ["x"]\n', "fixing")):
        root = _project(tmp_path / word, table)
        with pytest.raises(ValueError, match=word):
            config.load(root)


def test_keywords_in_config_are_kept_lower_case_by_type(tmp_path):
    root = _project(tmp_path, '[model_routing.keywords]\ndebugging = ["Hata", "çöküyor"]\n')

    assert config.load(root).keywords == {"debugging": ("hata", "çöküyor")}


def test_the_task_hash_ignores_case_and_spacing_and_is_not_the_text():
    assert task_hash("Fix  the Typo") == task_hash("fix the typo")
    assert task_hash("fix the typo") != task_hash("fix the bug")
    assert len(task_hash("anything")) == 8
