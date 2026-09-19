"""Semantic model produced by language plugins.

This is the first structured layer after parsing. It is intentionally
language-agnostic so that all plugins map into the same shape.
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class Import:
    """A dependency pulled into a file."""
    module: str
    name: Optional[str] = None
    is_relative: bool = False
    alias: Optional[str] = None
    line: int = 0
    level: int = 0
    """Relative-import depth: 0 absolute, 1 `.`, 2 `..`.

    Python's ast carries this on ImportFrom and nothing downstream can recover
    it afterwards: ast.ImportFrom.module never contains the leading dots, so
    counting them from the module name always yielded 0 and every relative
    import was treated as depth 1.
    """


@dataclass
class Export:
    """A symbol exposed by a file."""
    name: str
    kind: str  # function, class, variable, type, etc.
    line: int = 0
    doc: Optional[str] = None


@dataclass
class Symbol:
    """Any named definition inside a file."""
    name: str
    kind: str  # function, class, method, variable, enum, interface, etc.
    line: int = 0
    doc: Optional[str] = None
    parent: Optional[str] = None


@dataclass
class FileSemantic:
    """Structured result of parsing a single source file."""
    path: str              # project-relative path
    language: str          # python | javascript | typescript | ...
    imports: List[Import] = field(default_factory=list)
    exports: List[Export] = field(default_factory=list)
    symbols: List[Symbol] = field(default_factory=list)
    doc: Optional[str] = None
    parsed_at: Optional[str] = None
    """When this file was actually parsed, UTC.

    Not when the scan ran: an unchanged file is reused from the cache, so a
    fact emitted today may have been observed several scans ago. Stamping every
    fact with the current scan time would be exactly the kind of confident
    wrong answer provenance exists to prevent."""
    role: Optional[str] = None
    """A role the plugin established from the source itself -- a Spring
    annotation, a shebang, an `if __name__ == "__main__"`.

    The classifier prefers this over its path heuristics, which cannot see an
    annotation and whose last resort ("main" in the filename) never fires on a
    Maven layout: 0 entry points were found across 2,149 Java files carrying 67
    @RestController classes.
    """


@dataclass
class ScanReport:
    """What a scan did not index, so silence never reads as absence.

    A scan that drops 730 real source modules under build/ and prints only
    "Scanned 803 files" is indistinguishable from a project that has 803 files.
    Every exclusion is counted by the rule that caused it, and every file in a
    language no plugin handles is counted by extension.
    """
    files_parsed: int = 0
    """Files that entered the model, whether parsed now or reused from cache."""
    skipped_by_ignore: Dict[str, int] = field(default_factory=dict)
    unsupported_extensions: Dict[str, int] = field(default_factory=dict)
    unresolved_imports: Dict[str, int] = field(default_factory=dict)
    """Imports that named something the resolver could not find, by specifier
    kind. A whole style failing -- every `@/…` alias, say -- looked identical on
    stdout to a project that uses no aliases at all."""
    coverage: Dict[tuple, Any] = field(default_factory=dict)
    """(detector, predicate) -> evidence.Coverage.

    What each detector was asked about, whether or not it found anything. A
    detector that examined 2,652 files and produced nothing is a different
    statement from a detector that was never asked, and without this they are
    the same silence."""

    def note_ignored(self, reason: str, count: int = 1) -> None:
        self.skipped_by_ignore[reason] = self.skipped_by_ignore.get(reason, 0) + count

    def note_unsupported(self, suffix: str) -> None:
        self.unsupported_extensions[suffix] = self.unsupported_extensions.get(suffix, 0) + 1

    def note_unresolved(self, kind: str) -> None:
        self.unresolved_imports[kind] = self.unresolved_imports.get(kind, 0) + 1

    def note_coverage(self, detector: str, predicate: str, found: int) -> None:
        """Account for one eligible file a detector examined for one predicate."""
        from core.knowledge.evidence import Coverage  # local: semantic stays import-light

        key = (detector, predicate)
        entry = self.coverage.get(key)
        if entry is None:
            entry = self.coverage[key] = Coverage(detector=detector, predicate=predicate)
        entry.record(found)


@dataclass
class ProjectSemantic:
    """Aggregation of FileSemantic objects for a whole project."""
    files: List[FileSemantic] = field(default_factory=list)
    detected_languages: List[str] = field(default_factory=list)
    report: ScanReport = field(default_factory=ScanReport)

    def add_file(self, file_semantic: FileSemantic) -> None:
        self.files.append(file_semantic)
        if file_semantic.language not in self.detected_languages:
            self.detected_languages.append(file_semantic.language)
