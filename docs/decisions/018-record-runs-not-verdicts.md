# ADR-018: Record Runs; Do Not Conclude From Them

**Status:** Accepted
**Date:** 2026-09-19
**Deciders:** yasinkilinc

## Context

The coverage ladder tops out at `asserted`: a test reaches the class that
raises a refusal and names the code. That is still static analysis. The only
thing that observes the behaviour rather than inferring it from structure is a
run.

Two questions follow, and they have different answers.

**Who runs it?** Executing a test needs a build tool, an environment,
credentials and minutes. `core/` is stdlib-only by design (ADR-010), and the
workspace this was measured in already has the right contract: a wrapper that
keeps the full log on disk and prints a fixed-shape digest, measured at 13k
tokens of Maven output reduced to five lines. Making EOS a build client would
trade a defining property for work something else already does well.

**What does a failure mean?** Nothing in an exit code separates "the rule is
not enforced" from "the test is wrong" from "the environment was". A system
that guessed would produce exactly the confident wrong answer this codebase is
built to avoid — and a wrong `confirmed-defect` costs more than a hundred
missed ones, because it is the claim people act on.

## Decision

**An adapter executes; EOS records the evidence.** `eos verify` takes the
command, the exit code, where the log is and optionally the output (only its
digest is stored), and writes an append-only record with the commit the tree
was on and the time.

**`outcome` is observed; `verdict` is not.** `outcome` is `passed`, `failed`
or `errored` — what the run did. `verdict` is one of five conclusions a person
may reach, and **EOS never writes one**. A failing run with no verdict prints
the reason it has none, once, at the moment it is recorded.

**A passing run is the top rung of the ladder** (`verified`), because it is the
only rung that observed anything. **A failing run does not demote the static
reading**: the test existing and the test passing are different facts, and the
failure is read in `eos findings` rather than by deleting what analysis found.

**Records live beside the notes, not under `.eos/data`.** `eos scan` rewrites
everything derived, and an execution cannot be recomputed from a file tree
(ADR-005). They are JSONL rather than Markdown because they are dated
observations, not prose: append-only, one object per run, and an unreadable
line is skipped rather than fatal.

## Consequences

- The spine the last several phases built is closed end to end: a behaviour
  code, the class that raises it, the test that reaches it, the draft for the
  assertion that is missing, and the record of what happened when it ran.
- EOS stays stdlib and stays out of the business of running builds.
- The `verified` count is small and honest by construction. It can only grow
  by someone running something, which is the point: a coverage number that can
  rise without anyone executing anything is measuring the analyser, not the
  software.
- No finding is ever labelled a defect by this system. The word appears only
  in a verdict a person chose, stored next to the command and exit code that
  person was looking at.
