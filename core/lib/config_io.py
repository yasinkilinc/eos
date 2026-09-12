"""TOML/JSON configuration helpers using only Python stdlib."""
import json
import tomllib
from pathlib import Path
from typing import Any, Dict


class ConfigIO:
    """Read TOML (Python >=3.11 tomllib) and JSON. Write TOML via a minimal serializer."""

    @staticmethod
    def read_toml(path: Path) -> Dict[str, Any]:
        with open(path, "rb") as f:
            return tomllib.load(f)

    @staticmethod
    def write_toml(path: Path, data: Dict[str, Any]) -> None:
        with open(path, "w", encoding="utf-8") as f:
            f.write(_to_toml(data))

    @staticmethod
    def read_json(path: Path) -> Any:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    @staticmethod
    def write_json(path: Path, data: Any, indent: int = 2) -> None:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=indent, ensure_ascii=False)


def _to_toml(obj: Any, prefix: str = "") -> str:
    """Minimal TOML serializer sufficient for EOS config files.

    Supports nested dicts, lists of scalars, and basic scalar types.
    Does not support arrays of tables or inline tables by design.
    """
    lines: list[str] = []
    simple: list[str] = []
    tables: Dict[str, Any] = {}

    for key, value in obj.items():
        norm = key.replace(" ", "_")
        if isinstance(value, dict):
            tables[norm] = value
        else:
            simple.append((norm, value))

    for norm, value in simple:
        lines.append(f"{norm} = {_toml_value(value)}")

    if simple and tables:
        lines.append("")

    for i, (norm, table) in enumerate(tables.items()):
        header = f"{prefix}{norm}"
        rendered = _to_toml(table, f"{header}.").rstrip()
        # A table holding only further nested tables (no scalar of its own,
        # e.g. "links" when the real content lives in "links.parent") needs
        # no header line of its own -- only its descendants' fully-qualified
        # headers carry meaning. Emitting one anyway produced a dangling
        # empty [links] section ahead of [links.parent].
        if any(not isinstance(v, dict) for v in table.values()):
            lines.append(f"[{header}]")
        lines.append(rendered)
        if i < len(tables) - 1:
            lines.append("")

    return "\n".join(lines) + "\n"


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        items = ", ".join(_toml_value(v) for v in value)
        return f"[{items}]"
    raise TypeError(f"Unsupported TOML value type: {type(value)}")
