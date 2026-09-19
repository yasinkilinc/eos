"""What EOS costs the agent that calls it.

This system exists to spend fewer tokens than rediscovery would, and that
claim needs a number rather than an assertion. `eos bench` measures against
baselines on demand; this records what actually happened, call by call, so the
cost of a habit is visible after the habit forms.

Three properties are load-bearing and each has a test: it records what was
called and never what was asked, it never breaks the command it is measuring,
and it is off unless a project turns it on.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

from core import telemetry

REPO = Path(__file__).resolve().parents[1]
EOS = [sys.executable, str(REPO / "core" / "eos.py")]
FIXTURE = Path(__file__).parent / "fixtures" / "java_structure" / "svc"


def _run(args, **kw):
    return subprocess.run(EOS + args, capture_output=True, text=True, encoding="utf-8", **kw)


def _project(tmp_path: Path, on: bool) -> Path:
    root = tmp_path / "svc"
    shutil.copytree(FIXTURE, root)
    assert _run(["init", str(root), "--no-ai"]).returncode == 0
    if on:
        config = root / ".eos" / "config.toml"
        config.write_text(config.read_text(encoding="utf-8") + "\n[telemetry]\nenabled = true\n",
                          encoding="utf-8")
    assert _run(["scan", str(root), "--full"]).returncode == 0
    return root


def test_nothing_is_recorded_unless_a_project_turns_it_on(tmp_path):
    """A tool that starts logging without being told is one people turn off."""
    root = _project(tmp_path, on=False)

    assert _run(["rules", str(root)]).returncode == 0

    assert telemetry.load(root) == []
    assert not telemetry.path_for(root).exists()


def test_a_call_records_its_command_duration_and_answer_size(tmp_path):
    root = _project(tmp_path, on=True)

    assert _run(["rules", str(root)]).returncode == 0

    entries = [entry for entry in telemetry.load(root) if entry["command"] == "rules"]
    assert entries, telemetry.load(root)
    entry = entries[-1]
    assert entry["chars"] > 0 and entry["tokens"] == entry["chars"] // 4, entry
    assert entry["ms"] >= 0 and entry["ok"] is True, entry


def test_a_flag_is_recorded_by_name_and_never_by_value(tmp_path):
    """A search query, a task or a note body is whatever somebody typed. A log
    that captured them would be a liability in every project EOS touches."""
    root = _project(tmp_path, on=True)
    secret = "an-internal-hostname-nobody-should-log"

    assert _run(["query", str(root), "--search", secret]).returncode == 0

    written = telemetry.path_for(root).read_text(encoding="utf-8")
    assert secret not in written, written
    entry = [e for e in telemetry.load(root) if e["command"] == "query"][-1]
    assert "--search" in entry["flags"], entry


def test_the_context_answer_is_the_file_not_the_line_about_it(tmp_path):
    """`eos context` writes a file and prints one line. Recording the line
    would make the most expensive call look like the cheapest."""
    root = _project(tmp_path, on=True)

    assert _run(["context", str(root)]).returncode == 0

    entry = [e for e in telemetry.load(root) if e["command"] == "context"][-1]
    written = (root / ".eos" / "data" / "brain" / "llm_context.md").read_text(encoding="utf-8")
    assert entry["chars"] == len(written), entry


def test_a_broken_telemetry_write_does_not_break_the_command(tmp_path, monkeypatch):
    """A statistic may not turn a working `eos rules` into a failing one."""
    root = _project(tmp_path, on=True)

    def explode(*_args, **_kwargs):
        raise OSError("disk is full")

    monkeypatch.setattr(telemetry, "record", explode)
    with telemetry.Timer(root, "rules") as timer:
        timer.chars = 10

    assert True, "the Timer swallowed the failure"


def test_counting_does_not_change_what_a_command_prints(tmp_path):
    """The counter forwards to the real stream rather than buffering. Replaying
    a buffer broke the escaping a non-UTF-8 terminal needs."""
    root = _project(tmp_path, on=True)

    off = _run(["rules", str(_project(tmp_path / "other", on=False))])
    on = _run(["rules", str(root)])

    assert off.stdout == on.stdout, "telemetry changed the output"


def test_cost_reports_per_command_medians(tmp_path):
    root = _project(tmp_path, on=True)
    for _ in range(3):
        assert _run(["rules", str(root)]).returncode == 0

    done = _run(["cost", str(root), "--format", "json"])

    assert done.returncode == 0, done.stderr
    report = json.loads(done.stdout)
    rules = [row for row in report["commands"] if row["command"] == "rules"][0]
    assert rules["calls"] == 3, rules
    assert rules["median_ms"] > 0 and rules["median_tokens"] > 0, rules


def test_cost_says_how_to_turn_it_on_when_it_is_off(tmp_path):
    root = _project(tmp_path, on=False)

    done = _run(["cost", str(root)])

    assert done.returncode == 0, done.stderr
    assert "enabled = true" in done.stdout, done.stdout
    assert "never" in done.stdout, "the answer must say what it does not record"


def test_reading_the_cost_is_not_itself_a_recorded_call(tmp_path):
    root = _project(tmp_path, on=True)
    assert _run(["rules", str(root)]).returncode == 0

    assert _run(["cost", str(root)]).returncode == 0

    assert not [entry for entry in telemetry.load(root) if entry["command"] == "cost"], (
        "reading the log changed what the log reports"
    )


def test_the_log_is_bounded(tmp_path, monkeypatch):
    """An unbounded log in every project is a slow leak."""
    root = _project(tmp_path, on=True)
    monkeypatch.setattr(telemetry, "MAX_LINES", 5)
    for _ in range(9):
        telemetry.record(root, "query", chars=10)

    assert len(telemetry.load(root)) == 5
