"""JavaScript/TypeScript plugin for EOS.

Uses regex-based parsing so that no npm/node dependency is required.
Sufficient for dependency graph + symbol extraction in Phase 0.
"""
import bisect
import re
from pathlib import Path
from typing import List

from ..base import LanguagePlugin
from ...knowledge.semantic import Export, FileSemantic, Import, Symbol


# The optional tail covers `import Default, { Named } from './x'` and
# `import Default, * as ns from './x'`. Without it neither clause matched, and
# because the whole group is optional the regex then required a quote straight
# after `import `, so the entire statement -- not just the default binding --
# produced no import at all.
_RE_IMPORT = re.compile(
    r"^\s*import\s+(?:type\s+)?(?:(?P<def>\{[^}]+\}|\*\s+as\s+\w+|\w+)"
    r"(?:\s*,\s*(?:\{[^}]+\}|\*\s+as\s+\w+))?\s+from\s+)?['\"](?P<module>[^'\"]+)['\"]",
    re.MULTILINE,
)
# `export { X } from './x'` and `export * from './x'` are imports as far as the
# dependency graph is concerned, and they are how a barrel file works: without
# them every index.ts is a dead end, which in a barrel-based codebase is most
# of the cross-module edges.
_RE_EXPORT_FROM = re.compile(
    r"^\s*export\s+(?:type\s+)?(?:\*(?:\s+as\s+\w+)?|\{[^}]*\})\s+from\s+['\"](?P<module>[^'\"]+)['\"]",
    re.MULTILINE,
)
_RE_REQUIRE = re.compile(
    r"(?:const|let|var)\s+(?P<name>\{[^}]+\}|\w+)\s*=\s*require\(['\"](?P<module>[^'\"]+)['\"]\)",
    re.MULTILINE,
)
_RE_EXPORT_FUNC = re.compile(
    r"(?:export\s+(?:default\s+)?)?(?:async\s+)?function\s+(?P<name>\w+)",
)
_RE_EXPORT_CONST = re.compile(
    r"export\s+(?:const|let|var)\s+(?P<name>\w+)",
)
_RE_CLASS = re.compile(
    r"(?:export\s+(?:default\s+)?)?class\s+(?P<name>\w+)",
)
_RE_INTERFACE = re.compile(
    r"export\s+interface\s+(?P<name>\w+)",
)
_RE_TYPE = re.compile(
    r"export\s+type\s+(?P<name>\w+)\s*=?",
)
_RE_ENUM = re.compile(
    r"(?:export\s+)?(?:const\s+)?enum\s+(?P<name>\w+)",
)
_RE_NAMESPACE = re.compile(
    r"(?:export\s+)?namespace\s+(?P<name>\w+)",
)
_RE_METHOD = re.compile(
    r"(?P<name>\w+)\s*\([^)]*\)\s*\{",
)


class JavaScriptPlugin(LanguagePlugin):
    name = "javascript"
    extensions = (".js", ".jsx", ".mjs")

    def detect(self, root: Path) -> bool:
        # Marker-based; .js/.jsx/.mjs files are caught by the extension walk.
        return (root / "package.json").exists()

    def parse_file(self, rel_path: str, content: str) -> FileSemantic:
        file_sem = FileSemantic(path=rel_path, language=self.name)
        self._prepare_lines(content)
        self._extract_imports(file_sem, content)
        self._extract_symbols(file_sem, content)
        return file_sem

    def _extract_imports(self, file_sem: FileSemantic, content: str) -> None:
        for match in _RE_IMPORT.finditer(content):
            module = match.group("module")
            imported = match.group("def")
            is_relative = module.startswith(".")
            file_sem.imports.append(
                Import(
                    module=module,
                    name=imported.strip("{} ") if imported else None,
                    is_relative=is_relative,
                    line=self._line(content, match),
                )
            )
        for match in _RE_REQUIRE.finditer(content):
            module = match.group("module")
            name = match.group("name")
            file_sem.imports.append(
                Import(
                    module=module,
                    name=name.strip("{} "),
                    is_relative=module.startswith("."),
                    line=self._line(content, match),
                )
            )
        for match in _RE_EXPORT_FROM.finditer(content):
            module = match.group("module")
            file_sem.imports.append(
                Import(
                    module=module,
                    name=None,
                    is_relative=module.startswith("."),
                    line=self._line(content, match),
                )
            )

    def _extract_symbols(self, file_sem: FileSemantic, content: str) -> None:
        # Simple heuristics; AST-based parsing can replace this in later phases.
        for match in _RE_EXPORT_FUNC.finditer(content):
            file_sem.exports.append(Export(name=match.group("name"), kind="function", line=self._line(content, match)))
            file_sem.symbols.append(Symbol(name=match.group("name"), kind="function", line=self._line(content, match)))
        for match in _RE_EXPORT_CONST.finditer(content):
            file_sem.exports.append(Export(name=match.group("name"), kind="variable", line=self._line(content, match)))
            file_sem.symbols.append(Symbol(name=match.group("name"), kind="variable", line=self._line(content, match)))
        for match in _RE_CLASS.finditer(content):
            file_sem.exports.append(Export(name=match.group("name"), kind="class", line=self._line(content, match)))
            file_sem.symbols.append(Symbol(name=match.group("name"), kind="class", line=self._line(content, match)))
        for match in _RE_INTERFACE.finditer(content):
            file_sem.exports.append(Export(name=match.group("name"), kind="interface", line=self._line(content, match)))
            file_sem.symbols.append(Symbol(name=match.group("name"), kind="interface", line=self._line(content, match)))
        for match in _RE_TYPE.finditer(content):
            file_sem.exports.append(Export(name=match.group("name"), kind="type", line=self._line(content, match)))
            file_sem.symbols.append(Symbol(name=match.group("name"), kind="type", line=self._line(content, match)))
        for match in _RE_ENUM.finditer(content):
            file_sem.exports.append(Export(name=match.group("name"), kind="enum", line=self._line(content, match)))
            file_sem.symbols.append(Symbol(name=match.group("name"), kind="enum", line=self._line(content, match)))
        for match in _RE_NAMESPACE.finditer(content):
            file_sem.exports.append(Export(name=match.group("name"), kind="namespace", line=self._line(content, match)))
            file_sem.symbols.append(Symbol(name=match.group("name"), kind="namespace", line=self._line(content, match)))

    def _prepare_lines(self, content: str) -> None:
        """Precompute newline offsets once per file.

        Counting '\\n' from the start of the file per match is quadratic in
        file size: a single 4.6 MB bundled .js took 330 s, with each doubling
        costing 4x, and produced no output while doing it.
        """
        self._offsets: List[int] = []
        start = content.find("\n")
        while start != -1:
            self._offsets.append(start)
            start = content.find("\n", start + 1)

    def _line(self, content: str, match: re.Match) -> int:
        return bisect.bisect_right(self._offsets, match.start()) + 1


class TypeScriptPlugin(JavaScriptPlugin):
    name = "typescript"
    extensions = (".ts", ".tsx")

    def detect(self, root: Path) -> bool:
        # Marker-based; .ts/.tsx files are caught by the extension walk.
        return (root / "tsconfig.json").exists()
