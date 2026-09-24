"""Lessons, decisions and derived confidence (ADR-024).

A failed run must leave something the next session can read; a recurring
failure must read as recurring; confidence is a word computed when asked and
never a field someone forgot to update; a check belongs to the run it
happened in; and a session is asked, once, about runs it left open.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from core import executions, notes, verification
from core.ai import writer

REPO = Path(__file__).resolve().parents[1]
EOS = [sys.executable, str(REPO / "core" / "eos.py")]

LESSON_BODY = "## What went wrong\nBuilt from main.\n\n## What was learned\nCheck the branch.\n\n## Next time\nRun git status first.\n"


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    (root / ".eos").mkdir(parents=True)
    monkeypatch.setenv("EOS_STATE_DIR", str(tmp_path / "state"))
    return root


def _procedure(project):
    return notes.parse_note(notes.add_note(project, kind="procedure", title="Deploy to staging",
                                           body="## Steps\n1. Build (tool: build)\n"))


# --- a failed run must leave a lesson -----------------------------------------------


def test_a_failed_run_without_a_lesson_is_refused_and_stays_open(project):
    run = executions.start(project, "Deploy", session="s")

    with pytest.raises(ValueError, match="must leave a lesson") as exc:
        executions.finish(project, run.id, outcome="failed")

    assert "--lesson" in str(exc.value) and "--kind lesson" in str(exc.value)
    assert executions.load(project)[0].open


def test_the_lesson_becomes_a_note_linked_to_the_run_and_the_procedure(project):
    slug = _procedure(project).procedure
    run = executions.start(project, "Deploy wallet", procedure=slug, target="staging", session="s")
    executions.event(project, run.id, kind="ran", tool="build", exit_code=2, ref="logs/b.log")

    executions.finish(project, run.id, outcome="failed", lesson="Built from main, not the ticket branch",
                      next_time="Check the branch before building")

    lesson, = notes.lessons_for(project, execution=run.id)
    assert (lesson.procedure, lesson.session) == (slug, "s")
    assert "build exited 2 (logs/b.log)" in notes.section_in(lesson.body, "What went wrong")
    assert notes.section_in(lesson.body, "Next time") == "Check the branch before building"


def test_the_same_lesson_twice_reads_as_a_recurring_failure_not_two_notes(project):
    first = executions.start(project, "Deploy", session="s")
    executions.finish(project, first.id, outcome="failed", lesson="Registry was down")
    second = executions.start(project, "Deploy again", session="s")
    executions.finish(project, second.id, outcome="failed", lesson="Registry was down")

    lessons = notes.lessons_for(project)
    assert len(lessons) == 1
    assert second.id in notes.section_in(lessons[0].body, "Seen again")


def test_a_lesson_written_first_lets_the_failed_run_finish_without_one(project):
    run = executions.start(project, "Deploy", session="s")
    notes.add_note(project, kind="lesson", title="Registry was down", body=LESSON_BODY, execution=run.id)

    executions.finish(project, run.id, outcome="failed")

    assert executions.load(project)[0].outcome == "failed"


def test_a_lesson_and_a_decision_without_their_sections_are_refused(project):
    with pytest.raises(ValueError, match="What was learned"):
        notes.add_note(project, kind="lesson", title="L", body="## What went wrong\nx\n\n## Next time\ny\n")
    with pytest.raises(ValueError, match="Component"):
        notes.add_note(project, kind="decision", title="D", body="## Why\nx\n\n## When\ny\n\n## Component\n\n")


def test_amending_a_lesson_keeps_the_run_that_taught_it(project, tmp_path):
    (project / "deploy.sh").write_text("v1\n")
    run = executions.start(project, "Deploy", session="s")
    path = notes.add_note(project, kind="lesson", title="Registry was down", body=LESSON_BODY,
                          execution=run.id, scope=["deploy.sh"])
    (project / "deploy.sh").write_text("v2\n")

    notes.amend_note(path, project, body=LESSON_BODY.replace("Built from main", "Built from a stale main"))

    assert notes.parse_note(path).execution == run.id


# --- confidence -------------------------------------------------------------------


def _run(project, slug, outcome):
    run = executions.start(project, "Deploy", procedure=slug, session="s")
    executions.finish(project, run.id, outcome=outcome, lesson="x broke" if outcome == "failed" else None)


def test_confidence_words(project):
    note = _procedure(project)
    assert notes.procedure_confidence(note) == "unverified"

    _run(project, note.procedure, "ok")
    note = notes.parse_note(note.path)
    verified = note.last_verified
    assert notes.procedure_confidence(note) == "fresh"
    assert notes.procedure_confidence(note, now=_days_after(verified, 60)) == "aging"
    assert notes.procedure_confidence(note, now=_days_after(verified, 120)) == "stale"

    _run(project, note.procedure, "failed")
    assert notes.procedure_confidence(notes.parse_note(note.path)) == "failing"
    assert "confidence" not in note.path.read_text()


def _days_after(stamp, days):
    import datetime
    return (datetime.datetime.fromisoformat(stamp) + datetime.timedelta(days=days)).isoformat()


# --- a check belongs to its run ------------------------------------------------------


def test_verify_inside_an_open_run_is_tied_to_it_and_lands_on_its_timeline(project):
    env = {**os.environ, "EOS_SESSION": "s-v", "EOS_STATE_DIR": os.environ["EOS_STATE_DIR"]}
    run = executions.start(project, "Deploy", session="s-v")

    done = subprocess.run(EOS + ["verify", str(project), "HEALTH_OK", "--outcome", "passed",
                                 "--command", "health-check staging", "--exit-code", "0"],
                          capture_output=True, text=True, env=env)

    assert done.returncode == 0, done.stderr
    check, = verification.load(project)
    assert (check.session, check.execution) == ("s-v", run.id)
    assert [e.kind for e in executions.load(project)[0].events] == ["verified"]
    shown = subprocess.run(EOS + ["run", "show", str(project), run.id], capture_output=True, text=True, env=env)
    assert "verified  HEALTH_OK passed" in shown.stdout


# --- the Stop hook asks about open runs -----------------------------------------------


def _stop(project, session):
    writer._write_hooks(project, "0.0.0")
    env = {**os.environ, "PATH": f"{REPO / 'bin'}:{os.environ.get('PATH', '')}"}
    payload = json.dumps({"session_id": session, "cwd": str(project)})
    return subprocess.run([sys.executable, str(project / ".claude" / "hooks" / "eos-close.py")],
                          input=payload, capture_output=True, text=True, env=env)


def test_the_stop_hook_asks_once_about_a_run_left_open(project):
    run = executions.start(project, "Deploy", session="s-stop")

    done = _stop(project, "s-stop")

    assert done.returncode == 2
    assert run.id in done.stderr and "--outcome failed --lesson" in done.stderr


def test_the_stop_hook_lets_a_session_with_nothing_open_end(project):
    run = executions.start(project, "Deploy", session="s-stop")
    executions.finish(project, run.id, outcome="ok")
    assert _stop(project, "s-stop").returncode == 0


def test_a_verification_is_indexed_and_searchable(project):
    """Verification records lived only in their JSONL and no query could
    reach them -- an audit's finding. They are a table now, and a search row."""
    import sqlite3
    from core import index
    run = executions.start(project, "Deploy", session="s")
    verification.record(project, "HEALTH_OK", "passed", "health-check staging", 0,
                        session="s", execution=run.id)

    db = index.build(project).path

    _, rows = index.search(db, "HEALTH_OK health-check")
    assert ("verification", "HEALTH_OK#0") in {(r[0], r[1]) for r in rows}
    assert sqlite3.connect(db).execute("SELECT execution, session FROM verification").fetchone() == (run.id, "s")
