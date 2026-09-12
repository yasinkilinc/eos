# ADR-007: Deprecate the Standalone `eos-dashboard` Workspace

**Status:** Accepted  
**Date:** 2026-07-05  
**Deciders:** yasinkilinc

## Context

A separate workspace outside this repository, `eos-dashboard`, was previously created. The new EOS architecture places the central UI inside the `eos/ui/` directory of the main repository, backed by a single SQLite database and FastAPI.

## Decision

Abandon the standalone `eos-dashboard` workspace. The canonical UI lives in `eos/ui/` and is developed alongside `eos/core/`.

## Consequences

- A single repository contains the engine and the dashboard.
- There is no risk of the dashboard drifting from the engine schema.
- The old `eos-dashboard` workspace can be archived or deleted.
