"""A file's structure in a few lines: headings and definitions with line numbers.

Used where a whole file would otherwise enter an agent's context: the outline
lets it read the part it needs. One regex per file kind, picked by suffix; a
kind without a pattern has no outline (the caller still gives the line count).
Deterministic, stdlib, no parser: a missed definition costs a line of the
outline, never the read.
"""
from __future__ import annotations

import re
from pathlib import Path

MAX_ENTRIES = 40
MAX_LINE = 100

_DECLARATION = re.compile(
    r"^\s*(?:@\w+(?:\([^)]*\))?\s+)*(?:(?:public|private|protected|internal|export|default|static|final|"
    r"abstract|sealed|open|override|async|pub(?:\([^)]*\))?|data|inline|suspend)\s+)*"
    r"(?:class|interface|enum|record|struct|trait|impl|object|type|func|fn|function|def|fun)\b")
_METHOD = re.compile(r"^\s{2,8}(?:(?:public|private|protected|static|final|abstract|synchronized|default|"
                     r"override|async)\s+)+[\w<>\[\],.? ]+\s+\w+\s*\(")
PATTERNS = {
    "markdown": re.compile(r"^#{1,4}\s+\S"),
    "python": re.compile(r"^\s*(?:async\s+def|def|class)\s+\w+"),
    "code": _DECLARATION,
    "shell": re.compile(r"^\s*(?:function\s+[\w-]+|[\w-]+\s*\(\)\s*\{)"),
    "toml": re.compile(r"^\s*\[\[?[^\]]+\]\]?\s*$"),
    "yaml": re.compile(r"^[A-Za-z_][\w.-]*:"),
    "sql": re.compile(r"^\s*(?:create|alter)\s+(?:or\s+replace\s+)?(?:table|view|function|procedure|index|trigger)\b",
                      re.I),
}
KINDS = {
    ".md": "markdown", ".markdown": "markdown", ".py": "python",
    ".java": "code", ".kt": "code", ".kts": "code", ".scala": "code", ".cs": "code", ".ts": "code",
    ".tsx": "code", ".js": "code", ".jsx": "code", ".mjs": "code", ".go": "code", ".rs": "code",
    ".swift": "code", ".php": "code", ".groovy": "code",
    ".sh": "shell", ".bash": "shell", ".zsh": "shell",
    ".toml": "toml", ".yaml": "yaml", ".yml": "yaml", ".sql": "sql",
}


def kind_of(path: str | Path) -> str | None:
    return KINDS.get(Path(path).suffix.lower())


def outline(text: str, kind: str | None, limit: int = MAX_ENTRIES) -> list[str]:
    """`<line>: <declaration>` for the first `limit` structural lines, and a
    `(+N more)` line when there are more."""
    pattern = PATTERNS.get(kind or "")
    if pattern is None:
        return []
    found, fenced = [], False
    for number, line in enumerate(text.splitlines(), 1):
        if kind == "markdown" and line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if fenced:
            continue
        if pattern.search(line) or (kind == "code" and _METHOD.search(line)):
            found.append(f"{number}: {line.strip()[:MAX_LINE]}")
    if len(found) > limit:
        return found[:limit] + [f"(+{len(found) - limit} more)"]
    return found
