# ADR-038: Gates read the ledger

## Status

Accepted, 2026-10-29. Phase P2 of the evidence-first platform design (nexus
`docs/superpowers/specs/2026-10-29-evidence-first-platform-design.md`, C2).

## Context

ADR-037 made a decision an event that names its evidence. Nothing yet stopped
an action that rested on an open assumption, on no look at the live system, or
on a production edit with no red test before it.

## Decision

**The ledger questions live in EOS, the scope lives in the host.** `core/gates.py`
answers one question from decisions and events only: "is this action backed by
evidence?". `eos gate check <path> --session S --action A [--file P] [--tool T]`
prints one JSON line `{"mode", "refusals": [{"rule", "detail", "fix", "ids"}]}`
and exits 0 always: a hook reads JSON, not the exit code. What counts as a test
file, a main-code edit, an environment write or a push is the host's knowledge
(paths, repositories, wrappers); the host maps it to one of `edit-test`,
`edit-main`, `edit-other`, `confirm`, `env-write`, `commit`, `push` and asks.
EOS never reads the repository, so it stays the same for every project.

Rules (`fix` is the one command that clears each): `no-open-run`,
`open-assumption`, `nothing-looked-at`, `red-first`, `root-cause`. The scope of
the decisions and events is the run's work item, else the run, as in
`eos decide`. `red-first` is exempt for the `product-upgrade` agent and for runs
of a procedure whose slug starts with `upgrade`. The run title for `root-cause`
is matched after folding Turkish characters.

**Mode is the user's switch.** `[gates] mode` in `.eos/config.toml` is `off`
(default; unknown values too), `warn` or `block`. `warn` records and tells;
`block` refuses. Moving from `warn` to `block` belongs to the user, after P6 has
shown what `warn` would have refused; EOS never raises it. In mode `off`
`eos gate check` still answers but writes nothing.

**What a gate event holds.** `record_refusals` appends one `noted` event,
`tool=gate`, `source=hook`, per (rule, target) per run; a repeat in the same
run writes nothing. `ref` is the target in repo-relative form (the part after
the git top-level of an absolute path, else its basename), `body` is
`<rule>: <action>` plus the decision ids. No path outside the repository, no
claim text and no check text is written: the ledger is pushed.

`fresh_verification` is the done gate's question (Task 2): the latest event of
the run that a wrapper or hook recorded as a passing `ran`/`verified`/`called`,
never a subagent report or a cli-written event. `after` is the last `changed`
event; order is the event's position in the run (`ord`), not its whole-second
stamp. `eos gate check` always prints one JSON line and exits 0; an internal
error or unknown action adds `"error"` and no refusals.

## Consequences

- The host owns hook wiring and the action mapping; changing a rule is one
  edit in `core/gates.py`, shared by every host.
- A gate cannot see what the host did not report: a wrong `--action` is a wrong
  answer, not a refusal.
