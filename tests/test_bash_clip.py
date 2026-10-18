"""`[hooks] bash_clip_chars`: after a Bash call, a long output is replaced
(PostToolUse `updatedToolOutput`) by its head and tail; the whole stays on disk."""
import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

from core import clip, hooks

REPO = Path(__file__).resolve().parents[1]
EOS = [sys.executable, str(REPO / "core" / "eos.py")]
RESPONSE = {"stdout": "", "stderr": "", "interrupted": False, "isImage": False}


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    root.mkdir()
    assert subprocess.run(EOS + ["init", str(root), "--no-ai"], capture_output=True).returncode == 0
    monkeypatch.setenv("EOS_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("EOS_HOOK_STATE_DIR", raising=False)
    return root


def _config(project, text):
    config = project / ".eos" / "config.toml"
    config.write_text(config.read_text(encoding="utf-8") + "\n[hooks]\n" + text, encoding="utf-8")


def _post(project, monkeypatch, capsys, response, command="seq 1 5000", tool="Bash", key="command"):
    payload = {"session_id": "s1", "cwd": str(project), "tool_name": tool, "tool_use_id": "t1",
               "tool_input": {key: command}, "tool_response": response}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    assert hooks.main(["post-tool"]) == 0
    out = capsys.readouterr().out.strip()
    if not out:
        return None
    specific = json.loads(out)["hookSpecificOutput"]
    assert specific["hookEventName"] == "PostToolUse"
    return specific.get("updatedToolOutput")


def _files(project):
    return list((project / ".eos" / "data" / "clip").glob("*.log"))


LONG = "".join(f"{n}\n" for n in range(1, 5001))


def test_off_by_default(project, monkeypatch, capsys):
    assert _post(project, monkeypatch, capsys, {**RESPONSE, "stdout": LONG}) is None


def test_long_stdout_is_clipped_to_head_tail_and_path_and_kept_whole(project, monkeypatch, capsys):
    _config(project, "bash_clip_chars = 200\n")
    updated = _post(project, monkeypatch, capsys, {**RESPONSE, "stdout": LONG})
    (saved,) = _files(project)
    assert saved.read_text() == LONG
    assert updated["stdout"].startswith(LONG[:100])
    assert LONG[-100:] in updated["stdout"]
    assert updated["stdout"].endswith(f"[clipped: full output in {saved}]")
    assert len(updated["stdout"]) < 200 + 80 + len(str(saved))


def test_other_fields_are_preserved_verbatim(project, monkeypatch, capsys):
    _config(project, "bash_clip_chars = 200\n")
    response = {**RESPONSE, "stdout": LONG, "interrupted": True, "extra": {"a": [1]}}
    updated = _post(project, monkeypatch, capsys, response)
    assert updated["interrupted"] is True and updated["isImage"] is False and updated["extra"] == {"a": [1]}
    assert updated["stderr"] == "" and set(updated) == set(response)


def test_short_output_is_untouched(project, monkeypatch, capsys):
    _config(project, "bash_clip_chars = 200\n")
    assert _post(project, monkeypatch, capsys, {**RESPONSE, "stdout": "hi\n"}) is None
    assert _files(project) == []


def test_stderr_only_long_output_is_clipped(project, monkeypatch, capsys):
    _config(project, "bash_clip_chars = 200\n")
    updated = _post(project, monkeypatch, capsys, {**RESPONSE, "stderr": LONG})
    (saved,) = _files(project)
    assert LONG in saved.read_text()
    assert updated["stderr"].startswith("1\n2\n") and len(updated["stderr"]) < 250
    assert updated["stdout"].endswith(f"[clipped: full output in {saved}]")


def test_both_streams_share_the_budget(project, monkeypatch, capsys):
    _config(project, "bash_clip_chars = 200\n")
    updated = _post(project, monkeypatch, capsys, {**RESPONSE, "stdout": LONG, "stderr": LONG})
    assert len(updated["stdout"]) + len(updated["stderr"]) < 500


def test_a_short_stream_beside_a_huge_one_is_kept_whole():
    error = "fatal: the real error message here"
    out, err = clip.clip_streams("x" * 1_000_000, error, 200, "F")
    assert err == error
    assert len(out) < 200 + 80
    out, err = clip.clip_streams(error, "y" * 1_000_000, 200, "F")
    assert out == error + "\n[clipped: full output in F]"


def test_skip_regex_leaves_the_call_alone(project, monkeypatch, capsys):
    _config(project, 'bash_clip_chars = 200\nbash_clip_skip = ["^seq "]\n')
    assert _post(project, monkeypatch, capsys, {**RESPONSE, "stdout": LONG}) is None


@pytest.mark.parametrize("response", ["text", None, ["x"], {"stderr": "x" * 999}, {"stdout": 5}])
def test_an_unfamiliar_response_shape_is_left_alone(project, monkeypatch, capsys, response):
    _config(project, "bash_clip_chars = 200\n")
    assert _post(project, monkeypatch, capsys, response) is None


def test_devin_exec_is_not_clipped(project, monkeypatch, capsys):
    _config(project, "bash_clip_chars = 200\n")
    assert _post(project, monkeypatch, capsys, {**RESPONSE, "stdout": LONG}, tool="exec", key="cmd") is None


def test_only_the_newest_fifty_files_are_kept_and_the_dir_ignores_itself(project, monkeypatch, capsys):
    _config(project, "bash_clip_chars = 100\n")
    for _ in range(55):
        _post(project, monkeypatch, capsys, {**RESPONSE, "stdout": LONG})
    assert len(_files(project)) == 50
    assert (project / ".eos" / "data" / "clip" / ".gitignore").read_text() == "*\n"


def test_an_unwritable_data_dir_costs_nothing(project, monkeypatch, capsys):
    _config(project, "bash_clip_chars = 100\n")
    (project / ".eos" / "data").mkdir(exist_ok=True)
    (project / ".eos" / "data" / "clip").write_text("a file, not a directory")
    assert _post(project, monkeypatch, capsys, {**RESPONSE, "stdout": LONG}) is None


def test_the_clip_rides_along_with_a_hook_note(project, monkeypatch, capsys):
    _config(project, "bash_clip_chars = 100\n")
    monkeypatch.setattr(hooks, "_post_tool_record",
                        lambda *a, **k: json.dumps({"hookSpecificOutput": {
                            "hookEventName": "PostToolUse", "additionalContext": "note"}}))
    payload = {"session_id": "s1", "cwd": str(project), "tool_name": "Bash",
               "tool_input": {"command": "x"}, "tool_response": {**RESPONSE, "stdout": LONG}}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    hooks.main(["post-tool"])
    specific = json.loads(capsys.readouterr().out)["hookSpecificOutput"]
    assert specific["additionalContext"] == "note" and "updatedToolOutput" in specific


def test_clip_text_pure_function():
    assert clip.clip_text("abc", 10) == "abc"
    out = clip.clip_text("A" * 50 + "M" * 100 + "Z" * 50, 100)
    assert out.startswith("A" * 50) and out.endswith("Z" * 50) and "M" not in out
    assert clip.clip_streams("ab", "cd", 10, "/p") is None
