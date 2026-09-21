"""The one call a session does not have to decide to make.

Offered as twelve tools, this engine was reached for in 4 of 25 sessions. The
brief is the answer to that number, so the properties under test here are the
ones that decide whether it survives contact with a session: it is small, it
says what nothing else can, it distinguishes "nothing matched" from "nothing
was ever written", and the hook that runs it is silent on every failure path
rather than noisy.
"""
import json
import subprocess
import sys
from pathlib import Path

from core import brief
from core import notes
from core import work

REPO = Path(__file__).resolve().parents[1]
EOS = [sys.executable, str(REPO / "core" / "eos.py")]


def _run(args, **kw):
    return subprocess.run(EOS + args, capture_output=True, text=True, encoding="utf-8", **kw)


def _project(tmp_path) -> Path:
    root = tmp_path / "svc"
    (root / ".eos").mkdir(parents=True)
    return root


def test_the_brief_leads_with_what_no_other_tool_can_produce(tmp_path):
    project = _project(tmp_path)
    work.open_item(project, "top-up history path", session="s1", agent="devin", claim=True)

    text = brief.build(project, session="s2", agent="claude")

    assert "IN FLIGHT (1)" in text
    assert "top-up history path" in text
    assert "devin/s1" in text


def test_an_item_this_session_holds_is_marked_as_its_own(tmp_path):
    project = _project(tmp_path)
    work.open_item(project, "top-up history path", session="s1", agent="claude", claim=True)

    assert "(yours)" in brief.build(project, session="s1")
    assert "(yours)" not in brief.build(project, session="s2")


def test_an_empty_project_still_says_what_to_do(tmp_path):
    """Silence at session start reads as "this tool is broken" and is acted on
    accordingly -- which is to say, not at all."""
    project = _project(tmp_path)

    text = brief.build(project)

    assert "IN FLIGHT (0)" in text
    assert "eos work add" in text
    assert "0 notes" in text


def test_notes_that_do_not_match_are_reported_apart_from_notes_that_do_not_exist(tmp_path):
    project = _project(tmp_path)
    notes.add_note(project, kind="finding", title="Mongo TLS trap on boot",
                   body="The driver keeps tlsCAFile even when the URI is localhost.")

    text = brief.build(project)

    assert "1 notes match this branch" in text or "0 of 1 notes match this branch" in text
    assert "nothing has been recorded in this project yet" not in text


def test_the_branch_is_the_query_when_nobody_has_typed_a_task(tmp_path):
    query = brief.query_from("feature/TICKET-123-top-up-history", ["TICKET-123"])

    assert query.startswith("TICKET-123 ")
    assert "history" in query and "top" in query
    assert "feature" not in query, "a word every branch carries ranks nothing"
    # Ranking against words every branch has is the same as not ranking at all.
    assert brief.query_from("main", []) == ""


def test_the_brief_stays_small_enough_to_be_read_every_session(tmp_path):
    """A session-start block that costs as much as reading two files is one
    somebody turns off inside a week."""
    project = _project(tmp_path)
    for number in range(12):
        work.open_item(project, f"item number {number}", session=f"s{number}",
                       agent="devin", claim=True)
    subjects = ("mongo", "kafka", "argocd", "jenkins", "okta", "redis",
                "postgres", "nginx", "helm", "vault", "grafana", "sonar")
    for subject in subjects:
        notes.add_note(project, kind="finding", title=f"what {subject} does here",
                       body=f"{subject}: " + "detail " * 300)

    text = brief.build(project)

    assert len(text) < 2000, f"the brief grew to {len(text)} characters"
    assert "more: eos work list" in text, "it must say what it left out"


def test_the_cli_brief_is_one_call_with_nothing_to_choose(tmp_path):
    project = _project(tmp_path)
    work.open_item(project, "asset sync defect", session="s1", agent="devin", claim=True)

    done = _run(["brief", str(project), "--session", "s2", "--agent", "claude"])

    assert done.returncode == 0, done.stderr
    assert "asset sync defect" in done.stdout


# --- the hook that removes the decision --------------------------------------

def _install(tmp_path) -> Path:
    root = tmp_path / "hooked"
    assert _run(["init", str(root)]).returncode == 0
    return root


def test_the_hook_prints_the_brief_it_was_installed_for(tmp_path):
    root = _install(tmp_path)
    work.open_item(root, "already in flight", session="s1", agent="devin", claim=True)

    done = subprocess.run(
        [sys.executable, str(root / ".claude" / "hooks" / "eos-brief.py")],
        input=json.dumps({"session_id": "abc", "cwd": str(root)}),
        capture_output=True, text=True, encoding="utf-8")

    assert done.returncode == 0, done.stderr
    assert "already in flight" in done.stdout, done.stdout


def test_the_hook_is_silent_when_eos_cannot_be_reached(tmp_path):
    """No `eos` on PATH, an unscanned project, an interpreter too old -- a
    session-start hook that fails noisily is one people delete, and the loop
    goes with it."""
    root = _install(tmp_path)
    for path in (root / ".eos" / "runtime").iterdir():
        if path.name == "eos.py":
            path.unlink()

    done = subprocess.run(
        [sys.executable, str(root / ".claude" / "hooks" / "eos-brief.py")],
        input=json.dumps({"session_id": "abc", "cwd": str(root)}),
        capture_output=True, text=True, encoding="utf-8",
        env={"PATH": "/nonexistent", "HOME": str(tmp_path)})

    assert done.returncode == 0
    assert done.stdout == ""
    assert done.stderr == ""


def test_the_hook_survives_input_that_is_not_the_json_it_expects(tmp_path):
    root = _install(tmp_path)

    done = subprocess.run(
        [sys.executable, str(root / ".claude" / "hooks" / "eos-brief.py")],
        input="not json at all", capture_output=True, text=True, encoding="utf-8")

    assert done.returncode == 0
    assert done.stderr == ""


def _break_the_runtime(root: Path) -> None:
    for path in (root / ".eos" / "runtime").iterdir():
        if path.name == "eos.py":
            path.unlink()


def test_a_session_whose_brief_failed_is_still_counted(tmp_path):
    """The blind spot that would make every other number here a lie.

    `eos cost` counts sessions by the calls they made. A hook that failed in
    silence would take its whole session out of the denominator, so a project
    where EOS could not be reached at all would report the best adoption
    figure it has ever had -- 100% of the sessions that worked."""
    from core import telemetry

    root = _install(tmp_path)
    config = root / ".eos" / "config.toml"
    config.write_text(config.read_text(encoding="utf-8") + "\n[telemetry]\nenabled = true\n",
                      encoding="utf-8")
    telemetry.record(root, "brief", session="earlier-session")  # creates the log
    _break_the_runtime(root)

    done = subprocess.run(
        [sys.executable, str(root / ".claude" / "hooks" / "eos-brief.py")],
        input=json.dumps({"session_id": "broken-session", "cwd": str(root)}),
        capture_output=True, text=True, encoding="utf-8",
        env={"PATH": "/nonexistent", "HOME": str(tmp_path)})

    assert done.returncode == 0 and done.stdout == ""
    failed = [entry for entry in telemetry.load(root)
              if entry.get("session") == "broken-session"]
    assert failed, "the session vanished from the log entirely"
    assert failed[0]["ok"] is False
    assert telemetry.summary(root)["sessions"]["failed_openings"] == 1


def test_a_failed_brief_writes_nothing_where_telemetry_was_never_turned_on(tmp_path):
    """Telemetry is off unless a project asks for it, and a hook is not a
    licence to start a log the engine itself would not have started."""
    from core import telemetry

    root = _install(tmp_path)
    _break_the_runtime(root)

    subprocess.run(
        [sys.executable, str(root / ".claude" / "hooks" / "eos-brief.py")],
        input=json.dumps({"session_id": "broken-session", "cwd": str(root)}),
        capture_output=True, text=True, encoding="utf-8",
        env={"PATH": "/nonexistent", "HOME": str(tmp_path)})

    assert not telemetry.path_for(root).exists()


# --- the hook that asks what happened to the claim ---------------------------

def _stop(root, payload) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(root / ".claude" / "hooks" / "eos-close.py")],
        input=json.dumps(payload), capture_output=True, text=True, encoding="utf-8")


def test_a_session_ending_on_its_own_claim_is_asked_about_it(tmp_path):
    root = _install(tmp_path)
    work.open_item(root, "top-up history path", session="mine", agent="claude", claim=True)

    done = _stop(root, {"session_id": "mine", "cwd": str(root)})

    assert done.returncode == 2, done.stdout
    assert "top-up history path" in done.stderr
    assert "eos work done" in done.stderr and "eos work drop" in done.stderr


def test_it_says_nothing_about_work_another_session_holds(tmp_path):
    """Closing someone else's claim is not this session's decision to make."""
    root = _install(tmp_path)
    work.open_item(root, "held elsewhere", session="theirs", agent="devin", claim=True)

    done = _stop(root, {"session_id": "mine", "cwd": str(root)})

    assert done.returncode == 0
    assert done.stderr == ""


def test_a_closed_claim_lets_the_session_end(tmp_path):
    root = _install(tmp_path)
    entry = work.open_item(root, "finished properly", session="mine", agent="claude", claim=True)
    work.append(root, work.EVENT_DONE, entry.id, session="mine", body="done")

    assert _stop(root, {"session_id": "mine", "cwd": str(root)}).returncode == 0


def test_the_stop_hook_never_asks_twice(tmp_path):
    """`stop_hook_active` is Claude Code saying a hook already stopped this
    session once. Ignoring it is how a hook loops a session forever."""
    root = _install(tmp_path)
    work.open_item(root, "still open", session="mine", agent="claude", claim=True)

    done = _stop(root, {"session_id": "mine", "cwd": str(root), "stop_hook_active": True})

    assert done.returncode == 0


def test_a_session_with_no_id_is_never_stopped(tmp_path):
    """With no session id there is no way to tell this session's claims from
    another agent's, and stopping over someone else's work is worse than
    saying nothing."""
    root = _install(tmp_path)
    work.open_item(root, "somebody's work", session="mine", agent="claude", claim=True)

    assert _stop(root, {"cwd": str(root)}).returncode == 0


def test_the_stop_hook_records_both_the_sessions_it_stopped_and_the_ones_it_did_not(tmp_path):
    """A hook that left a trace only when it fired would be missing every
    clean session from the denominator, and the ratio would look worst
    exactly when sessions started closing their work."""
    from core import telemetry

    root = _install(tmp_path)
    config = root / ".eos" / "config.toml"
    config.write_text(config.read_text(encoding="utf-8") + "\n[telemetry]\nenabled = true\n",
                      encoding="utf-8")
    telemetry.record(root, "brief", session="setup")  # creates the log
    entry = work.open_item(root, "left open", session="messy", agent="claude", claim=True)
    work.open_item(root, "also left open", session="tidy", agent="claude", claim=True)

    assert _stop(root, {"session_id": "messy", "cwd": str(root)}).returncode == 2
    work.append(root, work.EVENT_DONE, entry.id, session="tidy")
    tidy = work.resolve(work.fold(work.load(root)), "also left open")
    work.append(root, work.EVENT_DONE, tidy.id, session="tidy")
    assert _stop(root, {"session_id": "tidy", "cwd": str(root)}).returncode == 0

    report = telemetry.summary(root)["sessions"]
    assert report["closed_sessions"] == 2, "the clean session must be in the denominator"
    assert report["left_work_open"] == 1


def test_the_stop_hook_does_not_stop_a_session_it_could_not_ask_about(tmp_path):
    """"Nothing to close" and "EOS could not be reached" are different answers,
    and only one of them is grounds for holding a session open."""
    root = _install(tmp_path)
    work.open_item(root, "unreachable", session="mine", agent="claude", claim=True)
    _break_the_runtime(root)

    done = subprocess.run(
        [sys.executable, str(root / ".claude" / "hooks" / "eos-close.py")],
        input=json.dumps({"session_id": "mine", "cwd": str(root)}),
        capture_output=True, text=True, encoding="utf-8",
        env={"PATH": "/nonexistent", "HOME": str(tmp_path)})

    assert done.returncode == 0
