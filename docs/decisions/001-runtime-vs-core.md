# ADR-001: Runtime vs. Core Repository Separation

**Status:** Accepted  
**Date:** 2026-07-05  
**Deciders:** yasinkilinc

## Context

EOS is meant to be dropped into arbitrary project folders on a developer machine. The code that runs inside a project must therefore avoid external dependencies, because the user may not have a package manager or virtual environment prepared for that specific folder.

At the same time, EOS needs a canonical development repository where the engine evolves, is versioned, and is distributed from.

## Decision

Separate the concerns into two physical locations:

- `eos/core/` in the development repository is the **canonical source** of the engine.
- `.eos/runtime/` inside each managed project is a **copy** of the engine installed at `eos init` time.

Updates replace `.eos/runtime/` using a manifest diff, but never touch `.eos/data/`.

## Consequences

- Projects can be scanned offline without `pip install`.
- The runtime is pinned per project; different projects may run different engine versions.
- Engine updates are deterministic: compare `core/manifest.json` with `.eos/runtime/manifest.json`, copy only changed files, and keep timestamped backups.
- A small storage overhead exists because the runtime is duplicated per project.
