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


def test_one_run_is_read_without_folding_the_others(project):
    # A hook looks up the session's one open run on every call; folding a
    # 3,700-event ledger for it cost the router 22 ms a decision.
    ledger = executions.path_for(project)
    ledger.parent.mkdir(parents=True, exist_ok=True)
    lines = [{"type": "start", "id": "x-a", "title": "A", "at": "2026-09-24T09:00:00+00:00"},
             {"type": "start", "id": "x-a-2", "title": "mentions \"x-a\" in its title",
              "at": "2026-09-24T09:01:00+00:00"},
             {"type": "event", "execution": "x-a", "at": "2026-09-24T09:02:00+00:00", "kind": "ran", "tool": "build"},
             {"type": "event", "execution": "x-a-2", "at": "2026-09-24T09:03:00+00:00", "kind": "ran", "tool": "test"},
             {"type": "finish", "id": "x-a-2", "outcome": "ok", "at": "2026-09-24T09:04:00+00:00"}]
    ledger.write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")
    full = {r.id: r for r in executions.load_path(ledger)}
    assert executions.load_path(ledger, only="x-a") == [full["x-a"]]
    assert executions.load_path(ledger, only="x-a-2") == [full["x-a-2"]]
    assert executions.load_path(ledger, only="x-none") == []
    # The writer keeps non-ASCII as is; an older line may carry the escape.
    with ledger.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"type": "start", "id": "x-ş", "title": "Ş", "at": "2026-09-24T09:05:00+00:00"},
                                ensure_ascii=False) + "\n")
        handle.write(json.dumps({"type": "finish", "id": "x-ş", "outcome": "ok",
                                 "at": "2026-09-24T09:06:00+00:00"}) + "\n")
    [record] = executions.load_path(ledger, only="x-ş")
    assert record.title == "Ş" and record.outcome == "ok"


def test_an_event_whose_start_line_was_lost_still_shows_the_run(project):
    ledger = executions.path_for(project)
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text(json.dumps({"type": "event", "execution": "x-orphan", "at": "2026-09-24T09:00:00+00:00",
                                  "kind": "ran", "tool": "build"}) + "\n", encoding="utf-8")

    found = executions.load(project)

    assert [r.id for r in found] == ["x-orphan"]
    assert found[0].events[0].tool == "build"
    assert found[0].open


def test_identical_consecutive_events_fold_into_one_display_group(project):
    run = executions.start(project, "Deploy", session="s-1")
    for _ in range(3):
        executions.event(project, run.id, kind="ran", tool="build", exit_code=0)
    executions.event(project, run.id, kind="ran", tool="build", exit_code=1)

    events = executions.load(project)[0].events
    groups = executions.fold_events_for_display(events)

    assert len(groups) == 2
    first, count, first_at, last_at = groups[0]
    assert count == 3 and first.exit_code == 0
    assert first_at <= last_at
    second, count2, _, _ = groups[1]
    assert count2 == 1 and second.exit_code == 1


def test_non_consecutive_identical_events_do_not_fold(project):
    run = executions.start(project, "Deploy", session="s-1")
    executions.event(project, run.id, kind="ran", tool="build", exit_code=0)
    executions.event(project, run.id, kind="ran", tool="test", exit_code=0)
    executions.event(project, run.id, kind="ran", tool="build", exit_code=0)

    groups = executions.fold_events_for_display(executions.load(project)[0].events)

    assert [count for _, count, _, _ in groups] == [1, 1, 1]


def test_run_show_folds_identical_consecutive_events_with_an_x_marker(project):
    env = {**os.environ, "EOS_SESSION": "s-fold"}
    started = _run(["run", "start", str(project), "--title", "Deploy wallet"], env)
    run_id = started.stdout.strip()
    for _ in range(3):
        assert _run(["run", "event", str(project), "--kind", "ran", "--tool", "build",
                     "--exit", "0"], env).returncode == 0
    assert _run(["run", "finish", str(project), "--outcome", "ok"], env).returncode == 0

    shown = _run(["run", "show", str(project), run_id], env)
    assert "x3" in shown.stdout
    assert shown.stdout.count("build") == 1  # folded to one display line, not three

    as_json = _run(["run", "show", str(project), run_id, "--format", "json"], env)
    data = json.loads(as_json.stdout)
    assert len(data["events"]) == 3  # the ledger's own record stays whole


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


def test_finishing_a_second_run_hands_the_pointer_back_to_the_first_still_open_one(project):
    """Seen on the host (E1): a session opened a short run inside a long one and
    finished it; the pointer was removed, so the long run -- open for another
    nineteen hours -- received no event of any kind after that."""
    first = executions.start(project, "Long task", session="s-7")
    second = executions.start(project, "Quick check", session="s-7")
    executions.finish(project, second.id, outcome="ok", session="s-7")

    executions.event(project, kind="ran", tool="build", session="s-7")

    by_id = {r.id: r for r in executions.load(project)}
    assert [e.tool for e in by_id[first.id].events] == ["build"]
    assert not by_id[second.id].events
    executions.finish(project, outcome="ok", session="s-7")
    with pytest.raises(ValueError, match="none is open"):
        executions.event(project, kind="ran", tool="build", session="s-7")


def test_a_run_finished_elsewhere_is_not_handed_the_pointer_back(project):
    first = executions.start(project, "Long task", session="s-7")
    second = executions.start(project, "Quick check", session="s-7")
    executions.finish(project, first.id, outcome="abandoned", session="other")
    executions.finish(project, second.id, outcome="ok", session="s-7")

    with pytest.raises(ValueError, match="none is open"):
        executions.event(project, kind="ran", tool="build", session="s-7")


def test_the_shell_helper_follows_the_pointer_handed_back(project, tmp_path):
    first = executions.start(project, "Long task", session="s-sh")
    second = executions.start(project, "Quick check", session="s-sh")
    executions.finish(project, second.id, outcome="ok", session="s-sh")

    _helper(["--kind", "called", "--tool", "db"], _env(tmp_path, CLAUDE_CODE_SESSION_ID="s-sh"))

    assert [e.tool for e in {r.id: r for r in executions.load(project)}[first.id].events] == ["db"]


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


def test_the_shell_helper_records_that_a_wrapper_could_not_reach_its_system(project, tmp_path):
    executions.start(project, "Deploy", session="s-sh")

    _helper(["--kind", "called", "--tool", "tracker", "--exit", "1", "--status", "unreachable"],
            _env(tmp_path, CLAUDE_CODE_SESSION_ID="s-sh"))

    assert executions.load(project)[0].events[0].status == "unreachable"


def test_the_shell_helper_keeps_the_event_and_drops_a_status_it_does_not_know(project, tmp_path):
    executions.start(project, "Deploy", session="s-sh")

    _helper(["--kind", "called", "--tool", "tracker", "--status", "sideways"],
            _env(tmp_path, CLAUDE_CODE_SESSION_ID="s-sh"))

    event = executions.load(project)[0].events[0]
    assert (event.tool, event.status) == ("tracker", None)


def test_an_event_may_say_its_system_was_unreachable(project):
    executions.start(project, "Deploy", session="s-9")

    executions.event(project, kind="called", tool="tracker", exit_code=1, status="unreachable", session="s-9")

    assert executions.load(project)[0].events[0].status == "unreachable"


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


def test_run_finish_warns_about_changed_files_outside_the_procedures_scope(project):
    notes.add_note(project, kind="procedure", title="Deploy wallet scope test",
                   body="## Steps\n1. build\n2. deploy\n",
                   scope=["src/wallet.py", "src/wallet_config.py"])
    env = {**os.environ, "EOS_SESSION": "s-scope"}
    assert _run(["run", "start", str(project), "--title", "run it",
                "--procedure", "Deploy wallet scope test"], env).returncode == 0
    assert _run(["run", "event", str(project), "--kind", "changed", "--ref", "src/wallet.py"],
               env).returncode == 0
    assert _run(["run", "event", str(project), "--kind", "changed", "--ref", "docs/README.md"],
               env).returncode == 0

    finished = _run(["run", "finish", str(project), "--outcome", "ok"], env)

    assert finished.returncode == 0
    assert "docs/README.md" in finished.stdout
    assert "src/wallet.py" not in finished.stdout


def test_run_finish_never_refuses_and_never_changes_outcome_for_out_of_scope_changes(project):
    notes.add_note(project, kind="procedure", title="Narrow scope test",
                   body="## Steps\n1. build\n", scope=["src/only.py"])
    env = {**os.environ, "EOS_SESSION": "s-scope2"}
    assert _run(["run", "start", str(project), "--title", "run it",
                "--procedure", "Narrow scope test"], env).returncode == 0
    assert _run(["run", "event", str(project), "--kind", "changed", "--ref", "elsewhere.py"],
               env).returncode == 0

    finished = _run(["run", "finish", str(project), "--outcome", "ok"], env)

    assert finished.returncode == 0
    record = executions.load(project)[0]
    assert record.outcome == "ok"


def test_run_finish_caps_the_warning_at_five_files_then_a_count(project):
    notes.add_note(project, kind="procedure", title="Many files scope test",
                   body="## Steps\n1. build\n", scope=["src/only.py"])
    env = {**os.environ, "EOS_SESSION": "s-scope3"}
    assert _run(["run", "start", str(project), "--title", "run it",
                "--procedure", "Many files scope test"], env).returncode == 0
    for n in range(7):
        assert _run(["run", "event", str(project), "--kind", "changed",
                    "--ref", f"other/file{n}.py"], env).returncode == 0

    finished = _run(["run", "finish", str(project), "--outcome", "ok"], env)

    for n in range(5):
        assert f"other/file{n}.py" in finished.stdout
    assert "other/file5.py" not in finished.stdout and "other/file6.py" not in finished.stdout
    assert "+2" in finished.stdout or "2 more" in finished.stdout


def test_run_finish_says_nothing_when_the_procedure_has_no_scope(project):
    notes.add_note(project, kind="procedure", title="No scope test", body="## Steps\n1. build\n")
    env = {**os.environ, "EOS_SESSION": "s-scope4"}
    assert _run(["run", "start", str(project), "--title", "run it",
                "--procedure", "No scope test"], env).returncode == 0
    assert _run(["run", "event", str(project), "--kind", "changed", "--ref", "anything.py"],
               env).returncode == 0

    finished = _run(["run", "finish", str(project), "--outcome", "ok"], env)

    assert "scope" not in finished.stdout.lower()


def test_run_start_without_any_session_says_how_capture_can_still_reach_it(project):
    env = {k: v for k, v in os.environ.items() if k not in ("EOS_SESSION", "CLAUDE_CODE_SESSION_ID")}
    done = _run(["run", "start", str(project), "--title", "Deploy"], env)
    assert done.returncode == 0
    assert "EOS_EXECUTION=" in done.stderr


# --- one session id across stores -----------------------------------------------------


def test_a_work_claim_and_a_note_written_from_the_cli_carry_the_harness_session(project):
    env = {**os.environ, "CLAUDE_CODE_SESSION_ID": "s-harness"}
    assert _run(["work", "add", str(project), "--title", "Deploy wallet", "--claim"], env).returncode == 0
    assert _run(["note", "add", str(project), "--kind", "finding", "--tags", "t", "--title", "Staging needs the VPN",
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

    # The third event is the lesson note the failed finish wrote (E1).
    assert row == ("failed", "staging", 3, "The image came from the wrong branch")
    assert [t[0] for t in timeline] == ["build", "deploy", "eos"]
    _, hits = index.search(db, "wallet wrong branch")
    assert ("execution", run.id) in {(h[0], h[1]) for h in hits}


def test_a_new_event_makes_the_index_stale(project):
    run = executions.start(project, "Deploy", session="s-1")
    db = index.build(project).path
    before = sqlite3.connect(db).execute("SELECT events FROM execution").fetchone()[0]

    executions.event(project, run.id, kind="ran", tool="build")
    index.refresh(project)

    assert sqlite3.connect(db).execute("SELECT events FROM execution").fetchone()[0] == before + 1


def _verified_project(tmp_path):
    import subprocess, sys
    from pathlib import Path

    root = tmp_path / "proj"
    root.mkdir()
    eos = [sys.executable, str(Path(__file__).resolve().parents[1] / "core" / "eos.py")]
    assert subprocess.run(eos + ["init", str(root), "--no-ai"], capture_output=True).returncode == 0
    (root / ".eos" / "knowledge").mkdir(parents=True, exist_ok=True)
    (root / ".eos" / "knowledge" / "verify.toml").write_text(
        "[[scope]]\nname = \"code\"\npaths = [\"src/**\"]\npasses = ['make\\s+test\\b']\nrun = \"make test\"\n",
        encoding="utf-8")
    return root, eos


def test_a_finished_run_says_whether_its_changes_were_checked(tmp_path, monkeypatch):
    import subprocess
    from core import executions

    monkeypatch.setenv("EOS_STATE_DIR", str(tmp_path / "state"))
    root, eos = _verified_project(tmp_path)
    a = executions.start(root, "checked", session="s1")
    executions.event(root, a.id, kind="changed", ref="src/a.py")
    executions.event(root, a.id, kind="verified", tool="verify", ref="code")
    assert executions.finish(root, a.id, outcome="ok").outcome_source == "verified"
    b = executions.start(root, "claimed", session="s2")
    executions.event(root, b.id, kind="verified", tool="verify", ref="code")
    executions.event(root, b.id, kind="changed", ref="src/a.py")
    assert executions.finish(root, b.id, outcome="ok").outcome_source == "claimed"
    c = executions.start(root, "reading", session="s3")
    executions.event(root, c.id, kind="changed", ref="docs/x.md")
    assert executions.finish(root, c.id, outcome="ok").outcome_source is None
    stats = subprocess.run(eos + ["run", "list", str(root), "--stats"], capture_output=True, text=True).stdout
    assert "verified 1, claimed 1, nothing to verify 1" in stats and "verified rate 50%" in stats


def test_finish_works_when_the_scopes_file_is_broken(tmp_path, monkeypatch):
    from core import executions

    monkeypatch.setenv("EOS_STATE_DIR", str(tmp_path / "state"))
    root, _ = _verified_project(tmp_path)
    (root / ".eos" / "knowledge" / "verify.toml").write_text("[[scope]\n", encoding="utf-8")
    run = executions.start(root, "r", session="s1")
    executions.event(root, run.id, kind="changed", ref="src/a.py")
    assert executions.finish(root, run.id, outcome="ok").outcome_source is None


# --- event ids -------------------------------------------------------------------------

ID_PATTERN = r"^e-[0-9a-f]{8}$"


def test_event_gets_an_id(project):
    import re

    executions.start(project, "Deploy", session="s-id")
    written = executions.event(project, kind="ran", tool="build", session="s-id")

    assert re.match(ID_PATTERN, written.id)
    [line] = [json.loads(raw) for raw in executions.path_for(project).read_text().splitlines()
              if json.loads(raw)["type"] == "event"]
    assert line["id"] == written.id
    assert executions.load(project)[0].events[0].id == written.id


def test_helper_prints_the_id_it_wrote(project, tmp_path):
    import re

    executions.start(project, "Deploy", session="s-sh")

    done = _helper(["--kind", "ran", "--tool", "db"], _env(tmp_path, CLAUDE_CODE_SESSION_ID="s-sh"))

    assert done.returncode == 0, done.stderr
    assert re.match(r"^evidence=e-[0-9a-f]{8}\n$", done.stdout)
    assert done.stdout == f"evidence={executions.load(project)[0].events[0].id}\n"


def test_helper_prints_nothing_without_a_run(project, tmp_path):
    done = _helper(["--kind", "ran", "--tool", "db"], _env(tmp_path, CLAUDE_CODE_SESSION_ID="s-none"))

    assert done.returncode == 0
    assert done.stdout == ""


def test_legacy_line_gets_execution_ord_id(project):
    ledger = executions.path_for(project)
    ledger.parent.mkdir(parents=True, exist_ok=True)
    lines = [{"type": "start", "id": "x-old", "title": "Old", "at": "2026-09-24T09:00:00+00:00"},
             {"type": "event", "execution": "x-old", "at": "2026-09-24T09:01:00+00:00", "kind": "ran"},
             {"type": "event", "execution": "x-old", "at": "2026-09-24T09:02:00+00:00", "kind": "ran"}]
    ledger.write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")

    assert [e.id for e in executions.load(project)[0].events] == ["x-old#0", "x-old#1"]


def test_ids_do_not_repeat(project):
    executions.start(project, "Deploy", session="s-many")
    ids = {executions.event(project, kind="ran", tool="build", session="s-many").id
           for _ in range(500)}

    assert len(ids) == 500


def test_an_index_built_before_event_ids_is_stale_and_rebuilt(project, monkeypatch):
    # The execution_event.id column changed the index's shape: an index built at
    # schema 8 must not look current, or the first `SELECT id` fails.
    monkeypatch.setattr(index, "SCHEMA_VERSION", 8)
    index.build(project)
    conn = sqlite3.connect(index.db_path(project))
    conn.execute("ALTER TABLE execution_event DROP COLUMN id")
    conn.commit()
    conn.close()
    monkeypatch.undo()

    assert index.is_stale(project) is True
    assert index.refresh(project) is not None
    conn = sqlite3.connect(index.db_path(project))
    try:
        assert "id" in [row[1] for row in conn.execute("PRAGMA table_info(execution_event)")]
    finally:
        conn.close()


def test_helper_records_output_fingerprint(project, tmp_path):
    import hashlib

    executions.start(project, "Deploy", session="s-sh")
    raw = tmp_path / "raw.txt"
    raw.write_bytes(b"row 1\nrow 2\n")

    done = _helper(["--kind", "ran", "--tool", "db", "--output", str(raw)],
                   _env(tmp_path, CLAUDE_CODE_SESSION_ID="s-sh"))

    assert done.returncode == 0, done.stderr
    event = executions.load(project)[0].events[0]
    assert event.output_sha256 == hashlib.sha256(b"row 1\nrow 2\n").hexdigest()
    assert event.output_path == str(raw.resolve()) or event.output_path == str(raw)
    assert os.path.isabs(event.output_path)


def test_helper_resolves_a_relative_output_path(project, tmp_path):
    executions.start(project, "Deploy", session="s-sh")
    (tmp_path / "raw.txt").write_bytes(b"x")
    env = _env(tmp_path, CLAUDE_CODE_SESSION_ID="s-sh")

    subprocess.run(["/bin/sh", str(HELPER), "--kind", "ran", "--tool", "db", "--output", "raw.txt"],
                   capture_output=True, text=True, env=env, cwd=tmp_path)

    event = executions.load(project)[0].events[0]
    assert os.path.isabs(event.output_path) and event.output_path.endswith("/raw.txt")
    assert event.output_sha256


@pytest.mark.parametrize("name", ["missing.txt", ""])
def test_helper_without_output_file_still_writes(project, tmp_path, name):
    executions.start(project, "Deploy", session="s-sh")

    done = _helper(["--kind", "ran", "--tool", "db", "--output", str(tmp_path / name) if name else tmp_path.as_posix()],
                   _env(tmp_path, CLAUDE_CODE_SESSION_ID="s-sh"))

    assert done.returncode == 0
    assert done.stdout.startswith("evidence=e-")
    event = executions.load(project)[0].events[0]
    assert (event.output_sha256, event.output_path) == (None, None)


def test_an_event_line_without_output_fields_stays_valid(project, tmp_path):
    executions.start(project, "Deploy", session="s-sh")
    _helper(["--kind", "ran", "--tool", "db"], _env(tmp_path, CLAUDE_CODE_SESSION_ID="s-sh"))

    raw = [json.loads(l) for l in executions.path_for(project).read_text().splitlines()][-1]
    assert "output_sha256" not in raw and "output_path" not in raw
    assert executions.load(project)[0].events[0].output_sha256 is None


def test_helper_writes_the_event_without_an_id_when_od_fails(project, tmp_path):
    run = executions.start(project, "Deploy", session="s-sh")
    fake = tmp_path / "fakebin"
    fake.mkdir()
    (fake / "od").write_text("#!/bin/sh\nexit 1\n")
    (fake / "od").chmod(0o755)

    done = _helper(["--kind", "ran", "--tool", "db"],
                   _env(tmp_path, CLAUDE_CODE_SESSION_ID="s-sh", PATH=f"{fake}:/usr/bin:/bin"))

    assert done.returncode == 0 and done.stdout == ""
    raw = [json.loads(l) for l in executions.path_for(project).read_text().splitlines()][-1]
    assert raw["kind"] == "ran" and "id" not in raw
    assert executions.load(project)[0].events[0].id == f"{run.id}#0"


@pytest.mark.parametrize("kind, prints", [("ran", True), ("called", True), ("read", True),
                                          ("verified", True), ("changed", False),
                                          ("noted", False), ("decided", False)])
def test_helper_prints_evidence_only_for_evidence_kinds(project, tmp_path, kind, prints):
    executions.start(project, "Deploy", session="s-sh")

    done = _helper(["--kind", kind, "--tool", "db"], _env(tmp_path, CLAUDE_CODE_SESSION_ID="s-sh"))

    assert done.returncode == 0
    written = executions.load(project)[0].events[0]
    assert written.kind == kind
    assert done.stdout == (f"evidence={written.id}\n" if prints else "")


def test_run_start_without_a_session_prints_an_eval_able_export_line(project):
    env = {k: v for k, v in os.environ.items() if k not in ("EOS_SESSION", "CLAUDE_CODE_SESSION_ID")}
    done = _run(["run", "start", str(project), "--title", "Deploy"], env)

    lines = done.stdout.splitlines()
    assert done.returncode == 0 and "EOS_EXECUTION=" in done.stderr
    assert lines[0] and not lines[0].startswith("export ")
    import shlex
    parts = shlex.split(lines[-1])
    assert parts[0] == "export" and parts[1] == f"EOS_EXECUTION={lines[0]}"
    assert parts[2] == f"EOS_EXECUTION_LEDGER={executions.path_for(project)}"


def test_run_start_with_a_session_prints_no_export_line(project):
    done = _run(["run", "start", str(project), "--title", "Deploy", "--session", "s1"], dict(os.environ))

    assert "export " not in done.stdout
