"""`eos route`: the decision, its reasons, and a refusal that says what it refused.

Run as a subprocess, the way an agent or a hook runs it, so the parser, the
session fill-in and telemetry are exercised along with the router.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

from core.routing.types import Decision

REPO = Path(__file__).resolve().parents[1]
EOS = [sys.executable, str(REPO / "core" / "eos.py")]
SENTINEL = "zanzibarquux"
LABELS = ("Task:", "Type:", "Complexity:", "Model:", "Effort:", "Confidence:", "Source:", "Reason:")


def _env(tmp_path, **extra):
    env = {k: v for k, v in os.environ.items()
           if k not in ("EOS_SESSION", "CLAUDE_CODE_SESSION_ID", "EOS_EXECUTION",
                        "EOS_EXECUTION_LEDGER", "EOS_ROUTE_MODEL", "EOS_ROUTE_EFFORT")}
    env["EOS_STATE_DIR"] = str(tmp_path / "state")
    env.update(extra)
    return env


def _run(tmp_path, args, **extra):
    return subprocess.run(EOS + args, capture_output=True, text=True, encoding="utf-8",
                          env=_env(tmp_path, **extra))


def _project(tmp_path, table=""):
    root = tmp_path / "proj"
    (root / ".eos").mkdir(parents=True)
    if table:
        (root / ".eos" / "config.toml").write_text(table, encoding="utf-8")
    return root


def test_the_human_format_has_every_labelled_line(tmp_path):
    root = _project(tmp_path)
    done = _run(tmp_path, ["route", str(root), "refactor the auth flow and update tests"])

    assert done.returncode == 0, done.stderr
    lines = done.stdout.splitlines()
    for label in LABELS:
        assert any(line.startswith(label) for line in lines), label
    assert "Task: refactor the auth flow and update tests" in lines
    assert any(line.startswith("Factors: ") for line in lines)
    assert any(line.startswith("Alternatives: ") for line in lines)


def test_the_task_does_not_need_quoting(tmp_path):
    root = _project(tmp_path)
    done = _run(tmp_path, ["route", str(root), "fix", "the", "typo", "--no-record"])
    assert "Type:        trivial_edit" in done.stdout


def test_json_is_the_decision_and_nothing_else(tmp_path):
    root = _project(tmp_path)
    done = _run(tmp_path, ["route", str(root), "fix the typo", "--json"])

    data = json.loads(done.stdout)
    assert set(data) == {f.name for f in Decision.__dataclass_fields__.values()}
    assert (data["model"], data["effort"], data["level"]) == ("haiku", "", "LOW")


def test_an_unknown_model_exits_2_and_names_the_available_ids(tmp_path):
    root = _project(tmp_path)
    done = _run(tmp_path, ["route", str(root), "fix the typo", "--model", "nope"])

    assert done.returncode == 2
    assert "nope" in done.stderr and "haiku, sonnet, opus" in done.stderr
    assert done.stdout == ""


def test_a_malformed_config_exits_2_with_the_key(tmp_path):
    root = _project(tmp_path, '[model_routing]\ndefault_effort = "extreme"\n')
    done = _run(tmp_path, ["route", str(root), "fix the typo"])
    assert done.returncode == 2 and "default_effort" in done.stderr


def test_an_effort_the_model_lacks_prints_the_clamp(tmp_path):
    root = _project(tmp_path, '[model_routing.models.sonnet]\nefforts = ["low", "medium", "high"]\n')
    done = _run(tmp_path, ["route", str(root), "refactor the auth flow and update tests",
                           "--model", "sonnet", "--effort", "max"])

    assert done.returncode == 0, done.stderr
    assert "Effort:      high" in done.stdout
    assert "clamped" in done.stdout


def test_files_move_the_file_count_factor(tmp_path):
    root = _project(tmp_path)
    without = json.loads(_run(tmp_path, ["route", str(root), "refactor the parser", "--json",
                                         "--no-record"]).stdout)
    with_files = json.loads(_run(tmp_path, ["route", str(root), "refactor the parser", "--json",
                                            "--no-record", "--file", "a.py", "--file", "b.py",
                                            "--file", "c.py", "--file", "d.py"]).stdout)
    assert with_files["factors"]["file_count"] > without["factors"]["file_count"]
    assert with_files["factors"]["file_count_source"] == "files"


def test_stats_on_an_empty_project_says_nothing_was_recorded(tmp_path):
    root = _project(tmp_path)
    done = _run(tmp_path, ["route", str(root), "--stats"])
    assert done.returncode == 0
    assert "No decisions recorded" in done.stdout


def test_stats_counts_a_decision_made_inside_a_finished_run(tmp_path):
    root = _project(tmp_path)
    assert _run(tmp_path, ["run", "start", str(root), "--title", "a task"], EOS_SESSION="s1").returncode == 0
    assert _run(tmp_path, ["route", str(root), "fix the typo"], EOS_SESSION="s1").returncode == 0
    assert _run(tmp_path, ["run", "finish", str(root), "--outcome", "ok"], EOS_SESSION="s1").returncode == 0

    done = _run(tmp_path, ["route", str(root), "--stats"])
    assert "trivial_edit" in done.stdout and "  haiku " in done.stdout and "ok 1" in done.stdout


def test_a_missing_task_exits_2(tmp_path):
    root = _project(tmp_path)
    done = _run(tmp_path, ["route", str(root)])
    assert done.returncode == 2 and "task is required" in done.stderr


def test_the_session_is_filled_from_the_harness(tmp_path):
    root = _project(tmp_path)
    _run(tmp_path, ["route", str(root), "fix the typo"], EOS_SESSION="harness-7")
    line = json.loads((root / ".eos" / "data" / "routing.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert line["session"] == "harness-7"


def test_telemetry_records_the_call_and_never_the_task(tmp_path):
    root = _project(tmp_path, "[telemetry]\nenabled = true\n")
    done = _run(tmp_path, ["route", str(root), f"refactor the {SENTINEL} module", "--json"])
    assert done.returncode == 0, done.stderr

    entries = [json.loads(line) for line in
               (root / ".eos" / "data" / "telemetry.jsonl").read_text(encoding="utf-8").splitlines()]
    assert any(e["command"] == "route" and "--json" in e["flags"] for e in entries)
    stored = "\n".join(p.read_text(encoding="utf-8", errors="replace")
                       for p in (root / ".eos").rglob("*") if p.is_file())
    assert SENTINEL not in stored


def test_run_start_routes_the_title_and_binds_it_to_the_run(tmp_path):
    root = _project(tmp_path, "[model_routing]\n")
    started = _run(tmp_path, ["run", "start", str(root), "--title", "fix the typo in the readme"],
                   EOS_SESSION="s1")
    assert started.returncode == 0
    assert started.stdout.strip().startswith("x-"), "stdout stays the run id alone"
    assert "ROUTE  trivial_edit LOW → haiku (conf" in started.stderr
    assert _run(tmp_path, ["run", "finish", str(root), "--outcome", "ok"], EOS_SESSION="s1").returncode == 0
    assert "LOW      haiku " in _run(tmp_path, ["route", str(root), "--stats"]).stdout


def test_run_start_without_a_routing_table_routes_nothing(tmp_path):
    root = _project(tmp_path)
    started = _run(tmp_path, ["run", "start", str(root), "--title", "fix the typo"], EOS_SESSION="s1")
    assert started.returncode == 0 and "ROUTE" not in started.stderr
    assert not (root / ".eos" / "data" / "routing.jsonl").exists()
