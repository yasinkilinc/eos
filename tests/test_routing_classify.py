"""A task's type comes from its words, the same way every time (ADR-025).

One plain sentence per type proves the tables and rules reach every type.
The rest prove the properties a keyword classifier elsewhere got wrong: short
tokens are word-anchored, ties break the same way twice, and a project's own
vocabulary reaches the tables without a code change.
"""
import time

import pytest

from core.routing import classify
from core.routing.types import TASK_TYPES


SENTENCES = [
    ("fix the typo in the readme", "trivial_edit"),
    ("add a getter for the user name", "simple_implementation"),
    ("implement pagination for the orders endpoint and add caching", "normal_implementation"),
    ("fix the crash when saving a draft", "debugging"),
    ("refactor the auth flow and update tests", "refactoring"),
    ("review this pull request for security issues", "code_review"),
    ("write unit tests for the date parser", "test_generation"),
    ("investigate why the nightly job is slow", "investigation"),
    ("design the service boundaries for a new billing domain", "architecture"),
    ("plan the milestones for the migration project", "planning"),
    ("find the race condition in the distributed lock algorithm", "complex_reasoning"),
    ("rename the logger field across the codebase", "repository_wide_change"),
]


@pytest.mark.parametrize("task, expected", SENTENCES)
def test_every_type_is_reached_by_a_plain_sentence(task, expected):
    assert classify.classify(task).type == expected


def test_the_sentences_above_cover_the_whole_taxonomy():
    covered = {expected for _, expected in SENTENCES}
    assert covered == set(TASK_TYPES)


def test_a_short_token_never_matches_inside_a_longer_word():
    assert not classify.matches("record the decision", "ci")
    assert classify.matches("set up ci for the repo", "ci")
    assert not classify.matches("fix the address field", "add")
    assert "ci" not in classify.classify("record the decision in the log").matched


def test_a_token_reaches_its_plain_inflections():
    assert classify.matches("the build fails nightly", "fail")
    assert classify.matches("add unit tests", "unit test")
    assert classify.matches("renamed the field", "rename")


def test_a_failure_with_a_change_verb_is_debugging_and_without_one_is_investigation():
    assert classify.classify("fix the crash on login").type == "debugging"
    assert classify.classify("the login page throws an exception").type == "investigation"


def test_a_question_is_an_investigation_even_with_failure_words():
    assert classify.classify("why does the build fail on ci").type == "investigation"


def test_trivial_signals_lose_to_anything_substantial():
    assert classify.classify("fix the typo").type == "trivial_edit"
    assert classify.classify("fix the typo and add a test for it").type == "test_generation"


def test_a_long_or_compound_implementation_is_normal():
    assert classify.classify("add a flag").type == "simple_implementation"
    assert classify.classify("add a flag and update the docs").type == "normal_implementation"
    long = "add a retry policy to the outbound client that backs off exponentially and caps at five tries"
    assert classify.classify(long).type == "normal_implementation"


def test_a_tie_breaks_by_taxonomy_order_every_time():
    # debugging `debug` 2.0 and test_generation `coverage` 2.0 tie; debugging comes first.
    for _ in range(5):
        assert classify.classify("debug the coverage").type == "debugging"


def test_no_match_at_all_is_a_normal_implementation_with_low_confidence():
    result = classify.classify("quarterly synergy")
    assert result.type == "normal_implementation"
    assert result.confidence == classify.NO_MATCH_CONFIDENCE
    assert result.matched == ()


def test_the_same_text_classifies_identically_twice():
    task = "refactor the payment adapter and extract the retry logic"
    assert classify.classify(task) == classify.classify(task)


def test_confidence_is_bounded():
    for task, _ in SENTENCES:
        assert 0.0 < classify.classify(task).confidence <= classify.MAX_CONFIDENCE


def test_config_keywords_reach_the_classifier():
    task = "login sayfasında hata var"
    assert classify.classify(task).type != "debugging"
    assert classify.classify(task, {"debugging": ("hata",)}).type == "debugging"


def test_a_long_paste_still_classifies_quickly():
    paste = " ".join(["the service returns an error when the cache is cold and the retry fails"] * 7)
    classify.classify(paste)  # patterns compile once
    started = time.perf_counter()
    classify.classify(paste)
    assert (time.perf_counter() - started) < 0.005
