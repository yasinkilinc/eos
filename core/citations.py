"""References in an answer that cannot be right (2.x roadmap E3a).

An agent's answer cites `path:line`, `path:start-end` or a markdown link to
`path#L12`. Two mistakes are certain and cheap to see: an absolute path that
does not exist, and a line past the end of a file that does. Everything else --
a relative path found under none of the bases, which may belong to another
repository, and a path abbreviated with `...` -- is left alone: the reader of this list stops a subagent, and a
false alarm there costs more than a missed one.

Depth 1 (`misquoted`, `eos cite`): a quote placed right after a reference --
`` path:12 `code` `` or a fenced block after a line ending in the reference --
must be found within two lines of the range it cites.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

# A file name with an extension, then a line (or a range, whose end is checked).
# The lookbehind keeps a path's start out: `~/`, `<repo>/`, `${ROOT}/`, `C:/` are
# not absolute paths on this machine.
_PLAIN = re.compile(r"(?<![\w./~$}>:@-])((?:/|\.{0,2}/)?[\w.-]+(?:/[\w.-]+)*\.[A-Za-z]\w{0,5}):(\d+)(?:-(\d+))?(?!\d)")
_LINK = re.compile(r"\]\(([^)\s#]+)#L(\d+)(?:-L(\d+))?\)")
MAX_BYTES = 8 * 1024 * 1024


def references(text: str) -> list[tuple[str, int]]:
    """(path, last line cited), in order of appearance, without repeats."""
    found = []
    for match in sorted(list(_PLAIN.finditer(text)) + list(_LINK.finditer(text)), key=lambda m: m.start()):
        path, start, end = match.group(1), int(match.group(2)), match.group(3)
        entry = (path, int(end) if end else start)
        if entry not in found:
            found.append(entry)
    return found


def _lines(path: Path) -> int | None:
    try:
        if path.stat().st_size > MAX_BYTES:
            return None
        data = path.read_bytes()
    except OSError:
        return None
    return data.count(b"\n") + (0 if data.endswith(b"\n") or not data else 1)


def _here(path: Path, bases: list[str | Path]) -> bool:
    roots = [Path(base).expanduser().resolve() for base in bases] + [Path.home()]
    return path.parent.is_dir() or any(path.is_relative_to(root) for root in roots)


def wrong(text: str, bases: list[str | Path]) -> list[str]:
    """`<reference> -- <why>` for each certainly wrong reference."""
    problems = []
    for path, line in references(text):
        if "/.../" in path or "..." in Path(path).parts or "…" in path:
            continue  # an abbreviated path is shorthand, not a claim about a file
        candidates = [Path(path)] if os.path.isabs(path) else [Path(base) / path for base in bases]
        existing = next((c for c in candidates if c.is_file()), None)
        if existing is None:
            # Missing only counts where this machine would have the file: under a
            # project base or home, or in a directory that exists here. `/app/...`
            # or `/home/runner/...` from a container or CI log may be right where
            # it was written.
            if os.path.isabs(path) and _here(Path(path), bases):
                problems.append(f"{path}:{line} -- no such file")
            continue
        count = _lines(existing)
        if count is not None and line > count:
            problems.append(f"{path}:{line} -- the file has {count} lines")
    return problems


# --- depth 1: quotes against the lines they cite ------------------------------------------

QUOTE_MIN = 10
SLACK_LINES = 2
_INLINE = re.compile(r"[\s:,\u2013\u2014-]{0,4}`([^`\n]{%d,})`" % QUOTE_MIN)
_FENCE = re.compile(r"[\s:.]*\n```[^\n]*\n(?P<code>.*?)(?:\n```|\Z)", re.S)


def _resolve(path: str, bases: list[str | Path]) -> Path | None:
    candidates = [Path(path)] if os.path.isabs(path) else [Path(base) / path for base in bases]
    return next((c for c in candidates if c.is_file()), None)


def _squash(text: str) -> str:
    return " ".join(text.split())


def _quote(text: str, start: int, end: int) -> str | None:
    line_start = text.rfind("\n", 0, start) + 1
    if text.count("`", line_start, start) % 2:
        # The reference is itself inside a code span: what follows its closing
        # backtick is prose, and a quote can only start after that.
        if text[end:end + 1] != "`":
            return None
        end += 1
    inline = _INLINE.match(text, end)
    if inline:
        return inline.group(1).strip()
    fence = _FENCE.match(text, end)
    if fence:
        return next((line.strip() for line in fence.group("code").splitlines() if line.strip()), None)
    return None


def misquoted(text: str, bases: list[str | Path]) -> list[str]:
    """`<reference> -- `<quote>` is not at those lines` for each quote not found
    within SLACK_LINES of the range it cites."""
    problems = []
    for match in sorted(list(_PLAIN.finditer(text)) + list(_LINK.finditer(text)), key=lambda m: m.start()):
        path, start = match.group(1), int(match.group(2))
        end = int(match.group(3)) if match.group(3) else start
        quote = _quote(text, match.start(), match.end())
        if (not quote or "..." in quote or "\u2026" in quote or references(quote)
                or ("/" in quote and not any(c.isspace() for c in quote))):
            continue  # an abbreviation, another reference or a path is not a quote
        existing = _resolve(path, bases)
        if existing is None or (_lines(existing) or 0) < start:
            continue  # a missing file or line is `wrong`'s to report
        try:
            lines = existing.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        window = _squash("\n".join(lines[max(start - 1 - SLACK_LINES, 0):end + SLACK_LINES]))
        if _squash(quote) not in window:
            reference = f"{path}:{start}" + (f"-{end}" if end != start else "")
            problems.append(f"{reference} -- `{quote}` is not at those lines")
    return problems


def check(text: str, bases: list[str | Path]) -> list[str]:
    """Everything certainly wrong in an answer: references, then quotes."""
    found = wrong(text, bases)
    return found + [p for p in misquoted(text, bases) if p not in found]
