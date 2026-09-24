"""The execution ledger (ADR-022): what a session did, recorded where it happened.

The acceptance gate (test_operational_memory) proves the capability exists.
These prove the properties the ADR chose it for: a lost line hides nothing,
a finish cannot contradict itself, capture from a shell needs no interpreter
and never fails the command around it, and one session id reaches every
store without anyone passing it.
"""
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from core import executions, index, notes, work

REPO = Path(__file__).resolve().parents[1]
EOS = [sys.executable, str(REPO / "core" / "eos.py")]
HELPER = REPO / "bin" / "eos-event"


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    (root / ".eos").mkdir(parents=True)
    monkeypatch.setenv("EOS_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("EOS_EXECUTION", raising=False)
    monkeypatch.delenv("EOS_EXECUTION_LEDGER", raising=False)
    return root


def _run(args, env=None):
    return subprocess.run(EOS + args, capture_output=True, text=True, encoding="utf-8",
                          env=env or os.environ.copy())


def _helper(args, env):
    return subprocess.run(["/bin/sh", str(HELPER), *args], capture_output=True, text=True, env=env)


# --- the fold ------------------------------------------------------------------------


def test_an_event_whose_start_line_was_lost_still_shows_the_run(project):
    ledger = executions.path_for(project)
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text(json.dumps({"type": "event", "execution": "x-orphan", "at": "2026-09-24T09:00:00+00:00",
                                  "kind": "ran", "tool": "build"}) + "\n", encoding="utf-8")

    found = executions.load(project)

    assert [r.id for r in found] == ["x-orphan"]
    assert found[0].events[0].tool == "build"
    assert found[0].open


def test_a_bad_line_costs_itself_and_nothing_else(project):
    run = executions.start(project, "Deploy", session="s-1")
    with open(executions.path_for(project), "a", encoding="utf-8") as handle:
        handle.write("<<<<<<< HEAD\n{not json\n")
    executions.event(project, run.id, kind="ran", tool="build")

    assert executions.load(project)[0].events[0].tool == "build"


def test_finishing_twice_with_the_same_outcome_changes_nothing(project):
    run = executions.start(project, "Deploy", session="s-1")
    executions.finish(project, run.id, outcome="ok")
    lines = executions.path_for(project).read_text().count("\n")

    executions.finish(project, run.id, outcome="ok")

    assert executions.path_for(project).read_text().count("\n") == lines


def test_a_finished_run_cannot_also_have_the_other_outcome(project):
    run = executions.start(project, "Deploy", session="s-1")
    executions.finish(project, run.id, outcome="ok")

    with pytest.raises(ValueError, match="already finished"):
        executions.finish(project, run.id, outcome="failed")


def test_unknown_kinds_and_outcomes_are_refused(project):
    run = executions.start(project, "Deploy", session="s-1")
    with pytest.raises(ValueError):
        executions.event(project, run.id, kind="happened")
    with pytest.raises(ValueError):
        executions.finish(project, run.id, outcome="mostly")


def test_a_ref_carrying_a_credential_is_refused(project):
    run = executions.start(project, "Deploy", session="s-1")
    with pytest.raises(ValueError, match="credential"):
        executions.event(project, run.id, kind="called", ref="token=ghp_abcdefghijklmnop1234")


# --- the current execution ------------------------------------------------------------


def test_an_event_without_an_id_goes_to_this_sessions_open_run(project):
    run = executions.start(project, "Deploy", session="s-7")

    executions.event(project, kind="ran", tool="build", session="s-7")
    executions.finish(project, outcome="ok", session="s-7")

    back = executions.load(project)[0]
    assert back.id == run.id and back.outcome == "ok" and back.events[0].tool == "build"


def test_finish_clears_the_pointer_so_a_later_event_does_not_attach(project):
    executions.start(project, "Deploy", session="s-7")
    executions.finish(project, outcome="ok", session="s-7")

    with pytest.raises(ValueError, match="none is open"):
        executions.event(project, kind="ran", tool="build", session="s-7")


# --- the shell helper -----------------------------------------------------------------


def _env(tmp_path, **extra):
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "EOS_STATE_DIR": str(tmp_path / "state")}
    env.update(extra)
    return env


def test_the_shell_helper_appends_to_the_sessions_open_run_with_no_interpreter(project, tmp_path):
    run = executions.start(project, "Deploy", session="s-sh")

    done = _helper(["--kind", "ran", "--tool", "build", "--target", "staging",
                    "--ref", "logs/build-118.log", "--exit", "0", "--ms", "4200"],
                   _env(tmp_path, CLAUDE_CODE_SESSION_ID="s-sh"))

    assert done.returncode == 0, done.stderr
    event = executions.load(project)[0].events[0]
    assert (run.id, event.tool, event.target, event.ref, event.exit_code, event.ms) == \
        (run.id, "build", "staging", "logs/build-118.log", 0, 4200)
    assert event.session == "s-sh"


def test_the_shell_helper_escapes_quotes_and_drops_line_breaks(project, tmp_path):
    executions.start(project, "Deploy", session="s-sh")

    _helper(["--kind", "ran", "--tool", "build", "--body", 'said "done"\nthen\\left'],
            _env(tmp_path, CLAUDE_CODE_SESSION_ID="s-sh"))

    assert executions.load(project)[0].events[0].body == 'said "done"then\\left'


@pytest.mark.parametrize("env_extra", [
    {},                                          # no session at all
    {"CLAUDE_CODE_SESSION_ID": "nobody-open"},   # a session with no open run
])
def test_the_shell_helper_never_fails_and_writes_nothing_when_nothing_is_open(project, tmp_path, env_extra):
    done = _helper(["--kind", "ran", "--tool", "build"], _env(tmp_path, **env_extra))

    assert done.returncode == 0
    assert done.stdout == "" and done.stderr == ""
    assert not executions.path_for(project).exists()


def test_the_shell_helper_never_fails_on_an_unwritable_ledger(tmp_path):
    env = _env(tmp_path, EOS_EXECUTION="x-1", EOS_EXECUTION_LEDGER="/nonexistent/dir/executions.jsonl")
    done = _helper(["--kind", "ran", "--tool", "build"], env)
    assert done.returncode == 0 and done.stderr == ""


def test_the_shell_helper_ignores_an_unknown_kind(project, tmp_path):
    executions.start(project, "Deploy", session="s-sh")
    _helper(["--kind", "happened"], _env(tmp_path, CLAUDE_CODE_SESSION_ID="s-sh"))
    assert executions.load(project)[0].events == []


def test_exported_variables_win_over_the_pointer(project, tmp_path):
    first = executions.start(project, "First", session="s-sh")
    second = executions.start(project, "Second", session="other")
    ledger = executions.path_for(project)

    _helper(["--kind", "ran", "--tool", "build"],
            _env(tmp_path, CLAUDE_CODE_SESSION_ID="s-sh",
                 EOS_EXECUTION=second.id, EOS_EXECUTION_LEDGER=str(ledger)))

    by_id = {r.id: r for r in executions.load(project)}
    assert by_id[second.id].events and not by_id[first.id].events


# --- the CLI ------------------------------------------------------------------------


def test_the_cli_round_trip_start_event_finish_show(project):
    env = {**os.environ, "EOS_SESSION": "s-cli"}
    started = _run(["run", "start", str(project), "--title", "Deploy wallet", "--target", "staging"], env)
    assert started.returncode == 0, started.stderr
    run_id = started.stdout.strip()

    assert _run(["run", "event", str(project), "--kind", "ran", "--tool", "build",
                 "--exit", "0"], env).returncode == 0
    assert _run(["run", "finish", str(project), "--outcome", "ok"], env).returncode == 0

    shown = _run(["run", "show", str(project), run_id], env)
    assert "outcome   ok" in shown.stdout and "build" in shown.stdout
    listed = _run(["run", "list", str(project), "--session", "s-cli"], env)
    assert run_id in listed.stdout


def test_run_start_without_any_session_says_how_capture_can_still_reach_it(project):
    env = {k: v for k, v in os.environ.items() if k not in ("EOS_SESSION", "CLAUDE_CODE_SESSION_ID")}
    done = _run(["run", "start", str(project), "--title", "Deploy"], env)
    assert done.returncode == 0
    assert "EOS_EXECUTION=" in done.stderr


# --- one session id across stores -----------------------------------------------------


def test_a_work_claim_and_a_note_written_from_the_cli_carry_the_harness_session(project):
    env = {**os.environ, "CLAUDE_CODE_SESSION_ID": "s-harness"}
    assert _run(["work", "add", str(project), "--title", "Deploy wallet", "--claim"], env).returncode == 0
    assert _run(["note", "add", str(project), "--kind", "finding", "--title", "Staging needs the VPN",
                 "--body", "Without it the health check times out."], env).returncode == 0

    holders = [h.get("session") for item in work.items(project) for h in item.holders]
    assert holders == ["s-harness"]
    assert [n.session for n in notes.load_notes(project)] == ["s-harness"]


def test_work_list_session_stays_a_filter_and_is_not_filled_in(project):
    env = {**os.environ, "CLAUDE_CODE_SESSION_ID": "s-harness"}
    work.open_item(project, "Someone else's item", claim=True, session="other")

    listed = _run(["work", "list", str(project)], env)

    assert "Someone else's item" in listed.stdout


# --- the index ------------------------------------------------------------------------


def test_the_index_holds_executions_and_their_timeline_and_finds_them_by_title(project):
    run = executions.start(project, "Deploy the wallet service", session="s-1", target="staging")
    executions.event(project, run.id, kind="ran", tool="build", exit_code=0)
    executions.event(project, run.id, kind="called", tool="deploy")
    executions.finish(project, run.id, outcome="failed", lesson="The image came from the wrong branch")

    db = index.build(project).path
    conn = sqlite3.connect(db)
    row = conn.execute("SELECT outcome, target, events, lesson FROM execution WHERE id = ?", (run.id,)).fetchone()
    timeline = conn.execute("SELECT tool FROM execution_event WHERE execution = ? ORDER BY ord",
                            (run.id,)).fetchall()
    conn.close()

    assert row == ("failed", "staging", 2, "The image came from the wrong branch")
    assert [t[0] for t in timeline] == ["build", "deploy"]
    _, hits = index.search(db, "wallet wrong branch")
    assert ("execution", run.id) in {(h[0], h[1]) for h in hits}


def test_a_new_event_makes_the_index_stale(project):
    run = executions.start(project, "Deploy", session="s-1")
    db = index.build(project).path
    before = sqlite3.connect(db).execute("SELECT events FROM execution").fetchone()[0]

    executions.event(project, run.id, kind="ran", tool="build")
    index.refresh(project)

    assert sqlite3.connect(db).execute("SELECT events FROM execution").fetchone()[0] == before + 1
