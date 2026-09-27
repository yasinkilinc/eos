"""`eos cost --sessions`: what EOS delivered to each session against what the model read (2.x roadmap C7)."""
import json
import subprocess
import sys
from pathlib import Path

from core import session_cost

REPO = Path(__file__).resolve().parents[1]


def _write(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _project(tmp_path: Path) -> Path:
    data = tmp_path / ".eos" / "data"
    _write(data / "telemetry.jsonl", [
        {"at": "2026-09-27T08:00:00+00:00", "command": "hook", "flags": ["session-start"], "session": "s-one",
         "chars": 2400, "tokens": 600, "ok": True},
        {"at": "2026-09-27T08:01:00+00:00", "command": "hook", "flags": ["user-prompt"], "session": "s-one",
         "chars": 1200, "tokens": 300, "ok": True},
        {"at": "2026-09-27T08:02:00+00:00", "command": "note", "flags": ["search"], "session": "s-one",
         "chars": 400, "tokens": 100, "ok": True},
        {"at": "2026-09-26T09:00:00+00:00", "command": "hook", "flags": ["session-start"], "session": "s-old",
         "chars": 800, "tokens": 200, "ok": True},
        {"at": "2026-09-27T08:03:00+00:00", "command": "scan", "flags": [], "session": None,
         "chars": 50, "tokens": 12, "ok": True},
    ])
    _write(data / "routing-usage.jsonl", [
        {"at": "2026-09-27T09:00:00+00:00", "session": "s-one",
         "models": {"model-a": {"messages": 10, "input_tokens": 1000, "output_tokens": 3000,
                                "cache_read_input_tokens": 90000, "cache_creation_input_tokens": 19000}},
         "subagent_models": {"model-b": {"messages": 2, "input_tokens": 50, "output_tokens": 70,
                                         "cache_read_input_tokens": 0, "cache_creation_input_tokens": 4000}}},
        {"at": "2026-09-27T10:00:00+00:00", "session": "s-model-only",
         "models": {"model-a": {"messages": 1, "input_tokens": 10, "output_tokens": 5,
                                "cache_read_input_tokens": 0, "cache_creation_input_tokens": 90}},
         "subagent_models": {}},
    ])
    _write(data / "sessions.jsonl", [
        {"at": "2026-09-27T09:00:00+00:00", "session": "s-one", "hints": 2, "outlined": 1,
         "verify_gates": 1, "verify_after_gate": 1, "subagents": 1, "events_recorded": {"ran:ok": 3}},
    ])
    return tmp_path


def test_the_three_sources_join_on_the_session(tmp_path):
    data = session_cost.report(_project(tmp_path))
    one = next(row for row in data["sessions"] if row["session"] == "s-one")
    assert one["eos"] == {"calls": 3, "chars": 4000, "tokens": 1000,
                          "by_command": {"hook session-start": 600, "hook user-prompt": 300, "note search": 100}}
    assert one["model"]["new_tokens"] == 20000        # input + cache creation: what entered the context
    assert one["model"]["read_tokens"] == 110000      # every token read, re-reads included
    assert one["model"]["output_tokens"] == 3000
    assert one["subagents"]["new_tokens"] == 4050
    assert one["hooks"]["hints"] == 2 and one["hooks"]["verify_gates"] == 1
    assert one["eos_share"] == 0.05                   # 1,000 of the 20,000 new tokens


def test_a_source_with_nothing_for_a_session_is_unmeasured_not_zero(tmp_path):
    data = session_cost.report(_project(tmp_path))
    model_only = next(row for row in data["sessions"] if row["session"] == "s-model-only")
    assert model_only["eos"] is None and model_only["hooks"] is None
    assert model_only["eos_share"] is None
    old = next(row for row in data["sessions"] if row["session"] == "s-old")
    assert old["model"] is None and old["eos_share"] is None
    assert data["unattributed_calls"] == 1


def test_since_keeps_sessions_seen_on_or_after_the_date(tmp_path):
    data = session_cost.report(_project(tmp_path), since="2026-09-27")
    assert {row["session"] for row in data["sessions"]} == {"s-one", "s-model-only"}


def test_newest_first_and_totals_over_measured_sessions_only(tmp_path):
    data = session_cost.report(_project(tmp_path))
    assert [row["session"] for row in data["sessions"]][0] == "s-model-only"
    assert data["totals"]["eos_tokens"] == 1200
    assert data["totals"]["new_tokens"] == 20100
    # the share is over sessions measured by both sources, never mixing in a half-seen one
    assert data["totals"]["eos_share"] == 0.05
    assert data["totals"]["both_measured"] == 1


def test_no_data_says_where_it_looked(tmp_path):
    (tmp_path / ".eos").mkdir()
    text = session_cost.render(session_cost.report(tmp_path))
    assert "no session" in text.lower()
    assert "telemetry" in text and "routing-usage" in text


def test_render_marks_unmeasured_and_derived(tmp_path):
    text = session_cost.render(session_cost.report(_project(tmp_path)))
    assert "s-one" in text and "5.0%" in text
    assert "—" in text
    assert "derived" in text.lower()


def test_cli(tmp_path):
    proj = _project(tmp_path)
    done = subprocess.run([sys.executable, str(REPO / "core" / "eos.py"), "cost", str(proj), "--sessions",
                           "--format", "json"], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout)["totals"]["both_measured"] == 1
