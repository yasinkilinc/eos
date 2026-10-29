"""The Stop hook's done gate: a completion claim needs a passing check after the last change."""
import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

from core import executions, gates, hooks

REPO = Path(__file__).resolve().parents[1]
EOS = [sys.executable, str(REPO / "core" / "eos.py")]

WORDS = ["bitti", "tamamlandı", "tamamlandi", "düzeldi", "duzeldi", "geçti", "gecti",
         "çalışıyor", "calisiyor", "done", "fixed", "passing", "passes", "works"]


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    root.mkdir()
    assert subprocess.run(EOS + ["init", str(root), "--no-ai"], capture_output=True).returncode == 0
    monkeypatch.setenv("EOS_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("EOS_HOOK_STATE_DIR", raising=False)
    monkeypatch.delenv("EOS_EXECUTION", raising=False)
    monkeypatch.delenv("EOS_EXECUTION_LEDGER", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "s1")
    return root


def _mode(root, value):
    (root / ".eos" / "config.toml").write_text(f'[hooks]\nclose = false\n[gates]\nmode = "{value}"\n', encoding="utf-8")


def _stop(monkeypatch, capsys, root, message, **extra):
    payload = {"session_id": "s1", "cwd": str(root), "last_assistant_message": message, **extra}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    assert hooks.main(["stop"]) == 0
    return capsys.readouterr().out


def _changed(root):
    executions.start(root, "a run", session="s1")
    executions.event(root, kind="changed", tool="Edit", target="a.py", source="hook", session="s1")


def _ran(root, *, kind="ran", tool="mvn", exit_code=0, source="wrapper"):
    executions.event(root, kind=kind, tool=tool, exit_code=exit_code, source=source, session="s1")


def _gate_events(root):
    return [e for r in executions.load(root) for e in r.events
            if e.kind == "noted" and e.tool == gates.TOOL]


@pytest.mark.parametrize("word", WORDS)
def test_each_word_is_a_claim(word):
    assert gates.done_claim(f"Hepsi {word.upper() if word.isascii() else word}.")


@pytest.mark.parametrize("text", [
    "run `done` first", "```\nall done\n```\nnothing else", "it is not passing",
    "this isn't working, and doesn't work", "it doesn't pass", "undone and abandoned",
    "the fixer", "", "tests still fail", "it is not yet done", "not yet fully done",
    "henüz bitti denemez", "HENÜZ BİTTİ", "daha bitti sayılmaz", "henüz tam bitti"])
def test_not_a_claim(text):
    assert not gates.done_claim(text)


def test_turkish_dotted_capital_folds():
    assert gates.done_claim("BİTTİ")
    assert gates.done_claim("ÇALIŞIYOR")


def test_claim_with_no_evidence_blocks(project, monkeypatch, capsys):
    _mode(project, "block")
    _changed(project)
    out = json.loads(_stop(monkeypatch, capsys, project, "bitti"))
    assert out["decision"] == "block" and "automation/mvn.sh test" in out["reason"]
    assert [e.body for e in _gate_events(project)] == ["done-without-evidence: done"]


def test_fresh_wrapper_run_passes(project, monkeypatch, capsys):
    _mode(project, "block")
    _changed(project)
    _ran(project)
    assert _stop(monkeypatch, capsys, project, "done") == ""
    assert _gate_events(project) == []


def test_run_before_the_change_does_not_count(project, monkeypatch, capsys):
    _mode(project, "block")
    executions.start(project, "a run", session="s1")
    _ran(project)
    executions.event(project, kind="changed", tool="Edit", target="a.py", source="hook", session="s1")
    assert _stop(monkeypatch, capsys, project, "fixed")


def test_subagent_verified_fires(project, monkeypatch, capsys):
    _mode(project, "block")
    _changed(project)
    _ran(project, kind="verified", tool="subagent", source="hook")
    assert json.loads(_stop(monkeypatch, capsys, project, "works"))["decision"] == "block"


def test_failing_run_fires(project, monkeypatch, capsys):
    _mode(project, "block")
    _changed(project)
    _ran(project, exit_code=1)
    assert json.loads(_stop(monkeypatch, capsys, project, "passes"))["decision"] == "block"


def test_no_changed_event_no_gate(project, monkeypatch, capsys):
    _mode(project, "block")
    executions.start(project, "a run", session="s1")
    assert _stop(monkeypatch, capsys, project, "done") == ""


def test_no_open_run_no_gate(project, monkeypatch, capsys):
    _mode(project, "block")
    assert _stop(monkeypatch, capsys, project, "done") == ""


def test_warn_records_and_stays_silent(project, monkeypatch, capsys):
    _mode(project, "warn")
    _changed(project)
    assert _stop(monkeypatch, capsys, project, "done") == ""
    assert [e.body for e in _gate_events(project)] == ["done-without-evidence: done"]


def test_off_does_nothing(project, monkeypatch, capsys):
    _mode(project, "off")
    _changed(project)
    assert _stop(monkeypatch, capsys, project, "done") == ""
    assert _gate_events(project) == []


def test_second_identical_stop_is_silent(project, monkeypatch, capsys):
    _mode(project, "block")
    _changed(project)
    assert _stop(monkeypatch, capsys, project, "done")
    assert _stop(monkeypatch, capsys, project, "done") == ""


@pytest.mark.parametrize("extra", [{"stop_hook_active": True}, {"agent_id": "a1", "agent_type": "Explore"}])
def test_skipped_for_stop_active_and_subagent(project, monkeypatch, capsys, extra):
    _mode(project, "block")
    _changed(project)
    assert _stop(monkeypatch, capsys, project, "done", **extra) == ""


def test_a_claim_after_a_denial_further_than_two_words_is_still_a_claim():
    assert gates.done_claim("not yet sure about that, but it works")
    assert gates.done_claim("henüz değil ama şimdi bitti")


def test_two_different_claims_in_one_run_record_two_events(project, monkeypatch, capsys):
    _mode(project, "warn")
    _changed(project)
    _stop(monkeypatch, capsys, project, "done")
    _stop(monkeypatch, capsys, project, "bitti")
    refs = [e.ref for e in _gate_events(project)]
    assert len(refs) == 2 and len(set(refs)) == 2
    import hashlib
    assert refs[0] == hashlib.sha256(b"done").hexdigest()[:8]
    assert all(len(r) == 8 for r in refs)
    assert [e.body for e in _gate_events(project)] == ["done-without-evidence: done"] * 2


def test_block_with_close_on_gives_both_the_open_run_and_the_done_reason(project, monkeypatch, capsys):
    (project / ".eos" / "config.toml").write_text(
        '[hooks]\nclose = true\n[gates]\nmode = "block"\n', encoding="utf-8")
    _changed(project)
    out = json.loads(_stop(monkeypatch, capsys, project, "bitti"))
    assert out["decision"] == "block"
    assert "run(s) open" in out["reason"] and "done-without-evidence" not in out["reason"]
    assert "completion claimed, no passing check" in out["reason"]
