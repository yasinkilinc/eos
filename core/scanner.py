"""Incremental project scanner for EOS.

Walks a project tree, applies ignore rules, parses changed files using
language plugins, and produces a ProjectSemantic model.

Incremental semantics:
  - Files whose mtime/size/hash match the cache are NOT re-parsed; their
    cached FileSemantic is reused so the graph is always complete.
  - `full=True` forces re-parsing every file.
"""
import fnmatch
import json
import os
from pathlib import Path
from typing import List, Set

from core.knowledge.semantic import FileSemantic, ProjectSemantic
from core.lib.cache_store import CacheStore
from core.plugins.base import LanguagePlugin
from core.plugins.registry import PluginRegistry


DEFAULT_IGNORE = {
    ".git",
    # Host-agent configuration (skills, agent profiles, MCP registration).
    # It configures whatever coding assistant is reading the project, not the
    # project itself, so it is never project source regardless of what an
    # integration writes there.
    ".claude",
    "__pycache__",
    ".venv",
    "venv",
    "node_modules",
    "dist",
    "build",
    "target",
    "bin",
    ".mvn",
    ".gradle",
    ".next",
    ".turbo",
    ".mypy_cache",
    ".pytest_cache",
    ".egg-info",
    ".tox",
}


class Scanner:
    """Scan a project and produce semantic model."""

    def __init__(self, project_root: Path, cache: CacheStore):
        self.root = project_root.resolve()
        self.cache = cache
        self.ignore_patterns: List[str] = []
        self.ignore_dirs: Set[str] = set(DEFAULT_IGNORE)
        self.max_depth = 15
        self._load_config()

    def _load_config(self) -> None:
        unignored: Set[str] = set()
        config_path = self.root / ".eos" / "config.toml"
        if config_path.exists():
            try:
                from core.lib.config_io import ConfigIO

                cfg = ConfigIO.read_toml(config_path)
                scan_cfg = cfg.get("scan", {})
                self.max_depth = int(scan_cfg.get("max_depth", self.max_depth))
                extra_ignores = scan_cfg.get("ignore", [])
                if isinstance(extra_ignores, list):
                    self.ignore_dirs.update(str(x) for x in extra_ignores)
                # `ignore` only ever added, so a project whose real source
                # lives under build/ or bin/ -- nuitka keeps 730 modules under
                # build/ -- could not be indexed at all.
                unignore = scan_cfg.get("unignore", [])
                if isinstance(unignore, list):
                    unignored = {str(x).rstrip("/") for x in unignore}
                    self.ignore_dirs.difference_update(unignored)
            except Exception:
                pass

        # Respect project .gitignore if present
        gitignore = self.root / ".gitignore"
        if gitignore.exists():
            with open(gitignore, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    # `unignore` has to reach these too. It only removed from
                    # ignore_dirs, while .gitignore lines land here, so the hint
                    # printed after a scan ("un-ignore a default with [scan]
                    # unignore") named rules the knob could not touch -- the
                    # user follows the instruction, nothing changes, and there
                    # is no next step.
                    if line and not line.startswith("#") and line.rstrip("/") not in unignored:
                        self.ignore_patterns.append(line)

    def ignore_reason(self, rel_path: str) -> str | None:
        """Which rule excludes this path, or None if nothing does.

        The reason is what gets reported: knowing that 730 files went to the
        `build` rule is actionable, knowing only that they are missing is not.
        """
        for part in Path(rel_path).parts:
            if part in self.ignore_dirs:
                return part
        for pattern in self.ignore_patterns:
            if self._matches_gitignore(pattern, rel_path):
                return pattern
        return None

    @staticmethod
    def _matches_gitignore(pattern: str, rel_path: str) -> bool:
        """Match one .gitignore line against a project-relative path.

        A trailing slash is gitignore's canonical directory form -- it is what
        GitHub's own templates emit -- and fnmatch cannot match `generated/`
        against `generated/stub.js` until it is stripped. Matching each path
        segment as well is what makes a bare directory name exclude its
        contents, which is also gitignore's behaviour.
        """
        pattern = pattern.rstrip("/")
        if not pattern:
            return False
        if fnmatch.fnmatch(rel_path, pattern):
            return True
        if fnmatch.fnmatch(Path(rel_path).name, pattern):
            return True
        return any(fnmatch.fnmatch(part, pattern) for part in Path(rel_path).parts)

    def is_ignored(self, rel_path: str) -> bool:
        return self.ignore_reason(rel_path) is not None

    @staticmethod
    def _count_files(path: Path) -> int:
        """Files under a pruned directory. Directory entries only, no reads."""
        try:
            return sum(len(files) for _, _, files in os.walk(path, followlinks=False))
        except OSError:
            return 1

    def _walk(self, root: Path, report=None):
        """Yield paths relative to `root` (posix separators), applying the
        same ignore rules regardless of which root is being walked -- parent
        (upstream) repos are the same Java/Maven shape as the project they
        overlay, so one ignore rule set covers both."""
        for dirpath, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
            rel_dir = Path(dirpath).relative_to(root).as_posix()
            depth = len(Path(rel_dir).parts) if rel_dir != "." else 0
            if depth > self.max_depth:
                dirnames[:] = []
                continue

            # Never descend into any .eos directory (own instance or nested
            # project boundary) — .eos holds metadata, not project source.
            if ".eos" in dirnames:
                dirnames.remove(".eos")

            filtered_dirs = []
            for d in dirnames:
                rel_d = (Path(rel_dir) / d).as_posix() if rel_dir != "." else d
                reason = self.ignore_reason(rel_d)
                if reason is None:
                    filtered_dirs.append(d)
                elif report is not None:
                    # Count what the prune actually drops. Recording 1 per
                    # directory printed "build (1)" for the 730-module package
                    # the feature exists to surface, and summed dirs with
                    # individually-matched files into one meaningless total.
                    report.note_ignored(reason, self._count_files(Path(dirpath) / d))
            dirnames[:] = filtered_dirs

            for filename in filenames:
                rel_file = (Path(rel_dir) / filename).as_posix() if rel_dir != "." else filename
                reason = self.ignore_reason(rel_file)
                if reason is None:
                    yield rel_file
                elif report is not None:
                    report.note_ignored(reason)

    def scan(self, full: bool = False) -> ProjectSemantic:
        project = ProjectSemantic()
        detected = PluginRegistry.detect_languages(self.root)
        if not detected:
            return project

        present_paths: Set[str] = set()
        for rel_file in self._walk(self.root, project.report):
            present_paths.add(rel_file)
            self._process_file(rel_file, self.root / rel_file, detected, project, full)

        if not full:
            self.cache.remove_missing(present_paths)

        self.cache.save()
        return project

    def scan_with_links(self, parent_roots: dict, full: bool = False) -> ProjectSemantic:
        """Like scan(), but also walks each linked parent root, storing its
        files under "@parent:<label>/<path-within-parent>" in the SAME cache
        so find_symbol/get_context see them without any change on their
        side. Opt-in and separate from scan() by design: a plain scan must
        stay exactly as fast as it is today."""
        from core.links import PARENT_PREFIX

        project = ProjectSemantic()
        detected = PluginRegistry.detect_languages(self.root)
        if not detected:
            return project

        present_paths: Set[str] = set()
        for rel_file in self._walk(self.root, project.report):
            present_paths.add(rel_file)
            self._process_file(rel_file, self.root / rel_file, detected, project, full)

        for label, parent_root in parent_roots.items():
            if not parent_root.is_dir():
                continue
            for rel_file in self._walk(parent_root, project.report):
                storage_key = f"{PARENT_PREFIX}{label}/{rel_file}"
                present_paths.add(storage_key)
                self._process_file(storage_key, parent_root / rel_file, detected, project, full)

        # Matches scan()'s existing asymmetry: a --full run reparses
        # everything but never prunes (unchanged pre-existing behaviour,
        # not something this task introduces); only the incremental path
        # prunes, now scoped by which parent labels were actually walked.
        if not full:
            self.cache.remove_missing(present_paths, parent_labels_scanned=set(parent_roots))

        self.cache.save()
        return project

    def _process_file(
        self,
        storage_key: str,
        abs_path: Path,
        detected: List[LanguagePlugin],
        project: ProjectSemantic,
        full: bool,
    ) -> tuple[int, int]:
        """Return (reused_from_cache, parsed_now).

        `storage_key` is what the file is filed under in the cache and in
        ProjectSemantic -- normally identical to its path relative to
        self.root, but a linked parent root's files are stored under a
        "@parent:<label>/" key that has no filesystem meaning; `abs_path` is
        always the real location to read bytes from.
        """
        plugin = PluginRegistry.plugin_for_file(storage_key, detected)
        if not plugin:
            # Not an error, but not nothing either: pglast reads as a pure
            # Python package while 25 MB of vendored C decides whether it
            # imports at all. Report the shape of what was left out.
            suffix = Path(storage_key).suffix
            if suffix:
                project.report.note_unsupported(suffix)
            return (0, 0)

        try:
            stat = abs_path.stat()
        except OSError:
            return (0, 0)

        if not full and not self.cache.is_changed(storage_key, stat.st_mtime, stat.st_size, abs_path.read_bytes()):
            cached_sem = self.cache.get_semantic(storage_key)
            if cached_sem is not None:
                project.add_file(cached_sem)
                project.report.files_parsed += 1
                return (1, 0)

        try:
            with open(abs_path, "rb") as f:
                content_bytes = f.read()
        except OSError:
            return (0, 0)

        try:
            text = content_bytes.decode("utf-8", errors="replace")
        except Exception:
            return (0, 0)

        file_sem = plugin.parse_file(storage_key, text)
        project.add_file(file_sem)
        project.report.files_parsed += 1
        self.cache.update(storage_key, stat.st_mtime, stat.st_size, content_bytes)
        self.cache.store_semantic(storage_key, file_sem)
        return (0, 1)