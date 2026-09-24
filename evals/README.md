# Evals: what a session with no context can actually do with this

The test suite checks that EOS answers correctly. It cannot check the thing
that has failed most often, which is whether a reader who has never seen this
project can get to the answer at all.

Every defect found this way so far was invisible from a unit test, because the
engine was right each time:

- `eos rules` returned nothing and did not say why. The session guessed at a
  rescan, guessed wrong, and spent an hour proving the tool had looked.
- The skill described `eos draft-test` as writing the test. It writes a
  skeleton. A reviewer told otherwise trusts the output instead of reading it.
- The question the work needed — one flow's refusals that no test asserts —
  did not exist, so the session ran the broad question and read a column by
  eye.

`tests/test_documents_match_reality.py` now catches the mechanical half of
that: a command a document names and the CLI does not have, a renamed flag, an
answer that is silence, a claim the command's own output contradicts. What it
cannot judge is whether a sentence is *useful*, whether the path through the
tools is the one a reader would find, or whether the question they needed
exists. That still takes a session with no context and a real question.

## The protocol

One scenario, one session, and the session must be genuinely fresh: no
transcript, no prior findings, nothing from whoever wrote the scenario. It is
given the skill document and a real task, and nothing else. It is told to be
unsparing, and that reporting a dead end is the point rather than a failure.

What it reports back is not the answer to the task. It is:

1. Every command it ran, in order, including the ones that went nowhere.
2. Whether it needed `eos --help` or the source to find a command, or found it
   in the document on first read.
3. Every wrong turn: what it believed, what made it believe that, and what
   corrected it.
4. Anything it wanted and could not ask for.
5. Every number it saw, so a later run can tell drift from disagreement.

Point 5 is worth the trouble. A number that changes between runs is either the
project moving or the engine changing, and neither is visible if nobody wrote
the first one down.

## Scoring

There is no score. There is a list of defects, and each one is either fixed,
recorded as a known limit, or argued down in writing. A run that finds nothing
is reported as finding nothing — it is evidence about the documents, not a
failed run, and padding it with nits would make the next run's findings
worth less.

The one number worth keeping is how long the session took to the first correct
command, because that is what the documents are for.

## Running one

`run-template.md` is the prompt, with two slots to fill: the project the
session works on, and the scenario. Fill them, start a session that has none
of your context, and paste it.

Scenarios are in `scenarios/`. They are written against no particular
codebase, because this repository deliberately carries no project identifiers
or workspace paths (`tools/check-clean.sh` enforces that). Supply the concrete
project when you run one.

One of them, `where-did-we-leave-off.md`, is not a documentation check: it
is the yardstick of `docs/plans/operational-memory.md`, run once before that
plan's first milestone and once after its last, by different sessions, so
that "done" is a comparison of two reports rather than a claim.

## Where results go

Not here, for the same reason: a result names a real project, its real
numbers, and often a real ticket. Keep the record wherever that project's
working notes live, one file per engine version, and cite the engine version
in it — a finding against 0.26.0 is not a finding against 0.31.0, and the only
way to tell later is to have written it down.
