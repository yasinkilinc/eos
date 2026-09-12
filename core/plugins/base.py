"""Base plugin interface for EOS language plugins."""
from abc import ABC, abstractmethod
from pathlib import Path
from typing import List

from ..knowledge.semantic import FileSemantic


class LanguagePlugin(ABC):
    """Every language plugin must implement detection and parsing."""

    name: str = ""
    extensions: tuple[str, ...] = ()

    @abstractmethod
    def detect(self, root: Path) -> bool:
        """Return True if the project uses this language."""
        ...

    @abstractmethod
    def parse_file(self, rel_path: str, content: str) -> FileSemantic:
        """Parse a single source file into a FileSemantic model."""
        ...

    def parent_ref(self, root: Path) -> str | None:
        """A version marker identifying the linked parent's baseline.

        Only some ecosystems have one. The default is "no marker"; a plugin
        overrides this when its build file carries a meaningful value.
        """
        return None
