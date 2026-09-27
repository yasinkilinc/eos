"""An operational run is verified by its procedure's `## Success` checks (ADR-028 addendum).

Measured on the host: none of 57 ok runs changed a file in a verify scope, so none
was verified or claimed. A procedure's Success lines name the checks that prove it
(`scripts/tracker.sh issue X` shows done); a run that ran each named tool with
exit 0 is verified, one that did not is claimed.
"""
import subprocess
import sys
from pathlib import Path

from core import executions, steps

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
    knowledge = root / ".eos" / "knowledge"
    knowledge.mkdir(parents=True, exist_ok=True)
    (knowledge / "capabilities.toml").write_text('[[capability]]\nname = "tracker"\nrun = "scripts/tracker.sh"\n'
                                                 'does = "issue <KEY>"\n', encoding="utf-8")
    slug = _eos("procedure", "new", str(root), "--title", "Close a ticket", "--steps", "-",
                "--success", "`scripts/tracker.sh issue <KEY>` shows it done; answer with PR `#id`",
                input="Close it (tool: tracker)\n").split("\t")[0]
    return root, slug


def test_the_success_lines_name_only_tools_that_exist(tmp_path, monkeypatch):
    root, slug = _project(tmp_path, monkeypatch)
    from core import notes

    assert steps.success_tools(root, notes.find_procedure(root, slug)) == ["tracker"]


def test_a_run_that_ran_its_success_check_is_verified_and_one_that_did_not_is_claimed(tmp_path, monkeypatch):
    root, slug = _project(tmp_path, monkeypatch)
    checked = _eos("run", "start", str(root), "--title", "Close FM-1", "--procedure", slug,
                   "--session", "s1").split()[0]
    _eos("run", "event", str(root), checked, "--kind", "called", "--tool", "tracker", "--exit", "0")
    _eos("run", "finish", str(root), checked, "--outcome", "ok")
    unchecked = _eos("run", "start", str(root), "--title", "Close FM-2", "--procedure", slug,
                     "--session", "s2").split()[0]
    said = _eos("run", "finish", str(root), unchecked, "--outcome", "ok")
    runs = {r.id: r for r in executions.load(root)}
    assert executions.outcome_source(root, runs[checked]) == ("verified", [])
    assert executions.outcome_source(root, runs[unchecked]) == ("claimed", ["success: tracker"])
    assert "Success check" in said and "tracker" in said


def test_a_failed_success_check_does_not_verify(tmp_path, monkeypatch):
    root, slug = _project(tmp_path, monkeypatch)
    run = _eos("run", "start", str(root), "--title", "Close FM-3", "--procedure", slug, "--session", "s1").split()[0]
    _eos("run", "event", str(root), run, "--kind", "called", "--tool", "tracker", "--exit", "1")
    _eos("run", "finish", str(root), run, "--outcome", "ok")
    [record] = executions.load(root)
    assert executions.outcome_source(root, record)[0] == "claimed"
