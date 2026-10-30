"""The prompt hook approves an open assumption only from the user's own words."""
import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

from core import decisions, executions, hooks, report

REPO = Path(__file__).resolve().parents[1]
EOS = [sys.executable, str(REPO / "core" / "eos.py")]


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    root.mkdir()
    assert subprocess.run(EOS + ["init", str(root), "--no-ai"], capture_output=True).returncode == 0
    monkeypatch.setenv("EOS_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("EOS_HOOK_STATE_DIR", raising=False)
    monkeypatch.delenv("EOS_EXECUTION", raising=False)
    monkeypatch.delenv("EOS_EXECUTION_LEDGER", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "s1")
    return root


def _assumption(root, claim="the table is empty"):
    return decisions.record(root, claim, [], session="s1")


def _prompt(monkeypatch, capsys, root, text):
    payload = {"session_id": "s1", "cwd": str(root), "prompt": text}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    assert hooks.main(["user-prompt"]) == 0
    return capsys.readouterr().out


def _state(root, decision_id):
    return next(d for d in decisions.for_scope(root, work_item=None, execution=executions.current(root, "s1")[0],
                                               session="s1") if d.id == decision_id)


def test_word_after_id_approves(project, monkeypatch, capsys):
    executions.start(project, "run", session="s1")
    found = _assumption(project)

    out = _prompt(monkeypatch, capsys, project, f"{found.id} onay")

    after = _state(project, found.id)
    written = [e for r in executions.load(project) for e in r.events if e.ref == found.id]
    assert after.state == "approved" and written[-1].source == "hook"
    assert after.check == "user prompt: onay"
    assert f"EOS: {found.id} approved by the user's prompt." in out


def test_word_before_id_does_not(project, monkeypatch, capsys):
    executions.start(project, "run", session="s1")
    found = _assumption(project)

    _prompt(monkeypatch, capsys, project, f"onay {found.id}")

    assert _state(project, found.id).state == "assumption"


def test_notice_does_not(project, monkeypatch, capsys):
    executions.start(project, "run", session="s1")
    found = _assumption(project)

    _prompt(monkeypatch, capsys, project, f"<task-notification>{found.id} onay</task-notification>")

    assert _state(project, found.id).state == "assumption"


def test_evidenced_decision_is_ignored(project, monkeypatch, capsys):
    executions.start(project, "run", session="s1")
    proof = executions.event(project, kind="ran", tool="gitx", source="wrapper", session="s1")
    found = decisions.record(project, "clean", [proof.id], session="s1")
    assert found.state == "evidenced"

    out = _prompt(monkeypatch, capsys, project, f"{found.id} onay")

    assert _state(project, found.id).state == "evidenced"
    assert "approved by the user" not in out


def test_two_ids_both_approved(project, monkeypatch, capsys):
    executions.start(project, "run", session="s1")
    first, second = _assumption(project, "first guess"), _assumption(project, "second guess")

    _prompt(monkeypatch, capsys, project, f"{first.id} onaylıyorum\n{second.id} APPROVED")

    assert _state(project, first.id).state == "approved"
    assert _state(project, second.id).state == "approved"


def test_no_open_run_does_nothing(project, monkeypatch, capsys):
    out = _prompt(monkeypatch, capsys, project, "d-1a2b3c4d onay")

    assert "approved by the user" not in out


def test_works_with_brief_off(project, monkeypatch, capsys):
    (project / ".eos" / "config.toml").write_text("[hooks]\nbrief = false\n", encoding="utf-8")
    executions.start(project, "run", session="s1")
    found = _assumption(project)

    out = _prompt(monkeypatch, capsys, project, f"{found.id} approve")

    assert _state(project, found.id).state == "approved"
    assert f"EOS: {found.id} approved by the user's prompt." in out


def test_excerpt_passes_checked():
    for word in ("onay", "onaylıyorum", "onayladım", "approve", "approved"):
        assert executions._checked(f"user prompt: {word}", "check")


@pytest.mark.parametrize("tail", ["onaylamıyorum", "onay yok", "disapprove", "onay değil", "onay degil",
                                  "onay verme", "approve not", "onaylama", "not approved"])
def test_refusal_is_not_approval(project, monkeypatch, capsys, tail):
    executions.start(project, "run", session="s1")
    found = _assumption(project)

    out = _prompt(monkeypatch, capsys, project, f"{found.id} {tail}")

    assert _state(project, found.id).state == "assumption"
    assert "approved by the user" not in out


@pytest.mark.parametrize("form", ["{id}: onay", "{id} - approved", "{ID} ONAY", "{id}, onaylıyorum", "{ID} ONAYLIYORUM"])
def test_plain_yes_forms_approve(project, monkeypatch, capsys, form):
    executions.start(project, "run", session="s1")
    found = _assumption(project)

    _prompt(monkeypatch, capsys, project, form.format(id=found.id, ID=found.id.upper()))

    assert _state(project, found.id).state == "approved"


@pytest.mark.parametrize("glue", ["hayır", "ve"])
def test_word_belongs_to_its_own_id(project, monkeypatch, capsys, glue):
    executions.start(project, "run", session="s1")
    first, second = _assumption(project, "first guess"), _assumption(project, "second guess")

    _prompt(monkeypatch, capsys, project, f"{first.id} {glue} {second.id} onay")

    assert _state(project, first.id).state == "assumption"
    assert _state(project, second.id).state == "approved"


@pytest.mark.parametrize("wrap", ["{pad}<task-notification>{t}</task-notification>",
                                  "<teammate-message from=x>{t}</teammate-message>",
                                  "x <custom-notification a=b>{t}"])
def test_notice_anywhere_blocks_approval(project, monkeypatch, capsys, wrap):
    executions.start(project, "run", session="s1")
    found = _assumption(project)

    _prompt(monkeypatch, capsys, project, wrap.format(pad="x" * 210, t=f"{found.id} onay"))

    assert _state(project, found.id).state == "assumption"


def _no_report(monkeypatch):
    """These count the approval's own reads; the report reads the ledgers on every prompt."""
    monkeypatch.setattr(report, "for_brief", lambda *a, **k: None)


def test_no_id_reads_no_ledger(project, monkeypatch, capsys):
    _no_report(monkeypatch)
    executions.start(project, "run", session="s1")
    calls = []
    monkeypatch.setattr(decisions, "all_records", lambda *a, **k: calls.append(1) or [])

    _prompt(monkeypatch, capsys, project, "just a normal prompt")

    assert calls == []


def test_failing_approve_keeps_earlier_lines(project, monkeypatch, capsys):
    executions.start(project, "run", session="s1")
    first, second = _assumption(project, "first guess"), _assumption(project, "second guess")
    real = decisions.approve

    def flaky(root, decision_id, **kw):
        if decision_id == second.id:
            raise ValueError("boom")
        return real(root, decision_id, **kw)

    monkeypatch.setattr(decisions, "approve", flaky)

    out = _prompt(monkeypatch, capsys, project, f"{first.id} onay\n{second.id} onay")

    assert f"EOS: {first.id} approved" in out and f"EOS: {second.id} approved" not in out
    assert f"EOS: {second.id} not approved — the approval could not be written." in out
    assert _state(project, second.id).state == "assumption"


# --- whole-branch review fixes (I3/M2, I6, I7, I8) ---


@pytest.mark.parametrize("tail", ["onay mı?", "onay?", "onay bekliyor", "onay bekleniyor", "onay sonra",
                                  "ONAY MI", "onay mü"])
def test_i6_a_question_or_a_wait_is_not_approval(project, monkeypatch, capsys, tail):
    executions.start(project, "run", session="s1")
    found = _assumption(project)

    out = _prompt(monkeypatch, capsys, project, f"{found.id} {tail}")

    assert _state(project, found.id).state == "assumption"
    assert "approved by the user" not in out


def test_i6_a_yes_ending_its_sentence_approves(project, monkeypatch, capsys):
    executions.start(project, "run", session="s1")
    found = _assumption(project)

    _prompt(monkeypatch, capsys, project, f"{found.id} onay.")

    assert _state(project, found.id).state == "approved"


def test_i7_a_child_session_with_the_run_in_its_env_approves_nothing(project, monkeypatch, capsys):
    run = executions.start(project, "run", session="s1")
    found = _assumption(project)
    execution, ledger = executions.current(project, "s1")
    assert execution == run.id
    monkeypatch.setenv("EOS_EXECUTION", execution)
    monkeypatch.setenv("EOS_EXECUTION_LEDGER", str(ledger))
    payload = {"session_id": "s2", "cwd": str(project), "prompt": f"{found.id} onay"}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))

    assert hooks.main(["user-prompt"]) == 0

    assert "approved by the user" not in capsys.readouterr().out
    monkeypatch.delenv("EOS_EXECUTION")
    monkeypatch.delenv("EOS_EXECUTION_LEDGER")
    assert _state(project, found.id).state == "assumption"


def test_i8_devin_approves_nothing(project, monkeypatch, capsys):
    executions.start(project, "run", session="s1")
    found = _assumption(project)
    payload = {"session_id": "s1", "cwd": str(project), "prompt": f"{found.id} onay"}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))

    hooks.main(["user-prompt", "--host", "devin"])

    assert _state(project, found.id).state == "assumption"


def test_m2_one_approval_reads_the_ledgers_once(project, monkeypatch, capsys):
    _no_report(monkeypatch)
    executions.start(project, "run", session="s1")
    found = _assumption(project)
    real = decisions.all_records
    calls = []
    monkeypatch.setattr(decisions, "all_records", lambda *a, **k: calls.append(1) or real(*a, **k))

    _prompt(monkeypatch, capsys, project, f"{found.id} onay")

    assert len(calls) == 1
    monkeypatch.setattr(decisions, "all_records", real)
    assert _state(project, found.id).state == "approved"


@pytest.mark.parametrize("text", ["see xd-1a2b3c4d onay in the log", "build-d-1a2b3c4d onay",
                                  "d-1a2b3c4d failed again, look"])
def test_m2_no_approval_shape_reads_no_ledger(project, monkeypatch, capsys, text):
    _no_report(monkeypatch)
    executions.start(project, "run", session="s1")
    calls = []
    monkeypatch.setattr(decisions, "all_records", lambda *a, **k: calls.append(1) or [])

    _prompt(monkeypatch, capsys, project, text)

    assert calls == []


# --- fix round 2: the yes alone on its line (I6), a refused yes is said (N1) ---


@pytest.mark.parametrize("tail", ["approve edeyim mi?", "onay vermem", "onay istemiyorum", "onay, değil mi?",
                                  "onay sonra deploy et", "onay mı?", "onay bekliyor", "onay vermiyorum",
                                  "onay ve deploy", "onay, sonra deploy et"])
def test_r2_anything_after_the_yes_approves_nothing_and_says_so(project, monkeypatch, capsys, tail):
    executions.start(project, "run", session="s1")
    found = _assumption(project)

    out = _prompt(monkeypatch, capsys, project, f"{found.id} {tail}")

    assert _state(project, found.id).state == "assumption"
    assert "approved by the user" not in out
    assert f'EOS: {found.id} not approved — write "{found.id} onay" alone on a line.' in out


@pytest.mark.parametrize("form", ["{id} onay", "{id} onay.", "{ID} ONAY!", "{id}: approved", "{id} onay  \nthanks",
                                  "look:\n{id} - onaylıyorum.\n"])
def test_r2_a_yes_at_the_end_of_its_line_approves(project, monkeypatch, capsys, form):
    executions.start(project, "run", session="s1")
    found = _assumption(project)

    out = _prompt(monkeypatch, capsys, project, form.format(id=found.id, ID=found.id.upper()))

    assert _state(project, found.id).state == "approved"
    assert "not approved" not in out


def test_r2_two_ids_on_two_lines_are_both_approved(project, monkeypatch, capsys):
    executions.start(project, "run", session="s1")
    first, second = _assumption(project, "first guess"), _assumption(project, "second guess")

    _prompt(monkeypatch, capsys, project, f"{first.id} onay\n{second.id} approved")

    assert _state(project, first.id).state == "approved"
    assert _state(project, second.id).state == "approved"


def test_r2_a_glued_word_is_no_yes(project, monkeypatch, capsys):
    executions.start(project, "run", session="s1")
    found = _assumption(project)

    out = _prompt(monkeypatch, capsys, project, f"{found.id}onay")

    assert _state(project, found.id).state == "assumption"
    assert "not approved" not in out


def test_r2_an_unknown_id_is_said(project, monkeypatch, capsys):
    executions.start(project, "run", session="s1")
    _assumption(project)

    out = _prompt(monkeypatch, capsys, project, "d-1a2b3c4d onay mı?")

    assert "EOS: d-1a2b3c4d not approved — no open assumption" in out


# --- an approval reaches any open assumption in the project ---


def _closed_assumption(root, session="s0", work_item=None):
    closed = executions.start(root, "yesterday", session=session, work_item=work_item)
    found = decisions.record(root, "the table is empty", [], session=session)
    executions.finish(root, closed.id, outcome="abandoned")
    return closed, found


def test_a_prompt_approves_an_assumption_of_a_closed_run(project, monkeypatch, capsys):
    closed, found = _closed_assumption(project)
    executions.start(project, "today", session="s1")

    out = _prompt(monkeypatch, capsys, project, f"{found.id} onay")

    assert f"EOS: {found.id} approved by the user's prompt." in out
    states = decisions.for_scope(project, work_item=None, execution=closed.id)
    assert [d.state for d in states] == ["approved"] and states[0].check == "user prompt: onay"


def test_a_prompt_approves_an_assumption_of_another_work_item(project, monkeypatch, capsys):
    from core import work

    other = work.open_item(project, "Other work").id
    _, found = _closed_assumption(project, work_item=other)
    executions.start(project, "today", session="s1")

    _prompt(monkeypatch, capsys, project, f"{found.id} onay")

    assert [d.state for d in decisions.for_scope(project, work_item=other, execution=None)] == ["approved"]


def test_an_approval_by_a_forged_cli_line_on_the_closed_run_still_does_not_count(project, monkeypatch, capsys):
    closed, found = _closed_assumption(project)
    executions.event(project, closed.id, kind="decided", tool="decide", ref=found.id, claim_state="approved",
                     source="cli", body="the table is empty")

    assert [d.state for d in decisions.for_scope(project, work_item=None, execution=closed.id)] == ["assumption"]


def test_a_session_that_opened_no_run_approves_nothing_and_says_so(project, monkeypatch, capsys):
    closed, found = _closed_assumption(project)

    out = _prompt(monkeypatch, capsys, project, f"{found.id} onay")

    assert [d.state for d in decisions.for_scope(project, work_item=None, execution=closed.id)] == ["assumption"]
    assert f"EOS: {found.id} not approved" in out
    assert f"eos decide --resolve {found.id} --evidence" in out


def test_a_child_session_approves_no_assumption_of_a_closed_run_and_says_so(project, monkeypatch, capsys):
    closed, found = _closed_assumption(project)
    executions.start(project, "the parent's run", session="s1")
    execution, ledger = executions.current(project, "s1")
    monkeypatch.setenv("EOS_EXECUTION", execution)
    monkeypatch.setenv("EOS_EXECUTION_LEDGER", str(ledger))
    payload = {"session_id": "child", "cwd": str(project), "prompt": f"{found.id} onay"}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))

    assert hooks.main(["user-prompt"]) == 0
    out = capsys.readouterr().out

    monkeypatch.delenv("EOS_EXECUTION")
    monkeypatch.delenv("EOS_EXECUTION_LEDGER")
    assert [d.state for d in decisions.for_scope(project, work_item=None, execution=closed.id)] == ["assumption"]
    assert f"EOS: {found.id} not approved" in out and "approved by the user" not in out


def test_a_runner_session_with_its_own_run_approves_nothing_and_says_so(project, monkeypatch, capsys):
    from core import work

    closed, found = _closed_assumption(project)
    item = work.open_item(project, "Parked work").id
    work.append(project, work.EVENT_CLAIM, item, session="runner-1", agent=work.RESUME_AGENT)
    executions.start(project, "resumed", session="runner-1", work_item=item)
    payload = {"session_id": "runner-1", "cwd": str(project), "prompt": f"{found.id} onay"}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))

    assert hooks.main(["user-prompt"]) == 0
    out = capsys.readouterr().out

    assert [d.state for d in decisions.for_scope(project, work_item=None, execution=closed.id)] == ["assumption"]
    assert f"EOS: {found.id} not approved" in out and "approved by the user" not in out


def test_devin_approves_no_assumption_of_a_closed_run(project, monkeypatch, capsys):
    closed, found = _closed_assumption(project)
    executions.start(project, "today", session="s1")
    payload = {"session_id": "s1", "cwd": str(project), "prompt": f"{found.id} onay"}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))

    hooks.main(["user-prompt", "--host", "devin"])

    assert "approved" not in capsys.readouterr().out
    assert [d.state for d in decisions.for_scope(project, work_item=None, execution=closed.id)] == ["assumption"]


def test_an_id_that_is_no_open_assumption_is_said_with_the_resolve_form(project, monkeypatch, capsys):
    executions.start(project, "run", session="s1")

    out = _prompt(monkeypatch, capsys, project, "d-1a2b3c4d onay")

    assert "EOS: d-1a2b3c4d not approved" in out and "no open assumption" in out
    assert "eos decide --resolve d-1a2b3c4d --evidence" in out


def test_an_already_closed_id_is_said(project, monkeypatch, capsys):
    executions.start(project, "run", session="s1")
    found = _assumption(project)
    _prompt(monkeypatch, capsys, project, f"{found.id} onay")

    out = _prompt(monkeypatch, capsys, project, f"{found.id} onay")

    assert f"EOS: {found.id} not approved" in out and "no open assumption" in out


def test_a_yes_with_more_after_it_also_gives_the_resolve_form(project, monkeypatch, capsys):
    executions.start(project, "run", session="s1")
    found = _assumption(project)

    out = _prompt(monkeypatch, capsys, project, f"{found.id} onay mı?")

    assert f"eos decide --resolve {found.id} --evidence" in out
