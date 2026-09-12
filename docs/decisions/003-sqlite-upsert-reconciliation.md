# ADR-003: SQLite Upsert Reconciliation by Permanent ID

**Status:** Accepted  
**Date:** 2026-07-05  
**Deciders:** yasinkilinc

## Context

`eos-ui` tracks many `.eos` instances in a central SQLite database. A project may be moved or renamed, so the filesystem path alone is not a stable identity. We need a reconciliation rule that can distinguish "same instance, new path" from "new instance".

## Decision

Store a permanent UUID in `.eos/id.txt` and use it as the primary key in `eos_instances.id`. The reconciliation rule during scan is:

1. Read `.eos/id.txt`.
2. Query `SELECT * FROM eos_instances WHERE id = ?`.
3. If found, `UPDATE path = ?` and `UPDATE last_scanned_at = ?`.
4. If not found, `INSERT` a new row.

The path column remains `UNIQUE NOT NULL` because only one record should point to a given absolute path at a time.

## Consequences

- Folder moves and renames do not create duplicate records.
- Deleting `.eos/id.txt` causes the instance to be treated as new on next scan.
- The database schema is simple and does not require a separate alias table.
