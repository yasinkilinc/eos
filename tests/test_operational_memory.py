"""The acceptance gate for docs/plans/operational-memory.md — one test per row.

Each test exercises the interface its milestone will build, then asks the
harness (`core/memory_audit.py`) the same question `eos doctor --memory`
asks a real project. The harness is read-only; the writes are here.

Eighteen of these are red today and are marked `xfail(strict=True)` with
the row they wait for. Strict is the whole point: the moment a milestone
makes one pass, the marker has to go in the same commit, or the suite fails
loudly. Nothing here may `skip` — a skipped row would look green from the
outside, which is exactly the failure the plan exists to prevent. When a
name this file uses turns out wrong, it is changed here and in the plan's
"Interfaces the harness pins" list together.

Fixture notes are written straight to disk where `add_note`'s dedup guards
would refuse deliberately similar notes; everything else goes through the
API, because the API is what is under test.
"""
import subprocess
import sys
from pathlib import Path

import pytest

from core import brief, index, memory_audit, notes

REPO = Path(__file__).resolve().parents[1]
EOS = [sys.executable, str(REPO / "core" / "eos.py")]

IMPLEMENTED = memory_audit.IMPLEMENTED


def _run(args, **kw):
    return subprocess.run(EOS + args, capture_output=True, text=True, encoding="utf-8", **kw)


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "proj"
    (project / ".eos").mkdir(parents=True)
    notes.add_note(project, kind="finding", title="Wallet balance returns 400 from the registry",
                   body="The endpoint registry points WLT_BLNC at the wrong host.")
    return project


def _write_note(project: Path, name: str, kind: str, title: str, body: str, **front) -> Path:
    directory = notes.notes_dir(project)
    directory.mkdir(parents=True, exist_ok=True)
    extra = "".join(f"{key}: {value}\n" for key, value in front.items())
    path = directory / name
    path.write_text(f"---\nkind: {kind}\ntitle: {title}\ncreated: 2026-09-01\n{extra}---\n\n{body}\n",
                    encoding="utf-8")
    return path


PROCEDURE_BODY = """## Steps
1. Check the branch is the one the ticket names (tool: git)
2. Build the service (tool: build)
3. Wait for the image, then verify the rollout (tool: deploy)
4. Verify application health (tool: health)

## Prerequisites
- The branch exists on the remote

## Success
- The health endpoint answers 200 within two minutes of the rollout
"""


def _procedure(project: Path, slug: str = "deploy-to-staging") -> Path:
    return notes.add_note(project, kind="procedure", title=f"Deploy a service to staging ({slug})",
                          body=PROCEDURE_BODY)


def _status(check_id: str, project: Path) -> memory_audit.Result:
    return memory_audit.check(check_id, project)


# --- green today ------------------------------------------------------------------


def test_c01_sqlite_persistence_is_rebuilt_from_the_files(tmp_path):
    project = _project(tmp_path)
    index.build(project)
    assert _status("C-01", project).status == IMPLEMENTED


def test_c02_project_knowledge_is_searchable(tmp_path):
    project = _project(tmp_path)
    assert _status("C-02", project).status == IMPLEMENTED


def test_c10_semantic_retrieval_is_out_of_scope_by_decision(tmp_path):
    project = _project(tmp_path)
    assert _status("C-10", project).status == memory_audit.DECIDED


def test_the_matrix_always_renders_every_row(tmp_path):
    """A harness that stops early looks like a harness with fewer rows."""
    project = _project(tmp_path)
    results = memory_audit.run(project)
    assert [r.id for r in results] == [check_id for check_id, *_ in memory_audit.CHECKS]
    assert len(results) == 21
    text = memory_audit.render(results)
    assert "of 21 rows" in text
    assert "Next:" in text


def test_doctor_memory_prints_the_matrix_and_exits_1_while_open(tmp_path):
    project = _project(tmp_path)
    done = _run(["doctor", str(project), "--memory"])
    assert done.returncode == 1, done.stderr
    # On an unused project nothing is MISSING any more, only unexercised
    # (PARTIAL); what must hold is that it says so and names the next row.
    assert "C-04" in done.stdout and "PARTIAL" in done.stdout and "Next:" in done.stdout
    assert "operational-memory.md" in done.stdout


# --- M1: the execution ledger ------------------------------------------------------


def test_c04_an_execution_with_events_is_readable_back(tmp_path):
    from core import executions
    project = _project(tmp_path)

    run = executions.start(project, "Deploy the wallet service to staging", session="s-1")
    executions.event(project, run.id, kind="ran", tool="build", target="staging", exit_code=0)
    executions.finish(project, run.id, outcome="ok")

    back = {x.id: x for x in executions.load(project)}[run.id]
    assert back.outcome == "ok" and len(back.events) == 1
    assert _status("C-04", project).status == IMPLEMENTED


def test_c05_events_come_back_in_time_order_with_session_tool_target(tmp_path):
    from core import executions
    project = _project(tmp_path)

    run = executions.start(project, "Deploy", session="s-1", target="staging")
    executions.event(project, run.id, kind="ran", tool="build", target="staging", exit_code=0)
    executions.event(project, run.id, kind="called", tool="deploy", target="staging", exit_code=0)
    executions.event(project, run.id, kind="verified", tool="health", target="staging", exit_code=0)
    executions.finish(project, run.id, outcome="ok")

    back = executions.load(project)[0]
    assert [e.tool for e in back.events] == ["build", "deploy", "health"]
    assert all(e.session == "s-1" for e in back.events)
    assert _status("C-05", project).status == IMPLEMENTED


def test_c06_an_event_appended_from_a_shell_one_liner_lands(tmp_path):
    from core import executions
    project = _project(tmp_path)
    run = executions.start(project, "Deploy", session="s-1")

    done = _run(["run", "event", str(project), run.id, "--kind", "ran",
                 "--tool", "build", "--target", "staging", "--exit", "0"])

    assert done.returncode == 0, done.stderr
    assert executions.load(project)[0].events[0].tool == "build"
    assert _status("C-06", project).status == IMPLEMENTED


def test_c19_one_session_id_joins_executions_work_and_notes(tmp_path):
    from core import executions, work
    project = _project(tmp_path)

    run = executions.start(project, "Deploy", session="s-9")
    executions.finish(project, run.id, outcome="ok")
    work.open_item(project, "Deploy", claim=True, session="s-9")
    notes.add_note(project, kind="finding", title="Staging needs the VPN",
                   body="Without it the health check times out.", session="s-9")

    assert [x.id for x in executions.by_session(project, "s-9")] == [run.id]
    assert any(holder.get("session") == "s-9"
               for item in work.items(project) for holder in item.holders)
    assert any(n.session == "s-9" for n in notes.load_notes(project))
    assert _status("C-19", project).status == IMPLEMENTED


# --- M2: procedures ---------------------------------------------------------------


def test_c03_a_procedure_note_parses_into_ordered_steps(tmp_path):
    project = _project(tmp_path)
    path = _procedure(project)

    steps = notes.procedure_steps(notes.parse_note(path))
    assert len(steps) == 4 and steps[0].startswith("Check the branch")
    assert _status("C-03", project).status == IMPLEMENTED


def test_c17_a_finished_execution_moves_the_procedure_counters(tmp_path):
    from core import executions
    project = _project(tmp_path)
    path = _procedure(project)
    slug = notes.parse_note(path).procedure

    ok = executions.start(project, "Deploy", procedure=slug)
    executions.finish(project, ok.id, outcome="ok")
    after_ok = notes.parse_note(path)
    assert (after_ok.runs_ok, after_ok.runs_failed) == (1, 0) and after_ok.last_verified

    failed = executions.start(project, "Deploy again", procedure=slug)
    executions.finish(project, failed.id, outcome="failed",
                      lesson="The image was built from the wrong branch; check it first.")
    after_failed = notes.parse_note(path)
    assert (after_failed.runs_ok, after_failed.runs_failed) == (1, 1)
    assert "wrong branch" in "\n".join(notes.procedure_known_failures(after_failed))

    abandoned = executions.start(project, "Deploy, given up", procedure=slug)
    executions.finish(project, abandoned.id, outcome="abandoned")
    after_abandoned = notes.parse_note(path)
    assert (after_abandoned.runs_ok, after_abandoned.runs_failed) == (1, 1)
    assert after_abandoned.last_execution == abandoned.id
    assert _status("C-17", project).status == IMPLEMENTED


# --- M3: task-aware retrieval -------------------------------------------------------


def test_c07_the_brief_picks_the_procedure_that_matches_the_task(tmp_path):
    project = _project(tmp_path)
    _procedure(project, "deploy-to-staging")
    notes.add_note(project, kind="procedure", title="Rotate the database credentials",
                   body="## Steps\n1. Generate the secret (tool: vault)\n2. Roll the pods (tool: deploy)\n")

    text = brief.build(project, task="deploy the wallet service to staging")

    assert "Deploy a service to staging" in text
    assert "Rotate the database credentials" not in text
    assert _status("C-07", project).status == IMPLEMENTED


def test_c08_the_brief_lists_the_last_executions_failed_ones_with_their_lesson(tmp_path):
    from core import executions
    project = _project(tmp_path)
    slug = notes.parse_note(_procedure(project)).procedure
    for outcome in ("ok", "ok", "failed", "ok"):
        run = executions.start(project, "Deploy", procedure=slug, target="staging")
        executions.finish(project, run.id, outcome=outcome,
                          lesson="Wrong branch" if outcome == "failed" else None)

    text = brief.build(project, task="deploy to staging")
    ranked = executions.ranked(project, procedure=slug, limit=3)

    assert len(ranked) == 3 and ranked[0].outcome == "ok"
    assert "failed" in text and "Wrong branch" in text
    assert _status("C-08", project).status == IMPLEMENTED


def test_c09_a_score_floor_keeps_or_retrieval_from_returning_the_whole_corpus(tmp_path):
    from core import executions
    project = _project(tmp_path)
    for n in range(50):
        _write_note(project, f"20260902-ordinary-{n}.md", "finding",
                    f"Which order does the customer see in state {n}",
                    f"The order of the steps the customer sees, and what it returns ({n}).")
    _procedure(project)
    run = executions.start(project, "Deploy the wallet service", target="staging")
    executions.finish(project, run.id, outcome="ok")
    db = index.build(project).path

    assert hasattr(index, "SCORE_FLOOR") and hasattr(notes, "SCORE_FLOOR")
    ranked = notes.search_notes(project, "which endpoint does the wallet balance registry use")
    assert 0 < len(ranked) < 10, "one answer and its neighbours, not fifty rows"
    _, rows = index.search(db, "deploy wallet staging")
    assert {row[0] for row in rows} & {"execution", "procedure"}
    assert _status("C-09", project).status == IMPLEMENTED


def test_c11_the_task_brief_fits_the_budget(tmp_path):
    project = _project(tmp_path)
    _procedure(project)

    text = brief.build(project, task="deploy to staging", budget=memory_audit.BRIEF_TOKEN_BUDGET)

    assert len(text) / memory_audit.CHARS_PER_TOKEN <= memory_audit.BRIEF_TOKEN_BUDGET
    assert _status("C-11", project).status == IMPLEMENTED


def test_c20_a_thousand_executions_cost_the_same_brief_as_ten(tmp_path):
    from core import executions
    project = _project(tmp_path)
    slug = notes.parse_note(_procedure(project)).procedure
    for n in range(1000):
        run = executions.start(project, f"Deploy #{n}", procedure=slug, target="staging")
        executions.finish(project, run.id, outcome="ok")

    text = brief.build(project, task="deploy to staging")

    assert len(text) / memory_audit.CHARS_PER_TOKEN <= memory_audit.BRIEF_TOKEN_BUDGET
    assert _status("C-20", project).status == IMPLEMENTED


def test_c21_both_hooks_are_written_and_registered_by_the_writer(tmp_path):
    from core.ai import writer
    project = _project(tmp_path)

    writer._write_hooks(project, "0.0.0")
    writer._register_hooks(project)

    assert (project / ".claude" / "hooks" / "eos-prompt.py").exists()
    assert _status("C-21", project).status == IMPLEMENTED


# --- M4: lessons, decisions, learning ---------------------------------------------


def test_c14_a_decision_note_validates_its_sections(tmp_path):
    project = _project(tmp_path)

    with pytest.raises(ValueError):
        notes.add_note(project, kind="decision", title="Use the wrapper for every deploy",
                       body="Because it records the result.")
    notes.add_note(project, kind="decision", title="Use the wrapper for every deploy",
                   body="## Why\nIt records the result.\n\n## When\n2026-09-01\n\n## Component\ndeploy\n")

    assert _status("C-14", project).status == IMPLEMENTED


def test_c15_a_lesson_links_to_the_execution_that_taught_it(tmp_path):
    from core import executions
    project = _project(tmp_path)
    run = executions.start(project, "Deploy")
    with pytest.raises(ValueError):
        executions.finish(project, run.id, outcome="failed")  # OM-41: no lesson, refused
    executions.finish(project, run.id, outcome="failed", lesson="Wrong branch")

    lessons = [n for n in notes.load_notes(project) if n.kind == "lesson"]
    assert lessons and lessons[0].execution == run.id
    assert "## What went wrong" in lessons[0].body
    assert _status("C-15", project).status == IMPLEMENTED


def test_c16_a_verification_inside_an_execution_is_tied_to_it(tmp_path):
    from core import executions, verification
    project = _project(tmp_path)
    run = executions.start(project, "Deploy", session="s-1")

    verification.record(project, "HEALTH_OK", "passed", "health-check staging", 0,
                        session="s-1", execution=run.id)

    assert verification.load(project)[0].execution == run.id
    assert _status("C-16", project).status == IMPLEMENTED


def test_c18_confidence_is_derived_at_read_time_and_never_stored(tmp_path):
    from core import executions
    project = _project(tmp_path)
    path = _procedure(project)
    slug = notes.parse_note(path).procedure
    run = executions.start(project, "Deploy", procedure=slug)
    executions.finish(project, run.id, outcome="ok")

    word = notes.procedure_confidence(notes.parse_note(path))

    assert word in {"fresh", "aging", "stale", "failing"}
    assert "confidence:" not in path.read_text(encoding="utf-8")
    assert _status("C-18", project).status == IMPLEMENTED


# --- M5: tool and change memory -----------------------------------------------------


def test_c12_tools_answers_which_ran_how_often_and_with_what_outcome(tmp_path):
    from core import executions
    project = _project(tmp_path)
    slug = notes.parse_note(_procedure(project)).procedure
    for outcome in ("ok", "failed"):
        run = executions.start(project, "Deploy", procedure=slug)
        executions.event(project, run.id, kind="ran", tool="build", target="staging", exit_code=0)
        executions.finish(project, run.id, outcome=outcome,
                          lesson="Wrong branch" if outcome == "failed" else None)

    used = {u.tool: u for u in executions.tools(project, procedure=slug)}

    assert used["build"].count == 2 and used["build"].last_outcome == "failed"
    assert _status("C-12", project).status == IMPLEMENTED


def test_c13_diff_lists_the_changed_paths_and_the_commit_range(tmp_path):
    from core import executions
    project = _project(tmp_path)
    run = executions.start(project, "Deploy")
    executions.event(project, run.id, kind="changed", ref="src/main/App.java")
    executions.finish(project, run.id, outcome="ok")

    delta = executions.diff(project, run.id)

    assert delta["paths"] == ["src/main/App.java"]
    assert "commit_start" in delta and "commit_end" in delta
    assert _status("C-13", project).status == IMPLEMENTED


def test_c19_a_session_id_nothing_else_shares_is_not_a_join(tmp_path):
    """An audit found the row satisfied by executions carrying a session id
    that no other store shared -- lenient by construction. A join needs two
    sides."""
    from core import executions
    project = _project(tmp_path)
    run = executions.start(project, "Deploy", session="s-lonely")
    executions.finish(project, run.id, outcome="ok")

    assert _status("C-19", project).status == memory_audit.PARTIAL
