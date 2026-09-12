"""Parent-project (upstream) link configuration.

FM microservices are thin overlays on an upstream parent: an FM service's own
source is often a handful of files, and the base implementation an agent
actually needs to read lives in a separately-cloned parent tree. This module
is only the config layer -- where a link is recorded and how its path
resolves. The scanner (core/scanner.py) is what actually walks a linked root,
and inspector.get_parent_implementation() is what surfaces it.
"""
from __future__ import annotations

import dataclasses
from pathlib import Path

from core.lib.config_io import ConfigIO

# Storage-key prefix for a parent-linked file's entry in file_cache.json,
# e.g. "@parent:parent/src/main/java/.../Foo.java". Chosen to be something a
# project's own relative paths can never collide with (no real project path
# starts with "@").
PARENT_PREFIX = "@parent:"


@dataclasses.dataclass(frozen=True)
class LinkedProject:
    """One configured link to another project's source tree."""

    path: str  # as stored in config.toml -- may be relative or absolute
    role: str
    ref: str | None = None


def _config_path(project_root: str | Path) -> Path:
    return Path(project_root).expanduser().resolve() / ".eos" / "config.toml"


def read_links(project_root: str | Path) -> dict[str, LinkedProject]:
    """Every configured link, keyed by label (e.g. "parent")."""
    config_path = _config_path(project_root)
    if not config_path.is_file():
        return {}
    cfg = ConfigIO.read_toml(config_path)
    raw = cfg.get("links", {})
    if not isinstance(raw, dict):
        return {}
    return {
        label: LinkedProject(path=entry.get("path", ""), role=entry.get("role", ""), ref=entry.get("ref"))
        for label, entry in raw.items()
        if isinstance(entry, dict)
    }


def write_link(
    project_root: str | Path, label: str, path: str, role: str, ref: str | None = None
) -> None:
    """Add or replace one link, preserving every other config.toml section."""
    config_path = _config_path(project_root)
    cfg = ConfigIO.read_toml(config_path) if config_path.is_file() else {}
    cfg.setdefault("links", {})
    entry: dict = {"path": path, "role": role}
    if ref:
        entry["ref"] = ref
    cfg["links"][label] = entry
    ConfigIO.write_toml(config_path, cfg)


def resolve_link_path(project_root: str | Path, link: LinkedProject) -> Path:
    """Absolute path a link points at. Relative paths resolve against the project root."""
    project = Path(project_root).expanduser().resolve()
    candidate = Path(link.path)
    if candidate.is_absolute():
        return candidate.resolve()
    return (project / candidate).resolve()
