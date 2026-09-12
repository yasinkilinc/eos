"""Single-source SQLite connection + schema application for eos-ui.

Location of the database follows ARCHITECTURE.md: ~/.eos-ui/eos.db.
The schema is sourced from core/lib/sqlite_schema.sql (single source of truth)
and applied idempotently with CREATE IF NOT EXISTS.
"""
import sqlite3
from pathlib import Path
from typing import Any, Iterable, List, Optional


_SCHEMA_PATH = Path(__file__).resolve().parents[2] / "core" / "lib" / "sqlite_schema.sql"
DEFAULT_DB_PATH = Path.home() / ".eos-ui" / "eos.db"


def _split_statements(sql: str) -> List[str]:
    """Naive splitter that yields executable statements.

    The schema file mixes PRAGMA, CREATE TABLE, CREATE INDEX and comments.
    Statements are terminated by ';'. Comments (-- lines) are stripped.
    """
    statements: List[str] = []
    buf: List[str] = []
    for line in sql.splitlines():
        stripped = line.split("--", 1)[0]
        buf.append(stripped)
        if stripped.rstrip().endswith(";"):
            stmt = "\n".join(buf).strip()
            if stmt:
                statements.append(stmt)
            buf = []
    tail = "\n".join(buf).strip()
    if tail:
        statements.append(tail)
    return statements


def connect(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """Open a connection and apply the schema if missing."""
    path = Path(db_path) if db_path else DEFAULT_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    # FastAPI runs a sync dependency, its path function and the generator's
    # cleanup on different threadpool workers, so the connection must not be
    # pinned to its creating thread.
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    sql = _SCHEMA_PATH.read_text(encoding="utf-8")
    # PRAGMA journal_mode = WAL cannot run inside a transaction; run it first.
    for stmt in _split_statements(sql):
        if stmt.upper().startswith("PRAGMA JOURNAL_MODE"):
            try:
                conn.execute(stmt)
            except sqlite3.OperationalError:
                pass
        else:
            conn.execute(stmt)
    conn.commit()
    return conn


def upsert_scan_root(conn: sqlite3.Connection, path: str) -> int:
    cur = conn.execute(
        "INSERT INTO eos_scan_roots(path) VALUES(?) "
        "ON CONFLICT(path) DO NOTHING",
        (path,),
    )
    conn.commit()
    return cur.lastrowid or 0


def list_scan_roots(conn: sqlite3.Connection) -> List[dict]:
    rows = conn.execute(
        "SELECT id, path, added_at, last_scan_at FROM eos_scan_roots ORDER BY id"
    ).fetchall()
    return [dict(r) for r in rows]


def delete_scan_root(conn: sqlite3.Connection, path: str) -> None:
    conn.execute("DELETE FROM eos_scan_roots WHERE path = ?", (path,))
    conn.commit()


def list_instances(conn: sqlite3.Connection) -> List[dict]:
    rows = conn.execute(
        "SELECT id, path, name, engine_version, tech_stack, status, "
        "created_at, last_scanned_at, last_updated_at, metadata "
        "FROM eos_instances ORDER BY name, path"
    ).fetchall()
    return [dict(r) for r in rows]


def get_instance(conn: sqlite3.Connection, instance_id: str) -> Optional[dict]:
    row = conn.execute(
        "SELECT id, path, name, engine_version, tech_stack, status, "
        "created_at, last_scanned_at, last_updated_at, metadata "
        "FROM eos_instances WHERE id = ?",
        (instance_id,),
    ).fetchone()
    return dict(row) if row else None


def get_instance_by_path(conn: sqlite3.Connection, path: str) -> Optional[dict]:
    row = conn.execute(
        "SELECT id FROM eos_instances WHERE path = ?", (path,)
    ).fetchone()
    return dict(row) if row else None


def upsert_instance(
    conn: sqlite3.Connection,
    instance_id: str,
    path: str,
    name: str,
    engine_version: str,
    tech_stack: Any,
    metadata: Any,
    last_scanned_at: Optional[str] = None,
) -> dict:
    """ID-first reconciliation (ADR-003).

    Behaves as: if id exists, UPDATE path/name/status; else INSERT.
    Never changes created_at on update; never orphans id.
    Returns the resulting row.
    """
    existing = conn.execute(
        "SELECT id, created_at FROM eos_instances WHERE id = ?", (instance_id,)
    ).fetchone()
    if existing:
        conn.execute(
            "UPDATE eos_instances SET path = ?, name = ?, engine_version = ?, "
            "tech_stack = ?, status = 'active', last_scanned_at = COALESCE(?, last_scanned_at), "
            "metadata = ? WHERE id = ?",
            (
                path,
                name,
                engine_version,
                _json(tech_stack),
                last_scanned_at,
                _json(metadata),
                instance_id,
            ),
        )
    else:
        conn.execute(
            "INSERT INTO eos_instances"
            "(id, path, name, engine_version, tech_stack, status, last_scanned_at, metadata) "
            "VALUES(?, ?, ?, ?, ?, 'active', ?, ?)",
            (
                instance_id,
                path,
                name,
                engine_version,
                _json(tech_stack),
                last_scanned_at,
                _json(metadata),
            ),
        )
    conn.commit()
    return get_instance(conn, instance_id) or {}


def set_instance_status(conn: sqlite3.Connection, instance_id: str, status: str) -> None:
    conn.execute(
        "UPDATE eos_instances SET status = ? WHERE id = ?", (status, instance_id)
    )
    conn.commit()


def delete_instance(conn: sqlite3.Connection, instance_id: str) -> int:
    cur = conn.execute("DELETE FROM eos_instances WHERE id = ?", (instance_id,))
    conn.commit()
    return cur.rowcount


def set_scan_root_scanned(conn: sqlite3.Connection, path: str, iso_ts: str) -> None:
    conn.execute(
        "UPDATE eos_scan_roots SET last_scan_at = ? WHERE path = ?",
        (iso_ts, path),
    )
    conn.commit()


def _json(value: Any) -> Optional[str]:
    if value is None:
        return None
    import json

    return json.dumps(value)