"""A note EOS itself writes is a `changed` event on the calling session's open run (E1).

Measured on the host: of the files a run's commits touched that no event named,
most were notes `eos note add` and `eos note amend` had written -- the editor
hooks never see a file a program writes. The command that writes the note knows
the file and, through the session pointer, the run.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

from core import executions, notes

EOS = [sys.executable, str(Path(__file__).resolve().parents[1] / "core" / "eos.py")]


def _init(root: Path) -> Path:
    root.mkdir(parents=True)
    assert subprocess.run(EOS + ["init", str(root), "--no-ai"], capture_output=True).returncode == 0
    return root


def _eos(*args, session="s1", stdin=None):
    env = {**os.environ, "EOS_SESSION": session} if session else {
        k: v for k, v in os.environ.items() if k not in ("EOS_SESSION", "CLAUDE_CODE_SESSION_ID")}
    done = subprocess.run(EOS + list(args), capture_output=True, text=True, env=env, input=stdin)
    assert done.returncode == 0, done.stderr
    return done.stdout


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("EOS_STATE_DIR", str(tmp_path / "state"))
    for name in ("EOS_EXECUTION", "EOS_EXECUTION_LEDGER"):
        monkeypatch.delenv(name, raising=False)
    return _init(tmp_path / "proj")


def _changed(root, run_id):
    record = {r.id: r for r in executions.load(root)}[run_id]
    return [(e.ref, e.tool, e.source) for e in record.events if e.kind == "changed"]


def _rel(path, root):
    return Path(path).resolve().relative_to(Path(root).resolve()).as_posix()


def test_note_add_records_the_note_it_wrote_on_the_open_run(project):
    run = _eos("run", "start", str(project), "--title", "Find the timeout").split()[0]
    out = _eos("note", "add", str(project), "--kind", "finding", "--tags", "t", "--title", "Staging needs the VPN",
               "--body", "Without it the health check times out.")
    path = out.split("Note written to ", 1)[1].strip()

    assert _changed(project, run) == [(_rel(path, project), "eos", "cli")]


def test_note_amend_records_the_note_it_rewrote(project):
    path = notes.add_note(project, kind="finding", title="Staging needs the VPN",
                          body="Without it the health check times out.")
    run = _eos("run", "start", str(project), "--title", "Recheck the VPN").split()[0]
    _eos("note", "amend", str(path), str(project), "--body", "Without it every request times out.")

    assert _changed(project, run) == [(_rel(path, project), "eos", "cli")]


def test_procedure_new_records_the_procedure_note(project):
    run = _eos("run", "start", str(project), "--title", "Write the release steps").split()[0]
    out = _eos("procedure", "new", str(project), "--title", "Release the service", "--steps", "-",
               stdin="Tag it\n")
    path = out.strip().split("\t")[1]

    assert _changed(project, run) == [(_rel(path, project), "eos", "cli")]


def test_a_failed_runs_lesson_is_recorded_on_that_run(project):
    run = _eos("run", "start", str(project), "--title", "Deploy the wallet").split()[0]
    _eos("run", "finish", str(project), run, "--outcome", "failed", "--lesson", "The VPN was down.")

    [lesson] = notes.lessons_for(project, execution=run)
    assert _changed(project, run) == [(_rel(lesson.path, project), "eos", "cli")]


def test_without_an_open_run_the_note_is_written_and_nothing_is_recorded(project):
    _eos("note", "add", str(project), "--kind", "finding", "--tags", "t", "--title", "Staging needs the VPN",
         "--body", "Without it the health check times out.", session=None)
    _eos("note", "add", str(project), "--kind", "finding", "--tags", "t", "--title", "Prod needs the VPN too",
         "--body", "Same timeout there.", session="nobody-started-a-run")

    assert len(notes.load_notes(project)) == 2
    assert executions.load(project) == []


def test_a_note_written_after_the_success_check_leaves_the_run_verified(project):
    knowledge = project / ".eos" / "knowledge"
    knowledge.mkdir(parents=True, exist_ok=True)
    (knowledge / "capabilities.toml").write_text('[[capability]]\nname = "tracker"\nrun = "scripts/tracker.sh"\n',
                                                 encoding="utf-8")
    slug = _eos("procedure", "new", str(project), "--title", "Close a ticket", "--steps", "-",
                "--success", "`scripts/tracker.sh issue <KEY>` shows it done", stdin="Close it (tool: tracker)\n",
                session=None).split("\t")[0]
    run = _eos("run", "start", str(project), "--title", "Close FM-1", "--procedure", slug).split()[0]
    _eos("run", "event", str(project), run, "--kind", "called", "--tool", "tracker", "--ref", "issue FM-1",
         "--exit", "0")
    _eos("note", "add", str(project), "--kind", "finding", "--tags", "t", "--title", "FM-1 needed a second reviewer",
         "--body", "The first one was on leave.")
    record = {r.id: r for r in executions.load(project)}[run]

    assert _changed(project, run) and executions.outcome_source(project, record) == ("verified", [])


def test_run_finish_does_not_call_a_note_eos_wrote_a_change_outside_the_procedures_scope(project):
    notes.add_note(project, kind="procedure", title="Deploy wallet scope test",
                   body="## Steps\n1. build\n", scope=["src/wallet.py"])
    _eos("run", "start", str(project), "--title", "run it", "--procedure", "Deploy wallet scope test")
    _eos("note", "add", str(project), "--kind", "finding", "--tags", "t", "--title", "The wallet build is slow",
         "--body", "Most of it is the dependency download.")
    finished = _eos("run", "finish", str(project), "--outcome", "ok")

    assert ".eos/knowledge" not in finished


@pytest.fixture
def ws(tmp_path, monkeypatch):
    monkeypatch.setenv("EOS_STATE_DIR", str(tmp_path / "state"))
    for name in ("EOS_EXECUTION", "EOS_EXECUTION_LEDGER"):
        monkeypatch.delenv(name, raising=False)
    hub = _init(tmp_path / "hub")
    service = _init(tmp_path / "services" / "order-capture")
    (hub / ".eos" / "projects.toml").write_text('[[project]]\nroot = "../services/order-capture"\n',
                                                encoding="utf-8")
    return hub, service


def test_in_a_workspace_the_event_follows_the_run_to_its_projects_ledger(ws):
    """C5b: a run started from the workspace root about a service lives in the
    service's ledger; a note written into the service's store is a file of that
    project, so its ref is relative to the service, the run's own root."""
    hub, service = ws
    run = _eos("run", "start", str(hub), "--title", "Fix the order-capture timeout").split()[0]
    assert run in {r.id for r in executions.load(service)}
    out = _eos("note", "add", str(service), "--kind", "finding", "--tags", "t", "--title", "The timeout is the pool size",
               "--body", "Ten connections are not enough at peak.")
    path = out.split("Note written to ", 1)[1].strip()

    assert _changed(service, run) == [(_rel(path, service), "eos", "cli")]
    assert executions.load(hub) == []


def test_a_workspace_note_outside_the_runs_project_is_named_by_its_absolute_path(ws):
    """The workspace store's note is no file of the service the run is about: an
    absolute ref, the shape the editor hook records a file outside the project in."""
    hub, service = ws
    run = _eos("run", "start", str(hub), "--title", "Fix the order-capture timeout").split()[0]
    out = _eos("note", "add", str(hub), "--kind", "finding", "--tags", "t", "--title", "Pool sizes are per service",
               "--body", "Each service sizes its own pool.")
    path = out.split("Note written to ", 1)[1].strip()

    assert _changed(service, run) == [(str(Path(path).resolve()), "eos", "cli")]
