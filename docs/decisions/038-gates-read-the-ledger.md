# ADR-038: Gates read the ledger

## Status

Accepted, 2026-10-29. Phase P2 of the evidence-first platform design (nexus
`docs/superpowers/specs/2026-10-29-evidence-first-platform-design.md`, C2, C3).

## Context

ADR-037 made a decision an event that names its evidence. Nothing yet stopped
an action that rested on an open assumption, on no look at the live system, or
on a production edit with no red test before it.

## Decision

**The ledger questions live in EOS, the scope lives in the host.** `core/gates.py`
answers one question from decisions and events only: "is this action backed by
evidence?". `eos gate check <path> --session S --action A [--file P] [--tool T] [--host H]`
prints one JSON line `{"mode", "refusals": [{"rule", "detail", "fix", "ids"}]}`
and exits 0 always: a hook reads JSON, not the exit code. What counts as a test
file, a main-code edit, an environment write or a push is the host's knowledge
(paths, repositories, wrappers); the host maps it to one of `edit-test`,
`edit-main`, `edit-other`, `confirm`, `env-write`, `commit`, `push` and asks.
EOS never reads the repository, so it stays the same for every project.

Rules (`fix` is the one command that clears each): `no-open-run`,
`open-assumption`, `nothing-looked-at`, `red-first`, `root-cause`. The scope of
the decisions and events is the run's work item, else the run, as in
`eos decide`. `nothing-looked-at` and `root-cause` count an `evidenced` or
`resolved` decision only while its evidence, checked again at gate time, still
passes: live-state evidence older than 24 hours no longer does. `red-first` is
exempt for the `product-upgrade` agent and for runs of a procedure whose slug
starts with `upgrade`. The run title for `root-cause` is matched after folding
Turkish characters. `eos gate check` reads every ledger once, and none when the
session has no open run.

**Mode is the user's switch.** `[gates] mode` in `.eos/config.toml` is `off`
(default; unknown values too), `warn` or `block`. A project with no `[gates]`
table takes its workspace's (`[workspace] root`), so a session standing in a
service has the workspace's mode. `warn` records and tells; `block` refuses.
Moving from `warn` to `block` belongs to the user, after P6 has shown what
`warn` would have refused; EOS never raises it. In mode `off` `eos gate check`
still answers but writes nothing.

**Devin waits for A19.** The mode is per project and shared by every host, and
Devin's hooks are not part of P2: `eos gate check --host devin` answers
`{"mode": "off", "refusals": []}` and reads nothing, and the Stop done gate and
prompt approvals do nothing for a Devin hook, until A19 brings them to Devin.

**What a gate event holds.** `record_refusals` appends one `noted` event,
`tool=gate`, `source=hook`, per (rule, action, target) per run; a repeat in the
same run writes nothing. `ref` is the target: an absolute path becomes the part
after its git top-level, else its basename; a relative target is kept as given
unless it is unsafe (a `..` part, whitespace, `=` or a quote), then its
basename. `body` is `<rule>: <action>` plus the decision ids. No path outside
the repository, no claim text and no check text is written: the ledger is
pushed. A refusal that cannot be recorded is still printed.

**The done gate (C3) is run-scoped.** The evidence gate above (C2) reads the
work item; the Stop hook's done gate reads only the run, from the run's own
ledger (the session pointer's), because event order means nothing across two
ledgers. A completion word in the main session's answer needs a passing check
after the run's last source change. A source change is a `changed` event whose
file has a source suffix (the note gate's list) and lies outside the project's
knowledge dir; a note or a document edit is none. A passing check is a `ran` or
`verified` event with exit 0 that a wrapper or a hook recorded, never a
`called` lookup, a subagent report or a cli-written event; order is the event's
position in the ledger (`ord`), not its whole-second stamp. Its refusal is
`done-without-evidence`, recorded with `action=done` and, as `ref`, the first
eight hex digits of the claim message's SHA-256: one event per distinct claim.

`eos gate check` always prints one JSON line and exits 0; an internal error or
unknown action adds `"error"` and no refusals.

## Consequences

- The host owns hook wiring and the action mapping; changing a rule is one
  edit in `core/gates.py`, shared by every host.
- A gate cannot see what the host did not report: a wrong `--action` is a wrong
  answer, not a refusal.

## Residual risk: forged approval and forged evidence

EOS cannot tell a hook call delivered by the harness from one invoked from a
shell, nor a wrapper's event from one a script wrote. Each path, and who closes it:

- **A child harness session.** `EOS_EXECUTION`/`EOS_EXECUTION_LEDGER` in the
  environment name the run before the session pointer does, so a
  `claude -p "d-… onay"` started from the session's shell fires a genuine
  prompt hook on the parent's run. Closed in EOS: an approval needs the run to
  have been opened by the prompting session (`run.session`), and a child has
  another session id.
- **A shell call to `eos hook`.** `eos hook user-prompt` with a forged payload
  (the right session id included) is indistinguishable from the harness. The
  host must refuse it.
- **What remains: writes that bypass the wrappers and the hooks.** A direct
  write to the ledger file, or Python calling `core.decisions.approve` or
  `core.executions.event` (any `source`, so a forged failing or passing `ran`),
  can close an assumption or clear `red-first` and the done gate. EOS cannot see
  these; the host must refuse them (direct ledger writes, `core.decisions` and
  `core.executions` imports, a direct `bin/eos-event` call).

ADR-037 accepted an unauthenticated `source` on the condition that the P2 gate
does not trust `source` alone. That condition is met by the session check
above together with the host's refusal of `eos hook`, `bin/eos-event` and the
writes that remain; it is not met by EOS alone. The record stays auditable: an
approval has source `hook` and the excerpt `user prompt: <word>`, visible in
`why-did` as `approved later`.
