# ADR-006: Git as the Engine Update Transport

**Status:** Proposed  
**Date:** 2026-07-05  
**Deciders:** yasinkilinc

## Context

When `eos update` is implemented, the engine needs a reliable way to fetch the latest canonical runtime. Options include HTTP downloads, package managers, and Git.

## Decision

Use **Git** as the canonical update transport.

The development repository is already versioned with Git. `eos update` can:

1. Check the current tag/commit in the canonical repository.
2. Copy changed files from `core/` to `.eos/runtime/`.
3. Record the new version in `.eos/VERSION`.

For projects without network access, the user can manually copy the `core/` folder; the same manifest diff logic applies.

## Consequences

- Reuses existing versioning infrastructure.
- Rollbacks are straightforward using Git refs.
- No custom server or package registry is required.
- Requires Git to be installed on the developer machine (already assumed for most software projects).
