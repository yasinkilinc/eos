"""The session brief shows the branch's ticket where it lives next door (2.x roadmap C5, read-only first).

A workspace keeps one ledger per project under a shared knowledge root; the
work and runs for a ticket are often recorded from the workspace root while a
session opens in the service. From a service on the ticket's branch, the brief
now names them instead of saying nothing is in flight.
"""
import subprocess
import sys
from pathlib import Path

from core import brief, executions, work

EOS = [sys.executable, str(Path(__file__).resolve().parents[1] / "core" / "eos.py")]


def _project(root: Path, knowledge: Path) -> Path:
    root.mkdir(parents=True)
    assert subprocess.run(EOS + ["init", str(root), "--no-ai"], capture_output=True).returncode == 0
    config = root / ".eos" / "config.toml"
    config.write_text(config.read_text(encoding="utf-8") + f'\n[knowledge]\ndir = "{knowledge}"\n', encoding="utf-8")
    return root


def _workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("EOS_STATE_DIR", str(tmp_path / "state"))
    shared = tmp_path / "knowledge"
    hub = _project(tmp_path / "hub", shared / "hub")
    service = _project(tmp_path / "service", shared / "service")
    for command in (["init", "-q"], ["checkout", "-q", "-b", "feature/FM-12"],
                    ["-c", "user.email=a@b", "-c", "user.name=a", "commit", "-q", "--allow-empty", "-m", "x"]):
        subprocess.run(["git", *command], cwd=service, check=True, capture_output=True)
    return hub, service


def test_the_tickets_work_and_runs_next_door_are_named(tmp_path, monkeypatch):
    hub, service = _workspace(tmp_path, monkeypatch)
    item = work.open_item(hub, "FM-12 digital channel order", session="s0")
    executions.start(hub, "FM-12 env1 test", session="s0")
    work.open_item(hub, "FM-99 something else", session="s0")
    text = brief.build(service)
    assert "ELSEWHERE" in text and "hub" in text
    assert item.id in text and "FM-12 env1 test" in text
    assert "FM-99" not in text


def test_nothing_is_said_off_a_ticket_branch_or_when_nothing_matches(tmp_path, monkeypatch):
    hub, service = _workspace(tmp_path, monkeypatch)
    work.open_item(hub, "FM-99 something else", session="s0")
    assert "ELSEWHERE" not in brief.build(service)
    work.open_item(hub, "FM-12 digital channel order", session="s0")
    assert "ELSEWHERE" not in brief.build(hub)          # the hub is on no ticket branch
