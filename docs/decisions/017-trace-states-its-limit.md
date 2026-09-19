# ADR-017: A Trace States What It Cannot See

**Status:** Accepted
**Date:** 2026-09-19
**Deciders:** yasinkilinc

## Context

With declaration-site edges in place (ADR-015), "what does this endpoint do"
looked answerable by walking the call graph forward from an entry point. It
is not, on the architecture this was measured against, and the numbers are not
marginal.

On one service with its linked parent:

- 235 files throw a behaviour code. **6 of them are reachable from any of the
  39 entry points.**
- 120 of the unreachable ones are `*Command` classes. A representative one has
  **zero inbound edges of any kind**.
- 252 of the project's 475 named components have no caller anywhere.

The cause is not a parser gap. A coordinator resolves each step of a chain by
bean name, from a row in a database, at run time. The configuration is the
program; the call graph is a partial view of it by construction. A forward walk
from `/fmProductOrder` correctly reaches the coordinator and then stops,
because in the source there is nothing after it.

A trace that printed only that walk would be accurate about what it followed
and wrong about what it implied — the most expensive kind of answer, because it
looks complete.

## Decision

`eos trace <entry-point>` reports three things, and the third is not optional.

1. What the entry point serves — the routes, from parsed annotation arguments.
2. What it reaches — a bounded forward walk over declaration-site edges, with
   the hop count per file, and the behaviour codes reachable that way.
3. **What no call-graph answer can reach**: the count of named components in
   this project with no inbound call, `new` or field edge, with examples.

Point 3 prints on every run, including the runs where it is inconvenient, and
including when point 2 is rich. "Registered under a name and uncalled" is a
generic, language-level signature of a component a container resolves at run
time — from a configuration row, an annotation scan, a service loader — so this
needs no knowledge of any particular framework.

A behaviour code thrown behind a runtime-wired component is **not** reported as
reachable. Pinned by a test, because the tempting shortcut — union everything
the endpoint might plausibly touch — is exactly the confident wrong answer.

## Consequences

- `eos trace` is trustworthy on a configuration-driven system: it says what it
  followed, and how much of the system it structurally could not.
- The bridge is the index extension. A project that holds its chain
  configuration can index it (`extensions/journeys.py` resolves bean name to
  owning class from an exported snapshot) and answer what the call graph cannot.
  EOS core supplies the signal that such an extension is *needed* — the
  uncalled-component count — without knowing anything about that project.
- Two more predicates on the generic `fact` table (`annotation`, `bean-name`),
  no new table. Type-level annotations only: a `@DisplayName` on a test method
  is a label for a human and would bury the handful that decide wiring.
- This is the reason a "workflow" entity was **not** introduced here. A
  reachability set is not a business workflow, and naming it one would encode a
  judgement as a fact — the same restraint ADR-016 applies to behaviour codes.
  A workflow model is worth building when a provider can fill it from something
  that actually states the workflow; a forward walk is not that.
