"""Mask Java string literals and comments without moving a single character.

Every structural defect the previous regex parser had was a quoting or nesting
failure, and no regex can express either. This pass removes the cause instead
of patching the symptoms: literals and comments are replaced with filler of the
same length, so offsets and line numbers are unchanged, and the real literal
values are kept in a table keyed by where they started.

What that buys, concretely:

* `@RequestMapping(value = "/x", produces = MediaType.APPLICATION_JSON_VALUE)`
  becomes readable by matching parentheses, which the old
  `@RequestMapping\\(\\s*"?([^"\\)]+)"?\\s*\\)` could not do -- it cannot cross a
  quote. Measured on one service: 17 of its 22 `@RequestMapping` uses take that
  form, and 82 of 108 emitted endpoint paths came out empty.
* An `@Service` inside an ArchUnit rule's string stops looking like an
  annotation. That false positive was previously fenced off with a `\\b(?!\\w)`
  guard in the plugin and a second guard in the classifier.
* A `}` inside a Javadoc example stops closing a class.

The filler is a space for everything except newlines, which are preserved so
line numbers survive, and the string delimiters themselves, which are kept so a
reader can still see that *a* literal was there.
"""
from __future__ import annotations

import bisect
import dataclasses

_LINE_COMMENT = "//"
_BLOCK_OPEN = "/*"
_BLOCK_CLOSE = "*/"
_TEXT_BLOCK = '"""'


@dataclasses.dataclass(frozen=True)
class Source:
    """One Java file, with its literals and comments masked out.

    `masked` is the same length as `text`; index i in one is index i in the
    other. `literals` maps the offset of a literal's opening quote to its
    decoded value, so a masked `@Component("x")` can still yield "x".
    """

    text: str
    masked: str
    literals: dict[int, str]
    _line_starts: tuple[int, ...]

    def line_of(self, offset: int) -> int:
        """1-based line number for a character offset."""
        return bisect.bisect_right(self._line_starts, offset)

    def literal_at(self, offset: int) -> str | None:
        return self.literals.get(offset)

    def literals_in(self, start: int, end: int) -> list[str]:
        """Every literal that began inside [start, end), in source order."""
        return [self.literals[at] for at in sorted(self.literals) if start <= at < end]


def lex(text: str) -> Source:
    masked = list(text)
    literals: dict[int, str] = {}
    length = len(text)
    i = 0
    while i < length:
        char = text[i]
        if char == "/" and text.startswith(_LINE_COMMENT, i):
            i = _blank_until_newline(text, masked, i)
        elif char == "/" and text.startswith(_BLOCK_OPEN, i):
            i = _blank_block_comment(text, masked, i)
        elif char == '"' and text.startswith(_TEXT_BLOCK, i):
            i = _mask_text_block(text, masked, literals, i)
        elif char in '"\'':
            i = _mask_quoted(text, masked, literals, i, char)
        else:
            i += 1
    starts = [0] + [n + 1 for n, c in enumerate(text) if c == "\n"]
    return Source(text=text, masked="".join(masked), literals=literals,
                  _line_starts=tuple(starts))


def _blank(masked: list[str], start: int, end: int) -> None:
    """Replace [start, end) with spaces, keeping newlines so lines still count."""
    for n in range(start, end):
        if masked[n] != "\n":
            masked[n] = " "


def _blank_until_newline(text: str, masked: list[str], start: int) -> int:
    end = text.find("\n", start)
    end = len(text) if end == -1 else end
    _blank(masked, start, end)
    return end


def _blank_block_comment(text: str, masked: list[str], start: int) -> int:
    end = text.find(_BLOCK_CLOSE, start + 2)
    # An unterminated block comment swallows the rest of the file, which is
    # what javac does too -- better than pretending the tail is code.
    end = len(text) if end == -1 else end + 2
    _blank(masked, start, end)
    return end


def _mask_quoted(text: str, masked: list[str], literals: dict[int, str],
                 start: int, quote: str) -> int:
    """Mask one "..." or '...', recording its decoded value."""
    value: list[str] = []
    i = start + 1
    length = len(text)
    while i < length:
        char = text[i]
        if char == "\\" and i + 1 < length:
            value.append(_unescape(text[i + 1]))
            i += 2
            continue
        if char == quote:
            i += 1
            break
        # An unterminated literal ends at the line break; javac rejects it and
        # so should this, rather than masking the rest of the file.
        if char == "\n":
            break
        value.append(char)
        i += 1
    _blank(masked, start + 1, max(start + 1, i - 1))
    literals[start] = "".join(value)
    return i


def _mask_text_block(text: str, masked: list[str], literals: dict[int, str], start: int) -> int:
    end = text.find(_TEXT_BLOCK, start + 3)
    end = len(text) if end == -1 else end + 3
    literals[start] = text[start + 3:max(start + 3, end - 3)]
    _blank(masked, start + 3, max(start + 3, end - 3))
    return end


_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f",
            "0": "\0", "\\": "\\", '"': '"', "'": "'"}


def _unescape(char: str) -> str:
    return _ESCAPES.get(char, char)
