"""Routing extension points a project's config uses for its own language, and the
subagent hook's safety rules (ADR-025 addendum, subagent routing plan R1).

The engine stays language-agnostic: every non-English word below comes from
config in the test, never from the engine."""
import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

import core.routing as routing
from core import executions, hooks
from core.routing import classify, config, evaluate, policy, registry, score
from core.routing.types import LEVELS, TASK_TYPES, TaskClass, normalise

REPO = Path(__file__).resolve().parents[1]
EOS = [sys.executable, str(REPO / "core" / "eos.py")]


def _project(tmp_path, table: str = "") -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    assert subprocess.run(EOS + ["init", str(root), "--no-ai"], capture_output=True).returncode == 0
    if table:
        path = root / ".eos" / "config.toml"
        path.write_text(path.read_text(encoding="utf-8") + "\n" + table, encoding="utf-8")
    return root


# --- normalisation and stems -------------------------------------------------------


def test_the_dotted_capital_i_lowers_to_a_plain_i():
    assert normalise("İPTAL  İşlemi") == "iptal işlemi"
    assert normalise("Implement IT") == "implement it", "the dotless capital still lowers to i"


def test_a_stem_matches_every_word_it_starts_and_nothing_inside_a_word():
    assert classify.matches("hataları düzeltelim", "hata*")
    assert classify.matches("hata", "hata*")
    assert not classify.matches("mühata", "hata*")
    assert classify.matches("yeniden düzenleyelim", "yeniden düzenle*")


def test_built_in_tables_keep_their_english_inflections():
    assert classify.matches("fixed the crash", "fix")
    assert not classify.matches("prefix", "fix")


@pytest.mark.parametrize("table, message", [
    ('[model_routing.keywords]\ndebugging = ["ha*"]\n', "shorter than"),
    ('[model_routing.keywords]\ndebugging = ["h*ta"]\n', "may only end"),
    ('[model_routing.rules]\nopeners = ["neden"]\n', "unknown rule list"),
    ('[model_routing.factors]\nmoney = ["para*"]\n', "unknown factor"),
    ('[model_routing]\nhook_min_confidence = 1.5\n', "from 0 to 1"),
])
def test_a_malformed_table_is_refused_with_its_key(tmp_path, table, message):
    with pytest.raises(ValueError, match=message):
        config.load(_project(tmp_path, table))


# --- config extends, never replaces ---------------------------------------------------


def test_config_keywords_classify_another_language(tmp_path):
    root = _project(tmp_path, '[model_routing.keywords]\ndebugging = ["hata*", "düzelt*"]\n')
    cfg = config.load(root)
    assert classify.classify("ödeme ekranındaki hataları düzeltelim", cfg.keywords, cfg.rules).type == "debugging"
    # English still classifies as it did
    assert classify.classify("fix the crash when saving a draft", cfg.keywords, cfg.rules).type == "debugging"


def test_rule_lists_extend_the_built_in_rules(tmp_path):
    """Rules act on a task some keyword classified, as the built-in ones always
    have: a prompt no table recognises stays normal_implementation."""
    root = _project(tmp_path, '[model_routing.keywords]\nnormal_implementation = ["güncelle*"]\n'
                              'debugging = ["kırmızı"]\n'
                              '[model_routing.rules]\nrepository_wide = ["tüm servislerde"]\n'
                              'question_openers = ["neden"]\n')
    cfg = config.load(root)
    assert classify.classify("tüm servislerde logback güncelle", cfg.keywords, cfg.rules).type \
        == "repository_wide_change"
    assert classify.classify("neden kırmızı", cfg.keywords, cfg.rules).type == "investigation"
    assert classify.classify("why does the build fail", cfg.keywords, cfg.rules).type == "investigation"
    assert classify.classify("tüm servislerde bak", {}, {}).type == "normal_implementation"


def test_factor_words_extend_the_built_in_factors():
    task = TaskClass("normal_implementation", 0.9, ())
    plain = score.score("ödeme adımını ekle", task)
    extended = score.score("ödeme adımını ekle", task, factor_words={"risk": ("ödeme*",)})
    assert plain.factors["failure_risk"] == 0.0 and extended.factors["failure_risk"] == 0.4
    assert score.score("payment step", task).factors["failure_risk"] == 0.4


def test_without_the_new_tables_every_decision_is_unchanged(tmp_path):
    corpus = REPO / "evals" / "routing" / "corpus.tsv"
    bare, extended = _project(tmp_path), tmp_path / "other"
    extended.mkdir()
    assert subprocess.run(EOS + ["init", str(extended), "--no-ai"], capture_output=True).returncode == 0
    cfg = extended / ".eos" / "config.toml"
    cfg.write_text(cfg.read_text() + '\n[model_routing.rules]\nquestion_openers = ["zzqx"]\n', encoding="utf-8")
    for row in evaluate.load(corpus):
        a = routing.route(bare, row["prompt"], record=False, fresh=True)
        b = routing.route(extended, row["prompt"], record=False, fresh=True)
        assert (a.task_type, a.level, a.model, a.effort) == (b.task_type, b.level, b.model, b.effort), row


# --- the safety invariant ---------------------------------------------------------------


def test_critical_never_lands_on_the_cheapest_model_by_policy():
    models = registry.from_entries(registry.DEFAULT_MODELS, {})
    for task_type in TASK_TYPES:
        for effort in ("auto", "low", "max"):
            decision = policy.decide(TaskClass(task_type, 0.9, ()), score.Complexity(0.9, "CRITICAL", {}),
                                     models, effort=effort, effort_source="flag" if effort != "auto" else "auto")
            assert decision.model != "haiku" and policy.meets(models.get(decision.model), "CRITICAL", task_type)


def test_an_override_below_the_requirement_is_never_applied(tmp_path):
    root = _project(tmp_path, '[model_routing]\nhook = true\nhook_dry_run = false\ndefault_model = "haiku"\n')
    cfg = config.load(root)
    decision = routing.route(root, "design the multi tenant architecture across every service",
                             record=False, fresh=True)
    assert decision.model == "haiku" and decision.level in ("HIGH", "CRITICAL")
    assert "below what" in routing.withheld(decision, cfg, registry.load(root, cfg))


def test_withheld_rules_in_order(tmp_path):
    root = _project(tmp_path, '[model_routing]\nhook = true\nhook_dry_run = false\nhook_min_confidence = 0.6\n')
    cfg = config.load(root)
    models = registry.load(root, cfg)
    decision = routing.route(root, "fix the typo in the readme", record=False, fresh=True)
    low = decision.__class__(**{**decision.to_dict(), "confidence": 0.59, "alternatives": ()})
    high = decision.__class__(**{**decision.to_dict(), "confidence": 0.6, "alternatives": ()})
    assert routing.withheld(low, cfg, models).startswith("confidence 0.59 below 0.60")
    assert routing.withheld(high, cfg, models) is None
    dry = config.parse({"hook": True, "hook_min_confidence": 0.0})
    assert routing.withheld(high, dry, models) == "dry run"


# --- the hook, live -----------------------------------------------------------------------


def _pre_agent(root, monkeypatch, capsys, prompt, tool_use_id):
    payload = {"session_id": "s1", "cwd": str(root), "tool_name": "Agent", "tool_use_id": tool_use_id,
               "tool_input": {"prompt": prompt, "subagent_type": ""}}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    assert hooks.main(["pre-agent"]) == 0
    return capsys.readouterr().out


def test_below_the_floor_the_call_goes_ahead_untouched_and_is_recorded(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("EOS_STATE_DIR", str(tmp_path / "state"))
    root = _project(tmp_path, '[model_routing]\nhook = true\nhook_dry_run = false\nhook_min_confidence = 0.99\n')
    executions.start(root, "a run", session="s1")
    assert _pre_agent(root, monkeypatch, capsys, "fix the typo in the readme", "t1") == ""
    [event] = [e for e in executions.load(root)[0].events if e.kind == "decided"]
    assert event.body.startswith("not applied, confidence") and event.tool_use_id == "route:t1"
    assert event.ref.startswith("route:") and "fix the typo" not in executions.path_for(root).read_text()


def test_at_or_above_the_floor_the_model_is_applied_and_recorded(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("EOS_STATE_DIR", str(tmp_path / "state"))
    root = _project(tmp_path, '[model_routing]\nhook = true\nhook_dry_run = false\nhook_min_confidence = 0.0\n')
    executions.start(root, "a run", session="s1")
    out = _pre_agent(root, monkeypatch, capsys, "design the service boundary for billing", "t2")
    assert json.loads(out)["hookSpecificOutput"]["updatedInput"]["model"] in hooks.SUBAGENT_MODELS
    [event] = [e for e in executions.load(root)[0].events if e.kind == "decided"]
    assert event.body.startswith("applied:") and "confidence" in event.body


# --- evaluation: splits and the gate -----------------------------------------------------


def _corpus(tmp_path, rows):
    path = tmp_path / "corpus.tsv"
    path.write_text("prompt\ttype\tlevel\tmodel\tsplit\n" + "".join("\t".join(r) + "\n" for r in rows),
                    encoding="utf-8")
    return path


def test_the_same_prompt_in_dev_and_test_is_refused(tmp_path):
    path = _corpus(tmp_path, [("fix it", "", "", "haiku", "dev"), ("Fix  it", "", "", "haiku", "test")])
    with pytest.raises(ValueError, match="both dev and test"):
        evaluate.load(path)


def test_test_split_prints_no_rows_and_is_recorded(tmp_path):
    root = _project(tmp_path, "[model_routing]\n")
    path = _corpus(tmp_path, [("fix the typo in the readme", "trivial_edit", "LOW", "haiku", "dev"),
                              ("design the billing architecture", "architecture", "CRITICAL", "opus", "test")])
    dev = subprocess.run(EOS + ["route", str(root), "--eval", str(path), "--split", "dev", "--json"],
                         capture_output=True, text=True)
    test = subprocess.run(EOS + ["route", str(root), "--eval", str(path), "--split", "test", "--json"],
                          capture_output=True, text=True)
    dev_result, test_result = json.loads(dev.stdout), json.loads(test.stdout)
    assert dev_result["prompts"] == 1 and test_result["prompts"] == 1
    assert test_result["misses"] == [] and test_result["suggested_words"] == []
    assert (root / ".eos" / "data" / "routing-eval.jsonl").read_text().count("\n") == 1
    assert {dev.returncode, test.returncode} <= {0, 1}
    assert test.returncode == (0 if test_result["gate"]["pass"] else 1)


def test_the_test_split_is_refused_while_a_label_is_pending_review(tmp_path):
    root = _project(tmp_path, "[model_routing]\n")
    path = tmp_path / "corpus.tsv"
    path.write_text("prompt\tmodel\tsplit\treview\nfix the typo\thaiku\tdev\tpending\n"
                    "design the billing architecture\topus\ttest\tpending\n", encoding="utf-8")
    assert evaluate.run(root, evaluate.load(path), split="dev")["prompts"] == 1
    with pytest.raises(ValueError, match="review=pending"):
        evaluate.run(root, evaluate.load(path), split="test")
    test = subprocess.run(EOS + ["route", str(root), "--eval", str(path), "--split", "test"],
                          capture_output=True, text=True)
    assert test.returncode == 2 and not (root / ".eos" / "data" / "routing-eval.jsonl").exists()


def test_gate_counts_under_routing_and_critical_on_the_cheapest(tmp_path):
    root = _project(tmp_path, '[model_routing]\ndefault_model = "haiku"\n')
    path = _corpus(tmp_path, [("design the billing architecture", "", "CRITICAL", "opus", ""),
                              ("plan the migration", "", "HIGH", "sonnet|opus", "")])
    result = evaluate.run(root, evaluate.load(path))
    assert result["critical_to_cheapest"]["count"] == 1
    assert result["under_routing"] == {"rate": 1.0, "count": 2, "high_or_critical": 2}
    assert result["gate"]["pass"] is False and result["gate"]["thresholds"]["model_accuracy_min"] == 0.85
