"""Typed procedure steps (2.x roadmap E2): marks parsed, linted, and runs attributed to steps."""
import subprocess
import sys
from pathlib import Path

from core import procedure_lint, steps
from core.executions import Event

EOS = [sys.executable, str(Path(__file__).resolve().parents[1] / "core" / "eos.py")]

BODY = """## Steps

1. Take the ids (tool: ids) (out: ids)
2. Write the script from them (in: ids) (out: script)
3. Check the grants (tool: grants) (in: script) (on_failure: 2)
4. Upload it (tool: upload) (in: script, ticket)
5. Clear the caches (tool: cache)

## Prerequisites

- A ticket key for the changeset label
"""


def _event(ord_, tool, exit_code=0, status=None):
    return Event(execution="x", ord=ord_, at="2026-09-27T04:00:00Z", kind="tool", tool=tool,
                 exit_code=exit_code, status=status)


def test_marks_are_parsed_and_stripped_from_the_step_text():
    parsed = steps.parse(BODY)
    assert [s.number for s in parsed] == [1, 2, 3, 4, 5]
    assert parsed[0].tool == "ids" and parsed[0].outputs == ("ids",)
    assert parsed[3].inputs == ("script", "ticket")
    assert parsed[2].on_failure == "2"
    assert parsed[1].text == "Write the script from them"
    assert parsed[4].tool == "cache" and parsed[4].inputs == ()


def test_an_input_nothing_produces_is_named_unless_a_prerequisite_mentions_it():
    assert steps.lint(steps.parse(BODY), BODY) == []
    body = BODY.replace("(in: script, ticket)", "(in: script, credentials)")
    assert steps.lint(steps.parse(body), body) == [
        "step 4 reads `credentials`, which no earlier step writes (out:) and no prerequisite names"]


def test_an_input_written_only_by_a_later_step_is_named():
    body = "## Steps\n\n1. Use it (in: report)\n2. Make it (out: report)\n"
    assert steps.lint(steps.parse(body), body) == [
        "step 1 reads `report`, which no earlier step writes (out:) and no prerequisite names"]


def test_on_failure_must_name_a_step_and_must_not_go_round_in_a_circle():
    body = "## Steps\n\n1. A (on_failure: 7)\n2. B (on_failure: 3)\n3. C (on_failure: step 2)\n4. D (on_failure: stop)\n"
    problems = steps.lint(steps.parse(body), body)
    assert "step 1: on_failure names step 7, and there are 4 steps" in problems
    assert "steps 2 -> 3 -> 2: on_failure goes round in a circle, a failure there never ends" in problems
    assert len(problems) == 2  # `stop` is not a step reference


def test_a_procedure_without_marks_has_nothing_to_lint():
    body = "## Steps\n\n1. Do a thing\n2. Do another\n"
    assert steps.lint(steps.parse(body), body) == []


def test_events_are_attributed_to_steps_by_tool_in_order():
    parsed = steps.parse(BODY)
    events = [_event(1, "automation/ids.sh"), _event(2, "gitx"), _event(3, "grants", exit_code=2),
              _event(4, "grants"), _event(5, "upload", exit_code=1)]
    rows = steps.attribute(parsed, events)
    assert [(r["step"], r["status"]) for r in rows] == [
        (1, "ok"), (2, "not seen"), (3, "ok"), (4, "failed"), (5, "not seen")]
    assert rows[3]["exit_code"] == 1 and rows[2]["events"] == 2
    assert steps.failed_step(parsed, events) == 4


def test_a_step_retried_after_a_later_one_is_matched_again():
    body = "## Steps\n\n1. Build (tool: mvn)\n2. Push (tool: git)\n"
    rows = steps.attribute(steps.parse(body), [_event(1, "mvn"), _event(2, "git", 1), _event(3, "mvn", 1)])
    assert [(r["step"], r["status"]) for r in rows] == [(1, "failed"), (2, "failed")]


def test_a_failed_status_counts_as_a_failure_without_an_exit_code():
    body = "## Steps\n\n1. Build (tool: mvn)\n"
    rows = steps.attribute(steps.parse(body), [_event(1, "mvn", exit_code=None, status="failed")])
    assert rows[0]["status"] == "failed"


def test_a_step_that_failed_and_then_passed_is_not_the_failed_step():
    """Seen on the host: an ok run printed `failed at step 6` for a retried call."""
    body = "## Steps\n\n1. Build (tool: mvn)\n2. Push (tool: git)\n"
    parsed = steps.parse(body)
    assert steps.failed_step(parsed, [_event(1, "mvn"), _event(2, "git", 1), _event(3, "git")]) is None
    assert steps.failed_step(parsed, [_event(1, "mvn", 1), _event(2, "git", 1), _event(3, "git")]) == 1


def test_no_failed_step_when_no_step_event_failed():
    assert steps.failed_step(steps.parse(BODY), [_event(1, "ids"), _event(2, "argo", 1)]) is None


# --- through the CLI -----------------------------------------------------------------------


def _project(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    assert subprocess.run(EOS + ["init", str(root), "--no-ai"], capture_output=True).returncode == 0
    return root


def _run(root, *args, **kwargs):
    done = subprocess.run(EOS + list(args), capture_output=True, text=True, **kwargs)
    assert done.returncode == 0, done.stderr
    return done.stdout


def test_lint_reports_step_problems(tmp_path):
    root = _project(tmp_path)
    _run(root, "procedure", "new", str(root), "--title", "Ship it", "--steps", "-", "--success", "it runs",
         input="Use it (in: report)\nMake it (out: report)\n")
    [report] = procedure_lint.lint(root)
    assert "step 1 reads `report`, which no earlier step writes (out:) and no prerequisite names" in report["problems"]


def test_run_show_and_procedure_show_name_the_step_that_failed(tmp_path, monkeypatch):
    monkeypatch.setenv("EOS_STATE_DIR", str(tmp_path / "state"))
    root = _project(tmp_path)
    slug = _run(root, "procedure", "new", str(root), "--title", "Ship it", "--steps", "-",
                "--success", "it runs", input="Build (tool: mvn)\nPush (tool: git)\n").split("\t")[0]
    run = _run(root, "run", "start", str(root), "--title", "Ship once", "--procedure", slug,
               "--session", "s1").split()[0]
    _run(root, "run", "event", str(root), run, "--kind", "ran", "--tool", "mvn", "--exit", "0")
    _run(root, "run", "event", str(root), run, "--kind", "ran", "--tool", "git", "--exit", "1")
    _run(root, "run", "finish", str(root), run, "--outcome", "failed", "--lesson",
         "the push was refused; read the hook's regex first")
    shown = _run(root, "run", "show", str(root), run)
    assert "failed at step 2: Push (tool: git)" in shown
    brief = _run(root, "brief", str(root), "--task", "Ship it")
    assert "failed at step 2" in brief
    procedure = _run(root, "procedure", "show", str(root), slug)
    assert " 1. ok      Build (tool: mvn)" in procedure
    assert " 2. failed  Push (tool: git)" in procedure
