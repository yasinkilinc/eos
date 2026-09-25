"""A complexity score is a sum of named factors, not a verdict (ADR-025).

Each level is reachable, the floors and the cap hold, a factor that reads the
project reads it or says it could not, and the printed factors add up to the
printed score -- so anyone can check the arithmetic.
"""
import subprocess
import sys
from pathlib import Path

import pytest

from core.routing import classify, score
from core.routing.types import TaskClass

REPO = Path(__file__).resolve().parents[1]
EOS = [sys.executable, str(REPO / "core" / "eos.py")]


def _score(task, **kw):
    return score.score(task, classify.classify(task), **kw)


def _weighted(factors):
    return sum(factors[name] * weight for name, weight in score.WEIGHTS)


@pytest.mark.parametrize("task, level", [
    ("fix the typo in the readme", "LOW"),
    ("add a getter for the user name", "LOW"),
    ("add pagination to the orders endpoint and update the client", "MEDIUM"),
    ("fix the crash when saving a draft", "MEDIUM"),
    ("refactor the payment adapter and extract the retry logic across 6 files", "HIGH"),
    ("design the migration of the billing schema across the codebase in 12 files", "CRITICAL"),
])
def test_every_level_is_reachable_by_a_sentence(task, level):
    assert _score(task).level == level


def test_the_printed_factors_add_up_to_the_printed_score():
    for task in ("fix the typo", "refactor the auth flow and update tests",
                 "design the service boundaries for a new billing domain"):
        result = _score(task)
        assert abs(_weighted(result.factors) - result.score) < 1e-4
        for name, _ in score.WEIGHTS:
            assert 0.0 <= result.factors[name] <= 1.0


def test_some_types_are_never_below_high():
    for kind in score.AT_LEAST_HIGH:
        result = score.score("x", TaskClass(kind, 0.9))
        assert result.level in ("HIGH", "CRITICAL"), kind


def test_a_trivial_edit_stays_low_unless_it_is_risky():
    many = [f"f{i}" for i in range(8)]
    assert score.score("fix the typo", TaskClass("trivial_edit", 0.9), files=many).level == "LOW"
    assert score._level(0.9, "trivial_edit", 0.4) == "LOW"
    assert score._level(0.9, "trivial_edit", 0.5) == "CRITICAL"


def test_uncertainty_alone_never_raises_a_level():
    sure = score.score("add a flag", TaskClass("simple_implementation", 0.95))
    unsure = score.score("add a flag maybe somehow", TaskClass("simple_implementation", 0.0))
    assert unsure.factors["uncertainty"] > sure.factors["uncertainty"]
    assert unsure.level == sure.level == "LOW"


def test_given_files_move_the_file_count_factor():
    few = _score("refactor the parser")
    many = _score("refactor the parser", files=[f"src/f{i}.py" for i in range(8)])
    assert few.factors["file_count"] < many.factors["file_count"] == 1.0
    assert many.factors["file_count_source"] == "files"


def test_a_file_count_in_the_text_is_read():
    result = _score("rename the handler in 4 java files")
    assert result.factors["file_count"] == 0.5
    assert result.factors["file_count_source"] == "text"
    assert _score("update three modules").factors["file_count_source"] == "text"


def test_without_an_index_the_dependency_factor_is_zero_and_says_so(tmp_path):
    result = _score("refactor util", files=["util.py"], project_root=tmp_path)
    assert result.factors["dependency_count"] == 0.0
    assert result.factors["dependency_count_source"] == "none"


def test_an_indexed_project_moves_the_dependency_factor(tmp_path):
    root = tmp_path / "proj"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "util.py").write_text("VALUE = 1\n", encoding="utf-8")
    for name in ("a", "b", "c", "d"):
        (root / "pkg" / f"{name}.py").write_text("import pkg.util\n", encoding="utf-8")
    for args in (["init", str(root), "--no-ai"], ["scan", str(root), "--full"], ["index", str(root)]):
        done = subprocess.run(EOS + args, capture_output=True, text=True, encoding="utf-8")
        assert done.returncode == 0, done.stderr

    result = _score("refactor the util module", files=["pkg/util.py"], project_root=root)

    assert result.factors["dependency_count"] == pytest.approx(4 / score.DEPENDENTS_FOR_FULL)
    assert result.factors["dependency_count_source"] in ("index", "graph.json")


def test_risk_words_raise_failure_risk():
    assert _score("update the billing cron").factors["failure_risk"] >= 0.4
    assert _score("update the greeting").factors["failure_risk"] == 0.0


def test_scoring_is_deterministic():
    task = "refactor the auth flow and update tests"
    assert _score(task) == _score(task)
