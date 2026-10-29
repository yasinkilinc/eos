"""`eos why-did`: the decisions of a run, work item or ticket, and what proved them."""
import os
import re
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
    e["TZ"] = "UTC"
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


def _proof(project, env, session, raw=None, *extra):
    args = ["--kind", "ran", "--tool", "db", *extra]
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
    lower = _run(["why-did", str(project), "fm-1"], env)
    assert lower.returncode == 0, lower.stderr
    assert lower.stdout == out.stdout


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


def test_why_did_unreadable_output_is_a_visible_state(project, env, tmp_path):
    if os.geteuid() == 0:
        pytest.skip("root reads every file")
    execution = _start(project, env, "s1")
    raw = tmp_path / "raw.txt"
    raw.write_bytes(b"rows")
    proof = _proof(project, env, "s1", raw)
    _decide(project, env, "s1", "the table has the column", proof)
    raw.chmod(0)
    try:
        out = _run(["why-did", str(project), execution], env)
    finally:
        raw.chmod(0o600)

    assert out.returncode == 0, out.stderr
    assert "output: unreadable" in out.stdout


def test_why_did_ticket_in_a_second_project_ledger(tmp_path, env, monkeypatch):
    ws = tmp_path / "ws"
    (ws / ".eos").mkdir(parents=True)
    svc = tmp_path / "svc"
    (svc / ".eos").mkdir(parents=True)
    (ws / ".eos" / "projects.toml").write_text(
        '[[project]]\nname = "svc"\nroot = "../svc"\n', encoding="utf-8")
    work.open_item(svc, "Fix it", ticket="FM-7")
    item = work.items(svc)[0].id
    _start(svc, env, "s1", item)
    did = _decide(svc, env, "s1", "service side claim")

    out = _run(["why-did", str(ws), "FM-7"], env)

    assert out.returncode == 0, out.stderr
    assert did in out.stdout


def test_why_did_shows_local_time(project, env):
    execution = _start(project, env, "s1")
    _decide(project, env, "s1", "a claim")
    utc = _run(["why-did", str(project), execution], env).stdout.split()[:2]
    istanbul = _run(["why-did", str(project), execution], {**env, "TZ": "Europe/Istanbul"}
                    ).stdout.split()[:2]

    def moment(parts):
        import datetime
        return datetime.datetime.strptime(" ".join(parts), "%Y-%m-%d %H:%M")

    assert re.fullmatch(r"\d{4}-\d\d-\d\d \d\d:\d\d", " ".join(istanbul))
    assert moment(istanbul) - moment(utc) == __import__("datetime").timedelta(hours=3)


def test_why_did_assumption_prints_reasons_check_and_missing(project, env):
    execution = _start(project, env, "s1")
    out = _run(["decide", str(project), "--claim", "pool is sized", "--check", "db.sh pg x",
                "--evidence", "e-00000000", "--session", "s1"], env)
    assert out.returncode == 0, out.stderr

    shown = _run(["why-did", str(project), execution], env).stdout

    assert "check=db.sh pg x" in shown
    assert "reason=unknown-id evidence=e-00000000" in shown
    assert re.search(r"^\s+e-00000000\s+missing$", shown, re.M)


def test_why_did_keeps_first_time_and_latest_state(project, env, tmp_path):
    execution = _start(project, env, "s1")
    made = _run(["decide", str(project), "--claim", "first claim", "--session", "s1"], env)
    first = made.stdout.split()[0].split("=")[1]
    other = _decide(project, env, "s1", "second claim")
    proof = _proof(project, env, "s1")
    done = _run(["decide", str(project), "--resolve", first, "--evidence", proof,
                 "--session", "s1"], env)
    assert done.returncode == 0, done.stderr

    shown = _run(["why-did", str(project), execution], env).stdout

    heads = [l for l in shown.splitlines() if re.match(r"\d{4}-", l)]
    assert [h.split()[2] for h in heads] == [first, other]
    assert "resolved" in heads[0] and "assumption" in heads[1]


def test_why_did_evidence_line_shows_the_ref(project, env, tmp_path):
    execution = _start(project, env, "s1")
    raw = tmp_path / "raw.txt"
    raw.write_bytes(b"rows")
    proof = _proof(project, env, "s1", raw, "--target", "env1", "--ref", "pg crm", "--exit", "0")
    _decide(project, env, "s1", "the table has the column", proof)

    shown = _run(["why-did", str(project), execution], env).stdout

    assert re.search(rf"^    {proof}  db env1 pg crm  exit 0  \d\d:\d\d  output: present \(sha ok\) ",
                     shown, re.M), shown


def test_why_did_evidence_from_another_day_shows_its_date(project, env):
    execution = _start(project, env, "s1")
    executions._append(executions.path_for(project), {
        "type": "event", "execution": execution, "ord": None, "at": "2026-10-01T10:00:00+00:00",
        "kind": "ran", "tool": "gitx", "source": "wrapper", "id": "e-0000beef"})
    _decide(project, env, "s1", "the branch is clean", "e-0000beef")

    shown = _run(["why-did", str(project), execution], env).stdout

    assert re.search(r"^    e-0000beef  gitx  2026-10-01 10:00  output: -$", shown, re.M), shown


def test_why_did_loads_the_ledgers_once(project, env, monkeypatch, capsys):
    import argparse

    from core import decisions
    from core.cli import whydid

    execution = _start(project, env, "s1")
    proofs = [executions.event(project, execution, kind="ran", tool="gitx", source="wrapper",
                               session="s1").id for _ in range(3)]
    _decide(project, env, "s1", "the branch is clean", *proofs)
    calls = []
    real = decisions.all_records

    def counted(*args, **kwargs):
        calls.append(args)
        return real(*args, **kwargs)

    monkeypatch.setattr(decisions, "all_records", counted)

    done = whydid.cmd_whydid(argparse.Namespace(words=[str(project), execution], path=None,
                                                grep=None, session=None))

    assert done == 0 and len(calls) == 1
    out = capsys.readouterr().out
    assert all(re.search(rf"^    {p}  gitx  ", out, re.M) for p in proofs), out


def test_why_did_survives_an_unparseable_at_and_sorts_it_first(project, env):
    execution = _start(project, env, "s1")
    good = _proof(project, env, "s1")
    with executions.path_for(project).open("a", encoding="utf-8") as handle:
        handle.write('{"type": "event", "execution": "%s", "kind": "ran", "tool": "db", '
                     '"source": "wrapper", "id": "e-cccccccc", "at": "garbled"}\n' % execution)
    first = _decide(project, env, "s1", "uses the good one", good)
    second = _decide(project, env, "s1", "uses the garbled one", "e-cccccccc")

    out = _run(["why-did", str(project), execution], env)

    assert out.returncode == 0, out.stderr
    assert "e-cccccccc" in out.stdout and "garbled" in out.stdout
    assert first in out.stdout and second in out.stdout


def test_why_did_shows_a_refused_resolve_and_the_later_resolution(project, env):
    execution = _start(project, env, "s1")
    did = _decide(project, env, "s1", "the table has the column")
    refused = _run(["decide", str(project), "--resolve", did, "--evidence", "e-00000000",
                    "--session", "s1"], env)
    assert refused.returncode == 3
    proof = _proof(project, env, "s1")
    closed = _run(["decide", str(project), "--resolve", did, "--evidence", proof,
                   "--session", "s1"], env)
    assert closed.returncode == 0, closed.stderr

    out = _run(["why-did", str(project), execution], env)

    assert "resolve refused" in out.stdout and "reason=unknown-id evidence=e-00000000" in out.stdout
    assert re.search(rf"resolved later: \d\d:\d\d {proof}", out.stdout), out.stdout


@pytest.mark.parametrize("zone", ["America/New_York", "Europe/Istanbul"])
def test_why_did_never_converts_or_prints_an_unreadable_at(project, env, zone):
    execution = _start(project, env, "s1")
    with executions.path_for(project).open("a", encoding="utf-8") as handle:
        handle.write('{"type": "event", "execution": "%s", "kind": "ran", "tool": "db", '
                     '"source": "wrapper", "id": "e-dddddddd", "at": "call 05551234567"}\n' % execution)
    _decide(project, env, "s1", "uses the odd one", "e-dddddddd")

    out = _run(["why-did", str(project), execution], {**env, "TZ": zone})

    assert out.returncode == 0, out.stderr
    assert "e-dddddddd" in out.stdout and "unreadable" in out.stdout
    assert "05551234567" not in out.stdout and "Traceback" not in out.stderr


def test_why_did_says_approved_later_for_an_approval(project, env):
    from core import decisions

    execution = _start(project, env, "s1")
    did = _decide(project, env, "s1", "the table has the column", "e-00000000")
    decisions.approve(project, did, prompt_excerpt="user prompt: onay", session="s1")

    out = _run(["why-did", str(project), execution], env)

    assert re.search(r"    approved later: \d\d:\d\d\n", out.stdout), out.stdout
    assert "resolved later" not in out.stdout
