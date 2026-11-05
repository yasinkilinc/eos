"""The prompt hook's telemetry says where its time went.

A 1.2 s prompt hook was 0.5 s of brief and 0.7 s nobody could name: the
report and the approvals read the decision store, which a session may not
call by hand (2026-11-05). The hook writes each phase's milliseconds itself.
"""
import io
import json
import sys

from core import hooks, telemetry


def _project(tmp_path):
    root = tmp_path / "proj"
    (root / ".eos").mkdir(parents=True)
    (root / ".eos" / "config.toml").write_text("[telemetry]\nenabled = true\n", encoding="utf-8")
    return root


def _lines(root):
    return [json.loads(line) for line in telemetry.path_for(root).read_text(encoding="utf-8").splitlines()]


def test_phases_are_written_rounded_and_only_when_given(tmp_path):
    root = _project(tmp_path)

    telemetry.record(root, "hook", ["user-prompt"], 20.0, phases={"brief": 12.34})
    telemetry.record(root, "hook", ["stop"], 3.0)

    with_phases, without = _lines(root)
    assert with_phases["phases"] == {"brief": 12.3}
    assert "phases" not in without


def test_the_prompt_hook_records_its_phases(tmp_path, monkeypatch, capsys):
    root = _project(tmp_path)
    payload = {"session_id": "s1", "cwd": str(root), "hook_event_name": "UserPromptSubmit",
               "prompt": "rotate the database credentials"}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))

    assert hooks.main(["user-prompt"]) == 0
    capsys.readouterr()

    [line] = [entry for entry in _lines(root) if entry["flags"] == ["user-prompt"]]
    assert {"approvals", "brief", "report", "workspace"} <= set(line["phases"])
    assert all(isinstance(ms, float) and ms >= 0 for ms in line["phases"].values())


def test_another_hook_records_no_phases(tmp_path, monkeypatch, capsys):
    root = _project(tmp_path)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"session_id": "s1", "cwd": str(root)})))

    hooks.main(["stop"])
    capsys.readouterr()

    assert all("phases" not in entry for entry in _lines(root))
