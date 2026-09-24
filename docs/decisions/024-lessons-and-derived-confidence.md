# ADR-024: A failed run leaves a lesson; confidence is derived, never stored

## Status

Accepted, 2026-09-24. Milestone M4 of `docs/plans/operational-memory.md`.

## Context

M1–M3 made the engine remember what happened and hand it to the next
session. What it could not yet do is learn: a failed run carried a free-text
`lesson` string in the ledger, and nothing else. The next session saw it in a
run line, if that run was among the last three. The lesson was not a note, so
it was not searchable beside the findings, not auditable, not injected when a
file it concerned was touched, and not linked to anything a person could
revise.

The other half is trust. A procedure's counters (ADR-023) say how often it
worked. A reader still has to turn "7 ok, 1 failed, last verified 40 days
ago" into "can I follow this", and every reader does it differently.

## Decision

**`lesson` and `decision` are note kinds.** Each has required sections,
validated like a defect's:

- `lesson` — `## What went wrong`, `## What was learned`, `## Next time`;
  front matter `execution:` (the run that taught it) and optionally
  `procedure:`.
- `decision` — `## Why`, `## When`, `## Component`.

Both are findings for retrieval (never bulk) and live in the notes store, so
they inherit search, injection, the audit and git transport.

**A failed run must leave a lesson.** `eos run finish --outcome failed` is
refused unless it carries `--lesson`, or a lesson note already names the
execution (`eos note add --kind lesson --execution <id>`). The refusal prints
both commands. With `--lesson`, the finish writes the lesson note itself:
what went wrong is the lesson and the run's non-zero exits, what was learned
is the lesson, next time is `--next-time` when given. A lesson already
recorded under the same title is not duplicated; the new run is appended to
it under `## Seen again`, so a recurring failure reads as recurring.

The Stop hook asks once about executions this session left open, as it asks
about claimed work: an open run reads to the next session as one still in
progress.

**Confidence is derived at read time and never stored.** `procedure
confidence` is a word computed from the ledger and the note when it is read:

| word | when |
|---|---|
| `failing` | the latest finished run of the procedure failed |
| `unverified` | no run has finished ok |
| `fresh` | last verified within 30 days |
| `aging` | within 90 days |
| `stale` | older |

printed with the numbers beside it. Stored, it would be the confidently
wrong shape ADR-020 refuses for `stale`: true when written, false a month
later, with nothing to say so. The engine still draws no verdict (ADR-018):
the word summarises observations, and a person decides what to do with a
`failing` procedure.

**A verification belongs to the run it happened in.** A verification record
carries `session` and `execution`; `eos verify` inside an open execution fills
both and appends a `verified` event to that run, so `eos run show` lists the
checks next to the steps they checked.

## Consequences

The fact table's `origin` ladder (`documented`, `inferred`, `verified`) is
still unused, and `fact.confidence` still holds detector constants. That is
a statement about static facts, not about procedures, and is left as a known
limit rather than conflated with this.

A session that fails and walks away without finishing leaves an open run,
not a missing lesson. The Stop hook narrows that window; it cannot close it.
