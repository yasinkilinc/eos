"""What a printed number is (2.x roadmap L4).

  measured    observed, or exact arithmetic on what was observed: a count, a
              duration, a token count the harness reported
  derived     computed through an estimate -- tokens from characters, a
              residency attributed to a source -- printed with `~`
  unmeasured  nobody looked: None in JSON, `—` in a report, `?` in a brief,
              never 0, because 0 is a claim that something was looked at and
              found empty

A report's JSON carries `provenance`, a map from a field (dotted, `[]` for list
items) to "measured" or "derived"; an absent value is None whatever its kind.
"""
from __future__ import annotations

MEASURED = "measured"
DERIVED = "derived"
UNMEASURED = "unmeasured"

NOT_MEASURED = "—"
NOT_MEASURED_BRIEF = "?"


def show(value, kind: str = MEASURED, *, spec: str = ",", brief: bool = False) -> str:
    """`value` formatted with `spec`, `~` in front when derived; `—` (`?` in a
    brief) when there is no value."""
    if value is None:
        return NOT_MEASURED_BRIEF if brief else NOT_MEASURED
    text = format(value, spec)
    return f"~{text}" if kind == DERIVED else text
