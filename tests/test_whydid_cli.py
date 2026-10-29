"""`eos why-did`: the decisions of a run, work item or ticket, and what proved them."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

from core import executions, work

REPO = Path(__file__).resolve().parents[1]
EOS = [sys.executable, str(REPO / "core" / "eos.py")]
HELPER = REPO / "bin" / "eos-event"


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


def _start(project, env, session, work_item=None):
    args = ["run", "start", str(project), "--title", "t", "--session", session]
    if work_item:
        args += ["--work", work_item]
    out = _run(args, env)
    assert out.returncode == 0, out.stderr
    return out.stdout.splitlines()[0]


def _proof(project, env, session, raw=None):
    args = ["--kind", "ran", "--tool", "db"]
    if raw:
        args += ["--output", str(raw)]
    e = {**env, "CLAUDE_CODE_SESSION_ID": session, "PATH": "/usr/bin:/bin"}
    done = subprocess.run(["/bin/sh", str(HELPER), *args], capture_output=True, text=True,
                          env=e, cwd=project)
    assert done.returncode == 0, done.stderr
    return done.stdout.strip().split("=", 1)[1]


def _decide(project, env, session, claim, *evidence):
    args = ["decide", str(project), "--claim", claim, "--session", session]
    for cited in evidence:
        args += ["--evidence", cited]
    out = _run(args, env)
    assert out.returncode == 0, out.stderr
    return out.stdout.split()[0].split("=")[1]


def test_why_did_lists_decisions_with_evidence(project, env, tmp_path):
    execution = _start(project, env, "s1")
    raw = tmp_path / "raw.txt"
    raw.write_bytes(b"rows")
    proof = _proof(project, env, "s1", raw)
    did = _decide(project, env, "s1", "the table has the column", proof)

    out = _run(["why-did", str(project), execution], env)

    assert out.returncode == 0, out.stderr
    lines = out.stdout.splitlines()
    assert did in lines[0] and "evidenced" in lines[0] and "the table has the column" in lines[0]
    assert proof in lines[1] and "output: present (sha ok)" in lines[1] and str(raw) in lines[1]


def test_why_did_by_ticket_spans_runs(project, env):
    work.open_item(project, "Fix it", ticket="FM-1")
    item = work.items(project)[0].id
    first = _start(project, env, "s1", item)
    one = _decide(project, env, "s1", "first claim")
    executions.finish(project, first, outcome="ok")
    _start(project, env, "s2", item)
    two = _decide(project, env, "s2", "second claim")

    out = _run(["why-did", str(project), "FM-1"], env)

    assert out.returncode == 0, out.stderr
    assert one in out.stdout and two in out.stdout
    assert out.stdout.index(one) < out.stdout.index(two)


def test_why_did_reports_missing_output(project, env, tmp_path):
    execution = _start(project, env, "s1")
    raw = tmp_path / "raw.txt"
    raw.write_bytes(b"rows")
    proof = _proof(project, env, "s1", raw)
    _decide(project, env, "s1", "the table has the column", proof)
    raw.unlink()

    out = _run(["why-did", str(project), execution], env)

    assert "output: gone" in out.stdout

    raw.write_bytes(b"changed")
    again = _run(["why-did", str(project), execution], env)
    assert "output: present (sha differs)" in again.stdout


def test_why_did_grep_filters_claims(project, env):
    execution = _start(project, env, "s1")
    _decide(project, env, "s1", "The Cache is cold")
    _decide(project, env, "s1", "pool is sized")

    out = _run(["why-did", str(project), execution, "--grep", "cache COLD"], env)

    assert "The Cache is cold" in out.stdout and "pool is sized" not in out.stdout


def test_why_did_unknown_scope_is_an_error(project, env):
    _start(project, env, "s1")

    out = _run(["why-did", str(project), "nothing-like-it"], env)

    assert out.returncode == 2 and out.stderr.startswith("error: ")


def test_why_did_without_decisions_says_so(project, env):
    execution = _start(project, env, "s1")

    out = _run(["why-did", str(project), execution], env)

    assert out.returncode == 0 and len(out.stdout.splitlines()) == 1
