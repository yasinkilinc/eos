"""The prompt hook approves an open assumption only from the user's own words."""
import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

from core import decisions, executions, hooks

REPO = Path(__file__).resolve().parents[1]
EOS = [sys.executable, str(REPO / "core" / "eos.py")]


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


def _assumption(root, claim="the table is empty"):
    return decisions.record(root, claim, [], session="s1")


def _prompt(monkeypatch, capsys, root, text):
    payload = {"session_id": "s1", "cwd": str(root), "prompt": text}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    assert hooks.main(["user-prompt"]) == 0
    return capsys.readouterr().out


def _state(root, decision_id):
    return next(d for d in decisions.for_scope(root, work_item=None, execution=executions.current(root, "s1")[0],
                                               session="s1") if d.id == decision_id)


def test_word_after_id_approves(project, monkeypatch, capsys):
    executions.start(project, "run", session="s1")
    found = _assumption(project)

    out = _prompt(monkeypatch, capsys, project, f"{found.id} onay")

    after = _state(project, found.id)
    written = [e for r in executions.load(project) for e in r.events if e.ref == found.id]
    assert after.state == "approved" and written[-1].source == "hook"
    assert after.check == "user prompt: onay"
    assert f"EOS: {found.id} approved by the user's prompt." in out


def test_word_before_id_does_not(project, monkeypatch, capsys):
    executions.start(project, "run", session="s1")
    found = _assumption(project)

    _prompt(monkeypatch, capsys, project, f"onay {found.id}")

    assert _state(project, found.id).state == "assumption"


def test_notice_does_not(project, monkeypatch, capsys):
    executions.start(project, "run", session="s1")
    found = _assumption(project)

    _prompt(monkeypatch, capsys, project, f"<task-notification>{found.id} onay</task-notification>")

    assert _state(project, found.id).state == "assumption"


def test_evidenced_decision_is_ignored(project, monkeypatch, capsys):
    executions.start(project, "run", session="s1")
    proof = executions.event(project, kind="ran", tool="gitx", source="wrapper", session="s1")
    found = decisions.record(project, "clean", [proof.id], session="s1")
    assert found.state == "evidenced"

    out = _prompt(monkeypatch, capsys, project, f"{found.id} onay")

    assert _state(project, found.id).state == "evidenced"
    assert "approved by the user" not in out


def test_two_ids_both_approved(project, monkeypatch, capsys):
    executions.start(project, "run", session="s1")
    first, second = _assumption(project, "first guess"), _assumption(project, "second guess")

    _prompt(monkeypatch, capsys, project, f"{first.id} onaylıyorum, {second.id} APPROVED")

    assert _state(project, first.id).state == "approved"
    assert _state(project, second.id).state == "approved"


def test_no_open_run_does_nothing(project, monkeypatch, capsys):
    out = _prompt(monkeypatch, capsys, project, "d-1a2b3c4d onay")

    assert "approved by the user" not in out


def test_works_with_brief_off(project, monkeypatch, capsys):
    (project / ".eos" / "config.toml").write_text("[hooks]\nbrief = false\n", encoding="utf-8")
    executions.start(project, "run", session="s1")
    found = _assumption(project)

    out = _prompt(monkeypatch, capsys, project, f"{found.id} approve")

    assert _state(project, found.id).state == "approved"
    assert f"EOS: {found.id} approved by the user's prompt." in out


def test_excerpt_passes_checked():
    for word in ("onay", "onaylıyorum", "onayladım", "approve", "approved"):
        assert executions._checked(f"user prompt: {word}", "check")
