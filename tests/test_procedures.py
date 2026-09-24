"""Procedures (ADR-023): how a recurring task is done here, and how it has gone.

The gate (test_operational_memory) proves a procedure parses and its counters
move. These prove the properties the ADR chose: steps are required, headings
are read the way a person writes them, counters move from one writer only and
survive every other rewrite, the audit catches a hand edit, and a host's own
catalogue is indexed without its counts leaking into the engine's.
"""
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from core import executions, index, notes

REPO = Path(__file__).resolve().parents[1]
EOS = [sys.executable, str(REPO / "core" / "eos.py")]

STEPS = """Deploying is three things in order.

## steps
1. Check the branch the ticket names (tool: git)
2) Build it (tool: build)
- Verify health (Tool: health)

## Prerequisites
- VPN is up

## Success
* health answers 200
"""


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    (root / ".eos").mkdir(parents=True)
    monkeypatch.setenv("EOS_STATE_DIR", str(tmp_path / "state"))
    return root


def _procedure(project, title="Deploy to staging", body=STEPS, **kw):
    return notes.parse_note(notes.add_note(project, kind="procedure", title=title, body=body, **kw))


def _run(args, stdin=None):
    return subprocess.run(EOS + args, capture_output=True, text=True, encoding="utf-8",
                          input=stdin, env=os.environ.copy())


# --- the note ------------------------------------------------------------------------


def test_a_procedure_without_steps_is_refused_and_told_to_be_a_finding(project):
    with pytest.raises(ValueError, match="## Steps"):
        notes.add_note(project, kind="procedure", title="Deploy", body="Just do it carefully.")


def test_sections_are_read_the_way_a_person_writes_them(project):
    note = _procedure(project)

    assert notes.procedure_steps(note) == [
        "Check the branch the ticket names (tool: git)", "Build it (tool: build)",
        "Verify health (Tool: health)"]
    assert notes.procedure_tools(note) == ["git", "build", "health"]
    assert notes.procedure_prerequisites(note) == ["VPN is up"]
    assert notes.procedure_success(note) == ["health answers 200"]


def test_a_new_procedure_starts_counted_at_zero_not_uncounted(project):
    note = _procedure(project)
    assert (note.procedure, note.runs_ok, note.runs_failed) == ("deploy-to-staging", 0, 0)


def test_an_explicit_slug_wins_over_the_title(project):
    assert _procedure(project, procedure="deploy-staging").procedure == "deploy-staging"


# --- the one writer --------------------------------------------------------------------


def _finish(project, slug, outcome, lesson=None):
    run = executions.start(project, "Deploy", procedure=slug, session="s-1")
    executions.finish(project, run.id, outcome=outcome, lesson=lesson)
    return run


def test_counters_move_per_outcome_and_abandoned_moves_nothing_but_the_pointer(project):
    note = _procedure(project)
    _finish(project, note.procedure, "ok")
    _finish(project, note.procedure, "failed", lesson="Built from the wrong branch.\nSecond line.")
    last = _finish(project, note.procedure, "abandoned")

    after = notes.parse_note(note.path)
    assert (after.runs_ok, after.runs_failed) == (1, 1)
    assert after.last_verified and after.last_execution == last.id
    failures = notes.procedure_known_failures(after)
    assert len(failures) == 1 and failures[0].endswith("Built from the wrong branch.")


def test_finishing_the_same_run_twice_counts_it_once(project):
    note = _procedure(project)
    run = _finish(project, note.procedure, "ok")
    executions.finish(project, run.id, outcome="ok")
    assert notes.parse_note(note.path).runs_ok == 1


def test_only_an_exact_slug_moves_counters(project):
    note = _procedure(project)
    _finish(project, "deploy", "ok")  # a prefix of the slug: a guess, not a name
    assert notes.parse_note(note.path).runs_ok == 0


def test_a_counter_update_keeps_every_other_front_matter_line(project):
    note = _procedure(project)
    raw = note.path.read_text()
    note.path.write_text(raw.replace("---\n\n", "owner: platform-team\n---\n\n", 1))

    _finish(project, note.procedure, "ok")

    text = note.path.read_text()
    assert "owner: platform-team" in text and "runs_ok: 1" in text
    assert notes.procedure_steps(notes.parse_note(note.path))


def test_amending_the_steps_does_not_reset_the_history(project, tmp_path):
    watched = project / "deploy.sh"
    watched.write_text("echo v1\n")
    note = _procedure(project, scope=["deploy.sh"])
    _finish(project, note.procedure, "ok")
    watched.write_text("echo v2\n")

    notes.amend_note(note.path, project, body=STEPS.replace("Build it", "Build and push it"))

    after = notes.parse_note(note.path)
    assert after.runs_ok == 1 and after.procedure == note.procedure
    assert "Build and push it (tool: build)" in notes.procedure_steps(after)


# --- the audit -------------------------------------------------------------------------


def test_the_audit_catches_a_hand_edited_counter_and_exits_1(project):
    note = _procedure(project)
    _finish(project, note.procedure, "ok")
    note.path.write_text(note.path.read_text().replace("runs_ok: 1", "runs_ok: 9"))

    done = _run(["procedure", "audit", str(project)])

    assert done.returncode == 1
    assert "counters say 9 ok" in done.stdout and "the ledger says 1" in done.stdout


def test_the_audit_names_never_verified_and_latest_failed_without_failing(project):
    never = _procedure(project, title="Rotate credentials",
                       body="## Steps\n1. Generate (tool: vault)\n")
    failing = _procedure(project)
    _finish(project, failing.procedure, "ok")
    _finish(project, failing.procedure, "failed", lesson="Registry down")

    report = {e["procedure"]: e for e in executions.audit_procedures(project)}

    assert "never verified by a finished run" in report[never.procedure]["problems"]
    assert any("latest run failed" in p for p in report[failing.procedure]["problems"])
    assert _run(["procedure", "audit", str(project)]).returncode == 0


# --- the CLI ---------------------------------------------------------------------------


def test_the_cli_writes_a_procedure_from_stdin_and_shows_its_recent_runs(project):
    written = _run(["procedure", "new", str(project), "--title", "Deploy to staging",
                    "--steps", "-", "--success", "health answers 200"],
                   stdin="Check the branch (tool: git)\nBuild (tool: build)\n")
    assert written.returncode == 0, written.stderr
    slug = written.stdout.split("\t")[0]
    _finish(project, slug, "ok")

    shown = _run(["procedure", "show", str(project), "staging"])
    assert " 1. Check the branch (tool: git)" in shown.stdout
    assert "1 ok / 0 failed" in shown.stdout and "recent runs" in shown.stdout
    listed = _run(["procedure", "list", str(project), "--tool", "build"])
    assert slug in listed.stdout


# --- a host's catalogue ----------------------------------------------------------------


def _with_catalogue(project, entries):
    catalogue = project / "catalogue.json"
    catalogue.write_text(json.dumps(entries), encoding="utf-8")
    (project / ".eos" / "config.toml").write_text(
        "[index]\n"
        f'extensions = ["{(REPO / "extensions" / "procedures.py").as_posix()}"]\n\n'
        "[procedures]\n"
        'catalogue = "catalogue.json"\n', encoding="utf-8")
    return catalogue


def test_a_hosts_catalogue_is_indexed_with_steps_and_last_state(project):
    _with_catalogue(project, [
        {"name": "topup", "summary": "Top up a prepaid line", "source": "catalog/topup.js",
         "steps": [{"name": "line", "tool": "api"}, {"name": "submit"}],
         "state": [{"env": "staging", "status": "FAILED", "updated_at": "2026-09-21T11:11:34Z",
                    "failed_step": "submit"}]},
        {"summary": "no name, skipped"},
    ])

    db = index.build(project).path
    conn = sqlite3.connect(db)
    steps = conn.execute("SELECT name, tool FROM procedure_step WHERE procedure = 'topup' ORDER BY ord").fetchall()
    failing = conn.execute("SELECT failed_step FROM procedure_state WHERE status = 'FAILED'").fetchall()
    issues = conn.execute("SELECT problem FROM build_issue WHERE source = 'procedures'").fetchall()
    conn.close()

    assert steps == [("line", "api"), ("submit", None)]
    assert failing == [("submit",)]
    assert any("no name" in problem for (problem,) in issues)
    _, hits = index.search(db, "top up prepaid")
    assert ("procedure", "topup") in {(h[0], h[1]) for h in hits}


def test_a_catalogue_change_makes_the_index_stale(project):
    catalogue = _with_catalogue(project, [{"name": "topup", "steps": [{"name": "line"}]}])
    db = index.build(project).path
    catalogue.write_text(json.dumps([{"name": "topup", "steps": [{"name": "line"}, {"name": "submit"}]}]))

    index.refresh(project)

    assert sqlite3.connect(db).execute("SELECT steps FROM catalogue_procedure").fetchone() == (2,)


def test_the_catalogues_counts_never_reach_a_procedure_notes_counters(project):
    note = _procedure(project, title="Top up a prepaid line", procedure="topup")
    _with_catalogue(project, [{"name": "topup", "steps": [{"name": "line"}],
                               "state": [{"env": "staging", "status": "COMPLETED"}]}])
    index.build(project)
    assert notes.parse_note(note.path).runs_ok == 0
