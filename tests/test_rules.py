"""Behaviour codes: what this project refuses to do, and what no test names.

A refusal identified by a constant is the closest thing to a business rule
that can be *read* out of source rather than inferred from it -- the code is
what the API returns, what a test asserts and what a ticket quotes. Measured on
one service with its linked parent: 403 distinct codes are thrown and 343 are
named by no test at all.

That number is a floor, not coverage. Naming a code proves the suite knows the
behaviour exists; it does not prove the path reaching it is exercised. The
command says so on every run, because a number this easy to quote is exactly
the kind that gets quoted as something it is not.
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


def test_both_throw_spellings_are_found(tmp_path):
    """`throw X.of("CODE", …)` is a factory call and `throw new X("CODE", …)` a
    constructor; a reader looking for only one finds half the behaviours."""
    answer = inspector.rules(_scanned(tmp_path))

    codes = {entry["code"] for entry in answer["codes"]}
    assert codes == {"AGE_LIMIT", "AGE_IMPLAUSIBLE"}, codes


def test_a_code_records_where_it_is_thrown_from(tmp_path):
    answer = inspector.rules(_scanned(tmp_path))

    limit = next(e for e in answer["codes"] if e["code"] == "AGE_LIMIT")

    assert limit["where"] == ["ValidateAgeCommand.execute"], limit
    assert limit["exceptions"] == ["ValidationException"], limit
    assert any(ref.endswith(".java:20") or ".java:" in ref for ref in limit["thrown_at"]), limit


def test_a_code_no_test_names_is_separated_from_one_a_test_names(tmp_path):
    answer = inspector.rules(_scanned(tmp_path))

    by_code = {entry["code"]: entry for entry in answer["codes"]}

    assert by_code["AGE_LIMIT"]["tests"], (
        "a code asserted by a test in the test tree must be reported as named"
    )
    assert by_code["AGE_IMPLAUSIBLE"]["tests"] == [], by_code["AGE_IMPLAUSIBLE"]
    assert answer["untested"] == 1 and answer["total"] == 2, answer


def test_untested_first_so_the_gap_leads(tmp_path):
    answer = inspector.rules(_scanned(tmp_path))

    assert answer["codes"][0]["code"] == "AGE_IMPLAUSIBLE", (
        f"the untested code must sort first: {[e['code'] for e in answer['codes']]}"
    )


def test_untested_only_filters(tmp_path):
    answer = inspector.rules(_scanned(tmp_path), untested_only=True)

    assert [entry["code"] for entry in answer["codes"]] == ["AGE_IMPLAUSIBLE"], answer


def test_a_message_that_is_not_a_code_is_not_a_behaviour(tmp_path):
    """`throw new IllegalStateException("something went wrong")` is prose. The
    underscore is what separates an identifier from a sentence."""
    root = _scanned(tmp_path)
    extra = root / "src" / "main" / "java" / "com" / "example" / "orders" / "Prose.java"
    extra.write_text(
        "package com.example.orders;\n"
        "public class Prose {\n"
        "    void go() { throw new IllegalStateException(\"something went wrong\"); }\n"
        "}\n", encoding="utf-8")
    assert _run(["scan", str(root), "--full"]).returncode == 0

    codes = {entry["code"] for entry in inspector.rules(root)["codes"]}

    assert codes == {"AGE_LIMIT", "AGE_IMPLAUSIBLE"}, codes


def test_cli_says_the_number_is_a_floor_not_coverage(tmp_path):
    root = _scanned(tmp_path)

    done = _run(["rules", str(root)])

    assert done.returncode == 0, done.stderr
    assert "AGE_IMPLAUSIBLE" in done.stdout
    assert "named by no test" in done.stdout
    assert "not coverage" in done.stdout, (
        f"a number this quotable must carry what it does not mean:\n{done.stdout}"
    )


def test_cli_emits_json(tmp_path):
    root = _scanned(tmp_path)

    done = _run(["rules", str(root), "--untested", "--format", "json"])

    assert done.returncode == 0, done.stderr
    answer = json.loads(done.stdout)
    assert [entry["code"] for entry in answer["codes"]] == ["AGE_IMPLAUSIBLE"]


def test_rules_without_an_index_says_how_to_build_one(tmp_path):
    root = tmp_path / "bare"
    (root / ".eos").mkdir(parents=True)

    with pytest.raises(ValueError, match="eos index"):
        inspector.rules(root)


def test_a_project_with_no_codes_answers_rather_than_failing(tmp_path):
    root = tmp_path / "py"
    root.mkdir()
    (root / "main.py").write_text("print(1)\n", encoding="utf-8")
    assert _run(["init", str(root), "--no-ai"]).returncode == 0
    assert _run(["scan", str(root), "--full"]).returncode == 0

    done = _run(["rules", str(root)])

    assert done.returncode == 0, done.stderr
    assert "eos why" in done.stdout, (
        "an empty answer must point at the command that says whether anything looked"
    )
