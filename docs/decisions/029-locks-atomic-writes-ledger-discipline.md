# ADR-029: Locks, atomic writes and ledger discipline

## Status

Accepted, 2026-09-27 (2.x roadmap F2, F5). Branch `eos-2x`, released in 1.7.0.

## Context

Several sessions write the same files: a procedure's counters when a run
finishes, a note's section, the telemetry, routing and hook logs when they trim,
the run pointer, the run ledger. Every one of those was read, changed and
written back with no lock. Measured: four processes incrementing one counter 25
times each kept 35 of 100 updates; sixteen runs of one procedure finished at the
same moment lost counts. A comparable tool lost 11 of 12 concurrent writes before
it added an exclusive lock (Ruflo #2878). The run ledger also grew without bound,
had no version on its lines, and accepted a finish for a run nobody started.

## Decision

- `core/lib/lock.py`: `<file>.lock` created with `O_EXCL`, holding the owner's pid
  and start time. Taken over when older than 10 s or when its owner is dead; a lock
  that is empty or unreadable (created, pid not yet written) is stale only by age —
  the first version judged it dead and let two writers in. Waiting past 15 s raises
  `LockTimeout`; a statistic waits 2 s and skips.
- `core/lib/atomic.py`: temp file beside the target, fsync, rename, directory fsync.
- Every shared read-modify-write uses both: procedure counters and note sections
  are re-read under the note's lock; the three logs append and trim under one lock;
  the run pointer is written atomically. `tests/test_write_discipline.py` holds the
  shared-state modules to that.
- The run ledger: `v` on every line (Python writer and `eos-event`); rotation past
  8 MB under its lock, keeping four old files that `load_path` folds oldest first;
  `finish` refuses an id never started.

Not done: collapsing identical consecutive events. On the host 20% of events repeat,
all real wrapper calls at distinct times; collapsing them needs readers that count
`count`, which belongs with the incremental fold (E5).

## Consequences

- A crashed writer delays the next one by at most 10 s.
- Lock files appear beside notes for the length of a write; they are removed after.
- `eos-event` (shell) appends without the lock; an append during a rotation lands
  in the rotated file, which the fold still reads.
