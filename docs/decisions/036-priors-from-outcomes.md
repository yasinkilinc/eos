# ADR-036: Priors from outcomes

## Status

Accepted, 2026-10-13. Roadmap item M6 (phase 2). Shipped **off by default**
(`[notes] prior_alpha = 0`); see "Measured".

## Context

Nothing read run outcomes back into retrieval: a note that verified runs kept
using ranked exactly like one no run ever opened. The research report (§17, M6)
proposed a bounded prior per note, moved by run outcomes, applied scaled in the
one scorer (F3), recomputed by `eos consolidate`, with the risk "priors bias
retrieval towards the past" mitigated by an integer budget and `alpha <= 0.2`.

A prior needs a link from a run to the notes it used. Before this change the
ledger held two:

- the procedure a run followed (`run start --procedure`, ADR-023) -- a note;
- `changed` events naming note files -- notes the run *wrote*, which are its
  output, not something it used (a lesson is written by the run that failed).

What a session read was not recorded anywhere a fold can reach. The hooks keep
the note bodies injected into a session in per-session state, but that state is
outside the ledger, per session rather than per run, and mutable; the agents'
own reads (`Read` of a note file, `cat <note>` after a search) left no trace.
On the host, of 68 runs labelled `verified`/`claimed`, 23 were verified, 9 of
those named a procedure, and none recorded a note read.

## Decision

**The link.** The hooks record a `read` event, `ref: note:<file name>`, no
tool, on the open run when a call reads a note file: a `Read` of it, or a shell
command naming it (`cat`, `sed -n`, ...). A note file is an existing `.md`
directly in the notes directory of the project or of a workspace project
(`projects.toml`). `eos note show` records the same event (source `cli`) when
the session has a run open. The event has no tool so it never satisfies a procedure's
Success check or sits on a step (ADR-031). A run references the note of the
procedure it followed and every note it read, each once.

**Derived, never stored.** There is no `prior` or `prior_event` table. A prior
is a fold of the execution ledger (ADR-020/022), recomputed on every read: no
counter to drift, lose a write or be edited by hand. The fold reads only start,
finish and note-read lines (0.02 s on the host's 10 MB ledger, against 0.26 s
for the full fold) and only when the weight is above 0.

**What moves a prior.** Only `outcome = ok` with `outcome_source = verified`
(ADR-028: the checks the run's changes needed passed after its last change).

- `claimed` does not: it is the session's word, and proxy evidence never
  promotes (§17 U4).
- `failed` does not demote: reading notes is what a session in trouble does, so
  a failure says nothing against what it read. Lessons carry failures (ADR-024).
- `abandoned`, open runs and `ok` with no check do not.
- A note written in the run's own session earns nothing from it: reading back
  what you just wrote is not using it.
- `eos note eval` does not move priors. The golden sets are the gate that
  decides whether priors help; priors learnt from them would make the gate
  measure itself.

**The budget.** Each contributing run is worth one integer point, split across
the notes it referenced. The exact shares (fractions) are summed over all runs,
and the total -- the number of contributing runs -- is handed out in integers by
largest remainder, an equal remainder going to the name that sorts first. So
the points add up to the runs exactly, and a tie inside one run is not settled
the same way in every run, which a per-run split with one point would do. A
prior stops at 100.

**Applied scaled, after the threshold.** `notes.rank` takes `priors` (file name
-> 0..100) and `alpha`, and adds `alpha * prior / 100` to a note that already
passed the match threshold and the score floor, which read relevance alone. A
prior reorders matches and never makes one, and a full prior at the maximum
weight is a fifth of the relevance scale. `search_notes` passes the project's
priors (so the brief, `eos note search`, MCP and `note eval` get them); a host
ranking several stores calls `priors.for_search(<project>, corpus)` and passes
the result.

**The knob.** `[notes] prior_alpha`, a number clamped to `[0, 0.2]`; anything
else is the default. 0 is off and skips the ledger read. Default 0.

**Visible.** `eos consolidate` prints a `PRIORS` block: points, contributing
runs, notes, the weight and whether search uses it, and the top notes. It folds
the runs the report already loaded (6 ms on the host; the whole report 7.7 s).

## Measured

The host's seven golden sets, 119 labelled queries, both paths: the host path
(`notes.rank` over the union of every store, the workspace's synonyms) and the
per-store path (`notes.search_notes` on the project holding the labelled note).
Weight 0.1, chosen before measuring. Priors from the host's ledger: 9 points
from 9 verified runs over 4 procedure notes.

| Set | n | host r@1 | host r@3 | per-store r@1 | per-store r@3 |
|---|---|---|---|---|---|
| workspace | 33 | 0.879 | 1.000 | 0.909 | 1.000 |
| procedures | 16 | 0.500 | 0.812 | 0.562 | 0.812 |
| journeys | 9 | 0.667 | 1.000 | 0.667 | 1.000 |
| one service | 20 | 0.800 | 0.900 | 0.900 | 0.900 |
| skill sections | 19 | 0.737 | 0.947 | 0.789 | 1.000 |
| instruction file | 14 | 0.714 | 0.929 | 0.714 | 0.929 |
| capability policy | 8 | 0.750 | 1.000 | 0.750 | 1.000 |
| all | 119 | 0.748 | 0.941 | 0.790 | 0.950 |

Before and after are identical: not one query's top three changed on either
path. A what-if, to see what the read link would have done: each verified run's
reads replaced by the note bodies the hooks injected into its session (14 runs,
10 notes) -- also no change, at weight 0.1 and at the maximum 0.2. The gate
(recall@3 not lower on any set, recall@1 not lower overall) passes, vacuously.
The phase 6 target, recall@1 >= 0.85 on every set, is not met with or without
priors (all sets 0.748 host, 0.790 per-store).

## Consequences

- Off by default: a pass in which nothing moved is no evidence that priors earn
  their place, and with the read link they will grow from data no gate has seen.
  A project turns them on with `[notes] prior_alpha = 0.1`, after re-measuring
  its golden sets once its ledger holds verified runs with note reads.
- No α was tuned. Tuning one on the 119 queries would fit the test set; a
  future tuning needs a dev/test split of the golden queries.
- Every note read from now on is one more `read` event in the ledger (the
  host's agents read notes a few times per run).
- Host history before this change contributes through procedures only: 9 of
  23 verified runs. Note reads before it are not recoverable from the ledger.
- MCP note reads are not recorded; a host whose agents read notes that way
  adds the event there.

## Addendum (2.4.2): recency weighted the same way, measured, not shipped

Freshness was tried as a second bonus beside the prior: `[notes] recency_weight
* 0.5 ** (age_days / 90)`, age from `updated:` else `created:`, added after the
threshold so it reorders matches and never makes one. Measured on the host's
note-search golden sets (950 notes), recall@1 at weight 0 / 0.05 / 0.1 / 0.2:

| set | queries | 0 | 0.05 | 0.1 | 0.2 |
|---|---|---|---|---|---|
| nexus | 33 | 0.818 | 0.818 | 0.818 | 0.818 |
| turkish prompts | 33 | 0.455 | 0.424 | 0.424 | 0.424 |
| instruction file | 14 | 0.786 | 0.786 | 0.786 | 0.786 |
| skill sections | 19 | 0.737 | 0.737 | 0.684 | 0.684 |
| capability policy | 8 | 0.875 | 0.875 | 0.875 | 0.875 |

No set improved; two lost a query at weight 0.1. The case it is meant for -- a
newer note reversing an older one -- is already handled by name: `--supersedes`
retires the older note from search, `valid_until` and expiry end a status note,
and a STALE mark flags one whose scope changed. Not shipped: a knob that only
loses on every measurement is code to maintain for nothing. Revisit with a
golden set of reversed-fact questions, which none of the current sets holds.
