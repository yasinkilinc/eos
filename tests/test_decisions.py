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

    assert found.state == "assumption" and "unknown-id" in found.reasons


def test_evidence_from_another_work_item_is_refused(project):
    executions.start(project, "A", session="sa", work_item="W1")
    proof = _tool_event(project, "sa")
    executions.start(project, "B", session="sb", work_item="W2")

    found = decisions.record(project, "the branch is clean", [proof.id], session="sb")

    assert found.state == "assumption" and "other-work-item" in found.reasons


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

    assert "not-recorded-by-a-tool" in found.reasons and found.state == "assumption"


def test_subagent_citation_count_is_not_evidence(project):
    executions.start(project, "Work", session="s1")
    report = _tool_event(project, "s1", kind="verified", tool="subagent", source="hook")

    found = decisions.record(project, "the branch is clean", [report.id], session="s1")

    assert "subagent-report" in found.reasons and found.state == "assumption"


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

    assert "stale" in old_live.reasons and old_live.state == "assumption"
    assert fresh_live.state == "evidenced"
    assert old_static.state == "evidenced"


def test_failed_call_is_not_evidence(project):
    executions.start(project, "Work", session="s1")
    failed = _tool_event(project, "s1", kind="called", tool="bssapi", exit_code=1)
    red_test = _tool_event(project, "s1", kind="ran", tool="mvn", exit_code=1)

    refused = decisions.record(project, "the api answers", [failed.id], session="s1")
    accepted = decisions.record(project, "the test is red", [red_test.id], session="s1")

    assert "failed-call" in refused.reasons and refused.state == "assumption"
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

    assert refused.state == "assumption" and "unknown-id" in refused.reasons
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

    assert recorded.reasons == ("unknown-id",)
    assert [d.reasons for d in read_back] == [("unknown-id",)]


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

    assert found.reasons == ("unknown-id",)


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
