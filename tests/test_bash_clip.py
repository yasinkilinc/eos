"""`[hooks] bash_clip_chars`: a Bash call's output is kept on disk and only its
head and tail reach the model. Every test that matters runs the rewritten
command through a real `bash -c`."""
import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

from core import clip, hooks

REPO = Path(__file__).resolve().parents[1]
EOS = [sys.executable, str(REPO / "core" / "eos.py")]


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


def _pre(project, monkeypatch, capsys, tool_input):
    payload = {"session_id": "s1", "cwd": str(project), "tool_name": "Bash", "tool_input": tool_input}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    assert hooks.main(["pre-tool"]) == 0
    return capsys.readouterr().out


def _rewrite(project, monkeypatch, capsys, command, key="command", **extra):
    """The command the hook asks the harness to run instead, or None."""
    out = _pre(project, monkeypatch, capsys, {key: command, **extra})
    if not out.strip():
        return None
    specific = json.loads(out)["hookSpecificOutput"]
    assert specific["hookEventName"] == "PreToolUse"
    return specific["updatedInput"][key]


def _bash(script, cwd=None):
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, cwd=cwd)


def test_off_by_default(project, monkeypatch, capsys):
    assert _rewrite(project, monkeypatch, capsys, "echo hi") is None


def test_background_calls_are_untouched(project, monkeypatch, capsys):
    _config(project, "bash_clip_chars = 100\n")
    assert _rewrite(project, monkeypatch, capsys, "echo hi", run_in_background=True) is None


def test_skip_patterns_and_pagers_are_untouched(project, monkeypatch, capsys):
    _config(project, 'bash_clip_chars = 100\nbash_clip_skip = ["^npm run dev"]\n')
    assert _rewrite(project, monkeypatch, capsys, "npm run dev --watch") is None
    assert _rewrite(project, monkeypatch, capsys, "seq 1 500 | head -5") is None
    assert _rewrite(project, monkeypatch, capsys, "seq 1 500 | tail -n 5") is None
    assert _rewrite(project, monkeypatch, capsys, "echo hi") is not None


def test_the_rest_of_the_input_is_kept_and_the_payloads_key_is_preserved(project, monkeypatch, capsys):
    _config(project, "bash_clip_chars = 100\n")
    out = _pre(project, monkeypatch, capsys, {"cmd": "echo hi", "timeout": 5000, "description": "d"})
    updated = json.loads(out)["hookSpecificOutput"]["updatedInput"]
    assert set(updated) == {"cmd", "timeout", "description"}
    assert updated["timeout"] == 5000 and updated["cmd"] != "echo hi"


def test_short_output_and_exit_code_are_unchanged(project, monkeypatch, capsys):
    _config(project, "bash_clip_chars = 1000\n")
    command = _rewrite(project, monkeypatch, capsys, "echo out; echo err >&2; false")
    run = _bash(command)
    assert run.returncode == 1
    assert sorted((run.stdout + run.stderr).split()) == ["err", "out"]
    assert _bash(command.replace("false", "true")).returncode == 0


def test_an_explicit_exit_keeps_its_code_and_still_prints(project, monkeypatch, capsys):
    _config(project, "bash_clip_chars = 1000\n")
    command = _rewrite(project, monkeypatch, capsys, "echo before; exit 3; echo never")
    run = _bash(command)
    assert run.returncode == 3
    assert run.stdout == "before\n"


def test_no_subshell_cd_stays_in_effect(project, monkeypatch, capsys, tmp_path):
    _config(project, "bash_clip_chars = 1000\n")
    target = tmp_path.resolve()
    command = _rewrite(project, monkeypatch, capsys, f"cd {target}\nexport EOS_CLIP_X=1")
    run = _bash(command + '\npwd -P; echo "x=$EOS_CLIP_X"', cwd="/")
    assert run.stdout.split() == [str(target), "x=1"]


def test_a_heredoc_command_works(project, monkeypatch, capsys):
    _config(project, "bash_clip_chars = 1000\n")
    command = _rewrite(project, monkeypatch, capsys, "python3 - <<'EOF'\nprint('from heredoc')\nEOF")
    run = _bash(command)
    assert run.returncode == 0 and run.stdout == "from heredoc\n"


def test_a_multiline_and_chain_works(project, monkeypatch, capsys):
    _config(project, "bash_clip_chars = 1000\n")
    command = _rewrite(project, monkeypatch, capsys, "echo a &&\n  echo b && \\\n  echo c # trailing comment")
    run = _bash(command)
    assert run.returncode == 0 and run.stdout == "a\nb\nc\n"


def test_long_output_is_clipped_with_the_path_and_kept_whole_on_disk(project, monkeypatch, capsys):
    _config(project, "bash_clip_chars = 200\n")
    command = _rewrite(project, monkeypatch, capsys, "seq 1 2000")
    run = _bash(command)
    assert run.returncode == 0
    files = list((project / ".eos" / "data" / "clip").glob("s1-*.log"))
    assert len(files) == 1
    assert files[0].read_text() == "".join(f"{n}\n" for n in range(1, 2001))
    assert f"[clipped: full output in {files[0]}]" in run.stdout
    assert run.stdout.startswith("1\n2\n") and run.stdout.rstrip().endswith("2000")
    assert len(run.stdout) < 400


def test_only_the_newest_fifty_clip_files_are_kept(project, monkeypatch, capsys):
    _config(project, "bash_clip_chars = 100\n")
    for _ in range(55):
        _rewrite(project, monkeypatch, capsys, "echo hi")
    assert len(list((project / ".eos" / "data" / "clip").glob("*.log"))) == 50


def test_the_helper_runs_with_the_hooks_interpreter(project, monkeypatch, capsys):
    _config(project, "bash_clip_chars = 100\n")
    command = _rewrite(project, monkeypatch, capsys, "echo hi")
    assert sys.executable in command and str(REPO / "core" / "clip.py") in command


def test_the_helper_never_raises(tmp_path, capsys):
    assert clip.main([str(tmp_path / "missing.log"), "100"]) == 0
    assert clip.main([str(tmp_path), "100"]) == 0
    assert clip.main([]) == 0
    assert clip.main([str(tmp_path / "x"), "notanumber"]) == 0
    capsys.readouterr()


def test_the_helper_splits_head_and_tail(tmp_path, capsys):
    f = tmp_path / "o.log"
    f.write_text("A" * 50 + "M" * 100 + "Z" * 50)
    assert clip.main([str(f), "100"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("A" * 50) and out.rstrip().endswith("Z" * 50) and "M" not in out
    assert f"[clipped: full output in {f}]" in out


def test_a_command_owning_its_trap_or_output_is_untouched(project, monkeypatch, capsys):
    _config(project, "bash_clip_chars = 100\n")
    assert _rewrite(project, monkeypatch, capsys, "trap 'echo bye' EXIT; echo hi") is None
    assert _rewrite(project, monkeypatch, capsys, "exec >/dev/null; echo hi") is None
    assert _rewrite(project, monkeypatch, capsys, "exec 2>/tmp/e; echo hi") is None
    assert _rewrite(project, monkeypatch, capsys, "exec 1>/tmp/o; echo hi") is None
    assert _rewrite(project, monkeypatch, capsys, "echo hi") is not None


def test_the_clip_dir_ignores_itself_and_pruning_keeps_that_file(project, monkeypatch, capsys):
    _config(project, "bash_clip_chars = 100\n")
    for _ in range(55):
        _rewrite(project, monkeypatch, capsys, "echo hi")
    folder = project / ".eos" / "data" / "clip"
    assert (folder / ".gitignore").read_text() == "*\n"
    assert len(list(folder.glob("*.log"))) == 50
