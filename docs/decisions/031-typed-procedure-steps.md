# ADR-031: Typed procedure steps

## Status

Accepted, 2026-09-27 (2.x roadmap E2). Branch `eos-2x`, released in 1.10.0.

## Context

A procedure's steps are prose with an optional `(tool: X)` (ADR-023). A failed run
said that it failed, never where: `run show` listed events, and a reader matched
them to steps by hand. Nothing said that a step read something no earlier step
produced, or that two steps sent failures back and forth to each other.

## Decision

- A step may carry more marks after its text, each optional: `(in: a, b)`,
  `(out: c)`, `(on_failure: N)` -- a step number, or words such as `stop`.
  `core/steps.py` parses them; the text is never interpreted.
- `eos procedure lint` adds: an input no earlier step writes and no prerequisite
  names; an on_failure naming a step that does not exist; on_failure jumps that
  form a circle.
- A run's events are placed on steps by tool, in order: the first step from the
  current one on whose tool matches, else the last one before it (a retry). A
  step's status is its last placed event's: `failed` by a non-zero exit or a
  `failed` status, `ok`, or `not seen`. `run show` prints `failed at step N: <the
  step as written>`; `procedure show` prints each step's status in the last
  finished run.
- Placement is by tool only, not by a `step` field on events: wrappers already
  record the tool, and a field they would have to fill would stay empty.

## Consequences

- Steps whose tool records no events show `-` (not seen). On the host's most-run
  procedure, three of seven steps name scripts that record nothing -- the gap is
  now visible where it was invisible.
- Two steps naming the same tool are told apart only by order.
- Not done: never-scheduled steps (needs a schedule), typed outputs checked
  against what a run produced, per-step outcomes in the brief.
