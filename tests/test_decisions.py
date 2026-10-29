import datetime

import pytest

from core import decisions, executions


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    (root / ".eos").mkdir(parents=True)
    monkeypatch.setenv("EOS_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("EOS_EXECUTION", raising=False)
    monkeypatch.delenv("EOS_EXECUTION_LEDGER", raising=False)
    return root


def _tool_event(project, session, *, kind="ran", tool="gitx", source="wrapper", **fields):
    return executions.event(project, kind=kind, tool=tool, source=source, session=session, **fields)


def _shifted(entry, **delta):
    at = datetime.datetime.fromisoformat(entry.at)
    return (at + datetime.timedelta(**delta)).isoformat(timespec="seconds")


def test_claim_with_wrapper_evidence_is_evidenced(project):
    executions.start(project, "Work", session="s1", work_item="W1")
    proof = _tool_event(project, "s1")

    found = decisions.record(project, "the branch is clean", [proof.id], session="s1")

    assert found.state == "evidenced" and found.reasons == ()
    assert found.id.startswith("d-") and len(found.id) == 10
    assert found.evidence == (proof.id,) and found.work_item == "W1"


def test_claim_without_evidence_is_assumption(project):
    executions.start(project, "Work", session="s1")

    found = decisions.record(project, "the branch is clean", [], session="s1")

    assert found.state == "assumption" and found.reasons == ("no-evidence",)


def test_unknown_id_is_refused(project):
    executions.start(project, "Work", session="s1")

    found = decisions.record(project, "the branch is clean", ["e-00000000"], session="s1")

    assert found.state == "assumption" and "unknown-id e-00000000" in found.reasons


def test_evidence_from_another_work_item_is_refused(project):
    executions.start(project, "A", session="sa", work_item="W1")
    proof = _tool_event(project, "sa")
    executions.start(project, "B", session="sb", work_item="W2")

    found = decisions.record(project, "the branch is clean", [proof.id], session="sb")

    assert found.state == "assumption" and f"other-work-item {proof.id}" in found.reasons


def test_evidence_from_an_earlier_run_of_the_same_item_counts(project):
    executions.start(project, "A", session="s1", work_item="W1")
    proof = _tool_event(project, "s1")
    executions.start(project, "B", session="s1", work_item="W1")

    found = decisions.record(project, "the branch is clean", [proof.id], session="s1")

    assert found.state == "evidenced" and found.reasons == ()


def test_cli_written_event_is_not_evidence(project):
    executions.start(project, "Work", session="s1")
    claimed = _tool_event(project, "s1", source="cli")

    found = decisions.record(project, "the branch is clean", [claimed.id], session="s1")

    assert f"not-recorded-by-a-tool {claimed.id}" in found.reasons and found.state == "assumption"


def test_subagent_citation_count_is_not_evidence(project):
    executions.start(project, "Work", session="s1")
    report = _tool_event(project, "s1", kind="verified", tool="subagent", source="hook")

    found = decisions.record(project, "the branch is clean", [report.id], session="s1")

    assert f"subagent-report {report.id}" in found.reasons and found.state == "assumption"


def test_subagent_tool_call_is_evidence(project):
    executions.start(project, "Work", session="s1")
    call = _tool_event(project, "s1", kind="ran", tool="bash", source="hook", agent="atlas")

    found = decisions.record(project, "the branch is clean", [call.id], session="s1")

    assert found.state == "evidenced" and found.reasons == ()


def test_live_evidence_older_than_24h_is_stale(project):
    executions.start(project, "Work", session="s1")
    live = _tool_event(project, "s1", tool="db")
    static = _tool_event(project, "s1", tool="gitx")

    old_live = decisions.record(project, "the row exists", [live.id], session="s1",
                                now=_shifted(live, hours=25))
    fresh_live = decisions.record(project, "the row exists", [live.id], session="s1",
                                  now=_shifted(live, hours=23))
    old_static = decisions.record(project, "the tree is clean", [static.id], session="s1",
                                  now=_shifted(static, days=30))

    assert f"stale {live.id}" in old_live.reasons and old_live.state == "assumption"
    assert fresh_live.state == "evidenced"
    assert old_static.state == "evidenced"


def test_failed_call_is_not_evidence(project):
    executions.start(project, "Work", session="s1")
    failed = _tool_event(project, "s1", kind="called", tool="bssapi", exit_code=1)
    red_test = _tool_event(project, "s1", kind="ran", tool="mvn", exit_code=1)

    refused = decisions.record(project, "the api answers", [failed.id], session="s1")
    accepted = decisions.record(project, "the test is red", [red_test.id], session="s1")

    assert f"failed-call {failed.id}" in refused.reasons and refused.state == "assumption"
    assert accepted.state == "evidenced"


@pytest.mark.parametrize("claim", ["MUHTEMELEN temiz", "olmalı temiz", "Galiba temiz",
                                   "it is probably clean", "it should be clean", "sanırım temiz"])
def test_hedge_words_force_assumption(project, claim):
    executions.start(project, "Work", session="s1")
    proof = _tool_event(project, "s1")

    found = decisions.record(project, claim, [proof.id], session="s1")

    assert found.state == "assumption" and "hedged" in found.reasons


def test_a_word_containing_a_hedge_is_not_hedged(project):
    executions.start(project, "Work", session="s1")
    proof = _tool_event(project, "s1")

    found = decisions.record(project, "the unlikelyhood list is empty", [proof.id], session="s1")

    assert found.state == "evidenced"


def test_resolve_closes_an_assumption(project):
    executions.start(project, "Work", session="s1", work_item="W1")
    assumed = decisions.record(project, "the branch is clean", [], session="s1")
    assert [d.id for d in decisions.open_assumptions(project, work_item="W1", execution=None)] == [assumed.id]
    proof = _tool_event(project, "s1")

    closed = decisions.resolve(project, assumed.id, [proof.id], session="s1")

    assert closed.state == "resolved" and closed.evidence == (proof.id,)
    assert decisions.open_assumptions(project, work_item="W1", execution=None) == []
    scoped = decisions.for_scope(project, work_item="W1", execution=None)
    assert [(d.id, d.state) for d in scoped] == [(assumed.id, "resolved")]


def test_resolve_without_valid_evidence_leaves_it_open(project):
    run = executions.start(project, "Work", session="s1")
    assumed = decisions.record(project, "the branch is clean", [], session="s1")

    refused = decisions.resolve(project, assumed.id, ["e-00000000"], session="s1")

    assert refused.state == "assumption" and "unknown-id e-00000000" in refused.reasons
    assert len(decisions.open_assumptions(project, work_item=None, execution=run.id)) == 1


def test_approve_is_written_by_the_hook(project):
    run = executions.start(project, "Work", session="s1")
    assumed = decisions.record(project, "use the second table", [], session="s1")

    approved = decisions.approve(project, assumed.id, prompt_excerpt="evet, ikinci tablo", session="s1")

    assert approved.state == "approved"
    written = [e for e in executions.load(project)[0].events if e.ref == assumed.id]
    assert written[-1].source == "hook" and written[-1].claim_state == "approved"
    assert decisions.open_assumptions(project, work_item=None, execution=run.id) == []


def test_evidence_in_rotated_ledger_is_found(project):
    executions.start(project, "Work", session="s1", work_item="W1")
    proof = _tool_event(project, "s1")
    executions._rotate(executions.path_for(project))
    assert not executions.path_for(project).exists()

    found = decisions.record(project, "the branch is clean", [proof.id], session="s1")

    assert found.state == "evidenced" and found.reasons == ()


def test_claim_with_a_credential_is_refused(project):
    executions.start(project, "Work", session="s1")

    with pytest.raises(ValueError, match="credential"):
        decisions.record(project, "the password is password=hunter2hunter2hunter2", [], session="s1")


def test_refusal_reasons_are_kept_on_the_ledger(project):
    executions.start(project, "Work", session="s1", work_item="W1")
    recorded = decisions.record(project, "the branch is clean", ["e-00000000"], session="s1")

    read_back = decisions.for_scope(project, work_item="W1", execution=None)

    assert recorded.reasons == ("unknown-id e-00000000",)
    assert [d.reasons for d in read_back] == [("unknown-id e-00000000",)]


def test_a_resolved_decision_read_back_has_no_reasons(project):
    executions.start(project, "Work", session="s1", work_item="W1")
    assumed = decisions.record(project, "the branch is clean", [], session="s1")
    proof = _tool_event(project, "s1")
    decisions.resolve(project, assumed.id, [proof.id], session="s1")

    read_back = decisions.for_scope(project, work_item="W1", execution=None)

    assert [(d.state, d.reasons) for d in read_back] == [("resolved", ())]


@pytest.mark.parametrize("bad", ["password=hunter2hunter2hunter2", "select 1", "free text"])
def test_malformed_evidence_is_refused_before_anything_is_written(project, bad):
    executions.start(project, "Work", session="s1")
    before = executions.path_for(project).read_text()

    with pytest.raises(ValueError):
        decisions.record(project, "the branch is clean", [bad], session="s1")

    assert executions.path_for(project).read_text() == before


def test_a_legacy_shaped_evidence_id_is_well_formed(project):
    run = executions.start(project, "Work", session="s1")

    found = decisions.record(project, "the branch is clean", [f"{run.id}#7"], session="s1")

    assert found.reasons == (f"unknown-id {run.id}#7",)


def test_an_assumption_in_a_workspace_ledger_is_visible(project, tmp_path, monkeypatch):
    elsewhere = tmp_path / "workspace" / "executions.jsonl"
    run = executions.start(project, "Work", session="s1", work_item="W1")
    monkeypatch.setenv("EOS_EXECUTION", run.id)
    monkeypatch.setenv("EOS_EXECUTION_LEDGER", str(elsewhere))
    executions._append(elsewhere, {"type": "start", "id": run.id, "title": "Work",
                                   "work_item": "W1", "at": "2026-10-29T09:00:00+00:00"})

    assumed = decisions.record(project, "the branch is clean", [], session="s1")

    assert elsewhere.exists()
    assert [d.id for d in decisions.open_assumptions(project, work_item="W1", execution=None)] == [assumed.id]
    assert [d.id for d in decisions.for_scope(project, work_item="W1", execution=None)] == [assumed.id]


def test_resolve_and_approve_need_an_open_assumption(project):
    executions.start(project, "Work", session="s1")
    proof = _tool_event(project, "s1")
    evidenced = decisions.record(project, "the branch is clean", [proof.id], session="s1")
    path = executions.path_for(project)
    before = path.read_text()

    with pytest.raises(ValueError):
        decisions.resolve(project, evidenced.id, [proof.id], session="s1")
    with pytest.raises(ValueError):
        decisions.approve(project, evidenced.id, prompt_excerpt="evet", session="s1")

    assert path.read_text() == before


def test_an_approved_state_from_a_non_hook_event_is_ignored(project):
    run = executions.start(project, "Work", session="s1")
    assumed = decisions.record(project, "use the second table", [], session="s1")
    executions.event(project, run.id, kind="decided", tool="decide", ref=assumed.id,
                     body=assumed.claim, claim_state="approved", source="cli", session="s1")

    state = decisions.for_scope(project, work_item=None, execution=run.id)[0].state

    assert state == "assumption"


@pytest.mark.parametrize("claim", ["olmalıdır temiz", "olmalıydı temiz", "muhtemeldir temiz",
                                   "sanıyorum temiz", "it seemed fine", "I assumed it is clean"])
def test_hedge_inflections_are_hedged(project, claim):
    executions.start(project, "Work", session="s1")
    proof = _tool_event(project, "s1")

    assert "hedged" in decisions.record(project, claim, [proof.id], session="s1").reasons


@pytest.mark.parametrize("claim", ["shoulder is fine", "assumption d-12345678 resolved",
                                   "olmak temiz"])
def test_words_that_only_look_like_hedges_are_not_hedged(project, claim):
    executions.start(project, "Work", session="s1")
    proof = _tool_event(project, "s1")

    assert decisions.record(project, claim, [proof.id], session="s1").state == "evidenced"


@pytest.mark.parametrize("bad", ["x-a/b#1", "x-k=v#1", "x-password=abc#1"])
def test_evidence_ids_have_the_exact_execution_id_shape(project, bad):
    executions.start(project, "Work", session="s1")
    before = executions.path_for(project).read_text()

    with pytest.raises(ValueError):
        decisions.record(project, "the branch is clean", [bad], session="s1")

    assert executions.path_for(project).read_text() == before


def test_a_real_execution_id_with_an_ordinal_is_accepted(project):
    run = executions.start(project, "Deploy the order capture", session="s1")

    found = decisions.record(project, "the branch is clean", [f"{run.id}#7"], session="s1")

    assert found.reasons == (f"unknown-id {run.id}#7",)


@pytest.fixture
def workspace(tmp_path, project):
    root = tmp_path / "ws"
    (root / ".eos").mkdir(parents=True)
    service = tmp_path / "svc"
    (service / ".eos").mkdir(parents=True)
    (root / ".eos" / "projects.toml").write_text(
        '[[project]]\nname = "svc"\nroot = "../svc"\n', encoding="utf-8")
    return root, service


def _record_in_service(workspace, monkeypatch):
    root, service = workspace
    run = executions.start(service, "Work", session="s1", work_item="W1")
    monkeypatch.setenv("EOS_EXECUTION", run.id)
    monkeypatch.setenv("EOS_EXECUTION_LEDGER", str(executions.path_for(service)))
    assumed = decisions.record(root, "use the second table", [], session="s1")
    monkeypatch.delenv("EOS_EXECUTION")
    monkeypatch.delenv("EOS_EXECUTION_LEDGER")
    return run, assumed


def test_a_sessionless_reader_finds_a_decision_in_a_workspace_ledger(workspace, monkeypatch):
    root, _ = workspace
    run, assumed = _record_in_service(workspace, monkeypatch)

    found = decisions.for_scope(root, work_item=None, execution=run.id)

    assert [d.id for d in found] == [assumed.id]
    assert [d.id for d in decisions.open_assumptions(root, work_item="W1", execution=None)] == [assumed.id]


def test_a_decision_in_two_ledgers_is_returned_once(workspace, monkeypatch):
    root, service = workspace
    run, assumed = _record_in_service(workspace, monkeypatch)
    own = executions.path_for(root)
    own.parent.mkdir(parents=True, exist_ok=True)
    own.write_text(executions.path_for(service).read_text(), encoding="utf-8")

    found = decisions.for_scope(root, work_item=None, execution=run.id)

    assert [d.id for d in found] == [assumed.id]


def test_evidence_kept_in_another_ledger_of_the_same_item_counts(workspace, monkeypatch):
    root, service = workspace
    earlier = executions.start(root, "Earlier", session="s0", work_item="W1")
    proof = _tool_event(root, "s0")
    run = executions.start(service, "Work", session="s1", work_item="W1")
    monkeypatch.setenv("EOS_EXECUTION", run.id)
    monkeypatch.setenv("EOS_EXECUTION_LEDGER", str(executions.path_for(service)))

    found = decisions.record(root, "the branch is clean", [proof.id], session="s1")

    assert earlier.id != run.id and found.state == "evidenced"


def test_two_stale_ids_give_two_entries_and_a_repeated_id_one(project):
    executions.start(project, "Work", session="s1")
    first = _tool_event(project, "s1", tool="db")
    second = _tool_event(project, "s1", tool="db")
    late = _shifted(second, hours=25)

    two = decisions.record(project, "the row exists", [first.id, second.id], session="s1", now=late)
    once = decisions.record(project, "the row exists", [first.id, first.id], session="s1", now=late)

    assert two.reasons == (f"stale {first.id}", f"stale {second.id}")
    assert once.reasons == (f"stale {first.id}",)


def test_find_evidence_reads_a_second_project_ledger(workspace):
    root, service = workspace
    run = executions.start(service, "Work", session="s2", work_item="W1")
    proof = executions.event(service, run.id, kind="ran", tool="gitx", source="wrapper",
                             session="s2")

    found = decisions.find_evidence(root, proof.id)

    assert found is not None and found[0].id == run.id and found[1].id == proof.id
    assert decisions.find_evidence(root, "e-00000000") is None


def test_project_roots_cover_the_workspace_and_its_projects(workspace):
    root, service = workspace

    found = decisions.project_roots(root)

    assert root.resolve() in found and service.resolve() in found


REVIEWER_CHECK = "db.sh pg env1 crm \"select * from cust where msisdn='905321234567'\""


@pytest.mark.parametrize("check", [REVIEWER_CHECK, "db.sh pg env1 crm 'x'", 'db.sh pg "x"',
                                   "db.sh pg msisdn=905321234567", "db.sh pg env1; rm",
                                   "db.sh pg SELECT", "db.sh pg Insert", "db.sh pg update",
                                   "db.sh pg delete", "db.sh pg WHERE", "db.sh " + "x" * 120])
def test_a_check_holding_a_query_or_value_is_refused(project, check):
    executions.start(project, "Work", session="s1")
    before = executions.path_for(project).read_text()

    with pytest.raises(ValueError, match="wrapper, subcommand and first operand"):
        decisions.record(project, "the row exists", [], check=check, session="s1")

    assert executions.path_for(project).read_text() == before


def test_a_refused_check_is_not_echoed(project):
    executions.start(project, "Work", session="s1")

    with pytest.raises(ValueError) as refused:
        decisions.record(project, "the row exists", [], check=REVIEWER_CHECK, session="s1")

    assert "905321234567" not in str(refused.value)


def test_a_plain_check_is_kept(project):
    executions.start(project, "Work", session="s1", work_item="W1")

    found = decisions.record(project, "the row exists", [], check="automation/db.sh pg env1 crm",
                             session="s1")

    assert found.check == "automation/db.sh pg env1 crm"
    assert [d.check for d in decisions.for_scope(project, work_item="W1", execution=None)] == [
        "automation/db.sh pg env1 crm"]


def test_words_holding_a_sql_verb_are_a_plain_check(project):
    executions.start(project, "Work", session="s1")

    found = decisions.record(project, "the row exists", [], check="bpmn.sh check updated-flow",
                             session="s1")

    assert found.check == "bpmn.sh check updated-flow"


@pytest.mark.parametrize("claim", ["", "   ", "\t\n"])
def test_an_empty_claim_is_refused(project, claim):
    executions.start(project, "Work", session="s1")
    before = executions.path_for(project).read_text()

    with pytest.raises(ValueError, match="claim"):
        decisions.record(project, claim, [], session="s1")

    assert executions.path_for(project).read_text() == before


def test_a_note_read_is_not_evidence(project):
    executions.start(project, "Work", session="s1")
    read = _tool_event(project, "s1", kind="read", tool=None, source="hook",
                       ref="note:20260901-wallet.md")

    found = decisions.record(project, "the wallet is credited", [read.id], session="s1")

    assert found.state == "assumption" and found.reasons == (f"note-not-evidence {read.id}",)
    assert decisions.reason_line(found.reasons[0]) == f"reason=note-not-evidence evidence={read.id}"


def _resolved_across_ledgers(workspace, monkeypatch):
    root, _ = workspace
    _, assumed = _record_in_service(workspace, monkeypatch)
    executions.start(root, "Later", session="s2", work_item="W1")
    proof = _tool_event(root, "s2")
    decisions.resolve(root, assumed.id, [proof.id], session="s2")
    return root, assumed


def test_the_latest_state_is_the_latest_in_time_across_ledgers(workspace, monkeypatch):
    root, assumed = _resolved_across_ledgers(workspace, monkeypatch)

    assert [(d.id, d.state) for d in decisions.for_scope(root, work_item="W1", execution=None)] == [
        (assumed.id, "resolved")]
    assert decisions.open_assumptions(root, work_item="W1", execution=None) == []


def test_the_fold_does_not_depend_on_ledger_read_order(workspace, monkeypatch):
    root, assumed = _resolved_across_ledgers(workspace, monkeypatch)
    records = decisions._all_records(root)

    for order in (records, list(reversed(records))):
        assert [(d.id, d.state) for d in decisions._fold(order, "W1", None)] == [
            (assumed.id, "resolved")]


def _forge(project, tmp_path, decision_id, *source):
    import os
    import subprocess
    from pathlib import Path

    helper = Path(__file__).resolve().parents[1] / "bin" / "eos-event"
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "EOS_STATE_DIR": str(tmp_path / "state"),
           "CLAUDE_CODE_SESSION_ID": "s1"}
    done = subprocess.run(["/bin/sh", str(helper), "--kind", "decided",
                           "--tool", "decide", "--ref", decision_id, *source],
                          capture_output=True, text=True, env=env, cwd=os.fspath(project))
    assert done.returncode == 0, done.stderr


def test_a_decided_line_from_a_shell_cannot_change_a_decision(project, tmp_path):
    run = executions.start(project, "Work", session="s1")
    assumed = decisions.record(project, "use the second table", [], session="s1")
    proof = _tool_event(project, "s1")
    decisions.resolve(project, assumed.id, [proof.id], session="s1")
    before = executions.path_for(project).read_text()

    for source in ([], ["--source", "cli"], ["--source", "hook"], ["--source", "wrapper"]):
        _forge(project, tmp_path, assumed.id, *source)

    assert executions.path_for(project).read_text() != before
    [found] = decisions.for_scope(project, work_item=None, execution=run.id)
    assert (found.state, found.claim) == ("resolved", "use the second table")


@pytest.mark.parametrize("state, source", [("bogus", "cli"), (None, "cli"), ("resolved", "wrapper"),
                                           ("evidenced", None)])
def test_a_decided_event_outside_the_known_states_or_sources_is_ignored(project, state, source):
    run = executions.start(project, "Work", session="s1")
    assumed = decisions.record(project, "use the second table", [], session="s1")
    executions.event(project, run.id, kind="decided", tool="decide", ref=assumed.id, body="other",
                     claim_state=state, source=source, session="s1")

    [found] = decisions.for_scope(project, work_item=None, execution=run.id)

    assert (found.state, found.claim) == ("assumption", "use the second table")


@pytest.mark.parametrize("claim", ["belki temiz", "temiz olabilir", "temiz olabilirdi",
                                   "tahminen temiz", "tahmin ediyorum temiz", "sanki temiz",
                                   "temiz gibi görünüyor", "it might be clean", "maybe it is clean",
                                   "perhaps it is clean", "apparently it is clean",
                                   "I think it is clean", "i THINK so"])
def test_more_hedge_words_are_hedged(project, claim):
    executions.start(project, "Work", session="s1")
    proof = _tool_event(project, "s1")

    assert "hedged" in decisions.record(project, claim, [proof.id], session="s1").reasons


@pytest.mark.parametrize("claim", ["the table has the column and three rows",
                                   "the mighty table is fine", "thinking cap is on"])
def test_a_plain_sentence_stays_evidenced(project, claim):
    executions.start(project, "Work", session="s1")
    proof = _tool_event(project, "s1")

    assert decisions.record(project, claim, [proof.id], session="s1").state == "evidenced"


@pytest.mark.parametrize("tool", ["env-ready", "bpmn", "cache-clear", "vpn"])
def test_wrapper_tools_that_read_an_environment_go_stale(project, tool):
    executions.start(project, "Work", session="s1")
    live = _tool_event(project, "s1", tool=tool)

    found = decisions.record(project, "env1 is awake", [live.id], session="s1",
                             now=_shifted(live, hours=25))

    assert f"stale {live.id}" in found.reasons


def _clock(monkeypatch, *stamps):
    from core.knowledge import evidence

    times = iter(stamps)
    last = [stamps[-1]]

    def now():
        last[0] = next(times, last[0])
        return last[0]

    monkeypatch.setattr(evidence, "utc_now", now)


@pytest.mark.parametrize("spaced", [True, False])
def test_the_fold_orders_by_time_then_by_what_can_follow(workspace, monkeypatch, spaced):
    stamps = [f"2026-10-29T09:00:{i:02d}+00:00" if spaced else "2026-10-29T09:00:00+00:00"
              for i in range(20)]
    _clock(monkeypatch, *stamps)
    root, assumed = _resolved_across_ledgers(workspace, monkeypatch)
    records = decisions._all_records(root)

    for order in (records, list(reversed(records))):
        assert [(d.id, d.state) for d in decisions._fold(order, "W1", None)] == [
            (assumed.id, "resolved")]
