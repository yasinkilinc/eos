"""Every number a report prints says what it is (2.x roadmap L4): measured, derived
(computed through an estimate, printed with `~`) or unmeasured (None, printed `—`,
never 0)."""
import json
import subprocess
import sys
from pathlib import Path

from core import context_cost, session_cost, telemetry
from core.lib import honest

REPO = Path(__file__).resolve().parents[1]


def test_the_helper_says_what_a_number_is():
    assert honest.show(1234, honest.MEASURED) == "1,234"
    assert honest.show(1234, honest.DERIVED) == "~1,234"
    assert honest.show(None, honest.DERIVED) == "—"
    assert honest.show(None, honest.MEASURED, brief=True) == "?"
    assert honest.show(0.25, honest.DERIVED, spec=".0%") == "~25%"
    assert honest.show(0, honest.MEASURED) == "0"


def _numeric_keys(value, prefix=""):
    """Dotted keys of every number in a report, list items collapsed to `[]`."""
    if isinstance(value, bool):
        return set()
    if isinstance(value, (int, float)):
        return {prefix}
    if isinstance(value, dict):
        found = set()
        for key, item in value.items():
            if key == "provenance":
                continue
            found |= _numeric_keys(item, f"{prefix}.{key}" if prefix else key)
        return found
    if isinstance(value, list):
        return set().union(*(_numeric_keys(item, f"{prefix}[]") for item in value)) if value else set()
    return set()


def _covered(report: dict) -> None:
    provenance = report.get("provenance")
    assert isinstance(provenance, dict), "a report must say what its numbers are"
    assert set(provenance.values()) <= {honest.MEASURED, honest.DERIVED}
    missing = {key for key in _numeric_keys(report)
               if not any(key == pattern or key.startswith(pattern + ".") or key.startswith(pattern + "[]")
                          for pattern in provenance)}
    assert not missing, missing


def _telemetry_project(tmp_path: Path) -> Path:
    (tmp_path / ".eos").mkdir()
    (tmp_path / ".eos" / "config.toml").write_text("[telemetry]\nenabled = true\n", encoding="utf-8")
    telemetry.record(tmp_path, "brief", [], 12.5, chars=1200, session="s1")
    telemetry.record(tmp_path, "note", ["search"], 3.0, chars=400, session="s1")
    return tmp_path


def test_eos_cost_says_its_tokens_are_derived(tmp_path):
    proj = _telemetry_project(tmp_path)
    report = telemetry.summary(proj)
    _covered(report)
    assert report["provenance"]["tokens"] == honest.DERIVED
    text = subprocess.run([sys.executable, str(REPO / "core" / "eos.py"), "cost", str(proj)],
                          capture_output=True, text=True).stdout
    assert "~540" in text        # the brief's median, 1,200 chars at 2.22 per token (core/context/budget.py)


def test_cost_context_is_covered_and_never_zero_for_unmeasured(tmp_path):
    folder = tmp_path / "transcripts"
    folder.mkdir()
    report = context_cost.report(tmp_path, transcripts=folder)
    _covered(report)
    assert report["explained"] is None
    for row in report["sources"]:
        assert row["share"] is None or row["share"] > 0


def test_cost_sessions_is_covered(tmp_path):
    proj = _telemetry_project(tmp_path)
    report = session_cost.report(proj)
    _covered(report)
    assert report["provenance"]["sessions[].eos.tokens"] == honest.DERIVED
    assert report["provenance"]["sessions[].model"] == honest.MEASURED


def test_the_json_the_cli_prints_carries_the_provenance(tmp_path):
    proj = _telemetry_project(tmp_path)
    for flags in ([], ["--sessions"]):
        done = subprocess.run([sys.executable, str(REPO / "core" / "eos.py"), "cost", str(proj), *flags,
                               "--format", "json"], capture_output=True, text=True)
        assert done.returncode == 0, done.stderr
        assert "provenance" in json.loads(done.stdout)


def test_totals_nobody_measured_are_none(tmp_path):
    """Review of L4: model totals were 0 when no session had a model line."""
    proj = _telemetry_project(tmp_path)
    totals = session_cost.report(proj)["totals"]
    assert totals["new_tokens"] is None and totals["read_tokens"] is None
    assert totals["eos_tokens"] == 720  # 1,200 + 400 chars at 2.22/token (core/context/budget.py)


def test_a_large_derived_count_keeps_its_column():
    data = {"since": None, "unattributed_calls": 0, "sources": {},
            "totals": {"sessions": 1, "both_measured": 0, "eos_share": None},
            "sessions": [{"session": "s1", "last_at": "2026-09-27T10:00", "eos_share": None, "model": None,
                          "hooks": None, "eos": {"calls": 3, "tokens": 1234567}}]}
    header, row = session_cost.render(data).splitlines()[2:4]
    assert row.index("~1,234,567") + len("~1,234,567") <= header.index("EOS tok") + len("EOS tok")
