"""The task brief (M3): what a session is handed when it says what it is about to do.

The gate proves the brief finds the procedure and the runs within budget.
These prove the rules that keep a hook firing on every prompt from becoming
the cost it exists to remove: silence when nothing matched, no repeat of a
block already delivered, a hard budget, and a task text that is never stored.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from core import brief, executions, notes
from core.ai import writer

REPO = Path(__file__).resolve().parents[1]
EOS = [sys.executable, str(REPO / "core" / "eos.py")]

STEPS = "## Steps\n1. Check the branch (tool: git)\n2. Build it (tool: build)\n3. Verify health (tool: health)\n"


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    (root / ".eos").mkdir(parents=True)
    monkeypatch.setenv("EOS_STATE_DIR", str(tmp_path / "state"))
    return root


def _procedure(project, title="Deploy a service to staging"):
    return notes.parse_note(notes.add_note(project, kind="procedure", title=title, body=STEPS))


def _runs(project, slug, outcomes, target="staging"):
    made = []
    for outcome in outcomes:
        run = executions.start(project, "Deploy wallet", procedure=slug, target=target, session="s")
        executions.finish(project, run.id, outcome=outcome,
                          lesson="Built from the wrong branch" if outcome == "failed" else None)
        made.append(run)
    return made


# --- what the brief says ---------------------------------------------------------


def test_with_no_procedure_it_says_so_rather_than_leaving_room_to_improvise(project):
    text = brief.build(project, task="rotate the database credentials")
    assert "PROCEDURE — none recorded for this task" in text


def test_runs_are_found_by_the_task_when_no_procedure_names_them(project):
    run = executions.start(project, "Rotate database credentials", session="s")
    executions.finish(project, run.id, outcome="ok")

    text = brief.build(project, task="rotate the database credentials")

    assert run.id in text and "LAST RUNS (1 of 1)" in text


def test_an_older_failure_outside_the_top_three_is_still_shown(project):
    slug = _procedure(project).procedure
    failed, *_ = _runs(project, slug, ["failed", "ok", "ok", "ok"])

    text = brief.build(project, task="deploy to staging")

    assert "earlier failure worth reading" in text and failed.id in text


def test_the_budget_is_a_ceiling_and_the_trim_is_announced(project):
    _procedure(project)
    text = brief.build(project, task="deploy to staging", budget=40)
    assert len(text) <= 40 * brief.CHARS_PER_TOKEN + 200  # the one trim line may overhang
    assert "trimmed to the brief's budget" in text


def test_task_only_is_empty_when_nothing_answers_the_prompt(project):
    _procedure(project)
    assert brief.build(project, task="continue", task_only=True) == ""


def test_task_only_leaves_out_the_branch_brief(project):
    _procedure(project)
    text = brief.build(project, task="deploy to staging", task_only=True)
    assert "PROCEDURE  Deploy a service to staging" in text and "IN FLIGHT" not in text


def test_the_session_start_brief_names_runs_left_open(project):
    run = executions.start(project, "Deploy wallet", session="s")
    assert f"RUNS OPEN (1)" in brief.build(project) and run.id in brief.build(project)


# --- ranking ------------------------------------------------------------------------


def test_ranking_is_most_recent_first_with_the_ledger_breaking_timestamp_ties(project):
    slug = _procedure(project).procedure
    made = _runs(project, slug, ["ok", "ok", "ok"])
    assert [r.id for r in executions.ranked(project, procedure=slug)] == [r.id for r in reversed(made)]


def test_a_target_given_puts_runs_against_it_first(project):
    slug = _procedure(project).procedure
    staging, = _runs(project, slug, ["ok"], target="staging")
    _runs(project, slug, ["ok"], target="production")
    assert executions.ranked(project, procedure=slug, target="staging")[0].id == staging.id


# --- the task is never stored -------------------------------------------------------


def test_the_task_text_reaches_no_file(project):
    (project / ".eos" / "config.toml").write_text("[telemetry]\nenabled = true\n")
    _procedure(project)
    secret_task = "deploy staging zqxjkv-marker-2931"

    done = subprocess.run(EOS + ["brief", str(project), "--task", secret_task],
                          capture_output=True, text=True, env=os.environ.copy())

    assert done.returncode == 0
    for path in project.rglob("*"):
        if path.is_file():
            assert "zqxjkv-marker-2931" not in path.read_text(errors="ignore"), path


# --- the prompt hook ----------------------------------------------------------------


def _hook(project, prompt, session="s-hook", env_extra=None):
    writer._write_hooks(project, "0.0.0")
    env = {**os.environ, "PATH": f"{REPO / 'bin'}:{os.environ.get('PATH', '')}", **(env_extra or {})}
    payload = json.dumps({"prompt": prompt, "session_id": session, "cwd": str(project)})
    return subprocess.run([sys.executable, str(project / ".claude" / "hooks" / "eos-prompt.py")],
                          input=payload, capture_output=True, text=True, env=env)


def test_the_prompt_hook_is_silent_on_a_prompt_nothing_answers(project):
    _procedure(project)
    done = _hook(project, "ok, continue")
    assert done.returncode == 0 and done.stdout == "" and done.stderr == ""


def test_the_prompt_hook_delivers_once_then_stays_quiet_until_something_changes(project):
    slug = _procedure(project).procedure

    first = _hook(project, "deploy the wallet service to staging")
    again = _hook(project, "now deploy it to staging please")
    _runs(project, slug, ["ok"])
    changed = _hook(project, "deploy to staging again")

    assert "PROCEDURE  Deploy a service to staging" in first.stdout
    assert again.stdout == "", "the same block twice in one session is paid for twice"
    assert "LAST RUNS (1 of 1)" in changed.stdout


def test_the_prompt_hook_exits_0_silently_when_eos_cannot_run(project):
    writer._write_hooks(project, "0.0.0")
    env = {"PATH": "/nonexistent", "HOME": str(project)}
    payload = json.dumps({"prompt": "deploy to staging", "session_id": "s", "cwd": str(project)})
    done = subprocess.run([sys.executable, str(project / ".claude" / "hooks" / "eos-prompt.py")],
                          input=payload, capture_output=True, text=True, env=env)
    assert done.returncode == 0 and done.stdout == "" and done.stderr == ""


def test_an_endpoint_table_is_not_read_to_a_session_as_a_related_note(project):
    directory = notes.notes_dir(project)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "20260901-api.md").write_text(
        "---\nkind: finding\ntitle: Deploy service API map, 120 endpoints\ncreated: 2026-09-01\n"
        "source: api-inventory\n---\n\n/deploy /service /staging\n", encoding="utf-8")

    assert brief.build(project, task="deploy the service to staging", task_only=True) == ""


def test_a_related_note_must_meet_the_task_in_its_title_or_tags(project):
    """A one-word prompt that happens to be a word in some note's prose --
    "continue", in any language -- is a reply, not a task, and the brief put
    two unrelated notes in front of it. Body-only matches stay in search,
    where somebody asked; they do not make it into unrequested context."""
    directory = notes.notes_dir(project)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "20260901-prose.md").write_text(
        "---\nkind: finding\ntitle: Wallet balance returns 400\ncreated: 2026-09-01\n---\n\n"
        "Continue the investigation from the registry.\n", encoding="utf-8")
    (directory / "20260902-tagged.md").write_text(
        "---\nkind: finding\ntitle: Registry host was wrong\ncreated: 2026-09-02\ntags:\n  - continue\n"
        "---\n\nUnrelated body.\n", encoding="utf-8")

    assert "Wallet balance returns 400" not in brief.build(project, task="continue", task_only=True)
    assert "Registry host was wrong" in brief.build(project, task="continue", task_only=True)


def test_a_word_in_most_run_titles_does_not_match_them_all(project):
    """Nine of ten runs are "Run the <x> scenario"; a prompt with "run" in it
    is most prompts, and it matched every one of them."""
    for n in range(9):
        r = executions.start(project, f"Run the scenario-{n} scenario on env1", target="env1", session="s")
        executions.finish(project, r.id, outcome="ok")
    other = executions.start(project, "Rotate the database credentials", target="vault", session="s")
    executions.finish(project, other.id, outcome="ok")

    assert executions.ranked(project, task="run the credential rotation") == \
        [executions.load(project)[-1]]
    assert executions.ranked(project, task="please run it") == []
