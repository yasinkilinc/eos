# ADR-013: Index Extensions Instead of Project-Specific Core Code

**Status:** Accepted
**Date:** 2026-09-19
**Deciders:** yasinkilinc

## Context

EOS was extracted from a private workspace (ADR-008), and the extraction
removed a capability rather than generalising it: indexing that workspace's
business *journeys* — the configuration-driven step chains that decide which
service runs which part of a business flow, which is not visible in the code
at all. That code was ~400 lines inside `core/index.py`, wired to one
company's directory layout and naming conventions.

Dropping it was right for the published repository and wrong for the idea. The
capability answers a question no generic code graph can ("which service
actually runs step 4 of top-up, in env1"), and the pattern it represents —
knowledge held in a project-shaped artifact that only that project can
interpret — is not rare. Generated API contracts, deployment inventories and
exported flow configurations all have the same shape.

Three options were considered:

1. Keep such code in `core/`, guarded by configuration. This is what was
   removed; it is how `core/index.py` reached 1,130 lines, and every new
   project shape would add to it.
2. Let each workspace fork EOS. The private workspace already carried a
   diverged copy, and it drifted: two commits of real fixes existed only on
   one side.
3. An extension point: core defines the contract, the project supplies the
   module.

## Decision

Add index extensions (`core/extensions.py`). A project names Python modules
under `[index] extensions` in `.eos/config.toml`; each may contribute `SCHEMA`
(tables), `COUNTS` (headline numbers), `sources()` (staleness inputs) and
`load()` (rows), against one documented build context. They write into the
same SQLite index as everything else — there is no second database and no
second query interface.

The journey indexing returns as `extensions/journeys.py` in this repository:
generic, configuration-driven, tested, and shipped as the reference
implementation of the contract rather than as a special case inside `core/`.

Failures split deliberately:

- A **configuration** mistake — an extension named but missing, unimportable,
  or declared with the wrong shape — raises `IndexBuildError` and leaves the
  previous index in place. Silently indexing less than the project asked for
  is exactly the failure this ADR exists to prevent, so it is never quiet.
- A **runtime** failure — a bad `SCHEMA`, a `load()` that raises — is recorded
  in `build_issue` and costs only that extension's tables. The notes, brain
  and history are worth more than one extension's rows.

An extension's own file content is part of the index's staleness digest, so
editing what it indexes rebuilds the index like editing any other source.
Which extensions ran, at which content digest, is written to `meta.extensions`
as provenance for the rows they produced.

## Consequences

- Project-specific indexing has a supported home that is not `core/` and not a
  fork. `check-clean.sh` stays enforceable because such code carries no
  company identifiers.
- Extensions run as imported Python inside the indexing process. They are the
  project's own code, trusted the way a git hook is; this is documented rather
  than sandboxed, because a sandbox strong enough to matter would also prevent
  the file and database access an extension exists to do.
- `core/index.py`'s build context is now a published contract
  (`BuildContext`), so its shape can no longer change freely.
- Two builds in one process re-import an edited extension, because the module
  name carries its content digest. Long-lived processes therefore do not serve
  stale extension code.
