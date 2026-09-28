"""E3 depth 2: a procedure's `## Success` checks EOS may run itself.

Only a check whose own bullet carries an explicit `(read-only)` marker is
ever run; everything else is listed. Never guessed from the command text.
"""
import json
import subprocess
import sys
from pathlib import Path

from core import executions, notes, verification

EOS = [sys.executable, str(Path(__file__).resolve().parents[1] / "core" / "eos.py")]


def _eos(*args, **kwargs):
    done = subprocess.run(EOS + list(args), capture_output=True, text=True, **kwargs)
    assert done.returncode == 0, done.stderr
    return done.stdout


def _project(tmp_path, monkeypatch):
    monkeypatch.setenv("EOS_STATE_DIR", str(tmp_path / "state"))
    root = tmp_path / "proj"
    root.mkdir()
    _eos("init", str(root), "--no-ai")
    return root


def _procedure(root, *success):
    slug = _eos("procedure", "new", str(root), "--title", "Ship it", "--steps", "-",
               *[a for item in success for a in ("--success", item)],
               input="Do it\n").split("\t")[0]
    return notes.find_procedure(root, slug)


# --- core.verification.run_depth2 ----------------------------------------------------------


def test_a_read_only_check_is_run_and_recorded(tmp_path, monkeypatch):
    root = _project(tmp_path, monkeypatch)
    note = _procedure(root, "`python3 -c \"print(1)\"` prints 1 (read-only)")
    results = verification.run_depth2(root, note)
    assert len(results) == 1
    [check] = results
    assert check.ran is True and check.read_only is True
    assert check.exit_code == 0 and check.ok is True
    assert isinstance(check.ms, int) and check.ms >= 0


def test_a_check_without_the_marker_is_listed_never_run(tmp_path, monkeypatch):
    root = _project(tmp_path, monkeypatch)
    note = _procedure(root, "`python3 -c \"import sys; sys.exit(1)\"` never runs")
    [check] = verification.run_depth2(root, note)
    assert check.ran is False and check.read_only is False
    assert check.exit_code is None and check.ok is None


def test_a_read_only_check_that_fails_is_recorded_as_not_ok(tmp_path, monkeypatch):
    root = _project(tmp_path, monkeypatch)
    note = _procedure(root, "`python3 -c \"import sys; sys.exit(3)\"` fails (read-only)")
    [check] = verification.run_depth2(root, note)
    assert check.ran is True and check.exit_code == 3 and check.ok is False


def test_a_passing_check_keeps_only_a_digest_never_the_output_text(tmp_path, monkeypatch):
    root = _project(tmp_path, monkeypatch)
    note = _procedure(root, "`python3 -c \"print('secret-marker')\"` prints (read-only)")
    [check] = verification.run_depth2(root, note)
    assert check.output_sha256 and len(check.output_sha256) == 64
    assert check.output_tail is None


def test_a_failing_check_keeps_a_bounded_tail_of_its_output(tmp_path, monkeypatch):
    root = _project(tmp_path, monkeypatch)
    note = _procedure(
        root,
        "`python3 -c \"import sys; print('boom-detail'); sys.exit(1)\"` fails (read-only)")
    [check] = verification.run_depth2(root, note)
    assert check.output_sha256 and "boom-detail" in check.output_tail
    assert len(check.output_tail) <= verification.DEPTH2_TAIL_CHARS


def test_a_running_check_writes_a_verified_event_on_the_open_run(tmp_path, monkeypatch):
    root = _project(tmp_path, monkeypatch)
    note = _procedure(root, "`python3 -c \"print(1)\"` prints 1 (read-only)")
    run = _eos("run", "start", str(root), "--title", "Ship it", "--procedure", note.procedure,
              "--session", "s1").split()[0]
    verification.run_depth2(root, note, execution=run, session="s1")
    [record] = executions.load(root)
    verified = [e for e in record.events if e.kind == "verified"]
    assert len(verified) == 1 and verified[0].exit_code == 0 and verified[0].status == "ok"


def test_only_the_marked_check_runs_when_several_are_named(tmp_path, monkeypatch):
    root = _project(tmp_path, monkeypatch)
    note = _procedure(
        root,
        "`python3 -c \"print(1)\"` runs (read-only)",
        "`python3 -c \"import sys; sys.exit(9)\"` does not run",
    )
    results = verification.run_depth2(root, note)
    ran = {c.command: c for c in results if c.ran}
    listed = {c.command: c for c in results if not c.ran}
    assert list(ran) == ['python3 -c "print(1)"']
    assert list(listed) == ['python3 -c "import sys; sys.exit(9)"']


# --- through the CLI -------------------------------------------------------------------------


def test_eos_verify_procedure_runs_the_marked_check_and_lists_the_rest(tmp_path, monkeypatch):
    root = _project(tmp_path, monkeypatch)
    note = _procedure(
        root,
        "`python3 -c \"print(1)\"` runs (read-only)",
        "`python3 -c \"import sys; sys.exit(9)\"` does not run",
    )
    shown = _eos("verify", str(root), "--procedure", note.procedure)
    assert "ran (" in shown and "listed, not run (no (read-only) marker)" in shown


def test_eos_verify_procedure_json_and_output_file(tmp_path, monkeypatch):
    root = _project(tmp_path, monkeypatch)
    note = _procedure(root, "`python3 -c \"print(1)\"` runs (read-only)")
    out = tmp_path / "report.json"
    _eos("verify", str(root), "--procedure", note.procedure, "--output", str(out))
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["procedure"] == note.procedure
    assert data["checks"][0]["ran"] is True and data["checks"][0]["ok"] is True


def test_eos_verify_procedure_exits_nonzero_when_a_run_check_failed(tmp_path, monkeypatch):
    root = _project(tmp_path, monkeypatch)
    note = _procedure(root, "`python3 -c \"import sys; sys.exit(2)\"` fails (read-only)")
    done = subprocess.run(EOS + ["verify", str(root), "--procedure", note.procedure],
                          capture_output=True, text=True)
    assert done.returncode == 1


def test_eos_verify_procedure_takes_an_explicit_session_flag(tmp_path, monkeypatch):
    """RV (day3b, item 1-2): every other stateful subcommand takes --session;
    --procedure mode silently lacked it and fell back to env-var detection only."""
    root = _project(tmp_path, monkeypatch)
    note = _procedure(root, "`python3 -c \"print(1)\"` runs (read-only)")
    run = _eos("run", "start", str(root), "--title", "Ship it", "--procedure", note.procedure,
              "--session", "s1").split()[0]
    _eos("verify", str(root), "--procedure", note.procedure, "--session", "s1")
    [record] = executions.load(root)
    assert record.id == run
    assert any(e.kind == "verified" for e in record.events)


def test_a_failing_read_only_check_is_recorded_as_an_error_event(tmp_path, monkeypatch):
    """RV (day3b, item 1-2): only the passing case was covered before."""
    root = _project(tmp_path, monkeypatch)
    note = _procedure(root, "`python3 -c \"import sys; sys.exit(1)\"` fails (read-only)")
    run = _eos("run", "start", str(root), "--title", "Ship it", "--procedure", note.procedure,
              "--session", "s1").split()[0]
    verification.run_depth2(root, note, execution=run, session="s1")
    [record] = executions.load(root)
    [verified] = [e for e in record.events if e.kind == "verified"]
    assert verified.exit_code == 1 and verified.status == "error"


def test_manual_mode_is_unchanged(tmp_path, monkeypatch):
    """eos verify <code> --outcome ... --command ... still works exactly as before."""
    root = _project(tmp_path, monkeypatch)
    out = _eos("verify", str(root), "AGE_IMPLAUSIBLE", "--outcome", "passed", "--command", "mvn test")
    assert "recorded passed for AGE_IMPLAUSIBLE" in out


def test_missing_arguments_is_a_clean_error_not_a_traceback(tmp_path, monkeypatch):
    root = _project(tmp_path, monkeypatch)
    done = subprocess.run(EOS + ["verify", str(root)], capture_output=True, text=True)
    assert done.returncode == 2 and "error:" in done.stderr
