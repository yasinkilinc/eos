"""One note about several projects of a workspace (2.x roadmap M4, ADR-035).

A journey spans services. It used to be copied into every service's store (56
files for 18 journeys on the host) and the copies drifted apart. Now it is one
note in the workspace's store naming the projects it is about; each of them
reads it as its own, with its scope entries relative to that project.
"""
import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

from core import hooks, notes

EOS = [sys.executable, str(Path(__file__).resolve().parents[1] / "core" / "eos.py")]


def _init(root: Path) -> Path:
    root.mkdir(parents=True)
    assert subprocess.run(EOS + ["init", str(root), "--no-ai"], capture_output=True).returncode == 0
    return root


@pytest.fixture
def ws(tmp_path, monkeypatch):
    monkeypatch.setenv("EOS_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("EOS_HOOK_STATE_DIR", raising=False)
    hub = _init(tmp_path / "hub")
    services = {}
    for name in ("svc-a", "svc-b", "svc-c"):
        svc = _init(tmp_path / "services" / name)
        config = svc / ".eos" / "config.toml"
        config.write_text(config.read_text(encoding="utf-8") + '\n[workspace]\nroot = "../../hub"\n',
                          encoding="utf-8")
        (svc / "src").mkdir()
        (svc / "src" / "A.java").write_text("class A {}\n", encoding="utf-8")
        services[name] = svc
    (hub / ".eos" / "projects.toml").write_text(
        "".join(f'[[project]]\nroot = "../services/{name}"\n\n' for name in services), encoding="utf-8")
    return hub, services


def _journey(hub, **kw):
    return notes.add_note(hub, kind="finding", title="Order journey across services",
                          body="The order goes capture, then catalog, then the wallet.", source="journey-map",
                          projects=kw.pop("projects", ["svc-a", "svc-b"]),
                          scope=kw.pop("scope", ["@project:svc-a/src/A.java"]), **kw)


def test_a_shared_note_names_its_projects_and_hashes_their_files(ws):
    hub, services = ws
    note = notes.parse_note(_journey(hub))
    assert note.projects == ("svc-a", "svc-b")
    assert note.scope == ["@project:svc-a/src/A.java"] and note.scope_hashes[0] is not None


def test_a_project_the_workspace_does_not_list_is_refused(ws):
    hub, _ = ws
    with pytest.raises(ValueError, match="svc-z"):
        _journey(hub, projects=["svc-a", "svc-z"])


def test_projects_outside_a_workspace_are_refused(ws):
    _, services = ws
    with pytest.raises(ValueError, match="workspace"):
        _journey(services["svc-a"], scope=[])


def test_each_named_project_reads_it_as_its_own(ws):
    hub, services = ws
    path = _journey(hub)
    [seen_a] = notes.shared_notes(services["svc-a"])
    [seen_b] = notes.shared_notes(services["svc-b"])
    assert seen_a.path == seen_b.path == path
    assert seen_a.scope == ["src/A.java"] and seen_b.scope == []
    assert notes.shared_notes(services["svc-c"]) == []
    assert [n.title for n in notes.search_notes(services["svc-a"], "order journey")] == ["Order journey across services"]
    assert notes.search_notes(services["svc-c"], "order journey") == []


def test_the_workspace_audits_the_file_in_the_project(ws):
    hub, services = ws
    _journey(hub)
    (services["svc-a"] / "src" / "A.java").write_text("class A { int x; }\n", encoding="utf-8")
    [stale] = notes.stale_notes(hub)
    assert stale["note"].endswith("order-journey-across-services.md")


def test_a_touched_file_brings_the_shared_note(ws, monkeypatch, capsys):
    hub, services = ws
    _journey(hub)
    payload = {"session_id": "m4", "cwd": str(hub), "hook_event_name": "PostToolBatch",
               "tool_calls": [{"tool_name": "Read",
                               "tool_input": {"file_path": str(services["svc-a"] / "src" / "A.java")}}]}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    assert hooks.main(["post-batch"]) == 0
    text = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]
    assert "Order journey across services" in text and "capture, then catalog" in text


def test_context_without_a_query_includes_it(ws):
    hub, services = ws
    _journey(hub)
    assert "Order journey across services" in notes.render_context_section(services["svc-b"], None, 4000)


def test_the_cli_writes_and_shows_it(ws):
    hub, services = ws
    added = subprocess.run(EOS + ["note", "add", str(hub), "--kind", "finding", "--title", "Wallet journey",
                                  "--body", "The wallet is charged last.", "--projects", "svc-a,svc-c"],
                           capture_output=True, text=True)
    assert added.returncode == 0, added.stderr
    shown = subprocess.run(EOS + ["note", "show", str(services["svc-c"]), "Wallet journey"],
                           capture_output=True, text=True)
    assert shown.returncode == 0 and "charged last" in shown.stdout


def test_a_golden_answer_may_be_a_shared_note(ws):
    from core import retrieval

    hub, services = ws
    path = _journey(hub)
    entries = [retrieval.GoldenEntry(query="order journey", expected=path.name, line=1)]
    report = retrieval.evaluate(services["svc-a"], entries)
    assert report["broken"] == [] and report["recall"][1] == 1.0


def test_a_hand_written_single_project_is_one_project(ws):
    hub, services = ws
    path = _journey(hub, projects=["svc-a"], scope=[])
    raw = path.read_text(encoding="utf-8").replace("projects:\n  - svc-a\n", "projects: svc-a\n")
    path.write_text(raw, encoding="utf-8")
    assert notes.parse_note(path).projects == ("svc-a",)
    assert [n.title for n in notes.shared_notes(services["svc-a"])] == ["Order journey across services"]
