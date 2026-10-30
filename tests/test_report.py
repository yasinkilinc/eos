"""`eos report`: done only with proof, waiting with its condition, questions one line each."""
import datetime
import io
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from core import decisions, executions, hooks, report, work

EOS = [sys.executable, str(Path(__file__).resolve().parents[1] / "core" / "eos.py")]
PAST = datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc)
FUTURE = datetime.datetime(2999, 1, 1, tzinfo=datetime.timezone.utc)


def _eos(*args, **kwargs):
    return subprocess.run(EOS + list(args), capture_output=True, text=True, **kwargs)


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    root.mkdir()
    assert _eos("init", str(root), "--no-ai").returncode == 0
    (root / ".eos" / "knowledge").mkdir(parents=True, exist_ok=True)
    (root / ".eos" / "knowledge" / "verify.toml").write_text(
        "[[scope]]\nname = \"code\"\npaths = [\"src/**\"]\npasses = ['make\\s+test\\b']\nrun = \"make test\"\n",
        encoding="utf-8")
    monkeypatch.setenv("EOS_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("EOS_HOOK_STATE_DIR", raising=False)
    monkeypatch.delenv("EOS_EXECUTION", raising=False)
    monkeypatch.delenv("EOS_EXECUTION_LEDGER", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "s1")
    return root


def _verified(root, title="Ship the retry", session="s1"):
    run = executions.start(root, title, session=session)
    executions.event(root, run.id, kind="changed", ref="src/a.py")
    proof = executions.event(root, run.id, kind="verified", tool="verify", ref="code", status="ok", source="hook")
    finished = executions.finish(root, run.id, outcome="ok")
    assert finished.outcome_source == "verified"
    return run, proof


def _section(text, name):
    """The lines of one section: from its heading to the next blank line."""
    lines = text.splitlines()
    start = next(n for n, line in enumerate(lines) if line.startswith(name))
    found = []
    for line in lines[start:]:
        if not line.strip():
            break
        found.append(line)
    return "\n".join(found)


def _render(root, since=PAST):
    return report.render(report.build(root, since=since))


def _park(root, title, *flags):
    item = work.open_item(root, title).id
    work.append(root, work.EVENT_BLOCK, item, **dict(flags))
    return item


# --- done -------------------------------------------------------------------------------


def test_a_verified_run_is_done_with_its_evidence_ref(project):
    run, proof = _verified(project)

    done = _section(_render(project), "DONE")

    assert run.id in done and "Ship the retry" in done and proof.id in done


def test_a_run_claimed_but_not_verified_is_never_done(project):
    claimed = executions.start(project, "Claimed fix", session="s1")
    executions.event(project, claimed.id, kind="verified", tool="verify", ref="code", status="ok", source="hook")
    executions.event(project, claimed.id, kind="changed", ref="src/a.py")
    assert executions.finish(project, claimed.id, outcome="ok").outcome_source == "claimed"

    text = _render(project)

    assert "Claimed fix" not in text and claimed.id not in text
    assert "DONE (0)" in text


@pytest.mark.parametrize("outcome", ["no-check", "failed", "abandoned", "open", "subagent"])
def test_nothing_but_a_verified_outcome_is_done(project, outcome):
    run = executions.start(project, "Not proven", session="s1")
    if outcome == "no-check":
        executions.event(project, run.id, kind="changed", ref="docs/x.md")
        assert executions.finish(project, run.id, outcome="ok").outcome_source is None
    elif outcome == "failed":
        executions.finish(project, run.id, outcome="failed", lesson="the vault path moved")
    elif outcome == "abandoned":
        executions.finish(project, run.id, outcome="abandoned")
    elif outcome == "subagent":
        executions.event(project, run.id, kind="verified", tool="subagent", source="hook", status="ok")
        executions.finish(project, run.id, outcome="ok")

    assert "Not proven" not in _section(_render(project), "DONE")


def test_a_verified_outcome_on_a_typed_event_is_not_done(project):
    """`eos run event` lets a session type `verified`; that is text, not a recorded check."""
    run = executions.start(project, "Typed proof", session="s1")
    executions.event(project, run.id, kind="changed", ref="src/a.py")
    executions.event(project, run.id, kind="verified", tool="verify", ref="code", exit_code=0, source="cli")
    assert executions.finish(project, run.id, outcome="ok").outcome_source == "verified"

    found = report.build(project, since=PAST)

    assert found["done"] == [] and found["unproven"] == [run.id]
    assert "Typed proof" not in _section(report.render(found), "DONE")
    assert "1 run" in _section(report.render(found), "DONE")


def test_a_typed_check_after_a_real_one_does_not_prove_the_change_between(project):
    run = executions.start(project, "Stale proof", session="s1")
    executions.event(project, run.id, kind="verified", tool="verify", ref="code", status="ok", source="hook")
    executions.event(project, run.id, kind="changed", ref="src/a.py")
    executions.event(project, run.id, kind="verified", tool="verify", ref="code", exit_code=0, source="cli")
    assert executions.finish(project, run.id, outcome="ok").outcome_source == "verified"

    assert report.build(project, since=PAST)["done"] == []


def test_a_success_check_a_wrapper_recorded_is_done_and_a_typed_one_is_not(project):
    knowledge = project / ".eos" / "knowledge"
    (knowledge / "capabilities.toml").write_text(
        '[[capability]]\nname = "tracker"\nrun = "scripts/tracker.sh"\ndoes = "issue <KEY>"\n', encoding="utf-8")
    made = _eos("procedure", "new", str(project), "--title", "Close a ticket", "--steps", "-",
                "--success", "`scripts/tracker.sh issue <KEY>` shows it done", input="Close it (tool: tracker)\n")
    slug = made.stdout.split("\t")[0]

    real = executions.start(project, "Close by wrapper", procedure=slug, session="s1")
    proof = executions.event(project, real.id, kind="called", tool="tracker", ref="issue T-1", exit_code=0,
                             source="wrapper")
    assert executions.finish(project, real.id, outcome="ok").outcome_source == "verified"
    typed = executions.start(project, "Close by typing", procedure=slug, session="s2")
    executions.event(project, typed.id, kind="called", tool="tracker", ref="issue T-2", exit_code=0, source="cli")
    assert executions.finish(project, typed.id, outcome="ok").outcome_source == "verified"

    done = _section(_render(project), "DONE")

    assert "Close by wrapper" in done and proof.id in done
    assert "Close by typing" not in done


def test_since_bounds_done(project):
    _verified(project)

    assert "Ship the retry" in _render(project, since=PAST)
    assert "Ship the retry" not in _render(project, since=FUTURE)


def test_a_project_of_the_workspace_is_read_with_its_own_verify_scopes(project, tmp_path):
    sibling = tmp_path / "svc"
    sibling.mkdir()
    assert _eos("init", str(sibling), "--no-ai").returncode == 0
    (sibling / ".eos" / "knowledge").mkdir(parents=True, exist_ok=True)
    (project / ".eos" / "knowledge" / "verify.toml").rename(sibling / ".eos" / "knowledge" / "verify.toml")
    (project / ".eos" / "projects.toml").write_text('[[project]]\nname = "svc"\nroot = "../svc"\n', encoding="utf-8")
    run, proof = _verified(sibling, title="Service fix", session="s9")
    item = work.open_item(sibling, "Service wait").id
    work.append(sibling, work.EVENT_BLOCK, item, body="env asleep", until="env:stage", check="env-ready stage",
                resume="go on")

    text = _render(project)

    assert run.id in _section(text, "DONE") and proof.id in _section(text, "DONE")
    assert item in _section(text, "WAITING")


# --- waiting ----------------------------------------------------------------------------


def test_a_parked_item_waits_with_its_condition_and_says_no_check_result_is_held(project):
    item = _park(project, "Billing retry", ("body", "env asleep"), ("until", "env:stage"),
                 ("check", "scripts/ready.sh stage"), ("resume", "go on"))

    waiting = _section(_render(project), "WAITING")

    assert item in waiting and "Billing retry" in waiting and "until env:stage" in waiting
    assert "check: scripts/ready.sh stage" in waiting
    assert "last check: not in the ledger" in waiting


def test_a_clock_wait_has_no_check_to_report(project):
    item = _park(project, "Later", ("until", "time:2999-01-01T00:00:00Z"), ("resume", "go on"))

    waiting = _section(_render(project), "WAITING")

    assert item in waiting and "until time:2999-01-01T00:00:00Z" in waiting
    assert "no check (a clock)" in waiting


def test_a_log_is_not_passed_off_as_a_check_result(project):
    item = _park(project, "Billing retry", ("until", "vpn"), ("check", "scripts/vpn.sh up"), ("resume", "go on"))
    work.append(project, work.EVENT_LOG, item, body="resume attempt: exit=0 session=abc")

    found = report.build(project, since=PAST)["waiting"]

    assert found[0]["last_check"] is None
    assert "last check: not in the ledger" in _render(project)


def test_a_plain_block_and_a_finished_item_are_not_reported(project):
    plain = _park(project, "Plainly stuck", ("body", "no idea"))
    gone = _park(project, "Was parked", ("until", "vpn"), ("check", "x"), ("resume", "y"))
    work.append(project, work.EVENT_DONE, gone)

    text = _render(project)

    assert plain not in text and gone not in text


# --- questions --------------------------------------------------------------------------


def test_an_open_assumption_and_a_user_wait_are_questions_one_line_each(project):
    executions.start(project, "run", session="s1")
    open_one = decisions.record(project, "the table\nis empty", [], session="s1")
    item = _park(project, "Pick a price", ("body", "which price\nwins?"), ("until", "user"))

    text = _render(project)
    questions = _section(text, "QUESTIONS").splitlines()

    assert questions[0].startswith("QUESTIONS (2)") and len(questions) == 3
    assert any(open_one.id in line and "the table is empty" in line for line in questions)
    assert any(item in line and "Pick a price" in line and "which price wins?" in line for line in questions)
    assert item not in _section(text, "WAITING")


def test_a_closed_assumption_is_no_question(project):
    run = executions.start(project, "run", session="s1")
    seen = executions.event(project, run.id, kind="called", tool="db", exit_code=0, source="wrapper")
    gone = decisions.record(project, "the table is empty", [], session="s1")
    assert decisions.resolve(project, gone.id, [seen.id], session="s1").state == "resolved"

    assert gone.id not in _render(project)


# --- the command ------------------------------------------------------------------------


def test_the_command_prints_three_sections(project):
    run, _ = _verified(project)

    done = _eos("report", str(project), "--since", "2020-01-01T00:00:00Z")

    assert done.returncode == 0, done.stderr
    for heading in ("DONE (1)", "WAITING (0)", "QUESTIONS (0)"):
        assert heading in done.stdout
    assert run.id in done.stdout


def test_the_command_refuses_an_unreadable_time(project):
    done = _eos("report", str(project), "--since", "yesterday-ish")

    assert done.returncode == 2 and "--since" in done.stderr and "Traceback" not in done.stderr


def test_the_command_reads_a_plain_date_and_writes_json(project):
    run, proof = _verified(project)

    done = _eos("report", str(project), "--since", "2020-01-01", "--format", "json")

    data = json.loads(done.stdout)
    assert [d["id"] for d in data["done"]] == [run.id]
    assert data["done"][0]["evidence"][0]["id"] == proof.id
    assert data["waiting"] == [] and data["questions"] == []


def test_the_command_writes_nothing(project):
    _verified(project)
    ledger = executions.path_for(project)
    before = ledger.read_bytes()

    assert _eos("report", str(project), "--since", "2020-01-01").returncode == 0

    assert ledger.read_bytes() == before


# --- the session-start brief ------------------------------------------------------------


def _hook(monkeypatch, capsys, event, root, *extra, session="s1", **fields):
    payload = {"session_id": session, "cwd": str(root), **fields}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    assert hooks.main([event, *extra]) == 0
    return capsys.readouterr().out


def _start(monkeypatch, capsys, root, *extra, session="s1"):
    return _hook(monkeypatch, capsys, "session-start", root, *extra, session=session, source="startup")


def test_the_brief_prints_the_report_when_something_is_new_since_the_last_prompt(project, monkeypatch, capsys):
    report.note_shown(project, at=PAST)
    run, proof = _verified(project)

    out = _start(monkeypatch, capsys, project)

    assert "DONE (1)" in out and proof.id in out and run.id in out
    assert out.index("DONE (1)") < out.index("EOS brief")


def test_the_brief_says_nothing_when_nothing_is_new(project, monkeypatch, capsys):
    _verified(project)
    _park(project, "Old wait", ("until", "vpn"), ("check", "x"), ("resume", "y"))
    report.note_shown(project, at=FUTURE)

    assert "DONE (" not in _start(monkeypatch, capsys, project)


def test_a_new_wait_alone_is_new(project, monkeypatch, capsys):
    report.note_shown(project, at=PAST)
    item = _park(project, "Fresh wait", ("until", "vpn"), ("check", "x"), ("resume", "y"))

    out = _start(monkeypatch, capsys, project)

    assert "WAITING (1)" in out and item in out


def test_a_claimed_run_alone_is_not_news(project, monkeypatch, capsys):
    report.note_shown(project, at=PAST)
    run = executions.start(project, "Read only", session="s1")
    executions.finish(project, run.id, outcome="ok")

    assert "DONE (" not in _start(monkeypatch, capsys, project)


def _runner_session(root, session="runner-1"):
    item = work.open_item(root, "Parked work").id
    work.append(root, work.EVENT_CLAIM, item, session=session, agent=work.RESUME_AGENT)
    return session


def test_a_resumed_session_gets_no_report(project, monkeypatch, capsys):
    report.note_shown(project, at=PAST)
    _verified(project)
    session = _runner_session(project)

    assert "DONE (" not in _start(monkeypatch, capsys, project, session=session)


def test_the_brief_caps_each_section_and_names_the_rest(project, monkeypatch, capsys):
    report.note_shown(project, at=PAST)
    for n in range(report.BRIEF_LIMIT + 2):
        _verified(project, title=f"Ship part {n}", session=f"s{n}")

    out = _start(monkeypatch, capsys, project)

    assert f"DONE ({report.BRIEF_LIMIT + 2})" in out
    assert "…2 more: eos report ." in out
    assert sum(f"Ship part {n}" in out for n in range(report.BRIEF_LIMIT + 2)) == report.BRIEF_LIMIT


# --- the mark means "last report shown" (Y2, Y3) ----------------------------------------


def _prompt(monkeypatch, capsys, root, text="please look at the billing retry", session="s1", extra=()):
    return _hook(monkeypatch, capsys, "user-prompt", root, *extra, session=session, prompt=text)


def test_the_night_a_session_stays_open_and_the_first_prompt_shows_it_once(project, monkeypatch, capsys):
    report.note_shown(project, at=PAST)
    run, proof = _verified(project, title="Finished overnight", session="night")

    first = _prompt(monkeypatch, capsys, project)
    second = _prompt(monkeypatch, capsys, project, "and now the next thing please")
    start = _start(monkeypatch, capsys, project)

    assert "DONE (1)" in first and run.id in first and proof.id in first
    assert "DONE (" not in second and run.id not in second
    assert "DONE (" not in start


def test_a_prompt_with_nothing_new_moves_no_mark(project, monkeypatch, capsys):
    report.note_shown(project, at=FUTURE)
    _verified(project)

    assert "DONE (" not in _prompt(monkeypatch, capsys, project)
    assert report.last_shown(project) == FUTURE


def test_a_prompt_that_shows_a_report_moves_the_mark(project, monkeypatch, capsys):
    report.note_shown(project, at=PAST)
    _verified(project)

    _prompt(monkeypatch, capsys, project)

    assert report.last_shown(project) > PAST


def test_a_session_start_that_shows_a_report_moves_the_mark_and_does_not_repeat(project, monkeypatch, capsys):
    report.note_shown(project, at=PAST)
    _verified(project)

    assert "DONE (1)" in _start(monkeypatch, capsys, project)
    assert report.last_shown(project) > PAST
    assert "DONE (" not in _start(monkeypatch, capsys, project)


def test_a_session_start_with_nothing_new_moves_no_mark(project, monkeypatch, capsys):
    report.note_shown(project, at=FUTURE)

    _start(monkeypatch, capsys, project)

    assert report.last_shown(project) == FUTURE


def test_with_no_mark_everything_is_new_once(project, monkeypatch, capsys):
    run, _ = _verified(project)

    assert run.id in _start(monkeypatch, capsys, project)
    assert report.last_shown(project) is not None
    assert run.id not in _start(monkeypatch, capsys, project)


def test_with_no_mark_and_nothing_recorded_nothing_prints_and_no_mark_is_made(project, monkeypatch, capsys):
    assert "DONE (" not in _start(monkeypatch, capsys, project)
    assert report.last_shown(project) is None


def test_the_command_shows_what_is_new_since_the_mark_and_does_not_move_it(project):
    run, _ = _verified(project)
    report.note_shown(project, at=PAST)

    first = _eos("report", str(project))
    again = _eos("report", str(project))

    assert run.id in first.stdout and run.id in again.stdout
    assert report.last_shown(project) == PAST


def test_the_command_after_a_shown_report_lists_no_done_again(project, monkeypatch, capsys):
    report.note_shown(project, at=PAST)
    run, _ = _verified(project)
    _start(monkeypatch, capsys, project)

    assert run.id not in _eos("report", str(project)).stdout
    assert run.id in _eos("report", str(project), "--since", "2020-01-01").stdout


def test_a_notice_shows_nothing_and_moves_nothing(project, monkeypatch, capsys):
    report.note_shown(project, at=PAST)
    _verified(project)

    out = _prompt(monkeypatch, capsys, project, "<task-notification>finished</task-notification>")

    assert "DONE (" not in out and report.last_shown(project) == PAST


def test_a_resumed_session_prompt_shows_nothing_and_moves_nothing(project, monkeypatch, capsys):
    report.note_shown(project, at=PAST)
    _verified(project)
    session = _runner_session(project)

    out = _prompt(monkeypatch, capsys, project, "go on with the parked work", session=session)

    assert "DONE (" not in out and report.last_shown(project) == PAST


def test_devin_gets_no_report_and_moves_no_mark(project, monkeypatch, capsys):
    report.note_shown(project, at=PAST)
    _verified(project)

    assert "DONE (" not in _start(monkeypatch, capsys, project, "--host", "devin")
    assert "DONE (" not in _prompt(monkeypatch, capsys, project, extra=("--host", "devin"))
    assert report.last_shown(project) == PAST


def _boom(*args, **kwargs):
    raise RuntimeError("the report broke")


def test_the_report_raises_and_the_brief_stays(project, monkeypatch, capsys):
    report.note_shown(project, at=PAST)
    monkeypatch.setattr(report, "build", _boom)

    assert "EOS brief" in _start(monkeypatch, capsys, project)
    assert report.last_shown(project) == PAST


def test_the_report_raises_at_a_prompt_and_the_approval_and_the_brief_stay(project, monkeypatch, capsys):
    executions.start(project, "run", session="s1")
    found = decisions.record(project, "the table is empty", [], session="s1")
    made = _eos("procedure", "new", str(project), "--title", "Billing retry fix", "--steps", "-",
                input="Look at the billing retry\n")
    assert made.returncode == 0, made.stderr
    report.note_shown(project, at=PAST)
    monkeypatch.setattr(report, "build", _boom)

    out = _prompt(monkeypatch, capsys, project, f"billing retry fix\n{found.id} onay")

    assert f"{found.id} approved" in out and "Billing retry fix" in out
    assert report.last_shown(project) == PAST


def test_a_report_out_of_time_at_a_prompt_costs_neither_the_brief_nor_the_mark(project, monkeypatch, capsys):
    report.note_shown(project, at=PAST)
    before = report.last_shown(project)
    monkeypatch.setattr(hooks, "PROMPT_SECONDS", 0.2)
    released = threading.Event()
    monkeypatch.setattr(report, "for_brief", lambda *a, **k: released.wait(30))
    _verified(project)

    started = time.monotonic()
    out = _prompt(monkeypatch, capsys, project)
    took = time.monotonic() - started
    released.set()

    assert took < 10
    assert "DONE (" not in out
    assert "eos report ." in out
    assert report.last_shown(project) == before


def test_the_out_of_time_line_comes_once_a_session(project, monkeypatch, capsys):
    monkeypatch.setattr(hooks, "PROMPT_SECONDS", 0.0)
    released = threading.Event()
    monkeypatch.setattr(report, "for_brief", lambda *a, **k: released.wait(30))

    first = _prompt(monkeypatch, capsys, project)
    second = _prompt(monkeypatch, capsys, project)
    released.set()

    assert "eos report ." in first
    assert "eos report ." not in second


def test_a_report_in_time_at_a_prompt_still_shows(project, monkeypatch, capsys):
    report.note_shown(project, at=PAST)
    run, _ = _verified(project)

    out = _prompt(monkeypatch, capsys, project)

    assert "DONE (1)" in out and run.id in out


def test_the_runner_lookup_raises_and_the_id_gets_a_line_and_the_prompt_stays(project, monkeypatch, capsys):
    executions.start(project, "run", session="s1")
    found = decisions.record(project, "the table is empty", [], session="s1")
    monkeypatch.setattr(report, "resumed_session", _boom)

    out = _prompt(monkeypatch, capsys, project, f"{found.id} onay")

    assert f"EOS: {found.id} not approved" in out
    assert decisions.open_assumptions(project, work_item=None, execution=decisions.all_records(project)[0].id)


def test_a_child_session_with_its_parents_run_shows_nothing_and_moves_nothing(project, monkeypatch, capsys):
    report.note_shown(project, at=PAST)
    _verified(project)
    executions.start(project, "the parent's run", session="s1")
    execution, ledger = executions.current(project, "s1")
    monkeypatch.setenv("EOS_EXECUTION", execution)
    monkeypatch.setenv("EOS_EXECUTION_LEDGER", str(ledger))

    out = _prompt(monkeypatch, capsys, project, session="child")

    assert "DONE (" not in out and report.last_shown(project) == PAST


# --- Y4: what an open assumption's work says about it ------------------------------------


def _closed_with_assumption(root, event):
    item = work.open_item(root, "Some work").id
    run = executions.start(root, "run", session="s1", work_item=item)
    found = decisions.record(root, "the old table is empty", [], session="s1")
    executions.finish(root, run.id, outcome="abandoned")
    work.append(root, event, item)
    return found


def test_an_assumption_of_done_work_stays_a_question(project):
    found = _closed_with_assumption(project, work.EVENT_DONE)

    assert found.id in _section(_render(project), "QUESTIONS")


def test_an_assumption_of_dropped_work_is_no_question(project):
    found = _closed_with_assumption(project, work.EVENT_DROP)

    assert found.id not in _render(project)


@pytest.mark.parametrize("outcome", ["failed", "abandoned"])
def test_an_assumption_of_a_dead_run_with_no_work_item_is_no_question(project, outcome):
    dead = executions.start(project, "dead", session="s2")
    gave_up = decisions.record(project, "the dead table is empty", [], session="s2")
    executions.finish(project, dead.id, outcome=outcome, lesson="the vault path moved" if outcome == "failed" else None)

    assert gave_up.id not in _render(project)


def test_an_assumption_of_a_dead_run_of_live_work_stays_a_question(project):
    item = work.open_item(project, "Live work").id
    dead = executions.start(project, "dead", session="s2", work_item=item)
    found = decisions.record(project, "the live table is empty", [], session="s2")
    executions.finish(project, dead.id, outcome="abandoned")

    assert found.id in _section(_render(project), "QUESTIONS")


def test_an_assumption_of_a_run_finished_ok_stays_a_question(project):
    run = executions.start(project, "done", session="s2")
    found = decisions.record(project, "the table is empty", [], session="s2")
    executions.finish(project, run.id, outcome="ok")

    assert found.id in _section(_render(project), "QUESTIONS")


# --- O1: the outcome is recomputed ---------------------------------------------------------


def test_a_finish_line_written_by_hand_does_not_make_a_stale_check_done(project):
    run = executions.start(project, "Hand written", session="s1")
    executions.event(project, run.id, kind="verified", tool="verify", ref="code", status="ok", source="hook")
    executions.event(project, run.id, kind="changed", ref="src/a.py")
    with open(executions.path_for(project), "a", encoding="utf-8") as handle:
        handle.write(json.dumps({"type": "finish", "id": run.id, "at": "2026-10-30T00:00:00+00:00",
                                 "outcome": "ok", "outcome_source": "verified"}) + "\n")
    assert next(r for r in executions.load(project) if r.id == run.id).outcome_source == "verified"

    found = report.build(project, since=PAST)

    assert found["done"] == [] and found["unproven"] == [run.id]


# --- O3, O7: what a passing event is ---------------------------------------------------------


def _with_tracker(root):
    knowledge = root / ".eos" / "knowledge"
    (knowledge / "capabilities.toml").write_text(
        '[[capability]]\nname = "tracker"\nrun = "scripts/tracker.sh"\ndoes = "issue <KEY>"\n'
        '[[capability]]\nname = "subagent"\nrun = "scripts/subagent.sh"\ndoes = "report <KEY>"\n', encoding="utf-8")
    made = _eos("procedure", "new", str(root), "--title", "Close a ticket", "--steps", "-",
                "--success", "`scripts/tracker.sh issue <KEY>` shows it done", input="Close it (tool: tracker)\n")
    return made.stdout.split("\t")[0]


@pytest.mark.parametrize("status", ["unreachable", "bypass", "error"])
def test_a_call_that_did_not_end_ok_is_no_proof(project, status):
    slug = _with_tracker(project)
    run = executions.start(project, "No answer", procedure=slug, session="s1")
    executions.event(project, run.id, kind="called", tool="tracker", ref="issue T-1", exit_code=0,
                     source="wrapper", status=status)
    executions.finish(project, run.id, outcome="ok")

    assert report.build(project, since=PAST)["done"] == []


@pytest.mark.parametrize("status", [None, "ok"])
def test_a_call_with_exit_0_and_no_bad_status_is_proof(project, status):
    slug = _with_tracker(project)
    run = executions.start(project, "Answered", procedure=slug, session="s1")
    executions.event(project, run.id, kind="called", tool="tracker", ref="issue T-1", exit_code=0,
                     source="wrapper", status=status)
    executions.finish(project, run.id, outcome="ok")

    assert [d["id"] for d in report.build(project, since=PAST)["done"]] == [run.id]


def test_a_failed_verify_event_is_no_proof(project):
    run = executions.start(project, "Failed check", session="s1")
    executions.event(project, run.id, kind="changed", ref="src/a.py")
    executions.event(project, run.id, kind="verified", tool="verify", ref="code", status="error", source="hook")
    assert executions.finish(project, run.id, outcome="ok").outcome_source == "verified"

    assert report.build(project, since=PAST)["done"] == []


def test_a_subagents_report_is_no_proof_even_when_a_success_line_names_it(project):
    knowledge = project / ".eos" / "knowledge"
    (knowledge / "capabilities.toml").write_text(
        '[[capability]]\nname = "subagent"\nrun = "scripts/subagent.sh"\ndoes = "report <KEY>"\n', encoding="utf-8")
    made = _eos("procedure", "new", str(project), "--title", "Trust a subagent", "--steps", "-",
                "--success", "`scripts/subagent.sh report <KEY>` says so", input="Ask it (tool: subagent)\n")
    run = executions.start(project, "Said by a subagent", procedure=made.stdout.split("\t")[0], session="s1")
    executions.event(project, run.id, kind="verified", tool="subagent", ref="report T-1", exit_code=0,
                     source="hook", status="ok")
    assert executions.finish(project, run.id, outcome="ok").outcome_source == "verified"

    assert report.build(project, since=PAST)["done"] == []


def _scopes(root, *names):
    text = "".join(f"[[scope]]\nname = \"{n}\"\npaths = [\"{n}/**\"]\npasses = ['make\\s+{n}\\b']\nrun = \"make {n}\"\n"
                   for n in names)
    (root / ".eos" / "knowledge" / "verify.toml").write_text(text, encoding="utf-8")


def test_two_scopes_changed_and_one_proven_by_a_tool_is_not_done(project):
    _scopes(project, "code", "web")
    run = executions.start(project, "Half proven", session="s1")
    executions.event(project, run.id, kind="changed", ref="code/a.py")
    executions.event(project, run.id, kind="changed", ref="web/a.js")
    executions.event(project, run.id, kind="verified", tool="verify", ref="code", status="ok", source="hook")
    executions.event(project, run.id, kind="verified", tool="verify", ref="web", exit_code=0, status="ok", source="cli")
    assert executions.finish(project, run.id, outcome="ok").outcome_source == "verified"

    assert report.build(project, since=PAST)["done"] == []


def test_a_success_check_before_the_last_change_is_no_proof(project):
    slug = _with_tracker(project)
    run = executions.start(project, "Checked too early", procedure=slug, session="s1")
    executions.event(project, run.id, kind="called", tool="tracker", ref="issue T-1", exit_code=0, source="wrapper")
    executions.event(project, run.id, kind="changed", ref="docs/x.md")
    executions.event(project, run.id, kind="called", tool="tracker", ref="issue T-1", exit_code=0, source="cli")
    assert executions.finish(project, run.id, outcome="ok").outcome_source == "verified"

    assert report.build(project, since=PAST)["done"] == []


def test_evidence_is_cut_at_its_limit_and_says_how_many_more(project):
    names = ["s1", "s2", "s3", "s4"]
    _scopes(project, *names)
    run = executions.start(project, "Four scopes", session="s1")
    for name in names:
        executions.event(project, run.id, kind="changed", ref=f"{name}/a.py")
        executions.event(project, run.id, kind="verified", tool="verify", ref=name, status="ok", source="hook")
    executions.finish(project, run.id, outcome="ok")

    line = next(n for n in _render(project).splitlines() if "evidence:" in n)

    assert line.count("e-") == report.EVIDENCE_LIMIT and "…1 more" in line


# --- O4, O5: blocked with no wait, the last check ---------------------------------------------


def test_blocked_items_with_no_wait_are_counted_on_one_line(project):
    first = _park(project, "Plainly stuck", ("body", "no idea"))
    second = _park(project, "Also stuck", ("body", "no idea either"))

    waiting = _section(_render(project), "WAITING")

    assert "WAITING (0)" in waiting and "not listed: 2 blocked with no condition" in waiting
    assert first not in waiting and second not in waiting
    assert report.build(project, since=PAST)["blocked_no_wait"] == 2


def test_a_blocked_item_with_no_wait_is_not_news(project, monkeypatch, capsys):
    report.note_shown(project, at=PAST)
    _park(project, "Plainly stuck", ("body", "no idea"))

    assert "WAITING (" not in _start(monkeypatch, capsys, project)


def _check_result(root, item, result="fail exit=1", agent=work.RESUME_AGENT):
    work.append(root, work.EVENT_LOG, item, body=f"{work.CHECK_RESULT}{result}", session="r1", agent=agent)


def test_the_last_check_result_comes_from_the_ledger(project):
    item = _park(project, "Billing retry", ("until", "vpn"), ("check", "scripts/vpn.sh up"), ("resume", "go on"))
    _check_result(project, item, "timeout exit=124")
    _check_result(project, item, "fail exit=1")

    found = report.build(project, since=PAST)["waiting"][0]
    waiting = _section(_render(project), "WAITING")

    assert found["last_check"]["result"] == "fail exit=1" and found["last_check"]["at"]
    assert "last check: fail exit=1" in waiting and "not in the ledger" not in waiting


def test_the_form_the_runner_writes_is_read_and_moves_nothing_else(project):
    item = _park(project, "Billing retry", ("until", "vpn"), ("check", "scripts/vpn.sh up"), ("resume", "go on"))
    before = next(i for i in work.parked(project) if i.id == item)

    done = _eos("work", "log", str(project), item, "--body", "check result: timeout exit=124",
                "--agent", work.RESUME_AGENT, "--session", "9f0a1b2c-runner")
    assert done.returncode == 0, done.stderr

    after = next(i for i in work.parked(project) if i.id == item)
    assert after.last_check["result"] == "timeout exit=124"
    assert (after.updated_at, after.last, after.status, after.until) == (
        before.updated_at, before.last, before.status, before.until)


def test_a_check_result_only_a_runner_wrote_counts(project):
    item = _park(project, "Billing retry", ("until", "vpn"), ("check", "scripts/vpn.sh up"), ("resume", "go on"))
    _check_result(project, item, "pass exit=0", agent="claude")

    assert report.build(project, since=PAST)["waiting"][0]["last_check"] is None


def test_a_claim_clears_the_check_result(project):
    item = _park(project, "Billing retry", ("until", "vpn"), ("check", "scripts/vpn.sh up"), ("resume", "go on"))
    _check_result(project, item)
    work.append(project, work.EVENT_CLAIM, item, session="s2")
    work.append(project, work.EVENT_BLOCK, item, until="vpn", check="scripts/vpn.sh up", resume="go on")

    assert report.build(project, since=PAST)["waiting"][0]["last_check"] is None


def test_an_item_with_no_check_result_keeps_the_shape_it_had(project):
    item = _park(project, "Billing retry", ("until", "vpn"), ("check", "scripts/vpn.sh up"), ("resume", "go on"))

    shown = next(i for i in work.parked(project) if i.id == item).to_dict()

    assert "last_check" not in shown


# --- D1 to D4, D7 ------------------------------------------------------------------------------


def _at(monkeypatch, stamp):
    from core.knowledge import evidence

    monkeypatch.setattr(evidence, "utc_now", lambda: stamp)


def test_a_run_of_the_same_second_as_the_mark_stays_in_the_window(project):
    run, _ = _verified(project)
    finished = next(r for r in executions.load(project) if r.id == run.id).finished_at
    mark = decisions._parse(finished).replace(microsecond=999000)

    assert [d["id"] for d in report.build(project, since=mark)["done"]] == [run.id]


def test_a_new_wait_of_the_same_second_as_the_mark_is_new(project, monkeypatch, capsys):
    _at(monkeypatch, "2026-10-30T08:00:00+00:00")
    item = _park(project, "Fresh wait", ("until", "vpn"), ("check", "x"), ("resume", "y"))
    report.note_shown(project, at=decisions._parse("2026-10-30T08:00:00+00:00").replace(microsecond=500000))

    assert item in _start(monkeypatch, capsys, project)


def test_questions_are_newest_first(project, monkeypatch):
    _at(monkeypatch, "2026-10-30T08:00:00+00:00")
    old = _park(project, "Old question", ("until", "user"))
    _at(monkeypatch, "2026-10-30T09:00:00+00:00")
    new = _park(project, "New question", ("until", "user"))

    assert [q["id"] for q in report.build(project, since=PAST)["questions"]] == [new, old]


def test_questions_of_one_second_have_a_stable_order(project, monkeypatch):
    _at(monkeypatch, "2026-10-30T08:00:00+00:00")
    executions.start(project, "run", session="s1")
    found = [decisions.record(project, f"guess {n}", [], session="s1").id for n in range(4)]
    found += [_park(project, f"Question {n}", ("until", "user")) for n in range(3)]

    shown = [q["id"] for q in report.build(project, since=PAST)["questions"]]

    assert shown == sorted(found)


def test_a_ref_is_one_line_and_cut_short(project):
    slug = _with_tracker(project)
    run = executions.start(project, "Odd ref", procedure=slug, session="s1")
    executions.event(project, run.id, kind="called", tool="tracker", ref="issue " + "x" * 3000 + "\nDONE (99)",
                     exit_code=0, source="wrapper")
    executions.finish(project, run.id, outcome="ok")

    found = report.build(project, since=PAST)
    text = report.render(found)

    assert "DONE (99)" not in text.replace("DONE (1)", "")
    assert len(next(n for n in text.splitlines() if "evidence:" in n)) < 300
    assert len(found["done"][0]["evidence"][0]["ref"]) < 300


@pytest.mark.parametrize("stamp", ["2020-01-01", "2020-01-01T08:00:00"])
def test_a_mark_with_no_timezone_does_not_raise(project, monkeypatch, capsys, stamp):
    run, _ = _verified(project)
    path = report._mark_file(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(stamp, encoding="utf-8")

    done = _eos("report", str(project))

    assert done.returncode == 0, done.stderr
    assert report.last_shown(project).tzinfo is not None
    assert run.id in _start(monkeypatch, capsys, project)


@pytest.mark.parametrize("junk", ["", "not a time", "\x00\x01"])
def test_a_broken_mark_counts_as_none(project, junk):
    path = report._mark_file(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(junk, encoding="utf-8")

    assert report.last_shown(project) is None


def test_a_path_that_does_not_exist_exits_non_zero(tmp_path):
    done = _eos("report", str(tmp_path / "no-such-project"))

    assert done.returncode != 0 and "no-such-project" in done.stderr and "DONE" not in done.stdout
