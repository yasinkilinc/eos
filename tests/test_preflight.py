import subprocess
import sys
from pathlib import Path

from core import preflight


def test_python_requirement_passes_on_this_interpreter():
    req = preflight.check_python()
    assert req.tier == 1
    assert req.ok is True
    assert req.found.startswith(f"{sys.version_info.major}.{sys.version_info.minor}")


def test_python_hint_names_every_platform():
    hint = preflight.PYTHON_HINT
    assert "brew install" in hint
    assert "apt install" in hint
    assert "pyenv" in hint


def test_node_is_tier_three():
    assert preflight.check_node().tier == 3


def test_venv_path_is_eos_owned(monkeypatch):
    monkeypatch.delenv("EOS_VENV", raising=False)
    path = preflight.venv_path()
    assert path.name == "venv"
    assert "eos" in str(path)


def test_venv_path_respects_override(monkeypatch, tmp_path):
    monkeypatch.setenv("EOS_VENV", str(tmp_path / "custom"))
    assert preflight.venv_path() == tmp_path / "custom"


def test_ui_packages_missing_lists_names():
    missing = preflight.ui_packages_missing()
    assert isinstance(missing, list)
    assert set(missing) <= {"fastapi", "uvicorn", "pydantic", "watchdog"}


def test_old_python_fails_with_a_hint(monkeypatch):
    """Acceptance criterion 5: a too-old interpreter must produce instructions."""
    monkeypatch.setattr(preflight.sys, "version_info", (3, 9, 6, "final", 0))
    req = preflight.check_python()
    assert req.ok is False
    assert req.tier == 1
    assert "brew install" in req.hint


def test_refusing_install_leaves_core_working(monkeypatch, capsys):
    monkeypatch.setattr(preflight, "ui_packages_missing", lambda: ["fastapi"])
    monkeypatch.setattr("builtins.input", lambda _: "n")
    assert preflight.install_ui_packages() is False
    out = capsys.readouterr().out
    assert "core CLI and MCP server are unaffected" in out


def test_install_prompt_names_where_and_where_not(monkeypatch, capsys):
    monkeypatch.setattr(preflight, "ui_packages_missing", lambda: ["fastapi"])
    monkeypatch.setattr("builtins.input", lambda _: "n")
    preflight.install_ui_packages()
    out = capsys.readouterr().out
    assert "EOS's own venv" in out
    assert "not your system python" in out.lower()
    assert "eos ui uninstall" in out


def test_install_failure_is_reported_not_raised(monkeypatch, capsys):
    """A venv/pip failure (no network, no python3-venv, disk full, ...) must
    produce an instruction, not a CalledProcessError traceback -- the same
    promise install_ui_packages already keeps for a plain refusal."""
    monkeypatch.setattr(preflight, "ui_packages_missing", lambda: ["fastapi"])

    def raise_called_process_error(cmd, check=True):
        raise subprocess.CalledProcessError(1, cmd)

    monkeypatch.setattr(preflight.subprocess, "run", raise_called_process_error)

    assert preflight.install_ui_packages(assume_yes=True) is False

    out = capsys.readouterr().out
    assert "python3-venv" in out
    assert "unaffected" in out.lower()


def test_ui_command_dispatches_without_keyerror(monkeypatch, capsys):
    """Ruling 1: `ui` must be registered in main()'s commands dict, or this
    raises KeyError before cmd_ui ever runs.

    `--help` cannot stand in for this: argparse's help action exits during
    parsing, before `commands[args.command](args)` is reached, so a missing
    dict entry would never surface through it."""
    from core import eos as eos_cli

    monkeypatch.setattr(preflight, "ui_packages_missing", lambda: [])

    rc = eos_cli.main(["ui", "start", "--no-install"])

    assert rc == 0
    assert "already installed" in capsys.readouterr().out


def test_ui_no_install_reports_without_installing(monkeypatch, capsys):
    """Ruling 2: --no-install must report and stop, never reaching
    install_ui_packages (no prompt, no install)."""
    from core import eos as eos_cli

    monkeypatch.setattr(preflight, "ui_packages_missing", lambda: ["fastapi"])

    def fail_if_called(**kwargs):
        raise AssertionError("install_ui_packages must not run under --no-install")

    monkeypatch.setattr(preflight, "install_ui_packages", fail_if_called)

    rc = eos_cli.main(["ui", "start", "--no-install"])

    assert rc == 0
    assert "fastapi" in capsys.readouterr().out


def test_ui_uninstall_missing_venv_is_a_noop(monkeypatch, tmp_path, capsys):
    from core import eos as eos_cli

    target = tmp_path / "does-not-exist"
    monkeypatch.setattr(preflight, "venv_path", lambda: target)

    def fail_if_called(*args, **kwargs):
        raise AssertionError("shutil.rmtree must not run when nothing is installed")

    monkeypatch.setattr(eos_cli.shutil, "rmtree", fail_if_called)

    rc = eos_cli.main(["ui", "uninstall"])

    assert rc == 0
    assert "nothing installed" in capsys.readouterr().out
