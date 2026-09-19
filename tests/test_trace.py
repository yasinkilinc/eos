"""What an entry point reaches -- and what no call graph can reach from it.

The second half is the finding, not a caveat. Measured on a real service: 235
files throw a behaviour code and only 6 are reachable from any of its 39 entry
points, because 120 of the rest are components a coordinator looks up by name
from a configuration row. 252 of that project's 475 named components have no
caller at all.

On that shape of system the call graph is not the program, and a trace that
printed only what it could follow would be a confident, incomplete answer. So
the runtime-wired population is printed on every run, including the runs where
it is inconvenient.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from core import inspector

REPO = Path(__file__).resolve().parents[1]
EOS = [sys.executable, str(REPO / "core" / "eos.py")]
FIXTURE = Path(__file__).parent / "fixtures" / "java_structure" / "svc"


def _run(args, **kw):
    return subprocess.run(EOS + args, capture_output=True, text=True, encoding="utf-8", **kw)


def _scanned(tmp_path: Path) -> Path:
    root = tmp_path / "svc"
    shutil.copytree(FIXTURE, root)
    assert _run(["init", str(root), "--no-ai"]).returncode == 0
    done = _run(["scan", str(root), "--full"])
    assert done.returncode == 0, done.stderr
    return root


CONTROLLER = "src/main/java/com/example/orders/OrderController.java"


def test_trace_names_the_route_the_entry_point_serves(tmp_path):
    answer = inspector.trace(_scanned(tmp_path), CONTROLLER)

    assert answer["endpoints"] == ["**POST** `/orders/submit`"], answer["endpoints"]


def test_trace_follows_calls_beyond_one_hop(tmp_path):
    answer = inspector.trace(_scanned(tmp_path), CONTROLLER, depth=3)

    reached = {entry["path"].rsplit("/", 1)[-1] for entry in answer["reaches"]}
    assert "AgeLimitPolicy.java" in reached, reached


def test_trace_reports_a_component_nothing_calls(tmp_path):
    """Registered under a name and uncalled is the signature of a class a
    container resolves at run time."""
    answer = inspector.trace(_scanned(tmp_path), CONTROLLER)

    wired = answer["runtime_wired"]
    beans = {example["bean"] for example in wired["examples"]}
    assert "chainStepCommand" in beans, wired
    assert wired["uncalled"] >= 1 and wired["total"] >= wired["uncalled"], wired


def test_a_behaviour_code_behind_a_runtime_component_is_not_claimed_reachable(tmp_path):
    """AGE_NEGATIVE is thrown by the uncalled component. Reporting it as
    reachable from the controller would be the confident wrong answer."""
    answer = inspector.trace(_scanned(tmp_path), CONTROLLER, depth=5)

    reachable = {entry["code"] for entry in answer["codes"]}
    assert "AGE_NEGATIVE" not in reachable, (
        f"a code behind a runtime-wired component was claimed reachable: {reachable}"
    )


def test_the_cli_prints_the_runtime_wired_line_every_time(tmp_path):
    root = _scanned(tmp_path)

    done = _run(["trace", str(root), CONTROLLER])

    assert done.returncode == 0, done.stderr
    assert "serves **POST** `/orders/submit`" in done.stdout, done.stdout
    assert "resolved at run time" in done.stdout, (
        f"the limit of the answer was not stated:\n{done.stdout}"
    )


def test_trace_emits_json(tmp_path):
    root = _scanned(tmp_path)

    done = _run(["trace", str(root), CONTROLLER, "--format", "json"])

    assert done.returncode == 0, done.stderr
    answer = json.loads(done.stdout)
    assert {"file", "reaches", "endpoints", "codes", "runtime_wired"} <= set(answer)


def test_trace_refuses_a_path_that_is_not_indexed(tmp_path):
    with pytest.raises(ValueError, match="No indexed node"):
        inspector.trace(_scanned(tmp_path), "does/not/exist.java")
