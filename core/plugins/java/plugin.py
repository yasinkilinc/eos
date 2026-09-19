"""Java / Spring Boot plugin for EOS.

Uses regex-based parsing to extract packages, imports, classes, interfaces,
records, and basic Spring Boot annotations.
"""
import bisect
import re
from pathlib import Path
from typing import List

from ..base import LanguagePlugin
from ...knowledge.semantic import Export, FileSemantic, Import, Symbol
from . import structure

_RE_PACKAGE = re.compile(r"^\s*package\s+(?P<pkg>[\w\.]+)\s*;", re.MULTILINE)
_RE_IMPORT = re.compile(r"^\s*import\s+(?:static\s+)?(?P<module>[\w\.]+)\s*;", re.MULTILINE)
_RE_JAVADOC = re.compile(r"/\*\*(.*?)\*/", re.DOTALL)

# Heuristic to find class/interface/enum/record declarations
_RE_CLASS = re.compile(
    r"(?:public|private|protected|abstract|final|static|\s)*\b(?P<kind>class|interface|record|enum)\s+(?P<name>\w+)",
    re.MULTILINE
)

# Basic heuristic to find methods
_RE_METHOD = re.compile(
    r"(?:public|protected|private)\s+(?:static\s+|final\s+|abstract\s+|synchronized\s+)?(?:[\w<>,\[\]\s]+)\s+(?P<name>\w+)\s*\([^)]*\)\s*(?:throws\s+[\w,\s]+)?\s*\{",
    re.MULTILINE
)

# Spring Boot Annotations Mapping
def _mapping_path(annotation) -> str:
    """The path a mapping annotation declares, in any of its three spellings.

    `@PostMapping("/x")` stores it unnamed, `@RequestMapping(value = "/x", ...)`
    under "value", and the `path = ` alias under "path". Reading only one of
    them is how 82 of 108 endpoint paths on one tree came out empty.
    """
    values = annotation.values
    return values.get("value") or values.get("path") or values.get("") or ""


_MAPPING_VERBS = {
    "@GetMapping": "GET", "@PostMapping": "POST", "@PutMapping": "PUT",
    "@DeleteMapping": "DELETE", "@PatchMapping": "PATCH",
}

_SPRING_ANNOTATIONS = {
    "@Service": "service",
    "@RestController": "entry-point",
    "@Controller": "entry-point",
    "@Repository": "component",
    "@Component": "component",
    "@Configuration": "config",
}

_PRODUCT_VERSION_RE = re.compile(r"<product\.version>\s*([^<\s]+)\s*</product\.version>")


class JavaPlugin(LanguagePlugin):
    name = "java"
    extensions = (".java",)

    def detect(self, root: Path) -> bool:
        # Marker-based; .java files are caught by the extension walk.
        return (root / "pom.xml").exists() or (root / "build.gradle").exists()

    def parent_ref(self, root: Path) -> str | None:
        pom = root / "pom.xml"
        if not pom.is_file():
            return None
        try:
            text = pom.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        match = _PRODUCT_VERSION_RE.search(text)
        return match.group(1) if match else None

    def parse_file(self, rel_path: str, content: str) -> FileSemantic:
        """Read one Java file through the masking lexer and the scope scanner.

        Nothing here searches the raw text any more. A `@Service` inside an
        ArchUnit rule's string, a `}` inside a Javadoc example and a
        commented-out declaration were all structural signals to the previous
        parser; the lexer removes them before this sees the file.
        """
        parsed = structure.parse(content)
        structure.resolve_calls(parsed)
        package = parsed.package or ""

        file_sem = FileSemantic(path=rel_path, language=self.name, package=parsed.package)
        for simple, qualified in parsed.imports.items():
            module, _, name = qualified.rpartition(".")
            file_sem.imports.append(Import(module=module or qualified, name=name or simple,
                                           is_relative=False,
                                           line=self._import_line(parsed, qualified)))

        role = self._role_of(parsed)
        file_sem.role = role
        file_sem.annotations = parsed.annotations
        file_sem.fields = parsed.fields
        file_sem.type_refs = parsed.type_refs
        file_sem.calls = parsed.calls
        file_sem.thrown = parsed.thrown
        file_sem.codes = parsed.codes

        for symbol in parsed.symbols:
            if symbol.kind == "method":
                # Qualified by its declaring type, which the old parser never
                # recorded: 5,943 methods were extracted with no parent at all,
                # so two same-named methods in one file were indistinguishable.
                symbol.parent = f"{package}.{symbol.parent}" if package and symbol.parent else symbol.parent
                file_sem.symbols.append(symbol)
                continue
            qualified = f"{package}.{symbol.name}" if package else symbol.name
            kind = role if role and symbol.kind == "class" else symbol.kind
            symbol.name, symbol.kind = qualified, kind
            file_sem.symbols.append(symbol)
            file_sem.exports.append(Export(name=qualified, kind=kind, line=symbol.line))

        file_sem.doc = self._doc_of(parsed, content)
        return file_sem

    @staticmethod
    def _import_line(parsed, qualified: str) -> int:
        at = parsed.source.masked.find(f"import {qualified}")
        if at == -1:
            at = parsed.source.masked.find(qualified)
        return parsed.source.line_of(at) if at != -1 else 0

    @staticmethod
    def _role_of(parsed) -> str | None:
        """The Spring role of the file's types, from parsed annotations.

        Read off declarations rather than searched for in the file, so the
        token-boundary guard the old code needed (`@Controller` firing inside
        `@ControllerAdvice`, 60 files mis-tagged in one service) is not a
        special case any more -- `@ControllerAdvice` is simply a different
        annotation name.
        """
        names = {a.name for a in parsed.annotations if a.target}
        if not names:
            names = {a.name for a in parsed.annotations}
        for annotation, role in _SPRING_ANNOTATIONS.items():
            if annotation in names:
                return role
        return None

    def _doc_of(self, parsed, content: str) -> str | None:
        """The first Javadoc, plus the endpoints this file declares.

        Endpoints now come from parsed annotation arguments, so the multi-
        argument form works: `@RequestMapping(value = "/x", produces = ...)`
        defeated the old regex, and 82 of 108 endpoint lines on one tree came
        out with an empty path.
        """
        lines: list[str] = []
        doc_match = _RE_JAVADOC.search(parsed.source.masked)
        if doc_match:
            for line in content[doc_match.start(1):doc_match.end(1)].splitlines():
                line = line.strip()
                if line.startswith("*"):
                    line = line[1:].strip()
                if line:
                    lines.append(line)

        endpoints = self.endpoints(parsed)
        if endpoints:
            if lines:
                lines.append("")
                lines.append("---")
            lines.append("### REST endpoints")
            lines.extend(f"- **{verb}** `{path}`" for verb, path in endpoints)
        return "\n".join(lines).strip() or None

    @staticmethod
    def endpoints(parsed) -> list[tuple[str, str]]:
        """(verb, path) for every mapping in the file, base path applied."""
        base = ""
        for annotation in parsed.annotations:
            if annotation.name == "@RequestMapping" and annotation.target:
                base = _mapping_path(annotation)
                break
        found = []
        for annotation in parsed.annotations:
            verb = _MAPPING_VERBS.get(annotation.name)
            if verb is None:
                continue
            path = _mapping_path(annotation)
            joined = f"{base.rstrip('/')}/{path.lstrip('/')}" if path else base
            found.append((verb, joined or "/"))
        return found

    def _prepare_lines(self, content: str) -> None:
        """Precompute newline offsets once per file.

        Counting newlines from the start of the file per match is quadratic in
        file size, and a Java service parses thousands of matches per file.
        """
        self._offsets: List[int] = []
        start = content.find("\n")
        while start != -1:
            self._offsets.append(start)
            start = content.find("\n", start + 1)

    def _line(self, content: str, match: re.Match) -> int:
        return bisect.bisect_right(self._offsets, match.start()) + 1
