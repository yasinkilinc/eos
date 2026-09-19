"""Recording what was run, and refusing to say what it meant.

EOS does not execute anything. Running a test needs a build tool, an
environment and minutes, and core/ is stdlib-only by design -- so an adapter
runs it and this records the evidence: command, exit code, a digest of the
output, where the log is, which commit the tree was on.

The verdict is deliberately not EOS's. A failing test can mean the rule is not
enforced, or the test is wrong, or the environment was; nothing in an exit code
separates those. A system that guessed would produce exactly the confident
wrong answer the rest of this codebase exists to avoid.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from core import inspector, verification

REPO = Path(__file__).resolve().parents[1]
EOS = [sys.executable, str(REPO / "core" / "eos.py")]
FIXTURE = Path(__file__).parent / "fixtures" / "java_structure" / "svc"


def _run(args, **kw):
    return subprocess.run(EOS + args, capture_output=True, text=True, encoding="utf-8", **kw)


def _scanned(tmp_path: Path) -> Path:
    root = tmp_path / "svc"
    shutil.copytree(FIXTURE, root)
    assert _run(["init", str(root), "--no-ai"]).returncode == 0
    assert _run(["scan", str(root), "--full"]).returncode == 0
    return root


def test_a_record_keeps_the_command_and_the_exit_code(tmp_path):
    root = _scanned(tmp_path)

    entry = verification.record(root, "AGE_IMPLAUSIBLE", "passed",
                                "mvn test -Dtest=ValidateAgeCommandTest", exit_code=0,
                                log="logs/build/x.log", output="BUILD SUCCESS")

    assert entry.command.startswith("mvn test"), entry
    assert entry.exit_code == 0 and entry.log == "logs/build/x.log", entry
    assert len(entry.output_sha256) == 64, entry
    assert entry.recorded_at.endswith("+00:00"), entry


def test_a_record_without_a_command_is_refused(tmp_path):
    """A result with no command behind it is not evidence of anything."""
    root = _scanned(tmp_path)

    with pytest.raises(ValueError, match="not evidence"):
        verification.record(root, "AGE_IMPLAUSIBLE", "passed", "   ")


def test_records_are_append_only_and_survive_a_rescan(tmp_path):
    """`eos scan` rewrites everything under .eos/data. An execution cannot be
    recomputed from a file tree, so it is not kept there."""
    root = _scanned(tmp_path)
    verification.record(root, "AGE_IMPLAUSIBLE", "failed", "mvn test", exit_code=1)
    verification.record(root, "AGE_IMPLAUSIBLE", "passed", "mvn test", exit_code=0)

    assert _run(["scan", str(root), "--full"]).returncode == 0

    records = verification.load(root)
    assert [entry.outcome for entry in records] == ["failed", "passed"], records
    assert verification.latest_by_code(root)["AGE_IMPLAUSIBLE"].outcome == "passed"


def test_a_passing_run_is_the_top_of_the_coverage_ladder(tmp_path):
    """The only rung that observed the behaviour rather than inferring it from
    structure."""
    root = _scanned(tmp_path)
    before = inspector.rules(root)
    assert {e["code"]: e["coverage"] for e in before["codes"]}["AGE_IMPLAUSIBLE"] == "reachable"

    verification.record(root, "AGE_IMPLAUSIBLE", "passed", "mvn test", exit_code=0)

    after = inspector.rules(root)
    graded = {e["code"]: e["coverage"] for e in after["codes"]}
    assert graded["AGE_IMPLAUSIBLE"] == "verified", after["coverage"]
    assert after["coverage"]["verified"] == 1


def test_a_failing_run_does_not_demote_the_static_reading(tmp_path):
    """The test existing and the test passing are different facts, and a
    failure is read in `eos findings`, not by removing what analysis found."""
    root = _scanned(tmp_path)

    verification.record(root, "AGE_IMPLAUSIBLE", "failed", "mvn test", exit_code=1)

    graded = {e["code"]: e["coverage"] for e in inspector.rules(root)["codes"]}
    assert graded["AGE_IMPLAUSIBLE"] == "reachable", graded


def test_an_invalid_verdict_is_refused(tmp_path):
    root = _scanned(tmp_path)

    with pytest.raises(ValueError, match="verdict must be one of"):
        verification.record(root, "AGE_IMPLAUSIBLE", "failed", "mvn test", verdict="probably-a-bug")


def test_no_verdict_is_ever_written_automatically(tmp_path):
    root = _scanned(tmp_path)

    entry = verification.record(root, "AGE_IMPLAUSIBLE", "failed", "mvn test", exit_code=1)

    assert entry.verdict is None, (
        "an exit code does not separate a wrong rule from a wrong test from a bad environment"
    )


def test_the_cli_says_a_failure_has_no_verdict_yet(tmp_path):
    root = _scanned(tmp_path)

    done = _run(["verify", str(root), "AGE_IMPLAUSIBLE", "--outcome", "failed",
                 "--command", "mvn test", "--exit-code", "1"])

    assert done.returncode == 0, done.stderr
    assert "no verdict recorded" in done.stderr, done.stderr


def test_findings_lists_runs_newest_first(tmp_path):
    root = _scanned(tmp_path)
    verification.record(root, "AGE_LIMIT", "passed", "mvn test -Dtest=A", exit_code=0)
    verification.record(root, "AGE_IMPLAUSIBLE", "failed", "mvn test -Dtest=B", exit_code=1,
                        verdict="test-defect", note="the mock returned the wrong type")

    done = _run(["findings", str(root)])

    assert done.returncode == 0, done.stderr
    assert done.stdout.index("AGE_IMPLAUSIBLE") < done.stdout.index("AGE_LIMIT"), done.stdout
    assert "test-defect" in done.stdout
    assert "the mock returned the wrong type" in done.stdout


def test_findings_can_show_only_what_did_not_pass(tmp_path):
    root = _scanned(tmp_path)
    verification.record(root, "AGE_LIMIT", "passed", "mvn test -Dtest=A", exit_code=0)
    verification.record(root, "AGE_IMPLAUSIBLE", "failed", "mvn test -Dtest=B", exit_code=1)

    done = _run(["findings", str(root), "--failed-only", "--format", "json"])

    assert done.returncode == 0, done.stderr
    assert [entry["code"] for entry in json.loads(done.stdout)] == ["AGE_IMPLAUSIBLE"]


def test_findings_with_nothing_recorded_points_at_the_next_step(tmp_path):
    root = _scanned(tmp_path)

    done = _run(["findings", str(root)])

    assert done.returncode == 0, done.stderr
    assert "eos draft-test" in done.stdout, done.stdout


def test_an_unreadable_line_is_skipped_not_fatal(tmp_path):
    root = _scanned(tmp_path)
    verification.record(root, "AGE_LIMIT", "passed", "mvn test", exit_code=0)
    target = verification.path_for(root)
    target.write_text(target.read_text(encoding="utf-8") + "{ not json\n", encoding="utf-8")

    assert [entry.code for entry in verification.load(root)] == ["AGE_LIMIT"]
