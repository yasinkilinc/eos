"""What kind of task this is, from its words alone (ADR-025).

Twelve types, weighted keyword tables, four ordered rules. No model, no
embedding: the router has to be cheaper than the decision it saves, and a
classification nobody can explain is one nobody can correct.

How a match is made matters more than the tables. A single token is anchored
on word boundaries and may carry a plain inflection (`s`, `es`, `ed`, `ing`,
`d`), so `test` reaches `tests` and `ci` never matches inside `decision` --
the defect a keyword router elsewhere shipped with (`cd` matching `decide`).
A phrase matches as written, bounded at both ends, and may carry the same
inflection on its last word (`unit tests`). Each distinct token counts
once, however often it repeats: a pasted log that says `error` forty times is
not forty times more of a bug.

The type with the highest total wins; ties go to the earlier type in
`TASK_TYPES`. Then four rules, in order, override the sum where the sum is
known to mislead:

1. A repository-wide phrase makes it a repository-wide change, whatever else
   the text says.
2. When failure vocabulary wins, a change verb (`fix`, `resolve`, `repair`)
   makes it debugging and its absence makes it investigation. A text that
   opens with a question (`why`, `where`, `what causes`, `investigate`,
   `find out`) is an investigation.
3. Trivial signals (`typo`, `rename`, `bump`, ...) win only when no other
   table scored above 1.0: "fix the typo" is trivial, "fix the typo and add
   a test for it" is not.
4. An implementation is simple when it names one change and is short: one
   change verb, no second verb joined by `and`, at most twelve words.
   Otherwise it is a normal implementation.
"""
from __future__ import annotations

import re
from functools import lru_cache

from core.routing.types import TASK_TYPES, TaskClass, normalise

# (token, weight). A phrase is any token containing a space or a hyphen.
TABLES: dict[str, tuple[tuple[str, float], ...]] = {
    "trivial_edit": (
        ("typo", 2.0), ("spelling", 2.0), ("whitespace", 2.0), ("indentation", 1.5),
        ("docstring", 1.5), ("readme", 1.5), ("rename", 1.5), ("bump", 1.5),
        ("comment", 1.0), ("version", 1.0), ("format", 1.0), ("formatting", 1.0),
        ("changelog", 1.0), ("log message", 1.0),
    ),
    # Rule 4 decides between the two implementation types; this table only
    # has to be able to win the sum.
    "simple_implementation": (),
    "normal_implementation": (
        ("implement", 1.0), ("add", 1.0), ("create", 1.0), ("build", 1.0),
        ("feature", 1.0), ("support", 0.5), ("integrate", 1.0), ("introduce", 1.0),
        ("extend", 0.5), ("endpoint", 1.0), ("wire", 0.5), ("update", 0.5), ("write", 0.5),
        ("ci", 1.0), ("pipeline", 1.0),
    ),
    "debugging": (
        ("debug", 2.0), ("debugging", 2.0), ("bug", 1.5), ("crash", 1.5),
        ("stack trace", 1.5), ("not working", 1.5), ("null pointer", 1.5),
        ("error", 1.0), ("exception", 1.0), ("broken", 1.0), ("fail", 1.0),
        ("failing", 1.0), ("failure", 1.0), ("regression", 1.0), ("repair", 1.0),
        ("fix", 0.5), ("resolve", 0.5),
    ),
    "refactoring": (
        ("refactor", 2.0), ("refactoring", 2.0), ("restructure", 2.0), ("reorganize", 1.5),
        ("reorganise", 1.5), ("decouple", 1.5), ("deduplicate", 1.5), ("clean up", 1.0),
        ("cleanup", 1.0), ("simplify", 1.0), ("extract", 1.0), ("modernize", 1.0),
        ("split", 0.5), ("move", 0.5),
    ),
    "code_review": (
        ("review", 2.0), ("code review", 2.0), ("critique", 1.5), ("second opinion", 1.5),
        ("audit", 1.5), ("pull request", 1.0), ("pr", 1.0), ("look over", 1.0),
    ),
    "test_generation": (
        ("unit test", 2.0), ("integration test", 2.0), ("coverage", 2.0), ("test", 1.5),
        ("testing", 1.5), ("test case", 1.5), ("tdd", 1.5), ("spec", 1.0), ("assert", 1.0),
    ),
    "investigation": (
        ("investigate", 2.0), ("investigation", 2.0), ("root cause", 2.0), ("why", 1.5),
        ("find out", 1.5), ("what causes", 1.5), ("how does", 1.5), ("look into", 1.5),
        ("diagnose", 1.5), ("research", 1.5), ("analyze", 1.0), ("analyse", 1.0),
        ("analysis", 1.0), ("explore", 1.0), ("understand", 1.0), ("where is", 1.0),
        ("trace", 1.0),
    ),
    "architecture": (
        ("architecture", 2.5), ("system design", 2.5), ("architect", 2.0),
        ("service boundaries", 2.0), ("design", 1.5), ("microservice", 1.5),
        ("data model", 1.5), ("domain model", 1.5), ("scalability", 1.5),
        ("event-driven", 1.5), ("adr", 1.5), ("boundary", 1.0), ("boundaries", 1.0),
        ("schema", 1.0),
    ),
    "planning": (
        ("plan", 2.0), ("planning", 2.0), ("roadmap", 2.0), ("estimate", 1.5),
        ("breakdown", 1.5), ("break down", 1.5), ("milestone", 1.5), ("prioritize", 1.5),
        ("prioritise", 1.5), ("strategy", 1.0), ("step by step", 1.0),
    ),
    "complex_reasoning": (
        ("race condition", 2.0), ("deadlock", 2.0), ("consensus", 2.0),
        ("complexity analysis", 2.0), ("algorithm", 1.5), ("prove", 1.5), ("proof", 1.5),
        ("trade-off", 1.5), ("tradeoff", 1.5), ("concurrency", 1.5), ("distributed", 1.5),
        ("concurrent", 1.0), ("optimize", 1.0), ("optimise", 1.0), ("optimization", 1.0),
        ("formal", 1.0),
    ),
    "repository_wide_change": (
        ("across the repo", 3.0), ("across the codebase", 3.0), ("whole codebase", 3.0),
        ("entire codebase", 3.0), ("every module", 3.0), ("all services", 3.0),
        ("repo-wide", 3.0), ("repository-wide", 3.0), ("codebase-wide", 3.0),
        ("all files", 2.0), ("everywhere", 2.0),
    ),
}

REPOSITORY_WIDE = ("across the repo", "across the codebase", "every module", "whole codebase",
                   "entire codebase", "all services", "everywhere", "repo-wide",
                   "repository-wide", "codebase-wide")
FAILURE_WORDS = ("fail", "failing", "failure", "error", "exception", "stack trace", "crash",
                 "broken", "bug", "regression", "not working")
CHANGE_VERBS = ("fix", "resolve", "repair")
QUESTION_OPENERS = ("why", "where", "what causes", "investigate", "find out", "how does",
                    "what is causing")
IMPLEMENTATION_VERBS = ("implement", "add", "create", "build", "write", "update", "support",
                        "integrate", "introduce", "extend", "wire", "make")
SIMPLE_MAX_WORDS = 12

NO_MATCH_CONFIDENCE = 0.30
RULE_CONFIDENCE = 0.80
MAX_CONFIDENCE = 0.95

_INFLECTION = r"(?:s|es|ed|ing|d)?"


@lru_cache(maxsize=512)
def _pattern(token: str) -> re.Pattern:
    escaped = re.escape(token)
    if " " in token or "-" in token:
        return re.compile(rf"(?<![\w-]){escaped}{_INFLECTION}(?![\w-])")
    return re.compile(rf"\b{escaped}{_INFLECTION}\b")


def matches(text: str, token: str) -> bool:
    return _pattern(token).search(text) is not None


def _found(text: str, tokens) -> tuple[str, ...]:
    return tuple(token for token in tokens if matches(text, token))


def classify(task: str, keywords: dict | None = None) -> TaskClass:
    """The task's type, how sure the tables are, and the words that decided it."""
    text = normalise(task)
    scores: dict[str, float] = {}
    hits: dict[str, tuple[str, ...]] = {}
    for task_type in TASK_TYPES:
        table = TABLES[task_type] + tuple((word, 1.0) for word in (keywords or {}).get(task_type, ()))
        seen: dict[str, float] = {}
        for token, weight in table:
            if token not in seen and matches(text, token):
                seen[token] = weight
        scores[task_type] = sum(seen.values())
        hits[task_type] = tuple(seen)

    ranked = sorted(TASK_TYPES, key=lambda t: (-scores[t], TASK_TYPES.index(t)))
    best, second = ranked[0], ranked[1]
    if scores[best] <= 0:
        return TaskClass("normal_implementation", NO_MATCH_CONFIDENCE, ())
    confidence = min(MAX_CONFIDENCE, scores[best] / (scores[best] + scores[second] + 1.0))
    chosen = TaskClass(best, round(confidence, 4), hits[best])

    # Rule 1 -- repository-wide.
    wide = _found(text, REPOSITORY_WIDE)
    if wide:
        return TaskClass("repository_wide_change", _rule_confidence(confidence), wide)

    # Rule 2 -- a question is an investigation; failure vocabulary splits on the verb.
    opener = next((w for w in QUESTION_OPENERS if re.match(rf"{re.escape(w)}\b", text)), None)
    if opener:
        return TaskClass("investigation", _rule_confidence(confidence), (opener,))
    if chosen.type in ("debugging", "investigation"):
        failures = _found(text, FAILURE_WORDS)
        if failures:
            verbs = _found(text, CHANGE_VERBS)
            kind = "debugging" if verbs else "investigation"
            return TaskClass(kind, _rule_confidence(confidence), verbs + failures)

    # Rule 3 -- trivial only when nothing else is really there.
    if chosen.type == "trivial_edit":
        others = [t for t in ranked if t != "trivial_edit" and scores[t] > 1.0]
        if others:
            other = others[0]
            chosen = TaskClass(other, round(min(MAX_CONFIDENCE, scores[other] /
                                                (scores[other] + scores["trivial_edit"] + 1.0)), 4),
                               hits[other])

    # Rule 4 -- one short change is simple.
    if chosen.type in ("simple_implementation", "normal_implementation"):
        kind = "simple_implementation" if _names_one_change(text) else "normal_implementation"
        return TaskClass(kind, chosen.confidence, chosen.matched)
    return chosen


def _names_one_change(text: str) -> bool:
    verbs = _found(text, IMPLEMENTATION_VERBS)
    if len(verbs) != 1:
        return False
    if re.search(rf"\band\s+(?:{'|'.join(map(re.escape, IMPLEMENTATION_VERBS))}){_INFLECTION}\b", text):
        return False
    return len(text.split()) <= SIMPLE_MAX_WORDS


def _rule_confidence(scored: float) -> float:
    return round(min(MAX_CONFIDENCE, max(RULE_CONFIDENCE, scored)), 4)
