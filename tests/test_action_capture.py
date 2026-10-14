"""E1 measured as the report defines it: how many editor and wrapper actions of
the project's last sessions reached the execution ledger as events (2.x roadmap E1).

CHANGE CAPTURE compares files a run's commits touched with the files its events
named; most of its misses were files programs wrote, which says little about the
hooks. This counts the actions themselves, from the harness transcripts.
"""
import json
import os
import tempfile
from pathlib import Path

import pytest

from core import action_capture, consolidate, context_cost, executions

T0 = "2026-10-01T10:00:00"


def _at(seconds: int, z: bool = True) -> str:
    import datetime

    moment = datetime.datetime.fromisoformat(T0) + datetime.timedelta(seconds=seconds)
    return moment.isoformat() + (".000Z" if z else "+00:00")


def _use(tid, name, seconds, session="s1", agent=None, **tool_input):
    entry = {"type": "assistant", "sessionId": session, "timestamp": _at(seconds),
             "message": {"id": f"m-{tid}", "content": [{"type": "tool_use", "id": tid, "name": name,
                                                        "input": tool_input}]}}
    if agent:
        entry["agentId"] = agent
    return entry


def _result(tid, seconds, content="ok", error=False, session="s1"):
    return {"type": "user", "sessionId": session, "timestamp": _at(seconds),
            "message": {"content": [{"type": "tool_result", "tool_use_id": tid, "content": content,
                                     "is_error": error}]}}


def _write(path: Path, entries) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(e) + "\n" for e in entries), encoding="utf-8")
    return path


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "proj"
    knowledge = root / ".eos" / "knowledge"
    knowledge.mkdir(parents=True)
    (knowledge / "capabilities.toml").write_text(
        '[[capability]]\nname = "tracker"\nrun = "scripts/tracker.sh"\n\n'
        '[[capability]]\nname = "edit"\nrun = "Edit tool"\n', encoding="utf-8")
    return root


def _ledger(root: Path, lines) -> None:
    path = executions.path_for(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        for line in lines:
            handle.write(json.dumps({"v": 1, **line}) + "\n")


def _run(root, run_id, start, end=None, session="s1"):
    lines = [{"type": "start", "id": run_id, "title": run_id, "session": session, "started_at": _at(start, z=False)}]
    if end is not None:
        lines.append({"type": "finish", "id": run_id, "at": _at(end, z=False), "outcome": "ok"})
    _ledger(root, lines)


def _event(root, run_id, seconds, kind="changed", tool=None, source="hook", tool_use_id=None, session="s1"):
    _ledger(root, [{"type": "event", "execution": run_id, "ord": None, "at": _at(seconds, z=False), "kind": kind,
                    "tool": tool, "ref": None, "session": session, "source": source,
                    "tool_use_id": tool_use_id}])


def _measure(root, folder, **kw):
    return action_capture.report(root, transcripts=folder, **kw)


def test_an_editor_action_is_captured_by_its_tool_use_id(project, tmp_path):
    folder = tmp_path / "t"
    _write(folder / "s1.jsonl", [
        _use("e1", "Edit", 10, file_path=str(project / "a.py")), _result("e1", 11),
        _use("e2", "Write", 20, file_path=str(project / "b.py")), _result("e2", 21),
        _use("e3", "MultiEdit", 30, file_path=str(project / "c.py")), _result("e3", 31),
        _use("e4", "NotebookEdit", 40, notebook_path=str(project / "d.ipynb")), _result("e4", 41),
        _use("r1", "Read", 50, file_path=str(project / "a.py")), _result("r1", 51),
    ])
    _run(project, "x-1", 0)
    _event(project, "x-1", 11, tool="edit", tool_use_id="e1")
    _event(project, "x-1", 31, tool="edit", tool_use_id="e3")

    data = _measure(project, folder)

    assert data["sessions"] == 1
    assert (data["editor"]["captured"], data["editor"]["measured"]) == (2, 4)
    assert data["wrapper"]["measured"] == 0 and data["wrapper"]["share"] is None
    assert data["share"] == 0.5


def test_a_wrapper_call_is_captured_by_the_event_its_wrapper_recorded_meanwhile(project, tmp_path):
    folder = tmp_path / "t"
    _write(folder / "s1.jsonl", [
        _use("b1", "Bash", 10, command="scripts/tracker.sh issue KEY-1 | head"), _result("b1", 14),
        _use("b2", "Bash", 20, command="scripts/tracker.sh issue KEY-2"), _result("b2", 22),
        _use("b3", "Bash", 30, command="scripts/tracker.sh issue KEY-3"), _result("b3", 31),
        _use("b4", "Bash", 40, command="ls -la"), _result("b4", 41),
    ])
    _run(project, "x-1", 0)
    _event(project, "x-1", 13, kind="called", tool="tracker", source="wrapper")   # b1
    _event(project, "x-1", 21, kind="called", tool="tracker", source="wrapper", session="s-other")
    _event(project, "x-1", 90, kind="called", tool="tracker", source="wrapper")   # long after b3

    data = _measure(project, folder)

    assert (data["wrapper"]["captured"], data["wrapper"]["measured"]) == (1, 3)
    assert data["wrapper"]["missed_by_tool"] == {"tracker": 2}


def test_one_wrapper_event_answers_one_call(project, tmp_path):
    folder = tmp_path / "t"
    _write(folder / "s1.jsonl", [
        _use("b1", "Bash", 10, command="scripts/tracker.sh issue KEY-1"),
        _use("b2", "Bash", 10, command="scripts/tracker.sh issue KEY-2"),
        _result("b1", 12), _result("b2", 12),
    ])
    _run(project, "x-1", 0)
    _event(project, "x-1", 12, kind="called", tool="tracker", source="wrapper")

    assert _measure(project, folder)["wrapper"]["captured"] == 1


def test_a_call_the_harness_moved_to_the_background_is_answered_by_a_later_event(project, tmp_path):
    """Measured on the host: `jenkins.sh wait` hit the Bash tool's 600 s timeout
    and the harness moved it to the background; its result came at once, the
    wrapper's event when the job ended -- counted a miss though nothing was lost."""
    folder = tmp_path / "t"
    moved = _result("b1", 610, "Command did not complete within its 600s timeout and was moved to the background")
    moved["toolUseResult"] = {"stdout": "", "backgroundTaskId": "b05m1r88d", "timedOutAfterMs": 600000}
    _write(folder / "s1.jsonl", [
        _use("b1", "Bash", 10, command="scripts/tracker.sh wait JOB 4075"), moved,
        _use("b2", "Bash", 20, command="scripts/tracker.sh issue KEY-2"), _result("b2", 21),
    ])
    _run(project, "x-1", 0)
    _event(project, "x-1", 900, kind="called", tool="tracker", source="wrapper")   # b1, when the job ended

    data = _measure(project, folder)

    assert (data["wrapper"]["captured"], data["wrapper"]["measured"]) == (1, 2)


def test_a_command_that_only_names_a_wrappers_file_is_not_its_call(project, tmp_path):
    """Measured on the host: `git add automation/x.sh`, `bash -n`, a note body
    naming a wrapper -- the mention split counted them as wrapper calls no
    wrapper event could ever answer."""
    folder = tmp_path / "t"
    _write(folder / "s1.jsonl", [
        _use("b1", "Bash", 10, command="git add scripts/tracker.sh && git commit -m 'tracker'"), _result("b1", 11),
        _use("b2", "Bash", 20, command="timeout 60 scripts/tracker.sh issue KEY-1"), _result("b2", 21),
        _use("b3", "Bash", 30, command="for k in 1 2; do scripts/tracker.sh issue KEY-$k; done"),
        _result("b3", 31),
    ])
    _run(project, "x-1", 0)

    assert _measure(project, folder)["wrapper"]["measured"] == 2


def test_an_action_with_no_run_open_is_counted_apart_from_the_share(project, tmp_path):
    folder = tmp_path / "t"
    _write(folder / "s1.jsonl", [
        _use("e1", "Edit", 5, file_path=str(project / "a.py")), _result("e1", 6),
        _use("e2", "Edit", 20, file_path=str(project / "a.py")), _result("e2", 21),
        _use("b1", "Bash", 120, command="scripts/tracker.sh issue KEY-1"), _result("b1", 121),
    ])
    _run(project, "x-1", 10, end=100)
    _event(project, "x-1", 21, tool="edit", tool_use_id="e2")
    _run(project, "x-other", 0, session="s-other")

    data = _measure(project, folder)

    assert data["outside_run"] == 2 and data["editor"]["outside_run"] == 1
    assert (data["captured"], data["measured"], data["share"]) == (1, 1, 1.0)


def test_a_refused_call_and_a_scratch_file_are_counted_apart(project, tmp_path):
    folder = tmp_path / "t"
    scratch = Path(tempfile.gettempdir()) / "eos-test-scratchpad" / "notes.md"
    _write(folder / "s1.jsonl", [
        _use("e1", "Edit", 10, file_path=str(project / "a.py")),
        _result("e1", 11, "<tool_use_error>File has not been read yet.</tool_use_error>", error=True),
        _use("b1", "Bash", 20, command="scripts/tracker.sh issue KEY-1"),
        _result("b1", 21, "PreToolUse:Bash hook error: blocked", error=True),
        _use("e2", "Write", 30, file_path=str(scratch)), _result("e2", 31),
        _use("e3", "Edit", 40, file_path=str(project / "a.py")), _result("e3", 41, "Exit code 1", error=True),
    ])
    _run(project, "x-1", 0)

    data = _measure(project, folder)

    assert (data["never_ran"], data["scratch"]) == (2, 1)
    assert (data["captured"], data["measured"]) == (0, 1)


def test_the_last_sessions_are_the_newest_transcripts_with_their_subagents(project, tmp_path):
    folder = tmp_path / "t"
    old = _write(folder / "old.jsonl", [_use("e0", "Edit", 1, file_path=str(project / "a.py")), _result("e0", 2)])
    new = _write(folder / "s1.jsonl", [_use("e1", "Edit", 10, file_path=str(project / "a.py")), _result("e1", 11)])
    _write(folder / "s1" / "subagents" / "agent-a1.jsonl",
           [_use("e2", "Edit", 20, agent="a1", file_path=str(project / "b.py")), _result("e2", 21)])
    os.utime(old, (1_000_000, 1_000_000))
    os.utime(new, (2_000_000, 2_000_000))
    _run(project, "x-1", 0)
    _event(project, "x-1", 21, tool="edit", tool_use_id="e2")

    data = _measure(project, folder, sessions=1)

    assert data["sessions"] == 1
    assert (data["editor"]["captured"], data["editor"]["measured"]) == (1, 2)


def test_a_workspace_run_in_a_projects_ledger_is_read(tmp_path):
    hub = tmp_path / "hub"
    (hub / ".eos" / "knowledge").mkdir(parents=True)
    service = tmp_path / "services" / "svc"
    (service / ".eos" / "knowledge").mkdir(parents=True)
    (hub / ".eos" / "projects.toml").write_text('[[project]]\nroot = "../services/svc"\n', encoding="utf-8")
    folder = tmp_path / "t"
    _write(folder / "s1.jsonl", [_use("e1", "Edit", 10, file_path=str(hub / "a.py")), _result("e1", 11)])
    _run(service, "x-1", 0)
    _event(service, "x-1", 11, tool="edit", tool_use_id="e1")

    data = _measure(hub, folder)

    assert (data["captured"], data["measured"]) == (1, 1)


def test_no_transcripts_is_not_measured_rather_than_zero(project, tmp_path):
    data = _measure(project, tmp_path / "nowhere")
    assert data["sessions"] == 0 and data["share"] is None and data["measured"] == 0


def test_consolidate_prints_the_action_capture_line_and_carries_it_in_json(project, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    folder = context_cost.transcripts_dir(project.resolve())
    _write(folder / "s1.jsonl", [
        _use("b1", "Bash", 10, command="scripts/tracker.sh issue KEY-1"), _result("b1", 12),
        _use("b2", "Bash", 20, command="scripts/tracker.sh issue KEY-2"), _result("b2", 22),
        _use("b3", "Bash", 200, command="scripts/tracker.sh issue KEY-3"), _result("b3", 201),
    ])
    _run(project, "x-1", 0, end=100)
    _event(project, "x-1", 11, kind="called", tool="tracker", source="wrapper")

    data = consolidate.report(project)
    text = consolidate.render(data)

    assert data["action_capture"]["measured"] == 2
    json.dumps(data)
    assert "ACTION CAPTURE  1/2 (50%) over 1 sessions: editor —, wrapper 1/2; 1 outside any run" in text


def test_consolidate_prints_no_action_capture_line_without_sessions(project, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    data = consolidate.report(project)
    assert data["action_capture"]["sessions"] == 0
    assert "ACTION CAPTURE" not in consolidate.render(data)


def test_a_tool_input_that_quotes_a_transcript_line_is_still_a_tool_call(project, tmp_path):
    folder = tmp_path / "t"
    quoted = json.dumps(_result("zz", 1))
    _write(folder / "s1.jsonl", [
        _use("e1", "Write", 10, file_path=str(project / "t.py"), content=quoted), _result("e1", 11),
    ])
    _run(project, "x-1", 0)
    _event(project, "x-1", 11, tool="write", tool_use_id="e1")

    assert (_measure(project, folder)["editor"]["captured"], _measure(project, folder)["measured"]) == (1, 1)
