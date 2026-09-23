"""Measure whether note search finds the note that answers a question.

The test suite asserts that search returns *something* for a query built out
of the note's own words, which is the one case that was never in doubt. What
has actually failed here is the opposite case: a question asked in somebody
else's words, where the note exists and is not returned. That is not a bug a
unit test can be written against, because there is no correct answer to assert
without a corpus and a human saying which note answers what.

So this measures instead of asserting. A golden file pairs a real question
with the note that answers it; running it reports recall@1/3/5 and MRR over
the whole set, and names every query whose answer was not first. The numbers
are only comparable against the same corpus and the same file, which is the
point: they are for telling whether a change to ranking helped, not for
publishing.

Two failure modes are deliberately kept apart:

  a MISS      the corpus holds the answer and search did not rank it -- a
              result, the thing being measured
  a BROKEN    the golden file names a note that does not exist any more --
    LINE      an error, because a superseded or renamed note silently
              becomes a permanent miss and drags the score down forever

`evaluate` reports both; the caller decides that a broken line is fatal and a
low score is not.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from core import notes

# Deep enough to see a near-miss without paying for a long list: a note ranked
# 30th and a note absent are the same answer to the person who typed the query.
DEFAULT_DEPTH = 10
RECALL_AT = (1, 3, 5)


@dataclass
class GoldenEntry:
    query: str
    expected: str
    line: int


@dataclass
class QueryResult:
    query: str
    expected: str
    rank: int | None
    top: list[str] = field(default_factory=list)


class GoldenError(ValueError):
    """The golden file cannot be read as one, which is not a low score."""


def parse_golden(path: str | Path) -> list[GoldenEntry]:
    """`query<TAB>expected-note-filename` per line; `#` comments and blanks out.

    A tab, not whitespace: questions have spaces in them and note filenames are
    long, so any other separator makes the file unreadable to the person
    maintaining it.
    """
    entries: list[GoldenEntry] = []
    text = Path(path).read_text(encoding="utf-8")
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if "\t" not in line:
            raise GoldenError(
                f"{path}:{number}: no tab; a line is '<query><TAB><note filename>'")
        query, expected = line.split("\t", 1)
        query, expected = query.strip(), expected.strip()
        if not query or not expected:
            raise GoldenError(f"{path}:{number}: both a query and a note filename are required")
        entries.append(GoldenEntry(query=query, expected=expected, line=number))
    if not entries:
        raise GoldenError(f"{path}: no queries")
    return entries


def evaluate(project_root: str | Path, entries: list[GoldenEntry],
             depth: int = DEFAULT_DEPTH) -> dict:
    """Recall@k and MRR for `entries` against this project's notes."""
    present = {note.path.name for note in notes.load_notes(project_root)}
    broken = [entry for entry in entries if entry.expected not in present]
    scored = [entry for entry in entries if entry.expected in present]

    results: list[QueryResult] = []
    for entry in scored:
        ranked = notes.search_notes(project_root, entry.query, limit=depth)
        names = [note.path.name for note in ranked]
        rank = names.index(entry.expected) + 1 if entry.expected in names else None
        results.append(QueryResult(entry.query, entry.expected, rank, names[:3]))

    total = len(results)
    recall = {
        k: round(sum(1 for r in results if r.rank and r.rank <= k) / total, 4) if total else 0.0
        for k in RECALL_AT
    }
    mrr = round(sum(1 / r.rank for r in results if r.rank) / total, 4) if total else 0.0
    return {
        "corpus": len(present),
        "queries": total,
        "depth": depth,
        "recall": recall,
        "mrr": mrr,
        "results": [
            {"query": r.query, "expected": r.expected, "rank": r.rank, "top": r.top}
            for r in results
        ],
        "broken": [
            {"line": entry.line, "query": entry.query, "expected": entry.expected}
            for entry in broken
        ],
    }


def render(report: dict) -> str:
    """The digest a person reads: the score, then only what went wrong."""
    lines = [
        f"{report['queries']} queries over {report['corpus']} notes, depth {report['depth']}",
        "  recall@1 {r1}   recall@3 {r3}   recall@5 {r5}   MRR {mrr}".format(
            r1=report["recall"][1], r3=report["recall"][3],
            r5=report["recall"][5], mrr=report["mrr"]),
    ]
    missed = [r for r in report["results"] if r["rank"] != 1]
    if missed:
        lines.append("")
        lines.append(f"Not first ({len(missed)}):")
        for r in missed:
            where = f"rank {r['rank']}" if r["rank"] else f"not in top {report['depth']}"
            lines.append(f"  [{where}] {r['query']}")
            lines.append(f"      wanted: {r['expected']}")
            lines.append(f"      got:    {r['top'][0] if r['top'] else '<nothing>'}")
    if report["broken"]:
        lines.append("")
        lines.append(f"Broken golden lines ({len(report['broken'])}) -- these are not misses, "
                     "the note named does not exist:")
        for entry in report["broken"]:
            lines.append(f"  line {entry['line']}: {entry['expected']}")
    return "\n".join(lines)
