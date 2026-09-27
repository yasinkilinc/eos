"""An operational run is verified by its procedure's `## Success` checks (ADR-028 addendum).

Measured on the host: none of 57 ok runs changed a file in a verify scope, so none
was verified or claimed. A procedure's Success lines name the checks that prove it
(`scripts/tracker.sh issue X` shows done); a run that ran each named tool with
exit 0 is verified, one that did not is claimed.
"""
import json
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
    _eos("run", "event", str(root), checked, "--kind", "called", "--tool", "tracker", "--ref", "issue FM-1",
         "--exit", "0")
    _eos("run", "finish", str(root), checked, "--outcome", "ok")
    unchecked = _eos("run", "start", str(root), "--title", "Close FM-2", "--procedure", slug,
                     "--session", "s2").split()[0]
    said = _eos("run", "finish", str(root), unchecked, "--outcome", "ok")
    runs = {r.id: r for r in executions.load(root)}
    assert executions.outcome_source(root, runs[checked]) == ("verified", [])
    assert executions.outcome_source(root, runs[unchecked]) == ("claimed", ["success: tracker issue"])
    assert "Success check" in said and "tracker" in said


def test_the_counter_moves_on_every_ok_run_and_shows_verified_and_claimed_beside_it(tmp_path, monkeypatch):
    # 2.x M2, decided 2026-09-27: counters stay on ok; the split is shown, not enforced.
    root, slug = _project(tmp_path, monkeypatch)
    checked = _eos("run", "start", str(root), "--title", "Close FM-1", "--procedure", slug,
                   "--session", "s1").split()[0]
    _eos("run", "event", str(root), checked, "--kind", "called", "--tool", "tracker", "--ref", "issue FM-1",
         "--exit", "0")
    _eos("run", "finish", str(root), checked, "--outcome", "ok")
    unchecked = _eos("run", "start", str(root), "--title", "Close FM-2", "--procedure", slug,
                     "--session", "s2").split()[0]
    _eos("run", "finish", str(root), unchecked, "--outcome", "ok")

    shown = _eos("procedure", "show", str(root), slug)
    assert "2 ok / 0 failed" in shown and "of the ok: verified 1, claimed 1" in shown
    as_json = json.loads(_eos("procedure", "show", str(root), slug, "--format", "json"))
    assert (as_json["runs_ok"], as_json["runs_verified"], as_json["runs_claimed"]) == (2, 1, 1)


def test_a_failed_success_check_does_not_verify(tmp_path, monkeypatch):
    root, slug = _project(tmp_path, monkeypatch)
    run = _eos("run", "start", str(root), "--title", "Close FM-3", "--procedure", slug, "--session", "s1").split()[0]
    _eos("run", "event", str(root), run, "--kind", "called", "--tool", "tracker", "--ref", "issue FM-3", "--exit", "1")
    _eos("run", "finish", str(root), run, "--outcome", "ok")
    [record] = executions.load(root)
    assert executions.outcome_source(root, record)[0] == "claimed"


def test_the_check_is_its_subcommand_and_comes_after_the_work(tmp_path, monkeypatch):
    root, slug = _project(tmp_path, monkeypatch)
    wrong_verb = _eos("run", "start", str(root), "--title", "Close FM-4", "--procedure", slug,
                      "--session", "s4").split()[0]
    _eos("run", "event", str(root), wrong_verb, "--kind", "called", "--tool", "tracker", "--ref", "search FM-4",
         "--exit", "0")
    _eos("run", "finish", str(root), wrong_verb, "--outcome", "ok")
    early = _eos("run", "start", str(root), "--title", "Close FM-5", "--procedure", slug, "--session", "s5").split()[0]
    _eos("run", "event", str(root), early, "--kind", "called", "--tool", "tracker", "--ref", "issue FM-5", "--exit", "0")
    _eos("run", "event", str(root), early, "--kind", "changed", "--tool", "edit", "--ref", "docs/x.md")
    _eos("run", "finish", str(root), early, "--outcome", "ok")
    runs = {r.id: r for r in executions.load(root)}
    assert executions.outcome_source(root, runs[wrong_verb])[0] == "claimed"
    assert executions.outcome_source(root, runs[early])[0] == "claimed"
