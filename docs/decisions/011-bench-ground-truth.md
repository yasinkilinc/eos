# ADR-011: `eos bench` Measures This Project, Against Ground Truth Where One Exists

**Status:** Accepted
**Date:** 2026-09-12
**Deciders:** yasinkilinc

## Context

A tool that claims to speed up code search needs a number, not an assertion.
But two dishonest shortcuts are easy to fall into: benchmarking against a
synthetic project chosen to flatter the tool, or benchmarking a claim that has
no independent ground truth to check it against (comparing "files EOS says are
impacted" to "files EOS says are impacted" proves nothing).

## Decision

`eos bench`, implemented in `core/bench.py`, measures EOS's own tools against
plain alternatives **on the project `eos bench` is run against** — never a
bundled fixture. Its two measurement classes are handled differently:

- **`find_symbol` has ground truth.** The language plugins already parsed the
  project and know which symbol is defined in which file — that is an
  objective fact, independent of EOS's search path. `eos bench` samples
  symbols from that parsed set and checks whether `find_symbol` returns the
  file the plugin already recorded.
- **Impact analysis has no ground truth.** "Which files does changing X
  affect?" has no independently-known correct answer, so `eos bench` compares
  against `grep` as a labeled baseline, not a correct answer, and the
  generated report says so explicitly.

The report is scoped to the project it ran against, and says so: a symbol
lookup that is fast on a small Python codebase may behave completely
differently on a large Java one, and the report never generalizes past the
one run it describes.

## Consequences

- `eos bench`'s numbers are reproducible by any user, on their own project,
  with no fixture to keep in sync with reality.
- The report cannot overstate what it measured — a comparison-to-baseline
  claim always says "baseline," never "correct."
- A future measurement class must fall into one of these two buckets before
  being added: real ground truth, or a labeled baseline. A number with
  neither does not go into `eos bench`.
