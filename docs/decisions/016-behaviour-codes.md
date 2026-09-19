# ADR-016: Behaviour Codes, Not "Business Rules"

**Status:** Accepted
**Date:** 2026-09-19
**Deciders:** yasinkilinc

## Context

The goal is for EOS to answer "which business behaviours are tested, and which
are not" without inventing requirements the code never stated. The tempting
route is inference: read the code, ask a model what rule it implements, record
the answer. This project has already paid for that route once — a predecessor
mined git diffs for knowledge, immediately needed a reconciliation pass to undo
the damage, and ended up with a trimmed machine record written over a real one.

There is a deterministic route on this kind of codebase. Measured on one
service plus its linked parent: 94 `throw` sites in the overlay alone carry a
SCREAMING_SNAKE string constant, across 70 distinct codes, and 403 across the
whole tree. Those constants are not incidental — the code is what the API
returns, what a test asserts, and what a ticket quotes.

## Decision

Extract **behaviour codes**, and call them that.

A `throw` whose statement carries a code-shaped literal — SCREAMING_SNAKE with
at least one underscore — yields `throws-code` and `throws-code-at` facts, with
the throwing class, method, exception type and `path:line`. The same constant
found anywhere else yields `names-code`. Both are `origin=extracted`,
`confidence=CERTAIN`: they are read, not inferred.

The underscore is the discriminator. `throw new IllegalStateException("something
went wrong")` is a message; `AGE_LIMIT` is a name the organisation shares. Both
spellings are read — `throw X.of("CODE", …)` is a factory call and
`throw new X("CODE", …)` a constructor, and a reader looking for only one finds
half the behaviours.

**`eos rules` is the command that makes the schema real.** It lists each code,
where it is thrown, and which test files name it — untested first, because the
gap is the answer people came for.

**The name is deliberately modest.** EOS does not claim to have found a
business rule: it has found an identifier the code throws. Whether that
identifier corresponds to an intended rule is a judgement nothing here makes.
Naming it `rule` in the data model would encode that judgement as a fact.

**The coverage claim is bounded on every run.** "343 of 403 codes are named by
no test" is a floor, not coverage — naming a code proves the suite knows the
behaviour exists, not that the path reaching it is exercised. The command
prints that sentence every time, because a number this quotable gets quoted as
something it is not.

## Consequences

- Question 6 of the brief ("which important behaviours are not tested") has a
  deterministic answer today, with no model in the loop and no new entity type
  whose schema could sit empty.
- It is a floor. A path-level answer needs the test-to-code mapping of a later
  phase; this one is honest about being the cheap half.
- A project whose refusals are not identified by constants gets an empty
  answer, and `eos rules` points at `eos why` — which reports whether the
  detector ran at all. Empty because nothing was found and empty because
  nothing looked stay distinguishable (ADR-014).
- No new table: the generic `fact` table absorbed three predicates, which is
  the property ADR-014 was designed for.
