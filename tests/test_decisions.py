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
