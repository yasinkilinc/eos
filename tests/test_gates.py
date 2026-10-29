"""`core.gates`: the ledger's answers to "is this action backed by evidence?"."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from core import decisions, executions, gates

REPO = Path(__file__).resolve().parents[1]
EOS = [sys.executable, str(REPO / "core" / "eos.py")]


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    (root / ".eos").mkdir(parents=True)
    monkeypatch.setenv("EOS_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("EOS_EXECUTION", raising=False)
    monkeypatch.delenv("EOS_EXECUTION_LEDGER", raising=False)
    return root


def _tool(project, session, *, kind="ran", tool="gitx", source="wrapper", **fields):
    return executions.event(project, kind=kind, tool=tool, source=source, session=session, **fields)


def _rules(project, session, action, **kw):
    return [r.rule for r in gates.check(project, session=session, action=action, **kw)]


def _evidenced(project, session, claim="the branch is clean"):
    return decisions.record(project, claim, [_tool(project, session).id], session=session)


def _loaded(records, entry):
    return decisions.event_index(records)[entry.id][1]


def _config(project, text):
    (project / ".eos" / "config.toml").write_text(text, encoding="utf-8")


def test_no_open_run_fires_for_every_action(project):
    for action in gates.ACTIONS:
        assert _rules(project, "s1", action) == ["no-open-run"]
    refusal = gates.check(project, session="s1", action="commit")[0]
    assert refusal.fix == 'eos run start . --title "…" --work <id>' and refusal.ids == ()


def test_no_open_run_does_not_fire_with_a_run(project):
    executions.start(project, "Work", session="s1")
    assert "no-open-run" not in _rules(project, "s1", "edit-test")


def test_nothing_looked_at_fires_then_clears(project):
    executions.start(project, "Work", session="s1")
    assert _rules(project, "s1", "edit-test") == ["nothing-looked-at"]
    _evidenced(project, "s1")
    assert _rules(project, "s1", "edit-test") == []


def test_a_resolved_decision_counts_as_looked_at(project):
    executions.start(project, "Work", session="s1")
    open_one = decisions.record(project, "the branch is clean", [], session="s1")
    decisions.resolve(project, open_one.id, [_tool(project, "s1").id], session="s1")
    assert _rules(project, "s1", "edit-test") == []


def test_an_assumption_alone_is_not_looked_at(project):
    executions.start(project, "Work", session="s1")
    decisions.record(project, "the branch is clean", [], session="s1")
    assert "nothing-looked-at" in _rules(project, "s1", "edit-test")


def test_open_assumption_fires_with_ids_and_checks(project):
    executions.start(project, "Work", session="s1")
    _evidenced(project, "s1")
    pending = decisions.record(project, "env1 has the row", [], check="automation/db.sh pg env1 crm",
                               session="s1")

    found = gates.check(project, session="s1", action="commit")

    assert [r.rule for r in found] == ["open-assumption"]
    assert found[0].ids == (pending.id,) and "automation/db.sh pg env1 crm" in found[0].detail
    assert pending.id in found[0].fix and "--resolve" in found[0].fix
    assert "open-assumption" not in _rules(project, "s1", "edit-test")


def test_open_assumption_clears_when_resolved(project):
    executions.start(project, "Work", session="s1")
    pending = decisions.record(project, "env1 has the row", [], session="s1")
    proof = _tool(project, "s1")
    decisions.resolve(project, pending.id, [proof.id], session="s1")
    assert _rules(project, "s1", "push") == []


@pytest.mark.parametrize("action", ["confirm", "commit", "push"])
def test_confirm_commit_push_check_the_three_run_rules(project, action):
    executions.start(project, "Work", session="s1")
    decisions.record(project, "env1 has the row", [], session="s1")
    assert _rules(project, "s1", action) == ["open-assumption", "nothing-looked-at"]


def test_scope_is_the_work_item_across_two_runs(project):
    executions.start(project, "A", session="sa", work_item="W1")
    pending = decisions.record(project, "env1 has the row", [], session="sa")
    executions.start(project, "B", session="sb", work_item="W1")
    _tool(project, "sb", tool="mvn", exit_code=1)

    found = gates.check(project, session="sb", action="edit-main")

    assert "open-assumption" in [r.rule for r in found]
    assert [r.ids for r in found if r.rule == "open-assumption"] == [(pending.id,)]


def test_another_work_item_is_out_of_scope(project):
    executions.start(project, "A", session="sa", work_item="W1")
    decisions.record(project, "env1 has the row", [], session="sa")
    executions.start(project, "B", session="sb", work_item="W2")
    assert "open-assumption" not in _rules(project, "sb", "commit")


def test_without_a_work_item_the_scope_is_the_run(project):
    executions.start(project, "A", session="sa")
    decisions.record(project, "env1 has the row", [], session="sa")
    executions.start(project, "B", session="sb")
    assert "open-assumption" not in _rules(project, "sb", "commit")


def test_red_first_fires_on_edit_main_without_a_failing_run(project):
    executions.start(project, "Work", session="s1")
    _evidenced(project, "s1")
    found = gates.check(project, session="s1", action="edit-main")
    assert [r.rule for r in found] == ["red-first"]
    assert "mvn.sh test" in found[0].fix and "apitest.sh run env1" in found[0].fix


def test_red_first_is_only_for_edit_main(project):
    executions.start(project, "Work", session="s1")
    _evidenced(project, "s1")
    for action in ("edit-test", "edit-other", "commit"):
        assert "red-first" not in _rules(project, "s1", action)


@pytest.mark.parametrize("tool", ["mvn", "apitest"])
def test_a_failing_run_clears_red_first(project, tool):
    executions.start(project, "Work", session="s1")
    _evidenced(project, "s1")
    _tool(project, "s1", tool=tool, exit_code=1)
    assert _rules(project, "s1", "edit-main") == []


def test_a_passing_or_other_tool_run_does_not_clear_red_first(project):
    executions.start(project, "Work", session="s1")
    _evidenced(project, "s1")
    _tool(project, "s1", tool="mvn", exit_code=0)
    _tool(project, "s1", tool="gitx", exit_code=1)
    _tool(project, "s1", tool="apitest", kind="read", exit_code=1)
    assert "red-first" in _rules(project, "s1", "edit-main")


def test_a_subagent_recorded_failure_counts_for_red_first(project):
    executions.start(project, "Work", session="s1")
    _evidenced(project, "s1")
    _tool(project, "s1", tool="mvn", exit_code=1, agent="general-purpose")
    assert "red-first" not in _rules(project, "s1", "edit-main")


def test_a_failure_in_an_earlier_run_of_the_work_item_counts(project):
    executions.start(project, "A", session="sa", work_item="W1")
    _tool(project, "sa", tool="mvn", exit_code=1)
    executions.start(project, "B", session="sb", work_item="W1")
    _evidenced(project, "sb")
    assert _rules(project, "sb", "edit-main") == []


def test_the_upgrade_agent_is_exempt_from_red_first(project):
    executions.start(project, "Work", session="s1")
    _evidenced(project, "s1")
    assert "red-first" not in _rules(project, "s1", "edit-main", tool="product-upgrade")
    assert "red-first" in _rules(project, "s1", "edit-main", tool="Explore")


def test_an_upgrade_procedure_is_exempt_from_red_first(project):
    executions.start(project, "Work", session="s1", procedure="upgrade-product-version")
    _evidenced(project, "s1")
    assert "red-first" not in _rules(project, "s1", "edit-main")


@pytest.mark.parametrize("title", ["Fix the cancel bug", "Hata düzeltme", "HATA DÜZELT", "duzeltme isi",
                                   "Düzeltilecek akış", "bug in billing"])
def test_root_cause_fires_for_a_fix_run_without_one(project, title):
    executions.start(project, title, session="s1")
    _evidenced(project, "s1", "env1 shows the row")
    found = gates.check(project, session="s1", action="env-write")
    assert [r.rule for r in found] == ["root-cause"]
    assert "kök neden:" in found[0].fix


@pytest.mark.parametrize("title", ["Add a column", "Debugging tools", "Prefix list", "Hatalı"])
def test_root_cause_ignores_other_titles(project, title):
    executions.start(project, title, session="s1")
    _evidenced(project, "s1")
    assert _rules(project, "s1", "env-write") == []


def test_root_cause_is_only_for_env_write(project):
    executions.start(project, "Fix it", session="s1")
    _evidenced(project, "s1")
    assert "root-cause" not in _rules(project, "s1", "commit")


@pytest.mark.parametrize("claim", ["root cause: the flag is null", "Kök Neden: bayrak null",
                                   "KOK NEDEN: bayrak null"])
def test_a_root_cause_decision_clears_it(project, claim):
    executions.start(project, "Fix it", session="s1")
    _evidenced(project, "s1", claim)
    assert _rules(project, "s1", "env-write") == []


def test_a_root_cause_that_is_only_an_assumption_does_not_clear_it(project):
    executions.start(project, "Fix it", session="s1")
    _evidenced(project, "s1")
    decisions.record(project, "root cause: the flag is null", [], session="s1")
    assert "root-cause" in _rules(project, "s1", "env-write")


def test_an_unknown_action_is_refused(project):
    executions.start(project, "Work", session="s1")
    with pytest.raises(ValueError):
        gates.check(project, session="s1", action="deploy")


def test_records_can_be_passed_in(project):
    executions.start(project, "Work", session="s1")
    _evidenced(project, "s1")
    records = decisions.all_records(project, "s1")
    assert gates.check(project, session="s1", action="edit-test", records=records) == []


def test_fresh_verification_needs_a_wrapper_pass_after_the_change(project):
    run = executions.start(project, "Work", session="s1")
    changed = _tool(project, "s1", kind="changed", tool="edit", source="hook")
    _tool(project, "s1", kind="verified", tool="subagent", exit_code=0)
    _tool(project, "s1", tool="mvn", exit_code=1)
    _tool(project, "s1", tool="mvn", exit_code=0, agent="reviewer", source="cli")
    records = decisions.all_records(project, "s1")
    assert gates.fresh_verification(records, run, after=_loaded(records, changed)) is None

    good = _tool(project, "s1", tool="mvn", exit_code=0)
    records = decisions.all_records(project, "s1")
    assert gates.fresh_verification(records, run, after=_loaded(records, changed)).id == good.id
    assert gates.fresh_verification(records, run, after=None).id == good.id


def test_fresh_verification_ignores_events_before_the_change(project):
    run = executions.start(project, "Work", session="s1")
    _tool(project, "s1", tool="mvn", exit_code=0)
    changed = _tool(project, "s1", kind="changed", tool="edit", source="hook")
    records = decisions.all_records(project, "s1")
    assert gates.fresh_verification(records, run, after=_loaded(records, changed)) is None


def test_fresh_verification_orders_by_ledger_position_inside_one_second(project, monkeypatch):
    from core.knowledge import evidence

    monkeypatch.setattr(evidence, "utc_now", lambda: "2026-10-29T12:00:05+00:00")
    run = executions.start(project, "Work", session="s1")
    _tool(project, "s1", tool="mvn", exit_code=0)
    changed = _tool(project, "s1", kind="changed", tool="edit", source="hook")
    records = decisions.all_records(project, "s1")
    assert gates.fresh_verification(records, run, after=_loaded(records, changed)) is None

    after = _tool(project, "s1", tool="mvn", exit_code=0)
    records = decisions.all_records(project, "s1")
    assert gates.fresh_verification(records, run, after=_loaded(records, changed)).id == after.id


def test_record_refusals_writes_one_event_per_rule_and_target(project):
    run = executions.start(project, "Work", session="s1")
    found = gates.check(project, session="s1", action="edit-main")
    gates.record_refusals(project, found, session="s1", action="edit-main", target="src/A.java")
    gates.record_refusals(project, found, session="s1", action="edit-main", target="src/A.java")
    gates.record_refusals(project, found, session="s1", action="edit-main", target="src/B.java")

    notes = [e for r in executions.load_path(executions.current(project, "s1")[1]) if r.id == run.id
             for e in r.events if e.tool == "gate"]

    assert {(e.ref, e.body.split(":")[0]) for e in notes} == {
        ("src/A.java", "nothing-looked-at"), ("src/A.java", "red-first"),
        ("src/B.java", "nothing-looked-at"), ("src/B.java", "red-first")}
    assert len(notes) == 4
    assert all(e.kind == "noted" and e.source == "hook" for e in notes)


def test_record_refusals_without_a_run_writes_nothing(project):
    found = gates.check(project, session="s1", action="commit")
    gates.record_refusals(project, found, session="s1", action="commit", target=None)
    assert not executions.path_for(project).exists()


def test_the_target_is_kept_repo_relative(project, tmp_path):
    repo = tmp_path / "svc"
    (repo / "src").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    executions.start(project, "Work", session="s1")
    found = gates.check(project, session="s1", action="edit-main")
    gates.record_refusals(project, found, session="s1", action="edit-main",
                          target=str(repo / "src" / "A.java"))
    gates.record_refusals(project, found, session="s1", action="edit-main",
                          target="/nonexistent-dir/x/B.java")

    events = [e for r in decisions.all_records(project, "s1") for e in r.events if e.tool == "gate"]

    assert {e.ref for e in events} == {"src/A.java", "B.java"}
    for entry in events:
        assert str(tmp_path) not in (entry.body or "") + (entry.ref or "")


def test_mode_defaults_and_reads_the_table(project, tmp_path):
    assert gates.MODES == ("off", "warn", "block")
    assert gates.mode(tmp_path / "nowhere") == "off"
    assert gates.mode(project) == "off"
    for value in ("warn", "block", "off"):
        _config(project, f'[gates]\nmode = "{value}"\n')
        assert gates.mode(project) == value


@pytest.mark.parametrize("text", ['[gates]\nmode = "bogus"\n', "[gates]\nmode = 3\n", "[hooks]\nx = 1\n",
                                  "not toml ["])
def test_an_unknown_mode_is_off(project, text):
    _config(project, text)
    assert gates.mode(project) == "off"


def _cli(args, project, tmp_path):
    env = {**os.environ, "EOS_STATE_DIR": str(tmp_path / "state")}
    env.pop("EOS_EXECUTION", None)
    env.pop("EOS_EXECUTION_LEDGER", None)
    return subprocess.run(EOS + args, capture_output=True, text=True, encoding="utf-8", env=env)


def test_the_cli_prints_json_and_exits_zero_with_mode_off_and_no_ledger(project, tmp_path):
    out = _cli(["gate", "check", str(project), "--session", "s1", "--action", "edit-main"],
               project, tmp_path)

    assert out.returncode == 0, out.stderr
    payload = json.loads(out.stdout)
    assert payload["mode"] == "off"
    assert [r["rule"] for r in payload["refusals"]] == ["no-open-run"]
    assert set(payload["refusals"][0]) == {"rule", "detail", "fix", "ids"}
    assert not executions.path_for(project).exists()


def test_the_cli_records_refusals_unless_off(project, tmp_path):
    _cli(["run", "start", str(project), "--title", "Work", "--session", "s1"], project, tmp_path)
    off = json.loads(_cli(["gate", "check", str(project), "--session", "s1", "--action", "edit-test",
                           "--file", "src/A.java"], project, tmp_path).stdout)
    _config(project, '[gates]\nmode = "warn"\n')
    warn = _cli(["gate", "check", str(project), "--session", "s1", "--action", "edit-test",
                 "--file", "src/A.java", "--tool", "Explore"], project, tmp_path)

    assert warn.returncode == 0, warn.stderr
    assert json.loads(warn.stdout)["mode"] == "warn" and off["mode"] == "off"
    os.environ["EOS_STATE_DIR"] = str(tmp_path / "state")
    try:
        events = [e for r in decisions.all_records(project, "s1") for e in r.events if e.tool == "gate"]
    finally:
        del os.environ["EOS_STATE_DIR"]
    assert [(e.ref, e.body.split(":")[0]) for e in events] == [("src/A.java", "nothing-looked-at")]


def test_the_cli_mode_command_prints_the_mode(project, tmp_path):
    _config(project, '[gates]\nmode = "block"\n')
    out = _cli(["gate", "mode", str(project)], project, tmp_path)
    assert out.returncode == 0 and out.stdout.strip() == "block"


def test_a_cli_written_failure_does_not_clear_red_first(project):
    executions.start(project, "Work", session="s1")
    _evidenced(project, "s1")
    _tool(project, "s1", tool="mvn", exit_code=1, source="cli")
    assert "red-first" in _rules(project, "s1", "edit-main")
    _tool(project, "s1", tool="mvn", exit_code=1, source="hook")
    assert "red-first" not in _rules(project, "s1", "edit-main")


@pytest.mark.parametrize("target", ["../../etc/x.java", "a b/C.java", "a=b/C.java", "src/'C'.java",
                                    'src/"C".java'])
def test_an_unsafe_relative_target_is_stored_as_its_basename(project, target):
    executions.start(project, "Work", session="s1")
    found = gates.check(project, session="s1", action="edit-main")
    gates.record_refusals(project, found, session="s1", action="edit-main", target=target)
    refs = {e.ref for r in decisions.all_records(project, "s1") for e in r.events if e.tool == "gate"}
    assert refs == {Path(target).name}


def test_the_cli_answers_json_when_the_check_raises(project, monkeypatch, capsys):
    import argparse

    from core.cli import gate

    def broken(*args, **kwargs):
        raise KeyError("ts")

    monkeypatch.setattr(gates, "check", broken)
    args = argparse.Namespace(gate_command="check", path=str(project), session="s1",
                              action="edit-test", file=None, tool=None)

    assert gate.cmd_gate(args) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert [json.loads(line) for line in lines] == [{"mode": "off", "refusals": [], "error": "KeyError"}]


@pytest.mark.parametrize("args", [["--action", "deploy"], []])
def test_the_cli_answers_json_for_an_unknown_or_missing_action(project, tmp_path, args):
    out = _cli(["gate", "check", str(project), "--session", "s1", *args], project, tmp_path)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout) == {"mode": "off", "refusals": [], "error": "unknown-action"}
