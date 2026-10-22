"""The Stop gate on notes (2.x roadmap C5): source changed and nothing recorded, or a note left false."""
import hashlib
import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

from core import hooks, note_gate

EOS = [sys.executable, str(Path(__file__).resolve().parents[1] / "core" / "eos.py")]


def _project(root: Path, gated: bool) -> Path:
    root.mkdir(parents=True)
    assert subprocess.run(EOS + ["init", str(root), "--no-ai"], capture_output=True).returncode == 0
    if gated:
        config = root / ".eos" / "config.toml"
        config.write_text(config.read_text(encoding="utf-8") + "\n[hooks]\nnotes = true\n", encoding="utf-8")
    return root


@pytest.fixture
def ws(tmp_path, monkeypatch):
    monkeypatch.setenv("EOS_STATE_DIR", str(tmp_path / "state"))
    for name in ("EOS_HOOK_STATE_DIR", "EOS_EXECUTION", "EOS_EXECUTION_LEDGER"):
        monkeypatch.delenv(name, raising=False)
    hub = _project(tmp_path / "hub", gated=False)
    svc = _project(tmp_path / "services" / "svc-a", gated=True)
    (hub / ".eos" / "projects.toml").write_text(
        'worktrees = ["../services/.worktrees"]\n\n[[project]]\nroot = "../services/svc-a"\n', encoding="utf-8")
    return tmp_path, hub, svc


def _hook(monkeypatch, capsys, event, payload):
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    assert hooks.main([event]) == 0
    return capsys.readouterr().out


def _edit(monkeypatch, capsys, hub, path: Path, text="class A {}\n", session="s1"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    _hook(monkeypatch, capsys, "post-tool", {"session_id": session, "cwd": str(hub), "tool_name": "Edit",
                                             "tool_input": {"file_path": str(path)}, "tool_use_id": str(path)})


def _stop(monkeypatch, capsys, hub, session="s1", **fields):
    out = _hook(monkeypatch, capsys, "stop", {"session_id": session, "cwd": str(hub), **fields})
    return json.loads(out)["reason"] if out.strip() else ""


def _config_off(hub):
    config = hub / ".eos" / "config.toml"
    config.write_text(config.read_text(encoding="utf-8") + "\n[hooks]\nclose = false\n", encoding="utf-8")


def test_source_changed_and_nothing_recorded_is_asked_until_answered(ws, monkeypatch, capsys):
    _, hub, svc = ws
    _config_off(hub)
    _edit(monkeypatch, capsys, hub, svc / "src" / "A.java")
    reason = _stop(monkeypatch, capsys, hub)
    assert "This session changed source and recorded nothing about it." in reason
    assert f"eos note add {svc} --kind finding" in reason and "--session s1" in reason
    assert _stop(monkeypatch, capsys, hub, stop_hook_active=True) == ""
    assert subprocess.run(EOS + ["note", "skip", str(svc), "--reason", "typo only", "--session", "s1"],
                          capture_output=True).returncode == 0
    assert _stop(monkeypatch, capsys, hub) == ""


def test_a_note_naming_the_session_answers_it(ws, monkeypatch, capsys):
    _, hub, svc = ws
    _config_off(hub)
    _edit(monkeypatch, capsys, hub, svc / "src" / "A.java")
    done = subprocess.run(EOS + ["note", "add", str(svc), "--kind", "finding", "--tags", "t", "--title", "A must stay final",
                                 "--body", "Subclassing A breaks the factory.", "--session", "s1"],
                          capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    assert _stop(monkeypatch, capsys, hub) == ""


def test_the_workspaces_own_files_and_documents_are_not_gated(ws, monkeypatch, capsys):
    _, hub, svc = ws
    _config_off(hub)
    _edit(monkeypatch, capsys, hub, hub / "scripts" / "tool.py")
    _edit(monkeypatch, capsys, hub, svc / "README.md")
    assert _stop(monkeypatch, capsys, hub) == ""


def test_a_worktree_edit_is_its_projects(ws, monkeypatch, capsys):
    tmp_path, hub, svc = ws
    _config_off(hub)
    _edit(monkeypatch, capsys, hub, tmp_path / "services" / ".worktrees" / "svc-a-PROJ-1" / "src" / "B.java")
    assert f"svc-a ({svc})" in _stop(monkeypatch, capsys, hub)


def test_a_handwritten_note_left_false_is_named_with_its_amend(ws, monkeypatch, capsys):
    _, hub, svc = ws
    _config_off(hub)
    source = svc / "src" / "A.java"
    source.parent.mkdir(parents=True)
    source.write_text("class A {}\n", encoding="utf-8")
    for title, body, extra in (("A is built by the factory", "Use the factory.", []),
                               ("A endpoint table", "| GET | /a |", ["--source", "api-inventory"])):
        done = subprocess.run(EOS + ["note", "add", str(svc), "--kind", "finding", "--tags", "t", "--title", title,
                                     "--body", body, "--scope", "src/A.java", *extra,
                                     "--session", "s1"], capture_output=True, text=True)
        assert done.returncode == 0, done.stderr
    _edit(monkeypatch, capsys, hub, source, text="class A { A() {} }\n")
    reason = _stop(monkeypatch, capsys, hub)
    assert "svc-a: a note here no longer agrees with the file it is about." in reason
    assert "'A is built by the factory'" in reason and "endpoint table" not in reason
    assert "--body '<what is true now>' --session s1" in reason and "--reaffirm" in reason
    assert "recorded nothing" not in reason


def test_amend_flags_follow_what_was_removed():
    assert note_gate.amend_flags({"gone": [], "changed": ["a"]}).startswith("--body")
    assert note_gate.amend_flags({"gone": ["a"], "changed": []}).startswith("--scope")
    both = note_gate.amend_flags({"gone": ["a"], "changed": ["b"]})
    assert "--scope" in both and "--body" in both


def test_an_edited_copy_matching_the_note_is_not_stale(tmp_path):
    copy = tmp_path / "A.java"
    copy.write_text("x", encoding="utf-8")
    stored = hashlib.sha256(b"x").hexdigest()
    assert note_gate._edited_copy_matches(stored, [str(copy)], {})
    assert not note_gate._edited_copy_matches(None, [str(copy)], {})
    assert not note_gate._edited_copy_matches(stored, [str(tmp_path / "missing")], {})


def _stale_item(n: int) -> dict:
    return {"title": f"Note {n} about the factory", "note": f"finding-{n}.md", "scope": f"src/A{n}.java",
            "path": f"/k/finding-{n}.md", "kind": "finding", "gone": [], "changed": [f"src/A{n}.java"]}


def test_many_stale_notes_show_five_in_full_and_count_the_rest(tmp_path):
    """Measured: Stop reasons of 25,952 chars listing every drifted note; the
    harness shows the model a 2,000-char preview of anything over 10,000."""
    text = note_gate.message({}, {"svc-a": (tmp_path, [_stale_item(n) for n in range(40)])}, "s1")

    assert [n for n in range(40) if f"'Note {n} about" in text] == [0, 1, 2, 3, 4]
    assert f"+35 more: eos note audit {tmp_path}" in text
    assert len(text) < 4000, len(text)


def test_the_five_note_limit_spans_projects(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    text = note_gate.message({}, {"a": (a, [_stale_item(n) for n in range(4)]),
                                  "b": (b, [_stale_item(n) for n in range(10, 14)])}, "s1")

    assert sum(f"'Note {n} about" in text for n in (0, 1, 2, 3, 10, 11, 12, 13)) == 5
    assert f"+3 more: eos note audit {b}" in text
    assert f"eos note audit {a}" not in text


def test_the_stop_reason_is_capped_with_a_line_saying_so(ws, monkeypatch, capsys):
    _, hub, _ = ws
    _config_off(hub)
    huge = "\n".join(f"line {i} " + "x" * 80 for i in range(400))
    monkeypatch.setattr(hooks, "_note_reasons", lambda root, hook: [huge, huge])

    reason = _stop(monkeypatch, capsys, hub)

    assert len(reason) <= hooks.STOP_REASON_CHARS, len(reason)
    assert reason.startswith("line 0 ")
    assert reason.splitlines()[-1].startswith("[EOS: cut ")
