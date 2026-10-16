"""A note whose scope file moved is said so wherever it is read, and the Stop
gate asks about it whatever the file's type.

Measured on a real store before this: 160 hand-written notes stale, 107 of them
scoped only to shell scripts, Markdown and config the gate never looked at, and
none marked in a brief, a search or `note show` -- a file changed by a pull, a
rebase or a teammate's commit left its notes reading as current everywhere.
"""
import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

from core import brief, hooks, notes

EOS = [sys.executable, str(Path(__file__).resolve().parents[1] / "core" / "eos.py")]


def _init(root: Path, hooks_table: str = "") -> Path:
    root.mkdir(parents=True)
    assert subprocess.run(EOS + ["init", str(root), "--no-ai"], capture_output=True).returncode == 0
    config = root / ".eos" / "config.toml"
    config.write_text(config.read_text(encoding="utf-8") + f"\n[hooks]\nclose = false\n{hooks_table}",
                      encoding="utf-8")
    return root


def _note(root: Path, title: str, scope: str) -> None:
    done = subprocess.run(EOS + ["note", "add", str(root), "--kind", "finding", "--tags", "deploy",
                                 "--title", title, "--body", "Run it with --dry-run first.", "--scope", scope,
                                 "--session", "s0"], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


def _script(root: Path, text: str) -> Path:
    path = root / "scripts" / "deploy.sh"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("EOS_STATE_DIR", str(tmp_path / "state"))
    for name in ("EOS_HOOK_STATE_DIR", "EOS_EXECUTION", "EOS_EXECUTION_LEDGER"):
        monkeypatch.delenv(name, raising=False)
    return tmp_path


def _hook(monkeypatch, capsys, event, payload):
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    assert hooks.main([event]) == 0
    return capsys.readouterr().out


def _edit_and_stop(monkeypatch, capsys, cwd: Path, path: Path, text: str) -> str:
    path.write_text(text, encoding="utf-8")
    _hook(monkeypatch, capsys, "post-tool", {"session_id": "s1", "cwd": str(cwd), "tool_name": "Edit",
                                             "tool_input": {"file_path": str(path)}, "tool_use_id": "t1"})
    out = _hook(monkeypatch, capsys, "stop", {"session_id": "s1", "cwd": str(cwd)})
    return json.loads(out)["reason"] if out.strip() else ""


def test_a_note_on_a_script_is_asked_about_when_the_script_changes(state, monkeypatch, capsys):
    root = _init(state / "svc", "notes = true\n")
    script = _script(root, "echo deploy\n")
    _note(root, "deploy.sh needs a dry run first", "scripts/deploy.sh")
    reason = _edit_and_stop(monkeypatch, capsys, root, script, "echo deploy --force\n")
    assert "a note here no longer agrees with the file it is about." in reason
    assert "'deploy.sh needs a dry run first'" in reason
    # A script is not source for the "record something" half.
    assert "recorded nothing" not in reason


def test_stale_alone_checks_existing_notes_without_asking_for_new_ones(state, monkeypatch, capsys):
    root = _init(state / "hub", "stale = true\n")
    script = _script(root, "echo deploy\n")
    _note(root, "deploy.sh needs a dry run first", "scripts/deploy.sh")
    other = root / "scripts" / "other.py"
    assert _edit_and_stop(monkeypatch, capsys, root, other, "print(1)\n") == ""
    reason = _edit_and_stop(monkeypatch, capsys, root, script, "echo deploy --force\n")
    assert "'deploy.sh needs a dry run first'" in reason and "recorded nothing" not in reason


def test_without_notes_or_stale_nothing_is_asked(state, monkeypatch, capsys):
    root = _init(state / "plain")
    script = _script(root, "echo deploy\n")
    _note(root, "deploy.sh needs a dry run first", "scripts/deploy.sh")
    assert _edit_and_stop(monkeypatch, capsys, root, script, "echo deploy --force\n") == ""


def test_a_file_changed_outside_any_session_marks_the_note_in_the_brief(state):
    root = _init(state / "proj")
    script = _script(root, "echo deploy\n")
    _note(root, "deploy.sh needs a dry run first", "scripts/deploy.sh")
    note = notes.load_notes(root)[0]
    assert brief.note_label(note, root) == "[finding, today]"
    script.write_text("echo deploy --force\n", encoding="utf-8")  # a pull, a rebase, a teammate
    assert brief.note_label(note, root) == "[finding, today] [STALE: scripts/deploy.sh changed]"
    assert brief.note_label(note) == "[finding, today]"
    text = brief.build(root, task="deploy dry run", task_only=True)
    assert "deploy.sh needs a dry run first  [finding, today] [STALE: scripts/deploy.sh changed]" in text


def test_show_and_search_mark_the_drift_and_say_how_to_clear_it(state):
    root = _init(state / "proj")
    script = _script(root, "echo deploy\n")
    _note(root, "deploy.sh needs a dry run first", "scripts/deploy.sh")
    clean = subprocess.run(EOS + ["note", "show", str(root), "dry run"], capture_output=True, text=True)
    assert clean.returncode == 0 and "STALE" not in clean.stdout
    script.write_text("echo deploy --force\n", encoding="utf-8")

    shown = subprocess.run(EOS + ["note", "show", str(root), "dry run"], capture_output=True, text=True)
    assert shown.returncode == 0, shown.stderr
    first = shown.stdout.splitlines()[0]
    assert first.startswith("STALE: scripts/deploy.sh changed since this note was written")
    assert "--reaffirm '<why it still holds>'" in shown.stdout and "--body '<what is true now>'" in shown.stdout

    found = subprocess.run(EOS + ["note", "search", str(root), "dry run"], capture_output=True, text=True)
    assert "deploy.sh needs a dry run first\t[STALE: scripts/deploy.sh changed]" in found.stdout

    note = notes.load_notes(root)[0]
    assert subprocess.run(EOS + ["note", "amend", str(note.path), str(root), "--reaffirm", "flag still holds"],
                          capture_output=True).returncode == 0
    again = subprocess.run(EOS + ["note", "show", str(root), "dry run"], capture_output=True, text=True)
    assert "STALE" not in again.stdout
