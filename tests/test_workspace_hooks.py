"""The workspace root's hooks brief the projects under it (2.x roadmap C5, the last part).

Sessions in a workspace of many projects start at its root. The root's brief
used to be the only one they got, so a host wrote its own hooks to brief the
projects too. With `.eos/projects.toml` the engine does it: at start the
projects with something live, on a prompt the projects it names, and the notes
a prompt names in stores no brief covered.
"""
import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

from core import executions, hooks, workspace

EOS = [sys.executable, str(Path(__file__).resolve().parents[1] / "core" / "eos.py")]


def _project(root: Path) -> Path:
    root.mkdir(parents=True)
    assert subprocess.run(EOS + ["init", str(root), "--no-ai"], capture_output=True).returncode == 0
    return root


@pytest.fixture
def ws(tmp_path, monkeypatch):
    monkeypatch.setenv("EOS_STATE_DIR", str(tmp_path / "state"))
    for name in ("EOS_HOOK_STATE_DIR", "EOS_EXECUTION", "EOS_EXECUTION_LEDGER", "EOS_SESSION"):
        monkeypatch.delenv(name, raising=False)
    hub = _project(tmp_path / "hub")
    _project(tmp_path / "services" / "order-capture")
    _project(tmp_path / "services" / "crm-asset")
    (hub / ".eos" / "projects.toml").write_text(
        '[[project]]\nroot = "../services/order-capture"\naliases = ["ordercapture"]\n\n'
        '[[project]]\nroot = "../services/crm-asset"\n', encoding="utf-8")
    return tmp_path, hub


def _hook(monkeypatch, capsys, event, payload):
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    assert hooks.main([event]) == 0
    return capsys.readouterr().out


def _start(monkeypatch, capsys, hub, session, **fields):
    return _hook(monkeypatch, capsys, "session-start", {"session_id": session, "cwd": str(hub), **fields})


def _prompt(monkeypatch, capsys, hub, text, session):
    return _hook(monkeypatch, capsys, "user-prompt", {"session_id": session, "cwd": str(hub), "prompt": text})


def _note(project: Path, name: str, title: str, body: str, **front) -> None:
    store = project / ".eos" / "knowledge"
    store.mkdir(parents=True, exist_ok=True)
    extra = "".join(f"{key}: {value}\n" for key, value in front.items())
    (store / name).write_text(f"---\nkind: finding\ntitle: {title}\ncreated: 2026-09-01\n{extra}---\n\n{body}\n",
                              encoding="utf-8")


def test_start_briefs_the_root_and_only_the_projects_with_something_live(ws, monkeypatch, capsys):
    tmp_path, hub = ws
    service = tmp_path / "services" / "order-capture"
    quiet = _start(monkeypatch, capsys, hub, "s-quiet")
    assert "EOS brief — hub" in quiet and "order-capture" not in quiet
    run = executions.start(service, "Deploy wallet to env1", session="s-live")
    out = _start(monkeypatch, capsys, hub, "s-live")
    assert "EOS brief — ../services/order-capture" in out and run.id in out
    assert "crm-asset" not in out
    # Its commands name the project's path, not `.` (the workspace's own store).
    assert "eos run finish ../services/order-capture <id>" in out


def test_a_prompt_briefs_the_projects_it_names_once_per_session(ws, monkeypatch, capsys):
    tmp_path, hub = ws
    service = tmp_path / "services" / "order-capture"
    assert subprocess.run(EOS + ["procedure", "new", str(service), "--title", "Deploy a service to env1",
                                 "--step", "Build it (tool: jenkins)", "--step", "Sync it (tool: argo)"],
                          capture_output=True).returncode == 0
    assert _prompt(monkeypatch, capsys, hub, "devam", "s-p") == ""
    out = _prompt(monkeypatch, capsys, hub, "deploy ordercapture to env1", "s-p")
    assert "EOS brief for this task — ../services/order-capture" in out
    assert "PROCEDURE  Deploy a service to env1" in out and "Build it (tool: jenkins)" in out
    assert "crm-asset" not in out
    assert _prompt(monkeypatch, capsys, hub, "now deploy order-capture to env1 please", "s-p") == ""
    assert "PROCEDURE  Deploy a service to env1" in _prompt(monkeypatch, capsys, hub, "deploy ordercapture", "s-2")
    hits = [p for p in tmp_path.rglob("*") if p.is_file() and b"now deploy order-capture" in p.read_bytes()]
    assert hits == []


def test_a_prompt_reaches_a_note_in_a_store_no_brief_covered(ws, monkeypatch, capsys):
    tmp_path, hub = ws
    asset = tmp_path / "services" / "crm-asset"
    _note(asset, "20260901-status.md", "PROJ-1588 status and resume point", "Where we left off.")
    _note(asset, "20260902-levy.md", "Levy is never swapped on reassignment", "The levy row stays.")
    for n in range(3, 8):
        _note(asset, f"2026090{n}-deploy.md", f"Deploy note {n}", "How the deploy went.")
    _note(asset, "20260908-table.md", "PROJ-1588 endpoint table", "rows", source="api-inventory")

    out = _prompt(monkeypatch, capsys, hub, "PROJ-1588 where are we", "e1")
    assert "PROJ-1588 status and resume point  (crm-asset)" in out
    assert 'eos note show ../services/crm-asset "<title>"' in out
    assert "endpoint table" not in out
    assert "PROJ-1588 status" in _prompt(monkeypatch, capsys, hub, "back on 1588, where did we leave off", "e2")
    assert "Levy is never swapped" in _prompt(monkeypatch, capsys, hub, "why does the levy change", "e3")
    assert "NOTES ELSEWHERE" not in _prompt(monkeypatch, capsys, hub, "how did the deploy go", "e4")
    assert "PROJ-1588" not in _prompt(monkeypatch, capsys, hub, "start PROJ-1700", "e5")
    # A store a brief above already covered is not listed again.
    assert "NOTES ELSEWHERE" not in _prompt(monkeypatch, capsys, hub, "PROJ-1588 crm-asset", "e6")


def test_the_hosts_own_command_adds_a_block_with_the_projects_it_covered(ws, monkeypatch, capsys):
    tmp_path, hub = ws
    script = tmp_path / "extra.py"
    script.write_text("import json,sys\ne=json.load(sys.stdin)\n"
                      "print('HOST', e['source'] or '-', ','.join(p['name'] for p in e['projects']) or '-')\n",
                      encoding="utf-8")
    config = hub / ".eos" / "config.toml"
    config.write_text(config.read_text(encoding="utf-8") + "\n[hooks]\n"
                      f'start_command = "{sys.executable} {script}"\n'
                      f'prompt_command = "{sys.executable} {script}"\n', encoding="utf-8")
    assert "HOST startup -" in _start(monkeypatch, capsys, hub, "h1", source="startup")
    assert "HOST - order-capture" in _prompt(monkeypatch, capsys, hub, "look at ordercapture", "h1")
    assert "HOST" not in _prompt(monkeypatch, capsys, hub, "look at ordercapture again", "h1")
    config.write_text(config.read_text(encoding="utf-8").replace(str(script), str(tmp_path / "missing.py")),
                      encoding="utf-8")
    assert "HOST" not in _start(monkeypatch, capsys, hub, "h2")


def test_without_projects_toml_the_brief_is_what_it_was(ws, monkeypatch, capsys):
    tmp_path, hub = ws
    service = tmp_path / "services" / "order-capture"
    executions.start(service, "Deploy wallet to env1", session="x")
    with_file = _start(monkeypatch, capsys, hub, "b1")
    (hub / ".eos" / "projects.toml").unlink()
    from core import brief

    assert _start(monkeypatch, capsys, hub, "b2") == brief.build(hub, session="b2") + "\n"
    assert "order-capture" in with_file


def test_a_session_started_in_a_project_gets_its_workspaces_procedure(ws, monkeypatch, capsys):
    tmp_path, hub = ws
    service = tmp_path / "services" / "order-capture"
    assert subprocess.run(EOS + ["procedure", "new", str(hub), "--title", "Deploy a service to env1",
                                 "--step", "Build it (tool: jenkins)"], capture_output=True).returncode == 0
    ask = {"session_id": "w1", "cwd": str(service), "prompt": "deploy order-capture to env1"}
    assert _hook(monkeypatch, capsys, "user-prompt", ask) == ""
    config = service / ".eos" / "config.toml"
    config.write_text(config.read_text(encoding="utf-8") + '\n[workspace]\nroot = "../../hub"\n', encoding="utf-8")
    out = _hook(monkeypatch, capsys, "user-prompt", {**ask, "session_id": "w2"})
    assert "EOS brief for this task — ../../hub" in out and "PROCEDURE  Deploy a service to env1" in out
    assert "eos run start ../../hub --title" in out
    # A link the workspace does not confirm is no link.
    (hub / ".eos" / "projects.toml").write_text('[[project]]\nroot = "../services/crm-asset"\n', encoding="utf-8")
    assert _hook(monkeypatch, capsys, "user-prompt", {**ask, "session_id": "w3"}) == ""


def test_relocate_rewrites_commands_not_prose():
    text = ('EOS brief — svc @ main\n  Read one: eos note show . "<title>"\n  …2 more: eos work list .\n'
            "  eos run finish . <id> --outcome ok\nA sentence ending with eos.")
    out = workspace.relocate(text, "svc", "../svc")
    assert out.splitlines()[0] == "EOS brief — ../svc @ main"
    assert 'eos note show ../svc "<title>"' in out and "eos work list ../svc" in out
    assert "eos run finish ../svc <id>" in out and out.endswith("ending with eos.")
