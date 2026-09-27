"""References in an answer that cannot be right (2.x roadmap E3a).

An agent's answer cites `path:line`, `path:start-end` or a markdown link to
`path#L12`. Two mistakes are certain and cheap to see: an absolute path that
does not exist, and a line past the end of a file that does. Everything else --
a relative path found under none of the bases, which may belong to another
repository, and a path abbreviated with `...` -- is left alone: the reader of this list stops a subagent, and a
false alarm there costs more than a missed one.
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


def wrong(text: str, bases: list[str | Path]) -> list[str]:
    """`<reference> -- <why>` for each certainly wrong reference."""
    problems = []
    for path, line in references(text):
        if "/.../" in path or "..." in Path(path).parts or "…" in path:
            continue  # an abbreviated path is shorthand, not a claim about a file
        candidates = [Path(path)] if os.path.isabs(path) else [Path(base) / path for base in bases]
        existing = next((c for c in candidates if c.is_file()), None)
        if existing is None:
            # Missing only counts on this machine's own tree: `/app/...` from a
            # container log may be right where it was written.
            top = Path(path).parts[1] if os.path.isabs(path) and len(Path(path).parts) > 1 else ""
            if top and Path("/", top).exists():
                problems.append(f"{path}:{line} -- no such file")
            continue
        count = _lines(existing)
        if count is not None and line > count:
            problems.append(f"{path}:{line} -- the file has {count} lines")
    return problems
