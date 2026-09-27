"""The shape a note must have before it is written: required sections, no
placeholder values, a scope that names real files."""
from __future__ import annotations

import re

from core.notes.store import KINDS, section_in, steps_in
from core.notes.procedures import RULES_MAX_CHARS, RULES_SECTION


# ADR-024. A lesson and a decision are only worth re-reading with all three;
# the defect schema's history is the evidence (see `_compose_body`).
_REQUIRED_SECTIONS = {
    "lesson": ("What went wrong", "What was learned", "Next time"),
    "decision": ("Why", "When", "Component"),
}


def _compose_body(kind, body, cause, solution, metric) -> str:
    """Validate the fields this kind requires and render the note body.

    A defect must carry cause, solution and metric. This is the one part of the
    earlier design worth copying verbatim: the schema that made all three
    mandatory is the only one that ever accumulated records worth re-reading,
    while every optional-field schema beside it stayed empty.
    """
    if kind not in KINDS:
        raise ValueError(f"Unknown note kind {kind!r}; expected one of: {', '.join(KINDS)}")

    if kind == "defect":
        provided = {"cause": cause, "solution": solution, "metric": metric}
        missing = [name for name, value in provided.items() if not (value or "").strip()]
        if missing:
            raise ValueError(
                "A defect note must record cause, solution and metric; "
                f"missing: {', '.join(missing)}"
            )
        sections = []
        if (body or "").strip():
            sections += [body.strip(), ""]
        sections += [
            "## Root cause", "", cause.strip(), "",
            "## Solution", "", solution.strip(), "",
            "## Metric", "", metric.strip(),
        ]
        return "\n".join(sections)

    if kind in _REQUIRED_SECTIONS:
        missing = [name for name in _REQUIRED_SECTIONS[kind] if not section_in(body or "", name)]
        if missing:
            raise ValueError(
                f"A {kind} note must have these sections, each with something under it: "
                + ", ".join(f"`## {name}`" for name in _REQUIRED_SECTIONS[kind])
                + f"; missing or empty: {', '.join(missing)}")
        return body.strip()

    if kind == "procedure":
        if not steps_in(body or ""):
            raise ValueError(
                "A procedure note must have a `## Steps` section with at least one "
                "numbered or bulleted step. Without steps it is a finding, and "
                "should be written as one.")
        rules = section_in(body or "", RULES_SECTION) or ""
        if len(rules) > RULES_MAX_CHARS:
            raise ValueError(
                f"A procedure's `## {RULES_SECTION}` section is printed whole by the task brief, "
                f"past its budget, so it is capped at {RULES_MAX_CHARS} characters; this one is "
                f"{len(rules)}. Keep the rules that must hold while the task runs and move the "
                "explanation to `## Prerequisites` or a finding.")
        return body.strip()

    if not (body or "").strip():
        raise ValueError("A finding note must have a body")
    return body.strip()


# A value that is nothing but an unsubstituted placeholder from a printed
# command: `<what is true now>`, `<TICKET>`, or a bare `...`/`…`. Anchored end
# to end on the whole stripped value, so a real body that mentions `List<T>`,
# quotes a diff, or trails off in an ellipsis mid-sentence is untouched -- the
# refusal is for a value that carries no claim at all.
_PLACEHOLDER_RE = re.compile(r"\A(?:<[^<>]*>|\.{3,}|…)\Z")


def _is_placeholder(value: str) -> bool:
    """Whether this value is an unsubstituted placeholder.

    The Stop-hook gate prints commands meant to be run, and an agent runs them
    as printed: `--body '<what is true now>'` and `--title '...' --body '...'`
    both arrive here intact. This is the same class of nothing as the empty
    and unchanged bodies already refused, and in `amend_note` it is worse than
    either -- amend re-hashes `scope_hashes`, so accepting a placeholder
    overwrites the note's real claim AND clears its stale flag, taking it
    permanently off the gate's radar on exit 0. A loud refusal naming the
    placeholder is the only safe answer.
    """
    return bool(_PLACEHOLDER_RE.match(value.strip()))


def _refuse_scope(scope: list[str] | None) -> None:
    """Every scope entry must name a real file, or the note monitors nothing.

    Two ways that failed silently, both found in the corpus rather than
    imagined. A printed command copied verbatim keeps its `--scope '<files>'`,
    and the entry was stored as the literal string with a null hash beside it:
    the note reads as scoped, `note audit` can never report it stale, and the
    injector can never match it to a touched file. And an empty entry among
    real ones (`["a", "", "b"]`) was dropped without a word, so a note ended up
    watching fewer files than its author wrote and nothing said which.

    Refused at the door, because neither is recoverable afterwards: the hashes
    are taken at write time, so a note that was never scoped correctly cannot
    be told from one whose files changed.
    """
    if not scope:
        return
    for position, entry in enumerate(scope, start=1):
        where = f"scope entry {position} of {len(scope)}"
        if not entry.strip():
            raise ValueError(
                f"The {where} is empty. An empty entry used to be dropped in "
                "silence, which left the note scoped to fewer files than it "
                "names; say the file or leave it out."
            )
        _refuse_placeholder(entry, where)


def _refuse_placeholder(value: str, what: str) -> None:
    """Raise if `value` is nothing but a placeholder, saying what to do."""
    if _is_placeholder(value):
        raise ValueError(
            f"The {what} is still the placeholder {value.strip()!r}. It was "
            "printed as an example, not as a value: replace it with the "
            f"{what} in your own words before running the command."
        )


# `_compose_body` writes these as literal `## Name` lines. A raw substring
# check against that literal is wrong in both directions: it is
# case-sensitive, so a heading written `## Root Cause` was refused as
# missing; and it is not anchored to a line, so `### Root cause` (a real,
# differently-leveled heading) happened to satisfy it only because its
# characters overlap "## Root cause" one position over -- the same substring
# would just as happily match inside a sentence that merely *mentions* the
# heading text and never states it. Anchoring to the start of a line and
# allowing any run of `#` fixes both: a real heading at any level matches for
# the right reason, prose that quotes the heading text does not.
_DEFECT_SECTIONS = ("Root cause", "Solution", "Metric")


_DEFECT_HEADING = {
    name: re.compile(rf"^#+[ \t]*{re.escape(name)}\b", re.IGNORECASE | re.MULTILINE)
    for name in _DEFECT_SECTIONS
}


def _canonical_scope(entries) -> frozenset:
    """A scope list reduced to what this system actually reads from it.

    Nothing anywhere reads a scope entry's position: `stale_notes` zips each
    entry to its own hash, `add_note` and `amend_note` hash entry by entry,
    retrieval never sorts. A repeated entry hashes twice to the same value.
    So "does this --scope re-point the note" is a question about the SET of
    entries, and comparing lists answers a different, order- and
    multiplicity-sensitive question -- which left `B,A` and `A,A,B` as
    working ways to re-hash a note while re-pointing nothing.
    """
    return frozenset(entries)
