"""Registry for available language plugins."""
import os
from pathlib import Path
from typing import List

from .base import LanguagePlugin
from .javascript.plugin import JavaScriptPlugin, TypeScriptPlugin
from .python.plugin import PythonPlugin
from .java.plugin import JavaPlugin


def _walk_filenames_bounded(root: Path, max_depth: int = 15):
    """Yield filenames under root, pruning ignore dirs and .eos.

    A single bounded walk replaces per-plugin ``rglob`` calls so language
    detection does not descend into node_modules / .venv / build outputs.
    """
    from core.scanner import DEFAULT_IGNORE

    root = root.resolve()
    for dirpath, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
        depth = len(Path(dirpath).relative_to(root).parts)
        if depth > max_depth:
            dirnames[:] = []
            continue
        dirnames[:] = [
            d for d in dirnames
            if d not in DEFAULT_IGNORE and d != ".eos"
        ]
        yield from filenames


class PluginRegistry:
    """Discovers and dispatches language plugins."""

    PLUGINS: List[LanguagePlugin] = [
        PythonPlugin(),
        TypeScriptPlugin(),
        JavaScriptPlugin(),
        JavaPlugin(),
    ]

    @classmethod
    def detect_languages(cls, root: Path) -> List[LanguagePlugin]:
        """Activate plugins whose file extensions or marker files are present."""
        suffixes = {Path(fn).suffix.lower() for fn in _walk_filenames_bounded(root)}
        detected = [p for p in cls.PLUGINS if any(ext in suffixes for ext in p.extensions) or p.detect(root)]
        return detected

    @classmethod
    def plugin_for_file(cls, rel_path: str, detected: List[LanguagePlugin]) -> LanguagePlugin | None:
        lower = rel_path.lower()
        for plugin in detected:
            if any(lower.endswith(ext) for ext in plugin.extensions):
                return plugin
        return None
