"""A long text narrowed to the lines a task's terms hit (2.x roadmap C3).

Where a whole file or a whole note would otherwise enter an agent's context,
the part that matters is usually a few spans around the lines that name what
the task is about. This finds them the way `grep -n -C` would, deterministically
and with the standard library only (ADR-010): the idea is RAGFlow's
`narrow_by_terms` (research report §3.3), its numbers kept -- whole lines, about
600 characters of context on each side of a hit, 1,200 per text by default.

  - A term matches as a word. `_` separates words, so `refund` finds
    `refund_payment`; `log` does not find `catalog`. When no term matches as a
    word, a term of four or more characters is looked for inside words
    (`retry` in `retrying`), and when that finds nothing either the caller
    gets None and keeps whatever it did before.
  - A term on more than a quarter of the lines says nothing about where to
    look; it is left out while another term is more selective.
  - A markdown table or a fenced block with a hit comes back whole or not at
    all: a row means nothing without its header, and code cut mid-block reads
    as code that is not there.
  - When the spans do not all fit, the ones hitting the most distinct terms
    win; the rest are counted, never dropped silently. Spans come back in file
    order, each under an `@@ L<first>-L<last>` line that a citation can use.
"""
from __future__ import annotations

import dataclasses
import re

CONTEXT_CHARS = 600
LIMIT_CHARS = 1200
# A term on more lines than this share of the non-blank ones is not selective.
UBIQUITOUS_SHARE = 0.25
MIN_STEM = 4

_FENCE = re.compile(r"^\s*(`{3,}|~{3,})")


@dataclasses.dataclass(frozen=True)
class Narrowed:
    text: str
    # (first, last) line numbers, 1-based and inclusive, in file order.
    spans: list[tuple[int, int]]
    # The terms that decided the spans, in the order they were given.
    terms: list[str]
    # Spans found and left out for the limit.
    omitted: int


def narrow_by_terms(text: str, terms, *, context: int = CONTEXT_CHARS,
                    limit: int = LIMIT_CHARS) -> Narrowed | None:
    """The spans of `text` around the lines `terms` hit, within `limit` characters."""
    wanted = list(dict.fromkeys(t.strip().casefold() for t in terms if t and t.strip()))
    lines = text.splitlines()
    if not wanted or not lines:
        return None
    hits = _hits(lines, wanted, whole_words=True) or _hits(
        lines, [t for t in wanted if len(t) >= MIN_STEM], whole_words=False)
    if not hits:
        return None
    nonblank = sum(1 for line in lines if line.strip())
    ceiling = max(3, int(nonblank * UBIQUITOUS_SHARE))
    selective = {t: rows for t, rows in hits.items() if len(rows) <= ceiling}
    chosen = selective or hits
    by_line: dict[int, set[str]] = {}
    for term, rows in chosen.items():
        for row in rows:
            by_line.setdefault(row, set()).add(term)

    blocks = _blocks(lines)
    spans = _merge([(*_span(lines, row, blocks, context), by_line[row], row in blocks, row)
                    for row in sorted(by_line)])
    picked, omitted, used = [], 0, 0
    for first, last, found, whole, anchor in sorted(spans, key=lambda s: (-len(s[2]), s[0])):
        piece = _render(lines, first, last)
        if used + len(piece) > limit and not whole:
            first, last = _shrink(lines, first, last, anchor, limit - used)
            piece = _render(lines, first, last) if first is not None else ""
            if not piece and not picked:
                header = f"@@ L{anchor + 1}-L{anchor + 1}\n"
                room = limit - used - len(header)
                if room > 0:
                    first = last = anchor
                    piece = header + lines[anchor][:room]
        if not piece or used + len(piece) > limit:
            omitted += 1
            continue
        picked.append((first, last, piece, found))
        used += len(piece)
    if not picked:
        return None
    picked.sort()
    decided = set().union(*(found for *_, found in picked))
    return Narrowed(
        text="".join(piece for _, _, piece, _ in picked),
        spans=[(first + 1, last + 1) for first, last, _, _ in picked],
        terms=[t for t in wanted if t in decided],
        omitted=omitted,
    )


def _hits(lines: list[str], terms: list[str], *, whole_words: bool) -> dict[str, list[int]]:
    found = {}
    for term in terms:
        escaped = re.escape(term)
        pattern = re.compile(rf"(?<![^\W_]){escaped}(?![^\W_])" if whole_words else escaped, re.I)
        rows = [i for i, line in enumerate(lines) if pattern.search(line)]
        if rows:
            found[term] = rows
    return found


def _blocks(lines: list[str]) -> dict[int, tuple[int, int]]:
    """Line index -> (first, last) of the table or fenced block it sits in."""
    owner: dict[int, tuple[int, int]] = {}
    i = 0
    while i < len(lines):
        fence = _FENCE.match(lines[i])
        if fence:
            marker = fence.group(1)
            end = next((j for j in range(i + 1, len(lines))
                        if lines[j].strip().startswith(marker[0] * len(marker))), None)
            if end is None:      # an unclosed fence is prose
                i += 1
                continue
        elif lines[i].lstrip().startswith("|"):
            end = i
            while end + 1 < len(lines) and lines[end + 1].lstrip().startswith("|"):
                end += 1
        else:
            i += 1
            continue
        for j in range(i, end + 1):
            owner[j] = (i, end)
        i = end + 1
    return owner


def _span(lines: list[str], row: int, blocks: dict, context: int) -> tuple[int, int]:
    if row in blocks:
        return blocks[row]
    first, budget = row, context
    while first > 0 and budget - len(lines[first - 1]) - 1 >= 0:
        first -= 1
        budget -= len(lines[first]) + 1
    last, budget = row, context
    while last + 1 < len(lines) and budget - len(lines[last + 1]) - 1 >= 0:
        last += 1
        budget -= len(lines[last]) + 1
    return first, last


def _merge(spans: list[tuple]) -> list[tuple]:
    """Spans that overlap or touch become one, anchored at its first hit."""
    merged: list[list] = []
    for first, last, found, whole, row in spans:
        if merged and first <= merged[-1][1] + 1:
            merged[-1][1] = max(merged[-1][1], last)
            merged[-1][2] = merged[-1][2] | found
            merged[-1][3] = merged[-1][3] or whole
        else:
            merged.append([first, last, set(found), whole, row])
    return [tuple(span) for span in merged]


def _render(lines: list[str], first: int, last: int) -> str:
    return f"@@ L{first + 1}-L{last + 1}\n" + "".join(line + "\n" for line in lines[first:last + 1])


def _shrink(lines: list[str], first: int, last: int, anchor: int, room: int):
    """The widest whole-line span around `anchor` inside `first..last` that fits `room`."""
    lo = hi = anchor
    if len(_render(lines, lo, hi)) > room:
        return None, None
    while True:
        grew = False
        for lo2, hi2 in ((lo - 1, hi), (lo, hi + 1)):
            if first <= lo2 and hi2 <= last and len(_render(lines, lo2, hi2)) <= room:
                lo, hi, grew = lo2, hi2, True
        if not grew:
            return lo, hi
