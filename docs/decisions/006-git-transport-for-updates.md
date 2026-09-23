# ADR-006: Git as the Engine Update Transport

**Status:** Accepted, 2026-09-23. Amended to describe what was built.  
**Date:** 2026-07-05  
**Deciders:** yasinkilinc

## Context

When `eos update` is implemented, the engine needs a reliable way to fetch the latest canonical runtime. Options include HTTP downloads, package managers, and Git.

## Decision

Use **Git** as the canonical update transport.

The development repository is already versioned with Git. Git brings `core/`
to the machine; `eos update` copies it into the project:

1. Copy changed files from the canonical `core/` to `.eos/runtime/`, by
   manifest diff.
2. Record the new version in the runtime.

For projects without network access, the user can manually copy the `core/` folder; the same manifest diff logic applies.

## Consequences

- Reuses existing versioning infrastructure.
- Rollbacks are straightforward using Git refs.
- No custom server or package registry is required.
- Requires Git to be installed on the developer machine (already assumed for most software projects).

## What shipped, and where it differs

Left "Proposed" for two and a half months while the thing it describes was
built, used and depended on. Recorded now against the code rather than
against the plan, because an ADR nobody closed is indistinguishable from one
nobody honoured.

`eos update` (`core/eos.py:cmd_update` → `core/lib/updater.py`) does steps 1
and 2 as written, `--dry-run` included. The proposal's first step — "check
the current tag/commit in the canonical repository" — was **not** built and
should not be: the engine never queries a remote. Staleness is decided by
comparing the runtime's recorded version against the engine's `core/VERSION`,
which is a file comparison needing no network, no remote and no git at all at
update time. Git is how a new `core/` arrives; it is not something the engine
calls.

One consequence of that worth stating, because it is load-bearing elsewhere:
a project whose runtime is older than the engine is detectable offline, which
is what lets a workspace refresh every project's runtime in one pass without
reaching the network once.
