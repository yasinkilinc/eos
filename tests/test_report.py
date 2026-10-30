"""`eos report`: done only with proof, waiting with its condition, questions one line each."""
import datetime
import io
import json
import subprocess
import sys
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


def test_an_assumption_of_a_closed_work_item_or_a_dead_run_is_no_question(project):
    item = work.open_item(project, "Closed work").id
    closed = executions.start(project, "run", session="s1", work_item=item)
    moot = decisions.record(project, "the old table is empty", [], session="s1")
    executions.finish(project, closed.id, outcome="abandoned")
    work.append(project, work.EVENT_DONE, item)
    dead = executions.start(project, "dead", session="s2")
    gave_up = decisions.record(project, "the dead table is empty", [], session="s2")
    executions.finish(project, dead.id, outcome="abandoned")

    text = _render(project)

    assert moot.id not in text and gave_up.id not in text


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
    report.note_prompt(project, at=PAST)
    run, proof = _verified(project)

    out = _start(monkeypatch, capsys, project)

    assert "DONE (1)" in out and proof.id in out and run.id in out
    assert out.index("DONE (1)") < out.index("EOS brief")


def test_the_brief_says_nothing_when_nothing_is_new(project, monkeypatch, capsys):
    _verified(project)
    _park(project, "Old wait", ("until", "vpn"), ("check", "x"), ("resume", "y"))
    report.note_prompt(project, at=FUTURE)

    assert "DONE (" not in _start(monkeypatch, capsys, project)


def test_the_brief_says_nothing_before_any_prompt_was_seen(project, monkeypatch, capsys):
    _verified(project)

    assert "DONE (" not in _start(monkeypatch, capsys, project)


def test_a_new_wait_alone_is_new(project, monkeypatch, capsys):
    report.note_prompt(project, at=PAST)
    item = _park(project, "Fresh wait", ("until", "vpn"), ("check", "x"), ("resume", "y"))

    out = _start(monkeypatch, capsys, project)

    assert "WAITING (1)" in out and item in out


def test_a_claimed_run_alone_is_not_news(project, monkeypatch, capsys):
    report.note_prompt(project, at=PAST)
    run = executions.start(project, "Read only", session="s1")
    executions.finish(project, run.id, outcome="ok")

    assert "DONE (" not in _start(monkeypatch, capsys, project)


def test_the_prompt_hook_records_the_users_last_prompt(project, monkeypatch, capsys):
    assert report.last_prompt(project) is None

    _hook(monkeypatch, capsys, "user-prompt", project, prompt="please look at the billing retry")

    assert report.last_prompt(project) is not None


def test_a_notice_is_not_the_users_prompt(project, monkeypatch, capsys):
    _hook(monkeypatch, capsys, "user-prompt", project, prompt="<task-notification>finished</task-notification>")

    assert report.last_prompt(project) is None


def _runner_session(root, session="runner-1"):
    item = work.open_item(root, "Parked work").id
    work.append(root, work.EVENT_CLAIM, item, session=session, agent=work.RESUME_AGENT)
    return session


def test_the_prompt_of_a_resumed_session_is_not_the_users(project, monkeypatch, capsys):
    session = _runner_session(project)

    _hook(monkeypatch, capsys, "user-prompt", project, session=session, prompt="go on with the parked work")

    assert report.last_prompt(project) is None


def test_a_resumed_session_gets_no_report(project, monkeypatch, capsys):
    report.note_prompt(project, at=PAST)
    _verified(project)
    session = _runner_session(project)

    assert "DONE (" not in _start(monkeypatch, capsys, project, session=session)


def test_devin_gets_no_report_and_moves_no_mark(project, monkeypatch, capsys):
    report.note_prompt(project, at=PAST)
    _verified(project)

    assert "DONE (" not in _start(monkeypatch, capsys, project, "--host", "devin")
    _hook(monkeypatch, capsys, "user-prompt", project, "--host", "devin", prompt="please look at the billing retry")
    assert report.last_prompt(project) == PAST


def test_the_brief_caps_each_section_and_names_the_rest(project, monkeypatch, capsys):
    report.note_prompt(project, at=PAST)
    for n in range(report.BRIEF_LIMIT + 2):
        _verified(project, title=f"Ship part {n}", session=f"s{n}")

    out = _start(monkeypatch, capsys, project)

    assert f"DONE ({report.BRIEF_LIMIT + 2})" in out
    assert "…2 more: eos report ." in out
    assert sum(f"Ship part {n}" in out for n in range(report.BRIEF_LIMIT + 2)) == report.BRIEF_LIMIT
