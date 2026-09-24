# ADR-023: A procedure is a note

## Status

Accepted, 2026-09-24. Milestone M2 of `docs/plans/operational-memory.md`.

## Context

A fresh session asked how a recurring task is done in a project it has
never seen has three places to look, and in the audited workspace all three
failed: the engine had no procedural entity at all; the build file described
build and test and stopped; the procedure itself lived as forty lines inside
an instruction document the session would have had to know to open, at the
cost of the whole document. The session answered honestly that no procedure
was recorded. A less careful one would have improvised a plausible sequence
of steps and presented it as the project's, which is the worse failure
because it reads as competence.

The execution ledger (ADR-022) records what a session did. It says nothing
about what a session *should* do, and it must not be made to: a run is what
happened once, a procedure is what every session should do, and the plan
this ADR belongs to depends on telling the two apart.

## Decision

**A procedure is a note of `kind: procedure`.** Not a fourth store, because a
procedure has a note's lifetime — durable, true until the system changes —
and notes already carry everything a procedure needs: retrieval, injection
when a scoped file is touched, scope hashes and the staleness audit, the Stop
gate, git transport. Not a work item, because work is what one session
intends and a procedure outlives every session that follows it.

**Authored sections, parsed.** The body carries `## Steps` (an ordered list,
required, at least one step; a step may name its tool as `(tool: <name>)`),
and optionally `## Prerequisites`, `## Success`, `## When not to use this`.
The engine parses these; it does not interpret them. Adding a note of this
kind without steps is refused, as a defect without a cause is.

**Observed fields, maintained by one writer.** Front matter holds
`procedure` (a slug, derived from the title unless given), and four fields
the engine owns: `runs_ok`, `runs_failed`, `last_verified`,
`last_execution`. They move in exactly one place — `eos run finish` for an
execution that names the procedure — and nowhere else:

- `ok` → `runs_ok` + 1, `last_verified` = the finish time
- `failed` → `runs_failed` + 1, and the lesson's first line is appended under
  `## Known failures`
- `abandoned` → `last_execution` only; an abandoned run says nothing about
  whether the procedure works

These are observations, not a verdict (ADR-018). Nothing here marks a
procedure recommended, trusted or deprecated; a person does that by editing
the prose. A hand edit of a counter is not prevented — the file is a text
file — but `eos procedure audit` recomputes the counters from the ledger and
reports any disagreement, so an edited count is visible, not silent.

**Every rewrite keeps them.** `note amend` rewrites front matter from the
note it read; it now carries the procedure fields through, because a revised
step list must not reset a procedure's history to zero.

**Catalogues the host already keeps are indexed, not copied.** Where a host
has machine-readable step definitions with recorded state — a scenario
engine, a runbook directory — an index extension (`extensions/procedures.py`)
reads a normalised JSON catalogue the host exports and makes it queryable
through `eos ask`. The engine never learns the host's format; the host's
exporter is the adapter (ADR-013).

## Consequences

A project has two kinds of procedure: authored notes (the ones the brief will
hand a session in M3, with counters) and indexed catalogue entries (queryable,
with the host's own last state, never counted by the engine). They are kept
apart on purpose: the engine only counts what it saw an execution finish.

Counters live in a git-tracked file, so two machines finishing runs of the
same procedure between pulls will conflict on the front matter. That is the
same conflict any two edits of one note produce, it is small, and the ledger
is the source of truth `procedure audit` recomputes from — so resolving it by
taking either side and running the audit loses nothing.

Confidence is not stored and not computed here. M4 derives it at read time
from these counters and the age of `last_verified`.

## Addendum (1.1.0): rules, and how a prompt names a procedure

**`## Rules`.** A procedure may carry the rules that must hold while its task
runs — typically ones a host moved out of its always-loaded instructions
because they matter only during that task. Moving a rule there is only safe if
it arrives, and an earlier measurement showed a rule that is findable but not
delivered does not change behaviour. So the brief prints the section right
under the procedure's header, **whole and past its budget**: a rule clipped
for length did not arrive. The exemption is bounded where it cannot drift —
when the note is written or amended, a Rules section longer than
`RULES_MAX_CHARS` (600) is refused.

**Naming.** The brief is context nobody asked for, so the bar for putting a
procedure in it is that the prompt *names the task*. Measured on a host store,
three things let the wrong one through or kept the right one out:

- The picker scored a procedure's body too. One word of a step ("continue
  from where it stopped") printed ~2,000 characters for "ok, continue". It
  now reads title and tags only; body matches stay in search.
- The tokenizer was ASCII-only, so "PR aç" lost both words and "planı" became
  "plan", matching an unrelated "rate-plan-change". Words are now letters and
  digits of any script, casefolded; a two-letter token survives when it is an
  acronym in capitals (PR, CI) or contains a non-ASCII letter ("aç").
- An issue key split into prefix and number, and the prefix alone matched
  every note about another issue of the same project. `NAME-123` is now one
  word (plus its number); the bare prefix is dropped from that text.

Title-and-tags coverage alone then missed a prompt whose other words are
filler in another language — rare only because the notes are in English, so
they outweighed the word that named the task. A procedure therefore also
counts as named when the prompt words its title and tags hold weigh at least
as much as one word carried by fewer than one note in seven (one in √N on a
store too small for that).
