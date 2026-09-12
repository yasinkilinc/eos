"""Python plugin for EOS.

Uses only stdlib `ast` for parsing. No third-party dependencies.
"""
import ast
import re
from pathlib import Path
from typing import List

from ..base import LanguagePlugin
from ...knowledge.semantic import Export, FileSemantic, Import, Symbol


class PythonPlugin(LanguagePlugin):
    name = "python"
    extensions = (".py",)

    def detect(self, root: Path) -> bool:
        # Marker-based detection; .py files are caught by the extension walk
        # in PluginRegistry.detect_languages.
        return any((root / m).exists() for m in ("pyproject.toml", "setup.py", "requirements.txt", "Pipfile"))

    def parse_file(self, rel_path: str, content: str) -> FileSemantic:
        file_sem = FileSemantic(path=rel_path, language=self.name)
        try:
            tree = ast.parse(content, filename=rel_path)
        except SyntaxError:
            return file_sem

        # Module docstring
        file_sem.doc = ast.get_docstring(tree)

        # Imports
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    file_sem.imports.append(
                        Import(
                            module=alias.name,
                            name=alias.name,
                            alias=alias.asname,
                            line=node.lineno,
                        )
                    )
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                for alias in node.names:
                    file_sem.imports.append(
                        Import(
                            module=module,
                            name=alias.name,
                            is_relative=node.level > 0,
                            level=node.level,
                            alias=alias.asname,
                            line=node.lineno,
                        )
                    )

        # Top-level definitions
        for node in tree.body:
            if isinstance(node, ast.FunctionDef):
                file_sem.exports.append(
                    Export(name=node.name, kind="function", line=node.lineno, doc=ast.get_docstring(node))
                )
                file_sem.symbols.append(
                    Symbol(name=node.name, kind="function", line=node.lineno, doc=ast.get_docstring(node))
                )
            elif isinstance(node, ast.ClassDef):
                file_sem.exports.append(
                    Export(name=node.name, kind="class", line=node.lineno, doc=ast.get_docstring(node))
                )
                file_sem.symbols.append(
                    Symbol(name=node.name, kind="class", line=node.lineno, doc=ast.get_docstring(node))
                )
                for child in node.body:
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        kind = "method"
                        file_sem.symbols.append(
                            Symbol(
                                name=f"{node.name}.{child.name}",
                                kind=kind,
                                line=child.lineno,
                                doc=ast.get_docstring(child),
                                parent=node.name,
                            )
                        )
            elif isinstance(node, ast.AsyncFunctionDef):
                file_sem.exports.append(
                    Export(name=node.name, kind="function", line=node.lineno, doc=ast.get_docstring(node))
                )
                file_sem.symbols.append(
                    Symbol(name=node.name, kind="function", line=node.lineno, doc=ast.get_docstring(node))
                )

        return file_sem
