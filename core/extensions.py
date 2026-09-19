"""Project-specific index extensions.

EOS indexes what is true of any project: authored notes, the brain documents,
the graph and git history. A project can also hold knowledge in a shape only
that project has -- one company's business-flow snapshots, another's generated
API contracts, a third's deployment inventory. Indexing those alongside
everything else is what makes a single query answer a real question, so the
extension point exists; keeping the project-specific code out of this
repository is what keeps EOS generic.

An extension is a Python module named in the project's `.eos/config.toml`:

    [index]
    extensions = ["tools/eos-ext/flows.py"]

Paths are resolved against the project root. A module may define any of:

    SCHEMA
        str -- extra `CREATE TABLE` / `CREATE INDEX` statements. Executed
        before `load`, in the same transaction as the core schema.

    COUNTS
        dict[str, str] -- label -> `SELECT COUNT(*) ...`, reported by
        `eos index` next to the core counts.

    sources(root, notes_dir) -> Iterable[Sequence]
        The files and facts this extension reads, as field tuples. They are
        hashed into the index's staleness digest *before* the build reads
        anything, so a change to them makes the index look stale rather than
        current. Return cheap identity for large inputs -- a path and an
        mtime, not a content digest of 600 files.

    load(build) -> None
        Fill the tables. `build` carries:
            build.conn       the sqlite3 connection being written
            build.root       the project root
            build.notes_dir  where this project's notes live
            build.meta       dict of meta rows to write
            build.read(path) -> (bytes, sha256) for one file
            build.issue(source, ref, problem)  record a skipped input
            build.search(source, ref, title, body)  add to full-text search

An extension is the project's own code and is trusted the way a git hook is:
it is imported and called inside the indexing process. Consequently a path
that is configured but missing, or a config value of the wrong shape, is a
hard error -- silently indexing less than the project asked for is the
failure this module exists to prevent. An extension that *runs* and raises is
different: it is recorded in `build_issue` and skipped, so one broken
extension cannot cost you the rest of the index.

Which extensions ran, and at which content digest, is written to the `meta`
table as `extensions`, so a row's provenance can be traced back to the code
that produced it.
"""
from __future__ import annotations

import dataclasses
import hashlib
import importlib.util
import sys
from pathlib import Path
from typing import Any

from core.lib.config_io import ConfigIO

_MODULE_PREFIX = "eos_ext_"


class ExtensionError(Exception):
    """An extension is configured but cannot be used. The index was not built."""


@dataclasses.dataclass(frozen=True)
class Extension:
    name: str
    path: Path
    digest: str
    module: Any

    @property
    def schema(self) -> str:
        return getattr(self.module, "SCHEMA", "") or ""

    @property
    def counts(self) -> dict:
        counts = getattr(self.module, "COUNTS", None) or {}
        if not isinstance(counts, dict):
            raise ExtensionError(f"{self.path}: COUNTS must be a dict of label -> SQL")
        return counts

    def sources(self, root: Path, notes_dir: Path):
        hook = getattr(self.module, "sources", None)
        if hook is None:
            return ()
        return hook(root, notes_dir) or ()

    def load(self, build) -> None:
        hook = getattr(self.module, "load", None)
        if hook is not None:
            hook(build)


def configured(root: Path) -> list[Path]:
    """The extension paths this project declares, resolved and checked to exist.

    Order is the order in the config: an extension may depend on a table an
    earlier one created, and nothing else defines a load order.
    """
    config = root / ".eos" / "config.toml"
    if not config.is_file():
        return []
    declared = ConfigIO.read_toml(config).get("index", {}).get("extensions")
    if declared is None:
        return []
    if not isinstance(declared, list) or not all(isinstance(entry, str) for entry in declared):
        raise ExtensionError(f"[index] extensions in {config} must be a list of paths")
    paths = []
    for entry in declared:
        path = Path(entry)
        path = path if path.is_absolute() else (root / path)
        # Resolved, so an extension that moved with a symlinked checkout is
        # still recognised as the same file by the staleness digest.
        path = path.resolve()
        if not path.is_file():
            raise ExtensionError(f"[index] extensions in {config} names {entry}, which is not a file")
        paths.append(path)
    return paths


def load_all(root: Path) -> list[Extension]:
    """Import every configured extension. Raises before the build starts, not during it."""
    loaded = []
    for path in configured(root):
        loaded.append(_import(path))
    return loaded


def _import(path: Path) -> Extension:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise ExtensionError(f"{path}: unreadable ({exc})") from exc
    digest = hashlib.sha256(data).hexdigest()
    # The digest is in the module name, so editing an extension between two
    # builds in one process re-imports it instead of reusing the stale module.
    name = f"{_MODULE_PREFIX}{path.stem}_{digest[:12]}"
    if name in sys.modules:
        return Extension(name=path.stem, path=path, digest=digest, module=sys.modules[name])
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ExtensionError(f"{path}: not an importable Python module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException as exc:
        del sys.modules[name]
        raise ExtensionError(f"{path}: failed to import ({exc})") from exc
    return Extension(name=path.stem, path=path, digest=digest, module=module)


def provenance(loaded: list[Extension]) -> str:
    """`name@digest12` per extension, for the meta table."""
    return ",".join(f"{ext.name}@{ext.digest[:12]}" for ext in loaded)
