# ADR-019: Telemetry Records Calls, Never Questions

**Status:** Accepted
**Date:** 2026-09-19
**Deciders:** yasinkilinc

## Context

EOS exists to cost an agent fewer tokens than rediscovery would. That is a
claim, and until now the only numbers behind it came from `eos bench`, which
measures against baselines on demand. What was missing is what actually
happens: which calls a project makes, how long they take, and how much comes
back. The cost of a habit is only visible after the habit forms.

The obvious implementation is also the one that makes a tool untrustworthy.
A log that captured arguments would capture a search query, a task
description, a note body — whatever somebody typed — and would sit in every
project EOS is dropped into.

## Decision

**Record the call, never the question.** The command name, the *names* of the
flags passed, the milliseconds, the size of the answer, whether the index had
to be rebuilt. There is no code path by which a flag's value reaches the file,
and a test asserts that a distinctive string passed to `--search` does not
appear in the log.

**Off unless a project turns it on** — `[telemetry] enabled = true`. A tool
that starts logging without being asked is one people disable entirely, and
then it measures nothing.

**It may never break the command it measures.** Every failure in the writer is
swallowed. The first implementation violated this in a subtler way: it
buffered stdout to measure it and replayed the buffer, which broke the
escaping a non-UTF-8 terminal needs — caught by the test that exists for that.
The counter now forwards to the real stream as the command writes.

**The answer is what the caller receives, not what reached the terminal.**
`eos context` writes a file and prints one line about it; recording the line
would make the most expensive call in the system look like the cheapest, so a
command may declare its real answer size.

**Medians, not means.** One cold index build is not what the next call costs,
and an average lets it claim otherwise.

## Consequences

- `eos cost` answers "what has EOS cost this project" per command, with
  counts, median latency, median tokens returned, rebuild count and failures.
- Reading the cost is not itself recorded: a reader that changes what it
  reports is not a measurement.
- The log is bounded at 10,000 lines — under 1.5 MB — so nobody has to prune
  it and it cannot become a slow leak in twenty projects at once.
- **No effort routing follows from this yet, deliberately.** ADR-011 requires
  ground truth or a labelled baseline, and "the minimum sufficient reasoning
  effort" has neither until a log can say whether a task succeeded at a given
  effort without escalation or rework. This records the raw material. The
  score, if it is ever published, comes after the log can justify it — and
  even then EOS publishes complexity and the host chooses, because the model
  in play is the caller's decision, not this system's.
