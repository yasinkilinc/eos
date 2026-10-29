"""`eos decide`: record a decision with its evidence, resolve it, list the open ones."""
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from core import executions

REPO = Path(__file__).resolve().parents[1]
EOS = [sys.executable, str(REPO / "core" / "eos.py")]


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "proj"
    (root / ".eos").mkdir(parents=True)
    return root


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("EOS_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("EOS_EXECUTION", raising=False)
    monkeypatch.delenv("EOS_EXECUTION_LEDGER", raising=False)
    e = os.environ.copy()
    e["EOS_STATE_DIR"] = str(tmp_path / "state")
    e.pop("EOS_EXECUTION", None)
    e.pop("EOS_EXECUTION_LEDGER", None)
    return e


def _run(args, env):
    return subprocess.run(EOS + args, capture_output=True, text=True, encoding="utf-8", env=env)


def _start(project, env, work=None):
    args = ["run", "start", str(project), "--title", "t", "--session", "s1"]
    if work:
        args += ["--work", work]
    out = _run(args, env)
    assert out.returncode == 0, out.stderr
    return out.stdout.splitlines()[0]


def _tool_event(project, env, execution):
    # a wrapper-recorded event is the evidence a decision may cite
    entry = executions.event(project, execution, kind="ran", tool="build", session="s1",
                             source="wrapper")
    return entry.id


def test_decide_without_evidence_is_an_assumption(project, env):
    _start(project, env)
    out = _run(["decide", str(project), "--claim", "the cache is cold", "--session", "s1"], env)
    assert out.returncode == 0, out.stderr
    lines = out.stdout.splitlines()
    assert re.fullmatch(r"decision=d-[0-9a-f]{8} state=assumption", lines[0])
    assert "reason=no-evidence evidence=-" in lines[1:]


def test_decide_with_evidence_is_evidenced(project, env):
    execution = _start(project, env)
    cited = _tool_event(project, env, execution)
    out = _run(["decide", str(project), "--claim", "build passes", "--evidence", cited,
                "--check", "run build", "--session", "s1"], env)
    assert out.returncode == 0, out.stderr
    assert re.fullmatch(r"decision=d-[0-9a-f]{8} state=evidenced\n", out.stdout)


def test_open_lists_assumptions_with_check(project, env):
    _start(project, env)
    made = _run(["decide", str(project), "--claim", "pool is sized", "--check", "db.sh pg x",
                 "--session", "s1"], env)
    did = made.stdout.split()[0].split("=")[1]
    plain = _run(["decide", str(project), "--claim", "other thing", "--session", "s1"], env)
    out = _run(["decide", str(project), "--open", "--session", "s1"], env)
    assert out.returncode == 0, out.stderr
    assert f"{did} pool is sized check=db.sh pg x" in out.stdout.splitlines()
    assert any(line.endswith("other thing check=-") for line in out.stdout.splitlines())
    assert plain.returncode == 0


def test_resolve_with_evidence_exits_0(project, env):
    execution = _start(project, env)
    cited = _tool_event(project, env, execution)
    made = _run(["decide", str(project), "--claim", "pool is sized", "--session", "s1"], env)
    did = made.stdout.split()[0].split("=")[1]
    out = _run(["decide", str(project), "--resolve", did, "--evidence", cited,
                "--session", "s1"], env)
    assert out.returncode == 0, out.stderr
    assert out.stdout == f"decision={did} state=resolved\n"


def test_resolve_without_evidence_exits_3(project, env):
    _start(project, env)
    made = _run(["decide", str(project), "--claim", "pool is sized", "--session", "s1"], env)
    did = made.stdout.split()[0].split("=")[1]
    out = _run(["decide", str(project), "--resolve", did, "--evidence", "e-00000000",
                "--session", "s1"], env)
    assert out.returncode == 3
    assert out.stdout.splitlines()[0] == f"decision={did} state=assumption"
    assert "reason=unknown-id evidence=-" in out.stdout.splitlines()


def test_malformed_evidence_exits_2_with_nothing_on_stdout(project, env):
    _start(project, env)
    out = _run(["decide", str(project), "--claim", "x is y", "--evidence", "bogus",
                "--session", "s1"], env)
    assert out.returncode == 2
    assert out.stdout == ""
    assert "evidence" in out.stderr


@pytest.mark.parametrize("extra", [["--claim", "a b"], ["--open"], ["--resolve", "d-00000000"]])
def test_no_open_run_exits_2_with_the_hint(project, env, extra):
    out = _run(["decide", str(project), "--session", "none", *extra], env)
    assert out.returncode == 2
    assert out.stdout == ""
    assert "`eos run start` opens one" in out.stderr


def test_no_approve_flag(project, env):
    _start(project, env)
    out = _run(["decide", str(project), "--claim", "x is y", "--approve", "d-00000000", "--session", "s1"], env)
    assert out.returncode == 2
    assert "unrecognized arguments: --approve" in out.stderr
