# ADR-022: What a session did is a third ledger

## Status

Accepted, 2026-09-24. Milestone M1 of `docs/plans/operational-memory.md`.

## Context

The engine keeps two kinds of memory. Notes say what is true (ADR-005), the
work ledger says what a session intends (ADR-020). Neither says what a
session **did**. ADR-020 is explicit about it — *an item is what a session
said, not what happened* — and an audit of a real workspace measured the
consequence: a fresh session asked "what happened the last time this was
deployed" read about twenty thousand characters and could only answer that
nothing was recorded, because nothing was.

Putting it in either existing store is wrong for reasons those stores
already give. A note that says "deployed at 09:43, build 118 green" is not
true or false and never goes stale; it happened. Auditing it for staleness,
injecting it on file touch or ranking it against findings would treat an
event as a claim. A work item is the wrong grain: one item ("deliver the
config change") spans several executions (the failed first attempt, the
retry, the verification), and an execution may belong to no item at all.

## Decision

A third append-only ledger, `executions.jsonl`, beside `work.jsonl` in the
knowledge directory, with the properties ADR-020 chose for the same reasons:
append-only and folded on read, so two writers never lose a line; a bad line
skipped, never fatal; carried between machines by git with the notes.

**One file, three line types.** `start` opens an execution with its title,
optional procedure and work item, session, agent, target, branch and the
commit it started from. `event` records one thing the session did. `finish`
closes it with an outcome and the commit it ended on. An execution's state
is the fold of its lines; an `event` line for an id with no `start` still
creates the execution, because a lost line must not hide what happened.

**Events are references, never payloads.** An event carries `kind` (a
closed vocabulary: `ran`, `read`, `changed`, `called`, `verified`, `noted`,
`decided`), `tool` (the wrapper or program), `target` (the environment or
system), `ref` (a log path, a cache path, a build id, a file path), an exit
code and a duration. Never command output, never a diff, never the prompt
(ADR-019): the ledger that carried payloads would be the context problem it
exists to solve, and the refs stay resolvable for as long as the logs and the
repository do.

**The outcome is declared, not concluded.** `ok`, `failed` or `abandoned`,
set by the session at `finish`. The engine records it and draws nothing from
it (ADR-018). What *uses* outcomes — procedure counters, lessons, confidence
— arrives in later milestones and is observation, not verdict.

**One session id across every store.** The id is read the way telemetry
reads it (ADR-021): `--session`, then `EOS_SESSION`, a project-declared
variable, then the harness's own (`CLAUDE_CODE_SESSION_ID`). It is written
into executions, and the CLI now fills it into work events and notes when
none was passed, so "what did this session do" is one join and not a guess.

**Capture happens where the action happens, in one line.** A host routes
its external actions through wrappers; the wrapper appends the event. Two
ways, same line format:

- `eos run event --kind ran --tool build --target staging --ref <log> --exit 0`
- `eos-event --kind ran --tool build …` — a POSIX-shell helper shipped in
  `bin/`, no interpreter, for wrappers where a Python start per call is too
  much. Measured: median 37 ms against 104 ms for `eos run event`; the time
  is the forks that escape each field, not the append.

Neither needs to be told which execution it belongs to. `EOS_EXECUTION` and
`EOS_EXECUTION_LEDGER` win when a host can export them; a harness that runs
every command in a fresh shell cannot, so `eos run start` also writes a
pointer keyed by session id (`$EOS_STATE_DIR`, default
`~/.local/state/eos/current/<session>`) and both writers resolve through it.
`finish` removes it.

**Capture never fails the wrapped command.** No execution open, no session
id, an unwritable ledger — the helper exits 0 and writes nothing. The cost
of that silence is visible rather than hidden: an execution with no events
is exactly what `eos doctor --memory` reports as C-06 PARTIAL.

## Consequences

The engine now holds the raw material for "what happened last time", and
nothing yet reads it at session start. That is M3 (`eos brief --task`); M1
only makes it recorded, listable (`eos run list`, `eos run show`) and
indexed (`execution`, `execution_event`, searchable by title and lesson).

Recording is only as complete as the host's capture. A command run around
the wrappers leaves no event, and the plan says so instead of pretending a
hook could reconstruct it from a command string.

The shell helper cannot run the credential refusal the Python path runs on
bodies and refs. It is meant for refs a wrapper generates (paths, build ids),
not text a person typed; a host that passes free text should use
`eos run event`.
