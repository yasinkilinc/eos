"""A run started from the workspace root lands in the ledger of the project it names (2.x roadmap C5, part 2)."""
import subprocess
import sys
from pathlib import Path

import pytest

from core import executions, workspace

EOS = [sys.executable, str(Path(__file__).resolve().parents[1] / "core" / "eos.py")]


def _project(root: Path) -> Path:
    root.mkdir(parents=True)
    assert subprocess.run(EOS + ["init", str(root), "--no-ai"], capture_output=True).returncode == 0
    return root


@pytest.fixture
def ws(tmp_path, monkeypatch):
    monkeypatch.setenv("EOS_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("EOS_SESSION", "s1")
    for name in ("EOS_EXECUTION", "EOS_EXECUTION_LEDGER"):
        monkeypatch.delenv(name, raising=False)
    hub = _project(tmp_path / "hub")
    _project(tmp_path / "services" / "order-capture")
    _project(tmp_path / "services" / "crm-batch2")
    (hub / ".eos" / "projects.toml").write_text(
        '[[project]]\nroot = "../services/order-capture"\naliases = ["ordercapture", "oc"]\n\n'
        '[[project]]\nroot = "../services/crm-batch2"\n\n'
        '[[project]]\nroot = "../services/not-cloned-here"\n', encoding="utf-8")
    return tmp_path, hub


def _start(hub, *args):
    return subprocess.run(EOS + ["run", "start", str(hub), *args], capture_output=True, text=True)


def _ids(root):
    return [r.id for r in executions.load(root)]


def test_a_title_naming_one_project_lands_in_its_ledger(ws):
    tmp_path, hub = ws
    done = _start(hub, "--title", "Fix the Order Capture timeout")
    assert done.returncode == 0, done.stderr
    run = done.stdout.split()[0]
    assert run in _ids(tmp_path / "services" / "order-capture")
    assert run not in _ids(hub)
    assert "order-capture's ledger" in done.stderr and "--here" in done.stderr


def test_events_and_finish_follow_the_run_to_that_ledger(ws):
    tmp_path, hub = ws
    run = _start(hub, "--title", "oc: build").stdout.split()[0]
    executions.event(hub, kind="ran", tool="mvn", session="s1")
    finished = subprocess.run(EOS + ["run", "finish", str(hub), run, "--outcome", "ok", "--session", "other"],
                              capture_output=True, text=True)
    assert finished.returncode == 0, finished.stderr
    record = {r.id: r for r in executions.load(tmp_path / "services" / "order-capture")}[run]
    assert record.outcome == "ok" and [e.tool for e in record.events] == ["mvn"]


def test_here_project_and_ambiguity(ws):
    tmp_path, hub = ws
    here = _start(hub, "--title", "oc refactor", "--here").stdout.split()[0]
    assert here in _ids(hub)
    forced = _start(hub, "--title", "env1 regression", "--project", "crm-batch2").stdout.split()[0]
    assert forced in _ids(tmp_path / "services" / "crm-batch2")
    both = _start(hub, "--title", "oc and crm-batch2 together")
    assert both.stdout.split()[0] in _ids(hub) and "--project picks one" in both.stderr
    unknown = _start(hub, "--title", "x", "--project", "nope")
    assert unknown.returncode == 1 and "known: order-capture, crm-batch2" in unknown.stderr


def test_a_near_miss_is_a_suggestion_and_digits_are_not_typos(ws):
    tmp_path, hub = ws
    near = _start(hub, "--title", "ordercaptur retry")
    assert near.stdout.split()[0] in _ids(hub)
    assert "almost names order-capture" in near.stderr
    other = _start(hub, "--title", "crm-batch3 retry")
    assert other.stdout.split()[0] in _ids(hub) and "almost" not in other.stderr


def test_without_projects_toml_nothing_changes(tmp_path, monkeypatch):
    monkeypatch.setenv("EOS_STATE_DIR", str(tmp_path / "state"))
    hub = _project(tmp_path / "hub")
    done = _start(hub, "--title", "order-capture work", "--session", "s1")
    assert done.stdout.split()[0] in _ids(hub) and "ledger" not in done.stderr
    assert _start(hub, "--title", "x", "--project", "a").returncode == 1


def test_a_broken_file_says_so(ws):
    _, hub = ws
    (hub / ".eos" / "projects.toml").write_text("[[project]]\nname = 'no root'\n", encoding="utf-8")
    done = _start(hub, "--title", "x")
    assert done.returncode == 1 and "has no root" in done.stderr
    with pytest.raises(ValueError):
        workspace.load(hub)
