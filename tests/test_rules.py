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
    assert codes == {"AGE_LIMIT", "AGE_IMPLAUSIBLE", "AGE_NEGATIVE"}, codes


def test_a_code_records_where_it_is_thrown_from(tmp_path):
    answer = inspector.rules(_scanned(tmp_path))

    limit = next(e for e in answer["codes"] if e["code"] == "AGE_LIMIT")

    assert limit["where"] == ["ValidateAgeCommand.execute"], limit
    assert limit["exceptions"] == ["ValidationException"], limit
    assert any(ref.endswith(".java:20") or ".java:" in ref for ref in limit["thrown_at"]), limit


def test_coverage_is_graded_not_binary(tmp_path):
    """Three states the fixture produces on purpose, because a tested/untested
    answer buries the middle one -- and on a real service the middle one is 134
    of 403 codes: the class is exercised and this refusal is not asserted."""
    answer = inspector.rules(_scanned(tmp_path))

    by_code = {entry["code"]: entry for entry in answer["codes"]}

    # The test holds it as an @InjectMocks field and asserts the string.
    assert by_code["AGE_LIMIT"]["coverage"] == "asserted", by_code["AGE_LIMIT"]
    # Same class, so the suite runs it -- but nothing checks this branch.
    assert by_code["AGE_IMPLAUSIBLE"]["coverage"] == "reachable", by_code["AGE_IMPLAUSIBLE"]
    # Thrown by a component nothing calls and no test names.
    assert by_code["AGE_NEGATIVE"]["coverage"] == "none", by_code["AGE_NEGATIVE"]
    assert answer["coverage"] == {"none": 1, "named": 0, "reachable": 1,
                                  "asserted": 1, "verified": 0}, answer
    assert answer["untested"] == 1 and answer["total"] == 3, answer


def test_reaching_a_class_is_recorded_separately_from_naming_a_code(tmp_path):
    answer = inspector.rules(_scanned(tmp_path))
    by_code = {entry["code"]: entry for entry in answer["codes"]}

    implausible = by_code["AGE_IMPLAUSIBLE"]
    assert implausible["reached_by"] and not implausible["tests"], (
        f"reaching and naming must stay distinguishable: {implausible}"
    )


def test_untested_first_so_the_gap_leads(tmp_path):
    answer = inspector.rules(_scanned(tmp_path))

    assert answer["codes"][0]["code"] == "AGE_NEGATIVE", (
        f"the least covered code must sort first: {[e['code'] for e in answer['codes']]}"
    )


def test_untested_only_filters(tmp_path):
    answer = inspector.rules(_scanned(tmp_path), untested_only=True)

    assert [entry["code"] for entry in answer["codes"]] == ["AGE_NEGATIVE"], answer


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

    assert codes == {"AGE_LIMIT", "AGE_IMPLAUSIBLE", "AGE_NEGATIVE"}, codes


def test_cli_prints_the_ladder_and_says_it_is_still_a_floor(tmp_path):
    root = _scanned(tmp_path)

    done = _run(["rules", str(root)])

    assert done.returncode == 0, done.stderr
    for state in ("asserted", "reachable", "named", "none"):
        assert state in done.stdout, f"{state} missing from:\n{done.stdout}"
    assert "Still a floor" in done.stdout, (
        f"a number this quotable must carry what it does not mean:\n{done.stdout}"
    )


def test_cli_emits_json(tmp_path):
    root = _scanned(tmp_path)

    done = _run(["rules", str(root), "--untested", "--format", "json"])

    assert done.returncode == 0, done.stderr
    answer = json.loads(done.stdout)
    assert [entry["code"] for entry in answer["codes"]] == ["AGE_NEGATIVE"]


def test_rules_without_an_index_says_how_to_build_one(tmp_path):
    root = tmp_path / "bare"
    (root / ".eos").mkdir(parents=True)

    with pytest.raises(ValueError, match="eos index"):
        inspector.rules(root)


def test_a_project_the_detector_does_not_apply_to_says_so(tmp_path):
    """Three empty answers, three different reasons. A project with no Java is
    not a project whose scan is too old, and neither is a Java project that
    raises no coded refusals."""
    root = tmp_path / "py"
    root.mkdir()
    (root / "main.py").write_text("print(1)\n", encoding="utf-8")
    assert _run(["init", str(root), "--no-ai"]).returncode == 0
    assert _run(["scan", str(root), "--full"]).returncode == 0

    done = _run(["rules", str(root)])

    assert done.returncode == 0, done.stderr
    assert "this project has none indexed" in done.stdout, done.stdout


def test_a_scan_too_old_to_have_looked_says_to_rescan(tmp_path):
    """Measured on a real service: a fresh session ran `eos rules`, got "no
    codes found", was told nothing actionable, and guessed at a rescan.
    Rebuilding the index cannot invent facts a scan never wrote."""
    import sqlite3
    from core import index

    root = _scanned(tmp_path)
    conn = sqlite3.connect(index.db_path(root))
    try:
        conn.execute("DELETE FROM fact WHERE predicate LIKE '%-code%'")
        conn.execute("DELETE FROM coverage WHERE detector LIKE 'codes@%'")
        conn.commit()
    finally:
        conn.close()

    done = _run(["rules", str(root)])

    assert done.returncode == 1
    assert "eos scan" in done.stderr, done.stderr
    assert "eos index" in done.stderr, "it must say which command does not help"


def test_a_java_project_that_raises_no_coded_refusals_is_not_an_error(tmp_path):
    root = tmp_path / "plain"
    src = root / "src" / "main" / "java" / "com" / "example"
    src.mkdir(parents=True)
    (root / "pom.xml").write_text("<project/>\n", encoding="utf-8")
    (src / "Quiet.java").write_text(
        "package com.example;\npublic class Quiet { public int two() { return 2; } }\n",
        encoding="utf-8")
    assert _run(["init", str(root), "--no-ai"]).returncode == 0
    assert _run(["scan", str(root), "--full"]).returncode == 0

    done = _run(["rules", str(root)])

    assert done.returncode == 0, done.stderr
    assert "found no behaviour codes" in done.stdout, done.stdout


def test_the_listing_is_bounded_but_the_tally_is_not(tmp_path):
    """Telemetry measured the unbounded listing at 37,874 tokens on one real
    service -- for a command the skill tells agents to run before writing a
    test. A habit that costs a third of a context window is not one anyone
    keeps."""
    root = _scanned(tmp_path)

    done = _run(["rules", str(root), "--limit", "1"])

    assert done.returncode == 0, done.stderr
    assert done.stdout.count("thrown at") == 1, done.stdout
    assert "and 2 more" in done.stdout, done.stdout
    # The four numbers are the part worth reading, and they cover every code.
    assert "3 code(s):" in done.stdout, done.stdout


def test_limit_zero_still_lists_everything(tmp_path):
    root = _scanned(tmp_path)

    done = _run(["rules", str(root), "--limit", "0"])

    assert done.returncode == 0, done.stderr
    assert done.stdout.count("thrown at") == 3, done.stdout
    assert "more, least covered first" not in done.stdout
