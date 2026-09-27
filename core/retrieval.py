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

import math
import time
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
    hits: int = 0
    ms: float = 0.0


class GoldenError(ValueError):
    """The golden file cannot be read as one, which is not a low score."""


def parse_golden(path: str | Path) -> list[GoldenEntry]:
    """`query<TAB>expected-note-filename` per line; `#` comments and blanks out.

    `a.md|b.md` accepts either note: a question two notes answer equally (a
    procedure later written from a section) is not a miss when search ranks
    the other one first. Which notes are equal is a person's labelling call.

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
    """Recall@k, MRR@depth, nDCG@depth, zero-hit count and search latency.

    Every scored query is in every denominator: a query search returned
    nothing for is a zero, not an absence (2.x roadmap F6).
    """
    present = {note.path.name for note in notes.load_notes(project_root)}

    def accepted(entry: GoldenEntry) -> list[str]:
        return [name.strip() for name in entry.expected.split("|") if name.strip()]

    broken = [entry for entry in entries if not any(name in present for name in accepted(entry))]
    scored = [entry for entry in entries if any(name in present for name in accepted(entry))]

    results: list[QueryResult] = []
    for entry in scored:
        started = time.perf_counter()
        ranked = notes.search_notes(project_root, entry.query, limit=depth)
        took = (time.perf_counter() - started) * 1000
        names = [note.path.name for note in ranked]
        wanted = set(accepted(entry))
        rank = next((index + 1 for index, name in enumerate(names) if name in wanted), None)
        results.append(QueryResult(entry.query, entry.expected, rank, names[:3], len(names), took))

    total = len(results)
    recall = {
        k: round(sum(1 for r in results if r.rank and r.rank <= k) / total, 4) if total else 0.0
        for k in RECALL_AT
    }
    mrr = round(sum(1 / r.rank for r in results if r.rank) / total, 4) if total else 0.0
    # One relevant note per query: DCG is 1/log2(rank+1) and the ideal DCG is 1.
    ndcg = round(sum(1 / math.log2(r.rank + 1) for r in results if r.rank) / total, 4) if total else 0.0
    return {
        "corpus": len(present),
        "queries": total,
        "depth": depth,
        "recall": recall,
        "mrr": mrr,
        "ndcg": ndcg,
        "zero_hit": sum(1 for r in results if r.hits == 0),
        "latency_ms": _percentiles([r.ms for r in results]),
        "results": [
            {"query": r.query, "expected": r.expected, "rank": r.rank, "top": r.top}
            for r in results
        ],
        "broken": [
            {"line": entry.line, "query": entry.query, "expected": entry.expected}
            for entry in broken
        ],
    }


def _percentiles(values: list[float]) -> dict:
    ordered = sorted(values)
    if not ordered:
        return {"p50": 0.0, "p90": 0.0}
    pick = lambda share: ordered[min(len(ordered) - 1, int(share * len(ordered)))]
    return {"p50": round(pick(0.5), 1), "p90": round(pick(0.9), 1)}


def _worst_first(result: dict) -> tuple:
    return (0 if result["rank"] is None else 1, -(result["rank"] or 0))


def render(report: dict) -> str:
    """The digest a person reads: the score, then only what went wrong."""
    lines = [
        f"{report['queries']} queries over {report['corpus']} notes, depth {report['depth']}",
        "  recall@1 {r1}   recall@3 {r3}   recall@5 {r5}   MRR@{d} {mrr}   nDCG@{d} {ndcg}".format(
            r1=report["recall"][1], r3=report["recall"][3], r5=report["recall"][5],
            d=report["depth"], mrr=report["mrr"], ndcg=report.get("ndcg", 0.0)),
        "  zero-hit {zero}   latency p50 {p50} ms   p90 {p90} ms".format(
            zero=report.get("zero_hit", 0), **report.get("latency_ms", {"p50": 0.0, "p90": 0.0})),
    ]
    missed = sorted((r for r in report["results"] if r["rank"] != 1), key=_worst_first)
    if missed:
        lines.append("")
        lines.append(f"Not first ({len(missed)}), worst first:")
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
