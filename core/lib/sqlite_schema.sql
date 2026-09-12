-- EOS UI SQLite schema
-- Single-user, local, serverless. WAL recommended when opened.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS eos_instances (
    id              TEXT PRIMARY KEY,
    path            TEXT UNIQUE NOT NULL,
    name            TEXT,
    engine_version  TEXT NOT NULL,
    tech_stack      JSON,
    status          TEXT NOT NULL DEFAULT 'active',
    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_scanned_at TIMESTAMP,
    last_updated_at TIMESTAMP,
    metadata        JSON
);

CREATE INDEX IF NOT EXISTS idx_eos_instances_status ON eos_instances(status);
CREATE INDEX IF NOT EXISTS idx_eos_instances_last_scanned_at ON eos_instances(last_scanned_at);

CREATE TABLE IF NOT EXISTS eos_scan_roots (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    path          TEXT UNIQUE NOT NULL,
    added_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_scan_at  TIMESTAMP
);

-- Reconciliation protocol note:
-- 1. Scanner reads .eos/id.txt.
-- 2. SELECT * FROM eos_instances WHERE id = ?.
-- 3. If found: UPDATE path = ?, status = 'active'.
-- 4. If not found: INSERT new row.
-- This ensures moves/renames are reconciled by ID rather than path.
