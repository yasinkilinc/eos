"""Classify symbols/files into knowledge node types and tag taxonomy."""
from pathlib import Path
from typing import List

from ..lib.paths import is_test_path
from .semantic import FileSemantic


class Classifier:
    """Map a file/symbol into a knowledge node type and tags."""

    TAG_TAXONOMY = [
        "component",
        "service",
        "util",
        "entry-point",
        "config",
        "test",
        "external-dep",
        "folder",
    ]

    def classify_file(self, file: FileSemantic) -> tuple[str, List[str]]:
        """Return (node_type, tags) for a source file.

        A role the plugin read out of the source wins over every path
        heuristic below: an annotation is evidence, a substring in a directory
        name is a guess.
        """
        lower_path = file.path.lower()
        filename = Path(lower_path).name

        tags: List[str] = []
        node_type = "component"

        is_test = is_test_path(file.path)

        # The test tree wins over a plugin role. An ArchUnit rule naming
        # "@Service" in a string literal, or a @Service-annotated fixture
        # loader, are both still tests -- letting the role win moved two such
        # files out of `test` on a real service.
        if not is_test and file.role:
            tags = [file.role]
            if file.language not in tags:
                tags.append(file.language)
            return file.role, tags

        if is_test:
            node_type = "test"
            tags.append("test")
        elif "config" in lower_path or filename in ("config.py", "settings.py", "setup.py", "pyproject.toml", "package.json", "tsconfig.json"):
            node_type = "config"
            tags.append("config")
        elif "service" in lower_path or "services" in lower_path:
            node_type = "service"
            tags.append("service")
        elif "util" in lower_path or "utils" in lower_path:
            node_type = "util"
            tags.append("util")
        elif "main" in filename or filename in ("app.py", "index.ts", "index.js", "server.py", "cli.py"):
            node_type = "entry-point"
            tags.append("entry-point")

        if file.language not in tags:
            tags.append(file.language)

        return node_type, tags

    def symbol_kind_to_tag(self, kind: str) -> str:
        kind = kind.lower()
        if kind in ("class", "interface"):
            return "component"
        if kind == "function":
            return "util"
        return "component"
