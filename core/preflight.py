"""Dependency tiers, and what EOS may do about each.

Three tiers, because they are not the same kind of problem:

T1  EOS cannot install it. Python is the obvious case -- EOS *is* Python, so
    code that installs Python could never run. All EOS can do is say exactly
    what to run, per platform, instead of failing with a traceback.
T2  EOS installs it, after asking, into a venv it owns -- never the system
    interpreter and never the project's environment.
T3  Optional. Absent means one feature is unavailable, not an error.
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

MIN_PYTHON = (3, 11)
UI_PACKAGES = ("fastapi", "uvicorn", "pydantic", "watchdog")

PYTHON_HINT = """\
  macOS    brew install python@3.12
  Debian   sudo apt install python3.12
  any      pyenv install 3.12 && pyenv global 3.12"""


@dataclass(frozen=True)
class Requirement:
    name: str
    ok: bool
    found: str | None
    tier: int
    hint: str


def check_python() -> Requirement:
    # Indexing, not .major/.minor/.micro: this must keep working when a test
    # monkeypatches sys.version_info to a plain tuple, which has no named
    # fields. Reading sys.version_info here (not at import time) is what lets
    # that monkeypatch reach this function at all.
    major, minor, micro = sys.version_info[0], sys.version_info[1], sys.version_info[2]
    found = f"{major}.{minor}.{micro}"
    ok = sys.version_info[:2] >= MIN_PYTHON
    return Requirement(
        name="python",
        ok=ok,
        found=f"{found} at {sys.executable}",
        tier=1,
        hint=PYTHON_HINT,
    )


def check_git() -> Requirement:
    path = shutil.which("git")
    return Requirement(
        name="git",
        ok=path is not None,
        found=path,
        tier=1,
        hint="  macOS    xcode-select --install\n  Debian   sudo apt install git",
    )


def check_node() -> Requirement:
    path = shutil.which("npm")
    return Requirement(
        name="npm",
        ok=path is not None,
        found=path,
        tier=3,
        hint="Only needed to build the UI from source. Released tarballs ship it prebuilt.",
    )


def venv_path() -> Path:
    override = os.environ.get("EOS_VENV")
    if override:
        return Path(override)
    return Path.home() / ".local" / "share" / "eos" / "venv"


def ui_packages_missing() -> list[str]:
    return [name for name in UI_PACKAGES if importlib.util.find_spec(name) is None]


def report() -> list[Requirement]:
    return [check_python(), check_git(), check_node()]


def install_ui_packages(assume_yes: bool = False) -> bool:
    """Create EOS's own venv and install the UI packages into it.

    Returns True when the packages are available afterwards.
    """
    missing = ui_packages_missing()
    if not missing:
        return True

    target = venv_path()
    print(f"eos ui: {len(missing)} Python packages missing ({', '.join(missing)})")
    print()
    print(f"  Where   {target}   (EOS's own venv)")
    print("  Not your system Python, and not this project's environment")
    print("  Size    ~28 MB")
    print("  Undo    eos ui uninstall")
    print()

    if not assume_yes:
        try:
            answer = input("Install now? [y/N] ").strip().lower()
        except EOFError:
            answer = ""
        if answer not in ("y", "yes"):
            print("eos ui: not installed. The core CLI and MCP server are unaffected.")
            return False

    try:
        subprocess.run([sys.executable, "-m", "venv", str(target)], check=True)
    except (subprocess.CalledProcessError, OSError) as exc:
        print(f"eos ui: could not create a venv at {target}: {exc}")
        print("  On Debian/Ubuntu this usually means the venv module is missing:")
        print("    sudo apt install python3-venv")
        print("  The core CLI and MCP server are unaffected; nothing was installed.")
        return False

    pip = target / "bin" / "pip"
    try:
        subprocess.run([str(pip), "install", "--quiet", *UI_PACKAGES], check=True)
    except (subprocess.CalledProcessError, OSError) as exc:
        print(f"eos ui: package install failed: {exc}")
        print(f"  Retry by hand: {pip} install {' '.join(UI_PACKAGES)}")
        print("  The core CLI and MCP server are unaffected; nothing was installed.")
        return False

    print(f"eos ui: installed into {target}")
    return True
