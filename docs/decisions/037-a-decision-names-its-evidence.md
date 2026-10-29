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
the open run (`core/decisions.py:26`, `:233-248`). An empty or whitespace-only
claim is refused (`:236-237`). A state change is another event with the same
`ref`. Nothing is stored outside the execution ledger (ADR-020/022).

**Which state is current** (`_fold`, `:184-217`). The decided events of the
scope are read from every ledger the project knows and sorted by `at`; the
latest in time wins, whichever ledger holds it. `at` has whole seconds, so
within one second a `resolved` or `approved` event sorts after the
`evidenced`/`assumption` event it can only follow; any other tie keeps read
order. Only an event that `record`, `resolve` or `approve` could have written
counts: its `claim_state` is one of `STATES` and its source is `cli` or
`hook` (`:30`, `:203-206`); `approved` counts only from `hook`.
`bin/eos-event` and `eos run event` cannot set `claim_state`, so a `decided`
line written from a shell (`eos-event --kind decided --tool decide --ref
<d-id>`, any `--source`) is ignored: it can neither move a resolved decision
back nor blank its claim. The source alone could not tell them apart, because
`eos-event --source cli|hook` is accepted.

**Evidence is a list of ledger event ids.** An id is `e-xxxxxxxx` (the id
`eos-event` writes on every event) or `<execution>#<n>` (the position of an
event written before ids existed, or of one `eos-event` wrote without an id
because `od` or `/dev/urandom` was missing, `bin/eos-event:112-118`). Anything
else is rejected before writing (`:36`, `:95-100`). `eos-event` prints
`evidence=<id>` only for `ran`, `called`, `read` and `verified`, the kinds a
decision may cite. Each event with an output carries a fingerprint: the
sha256 of the raw output file and its absolute path, written by
`bin/eos-event --output <file>`. The content is never copied into the ledger.

**The check names a command, never a query or a value**
(`core/executions.py:284-297`). `--check` is the wrapper, its subcommand and
first operand only (`automation/db.sh pg env1 crm`). A check holding a quote
(`'` or `"`), `=`, `;`, a SQL verb as a word (`select`, `insert`, `update`,
`delete`, `where`, any case), a run of 5 or more digits anywhere (an msisdn
has 12, a customer id 6; `env1`, `FEMBS2-2220` and a build number up to 4
digits pass) or longer than 120 characters is refused with a `ValueError`
before anything is written, and `eos decide` exits 2 with `error: ...` telling
the caller to drop identifiers and values. It is never truncated, and the
refused value is not echoed. The ledger is pushed: `db.sh pg env1 crm "select
* from cust where msisdn='905321234567'"` or a bare `obs.sh orders env1
905321234567` would have pushed a customer's number. The rule sits
on the `check` field of `executions.event`, so the prompt excerpt `approve`
keeps as the check follows it too; the P2 prompt hook must cut its excerpt to
fit.

**What counts as evidence** (`_refusal`, `core/decisions.py:75-92`). A cited
event is accepted only when all of these hold:

- it belongs to this work item (or, when the run has none, to this run);
- it is not `kind=verified, tool=subagent`;
- it is not an EOS note read (`ref` starting `note:`): a note is what an
  earlier session wrote down, not a check this one ran;
- its kind is `ran`, `called`, `read` or `verified`, and its source is
  `wrapper` or `hook` (`:27-28`);
- a `called` event has no non-zero exit code;
- if its tool is a live environment tool (`LIVE_TOOLS`, `:21-23`: db, obs,
  admintoolbox, argo, bssapi, apitest, scenario, env-ready, grafana, kafka,
  ntf, eca, usermgmt, bpmn, cache-clear, vpn, db-grant-check, schema-drift,
  s3, jenkins), it is at most 24 hours old (`LIVE_MAX_AGE`, `:24`). Every other event stays valid for the life of the
  work item.

**Refusal codes.** A decision with any refusal is an `assumption`, and every
code that applied is stored and printed as `reason=<code> evidence=<id>`
(`:60-62`):

- `unknown-id`: no event with that id in any ledger the project reads.
- `other-work-item`: the event belongs to another work item (or run).
- `subagent-report`: a `verified` event whose tool is `subagent`.
- `note-not-evidence`: the event is an EOS note read (`ref` = `note:...`).
- `no-source`: the event has no `source` (a line written before P1 added it).
- `not-recorded-by-a-tool`: the kind is not `ran|called|read|verified`, or the
  source is not `wrapper` or `hook` (for example `cli`).
- `failed-call`: a `called` event with a non-zero exit code.
- `stale`: a live environment tool's event older than 24 hours.
- `hedged`: the claim itself contains a hedge word (`:32-35`, matched as
  whole words, any case, after folding Turkish letters): `muhtemel*`,
  `olmali*`, `saniyor*`, `sanirim`, `galiba`, `herhalde`, `belki`,
  `olabilir*`, `tahmin*`, `sanki`, `gibi gorunuyor*`, `probably`, `likely`,
  `should`, `seem(s|ed)`, `assume(d|s)`, `assuming`, `presumably`, `might`,
  `maybe`, `perhaps`, `apparently`, `I think`. Added by `record` whatever the
  evidence.
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
  (`:205-206`), and `eos decide` has no way to ask for it.

**`eos why-did [path] <run id | work item id | ticket key>`** prints the
decisions of that scope in time order, each with its state, check, reasons
and cited events: tool, target, ref, exit code, time, and the output file as
`present (sha ok)`, `present (sha differs)`, `gone` or `unreadable`, with its
path (`core/cli/whydid.py:25-50`), for example
`e-9f8e7d6c  db env1 pg crm  exit 0  14:01  output: present (sha ok) <path>`.
Times are local; an evidence time carries its date when its local day is not
the decision's. A ticket key matches in any case. It reads every ledger once
per call (`decisions.all_records`) and looks cited ids up in one index
(`decisions.event_index`). `--grep` keeps decisions whose claim holds every
word. It writes nothing.

## What it does not do

- **No relevance check.** The rules test the evidence, not whether it speaks
  to the claim. A decision can cite a real, fresh, wrapper-recorded event that
  has nothing to do with it and be `evidenced`. A reader of `eos why-did` must
  still read the cited output.
- **`--source` is not authenticated.** `bin/eos-event` takes `--source
  wrapper|hook|cli` from its caller (`bin/eos-event:5`, `:54-57`); anyone who
  can run it can write a `wrapper` or `hook` event. The source check keeps
  honest paths apart; it is not a barrier. P1 accepts this on three
  conditions:
  - the P2 gate that blocks work must not trust `source` alone (met when the
    host refusals land, nexus P2 Task 7; ADR-038 "Residual risk");
  - the workspace's guard (nexus `wrapper_guard`) must refuse an agent's
    direct `eos-event` call, so only a wrapper writes `wrapper` events;
  - this ADR says so, which it does here.
- **Known gaps left for P2.**
  - A legacy id `<execution>#<n>` is the event's position in the loaded run
    (`core/executions.py:700`); log rotation can shift it, so the id may then
    name a different event or none.
  - (P2 closed the unparseable `at`, the source-less code, `Decision.agent` and the
    later-resolution line of `why-did`.)
- It does not stop an action taken on an assumption. It makes the assumption
  visible and listable; enforcement is the evidence gate (P2).

## Consequences

- From 2.5.0 a task decision has a place in the ledger, and "why" is
  answerable from it with the output fingerprint to check against.
- The 24-hour limit and the `LIVE_TOOLS` set are code constants; a new live
  environment wrapper must be added to the set or its events never go stale.
  Tool names are the wrapper's file name without `.sh` (nexus
  `automation/lib/eos-capture.sh`), or the program a hook saw in a Bash
  command (`vpn` arrives that way, from the hook).
- Hedge detection is a word list: a hedge in other words passes as
  `evidenced`, and a claim quoting "should" is marked `hedged`.
- An output file moved or deleted after the event shows as `gone`; the event
  still counts as evidence.
- A check that cannot be written as wrapper, subcommand and operand is
  refused rather than shortened; the decision can still be recorded without
  `--check`.
