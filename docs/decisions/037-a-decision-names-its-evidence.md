# ADR-037: A decision names its evidence

## Status

Accepted, 2026-10-29. Shipped in 2.5.0. Phase P1 of the evidence-first
platform design (nexus `docs/superpowers/specs/2026-10-29-evidence-first-platform-design.md`).

## Context

A session acted on things it could have checked: a failed run's lesson, again
and again, was "something checkable was assumed". The ledger could not say
afterwards what a decision rested on, because it held no task decisions at all.

Measured in the nexus EOS store on 2026-10-29 (`eos query`), per the design
spec:

- 474 `decided` events, every one `tool=route` (model routing). Not one
  recorded a decision about the task, so a later "why" had nothing to answer
  from.
- 443 `verified` events, 295 of them `tool=subagent`. That is a citation
  count, not a verification, so "verified" in the ledger did not mean a check
  had run. Re-measured after `automation/env-ready.sh` landed: 475 and 297,
  still no task decision.

## Decision

**A decision is an event.** `eos decide --claim "<one sentence>"
[--evidence <id>]... [--check "<command>"]` appends one `decided` event, tool
`decide`, `ref` = the decision id (`d-` plus 8 hex digits), to the ledger of
the open run (`core/decisions.py:25`, `:209-222`). A state change is another
event with the same `ref`; the latest wins (`:172-193`). Nothing is stored
outside the execution ledger (ADR-020/022).

**Evidence is a list of ledger event ids.** An id is `e-xxxxxxxx` (the id
`eos-event` writes on every event) or `<execution>#<n>` (the position of an
event written before ids existed). Anything else is rejected before writing
(`:31`, `:88-93`). Each event with an output carries a fingerprint: the
sha256 of the raw output file and its absolute path, written by
`bin/eos-event --output <file>`. The content is never copied into the ledger.

**What counts as evidence** (`_refusal`, `core/decisions.py:70-85`). A cited
event is accepted only when all of these hold:

- it belongs to this work item (or, when the run has none, to this run);
- it is not `kind=verified, tool=subagent`;
- its kind is `ran`, `called`, `read` or `verified`, and its source is
  `wrapper` or `hook` (`:26-27`);
- a `called` event has no non-zero exit code;
- if its tool is a live environment tool (`LIVE_TOOLS`, `:21-22`: db, obs,
  admintoolbox, argo, bssapi, apitest, scenario, env-ready, grafana, kafka,
  ntf, eca, usermgmt), it is at most 24 hours old (`LIVE_MAX_AGE`, `:23`).
  Every other event stays valid for the life of the work item.

**Refusal codes.** A decision with any refusal is an `assumption`, and every
code that applied is stored and printed as `reason=<code> evidence=<id>`
(`:55-57`):

- `unknown-id`: no event with that id in any ledger the project reads.
- `other-work-item`: the event belongs to another work item (or run).
- `subagent-report`: a `verified` event whose tool is `subagent`.
- `not-recorded-by-a-tool`: the kind is not `ran|called|read|verified`, or the
  source is not `wrapper` or `hook` (for example `cli`).
- `failed-call`: a `called` event with a non-zero exit code.
- `stale`: a live environment tool's event older than 24 hours.
- `hedged`: the claim itself contains a hedge word (`probably`, `likely`,
  `should`, `assume`, `muhtemelen`, `sanirim`, ...; `:28-30`, matched after
  folding Turkish letters). Added by `record` whatever the evidence.
- `no-evidence`: no `--evidence` given.

**States** (`STATES`, `:20`).

- `evidenced`: recorded with evidence and no refusal.
- `assumption`: any refusal. `eos decide --open` lists the open ones with
  their `check`.
- `resolved`: `eos decide --resolve <d-id> --evidence <id>` on an assumption,
  with evidence that passes every rule. Refused evidence writes nothing: the
  assumption stays as it was, the codes are printed, the exit code is 3.
- `approved`: the user said yes. Only the prompt hook calls
  `decisions.approve` (source `hook`, the prompt excerpt kept as `check`).
  The fold drops an `approved` event whose source is not `hook`
  (`:185-186`), and `eos decide` has no way to ask for it.

**`eos why-did [path] <run id | work item id | ticket key>`** prints the
decisions of that scope in time order, each with its state, check, reasons
and cited events: tool, target, exit code, time, and the output file as
`present (sha ok)`, `present (sha differs)`, `gone` or `unreadable`, with its
path (`core/cli/whydid.py:20-46`). `--grep` keeps decisions whose claim holds
every word. It writes nothing.

## What it does not do

- **No relevance check.** The rules test the evidence, not whether it speaks
  to the claim. A decision can cite a real, fresh, wrapper-recorded event that
  has nothing to do with it and be `evidenced`. A reader of `eos why-did` must
  still read the cited output.
- **`--source` is not authenticated.** `bin/eos-event` takes `--source
  wrapper|hook|cli` from its caller (`bin/eos-event:5`, `:54-57`); anyone who
  can run it can write a `wrapper` or `hook` event. The source check keeps
  honest paths apart; it is not a barrier. A P2 gate that blocks work must not
  trust the source alone.
- **Known gaps left for P2.**
  - An event whose `at` cannot be parsed makes `_parse` raise for a live-tool
    event, so `eos decide` fails with an error instead of a `stale` code.
  - A legacy id `<execution>#<n>` is the event's position in the loaded run
    (`core/executions.py:682`); log rotation can shift it, so the id may then
    name a different event or none.
- It does not stop an action taken on an assumption. It makes the assumption
  visible and listable; enforcement is the evidence gate (P2).

## Consequences

- From 2.5.0 a task decision has a place in the ledger, and "why" is
  answerable from it with the output fingerprint to check against.
- The 24-hour limit and the `LIVE_TOOLS` set are code constants; a new live
  environment wrapper must be added to the set or its events never go stale.
- Hedge detection is a word list: a hedge in other words passes as
  `evidenced`, and a claim quoting "should" is marked `hedged`.
- An output file moved or deleted after the event shows as `gone`; the event
  still counts as evidence.
