"""Reconcile scan-roots with the eos_instances table (ID-first).

Walks each registered scan-root, finds every ``.eos/id.txt`` beneath it
(stopping deeper at nested .eos boundaries), and upserts the matching row
by ID. Bridge fields (engine_version, tech_stack, last_scanned_at,
metadata) are read from the engine artifacts sitting next to id.txt:

  .eos/runtime/VERSION            -> engine_version
  .eos/data/last_scan.json        -> tech_stack, metadata, last_scanned_at

Instances whose ``.eos`` disappeared are marked ``missing`` (never deleted)
so the row and its history survive disk-side rearrangements.
"""
import json
import os
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from . import db


_EOS_DIR = ".eos"

# Directories never traversed when walking scan-roots for .eos instances.
# Mirrors core.scanner.DEFAULT_IGNORE so reconcile does not descend into
# node_modules / .venv / build outputs while locating instances.
_RECONCILE_IGNORE = {
    ".git", "__pycache__", ".venv", "venv", "node_modules",
    "dist", "build", ".next", ".turbo", ".mypy_cache",
    ".pytest_cache", ".egg-info", ".tox", "Pods", ".build",
}


@dataclass
class ReconcileReport:
    added: List[str] = field(default_factory=list)
    updated: List[str] = field(default_factory=list)
    missing_marked: int = 0
    unmanaged_marked: int = 0
    roots_scanned: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "added": self.added,
            "updated": self.updated,
            "missing_marked": self.missing_marked,
            "unmanaged_marked": self.unmanaged_marked,
            "roots_scanned": self.roots_scanned,
        }


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_id_file(eos_dir: Path) -> Optional[str]:
    id_file = eos_dir / "id.txt"
    if id_file.is_file():
        try:
            return id_file.read_text(encoding="utf-8").strip()
        except OSError:
            return None

    legacy_db = eos_dir / "eos.db"
    if not legacy_db.is_file():
        return None
    try:
        conn = sqlite3.connect(f"file:{legacy_db}?mode=ro", uri=True)
        try:
            row = conn.execute("SELECT uuid FROM projects LIMIT 1").fetchone()
        finally:
            conn.close()
        return row[0] if row else None
    except sqlite3.Error:
        return None


def read_engine_version(eos_dir: Path) -> str:
    # After `eos update`, VERSION lives in .eos/runtime/VERSION (the deployed
    # engine). Before any update, cmd_init writes .eos/VERSION. Check both so
    # engine_version is correct in either state.
    for candidate in (eos_dir / "runtime" / "VERSION", eos_dir / "VERSION"):
        if candidate.is_file():
            try:
                value = candidate.read_text(encoding="utf-8").strip()
                if value:
                    return value
            except OSError:
                pass
    return "unknown"


def read_last_scan(eos_dir: Path) -> dict:
    path = eos_dir / "data" / "last_scan.json"
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except (OSError, json.JSONDecodeError):
            pass

    legacy_db = eos_dir / "eos.db"
    if not legacy_db.is_file():
        return {}
    try:
        conn = sqlite3.connect(f"file:{legacy_db}?mode=ro", uri=True)
        try:
            snapshot = conn.execute(
                "SELECT file_count, symbol_count, import_count, created_at "
                "FROM knowledge_snapshots ORDER BY id DESC LIMIT 1"
            ).fetchone()
            languages = [row[0] for row in conn.execute(
                "SELECT DISTINCT language FROM files WHERE language IS NOT NULL ORDER BY language"
            ).fetchall()]
        finally:
            conn.close()
    except sqlite3.Error:
        return {}
    if not snapshot:
        return {"languages": languages}
    return {
        "languages": languages,
        "files_parsed": snapshot[0],
        "nodes": snapshot[1],
        "edges": snapshot[2],
        "last_scanned_at": snapshot[3],
    }


def walk_root_for_eos(root: Path) -> List[Path]:
    """Return absolute paths to every .eos directory beneath root.

    Uses os.walk (Python 3.11 compatible; ``Path.walk`` requires 3.12).
    Prunes common build/dependency directories and stops descending at each
    .eos boundary so a located project's internal tree is not traversed.
    """
    found: List[Path] = []
    root = root.resolve()
    if not root.is_dir():
        return found

    # If the scan root is itself a project, its .eos is the only one of interest.
    if (root / _EOS_DIR).is_dir():
        return [root / _EOS_DIR]

    for dirpath, dirnames, _ in os.walk(root, topdown=True, followlinks=False):
        # Prune ignored dirs in-place to control os.walk descent.
        dirnames[:] = [d for d in dirnames if d not in _RECONCILE_IGNORE]
        eos_path = Path(dirpath) / _EOS_DIR
        if eos_path.is_dir():
            found.append(eos_path)
            # Stop at the project boundary; do not descend into this project.
            dirnames[:] = []
    return found


def upsert_one(conn: sqlite3.Connection, eos_dir: Path, scanned_at: str) -> dict:
    """Upsert one .eos directory into eos_instances by ID."""
    instance_id = read_id_file(eos_dir)
    if not instance_id:
        return {}

    project_root = eos_dir.parent
    name = project_root.name
    engine_version = read_engine_version(eos_dir)

    last_scan = read_last_scan(eos_dir)
    tech_stack = last_scan.get("languages") or []
    metadata = {
        "files_parsed": last_scan.get("files_parsed"),
        "nodes": last_scan.get("nodes"),
        "edges": last_scan.get("edges"),
    }
    metadata = {k: v for k, v in metadata.items() if v is not None}

    existing = db.get_instance(conn, instance_id)
    result = db.upsert_instance(
        conn,
        instance_id=instance_id,
        path=str(project_root),
        name=name,
        engine_version=engine_version,
        tech_stack=tech_stack,
        metadata=metadata or None,
        last_scanned_at=scanned_at,
    )
    result["_action"] = "added" if existing is None else "updated"
    result["_was_existing"] = existing is not None
    if existing is None:
        # upsert_instance returned the freshly-inserted row; create the flag
        result["_action"] = "added"
    return result


def reconcile(conn: sqlite3.Connection) -> ReconcileReport:
    report = ReconcileReport()
    scanned_at = now_iso()
    alive_paths: List[str] = []
    roots = db.list_scan_roots(conn)

    for root in roots:
        root_path = Path(root["path"])
        report.roots_scanned.append(str(root_path))
        if not root_path.is_dir():
            continue
        for eos_dir in walk_root_for_eos(root_path):
            row = upsert_one(conn, eos_dir, scanned_at)
            if not row:
                continue
            if row.get("_was_existing"):
                report.updated.append(row["id"])
            else:
                report.added.append(row["id"])
            alive_paths.append(row["path"])
        db.set_scan_root_scanned(conn, str(root_path), scanned_at)

    # Re-evaluate status of instances not seen during this walk:
    #   .eos still on disk but outside any scan-root -> 'unmanaged'
    #   .eos gone from disk                      -> 'missing'
    # Instances already 'missing' are left untouched; upsert above restores
    # found ones to 'active'.
    alive = set(alive_paths)
    for r in conn.execute(
        "SELECT id, path FROM eos_instances WHERE status IN ('active', 'unmanaged')"
    ).fetchall():
        if r["path"] in alive:
            continue
        if (Path(r["path"]) / _EOS_DIR).is_dir():
            conn.execute(
                "UPDATE eos_instances SET status = 'unmanaged' WHERE id = ?", (r["id"],)
            )
            report.unmanaged_marked += 1
        else:
            conn.execute(
                "UPDATE eos_instances SET status = 'missing' WHERE id = ?", (r["id"],)
            )
            report.missing_marked += 1
    conn.commit()
    return report
