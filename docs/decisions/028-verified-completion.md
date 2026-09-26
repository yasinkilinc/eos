# ADR-028: Verified completion — a change is done when its check passed after it

## Status

Accepted, 2026-09-27. Extends ADR-027 (the plugin's hooks) and ADR-024 (a failed
run leaves a lesson).

## Context

In the host workspace 56 of 62 runs finished `ok` and 3 `failed` — every outcome
declared by the agent, none checked. The failure its owner named as the most
tiring is "done, but not done": tests, pushes and fixes reported complete that
were not. The hooks already record every edit (`changed`) and every command's
exit code; nothing compared them with the claim. Procedures carry a `## Success`
section as text only.

## Decision

- **Scopes are data.** `verify.toml` beside the notes: each scope names its files
  (globs; `**`, `*`, `{name}` capturing one segment), the commands that count as its
  check (regex, placeholders filled and escaped) and the command to suggest. Files
  in no scope are never asked about.
- **`core/verify.py` is pure.** Path → scope instance (`fm-service:fm-crm-asset`),
  command → the waiting instances it checks, ordered events → instances changed
  after their last passing check. A command clears a scope only when the check
  starts a shell segment (`&&`, `||`, `;`, newline; leading `bash`/`sh`/`env` and
  variable assignments allowed) — a `grep` or `echo` that mentions it does not — and
  a piped check counts only under `pipefail`, since a pipeline's status is its last
  command's.
- **The turn stops once.** The plugin's Stop handler blocks the main session when an
  instance is dirty, naming at most three with their check, ~600 characters: "run the
  check, or say in your answer that this is not verified". The same set is not asked
  about twice; never while `stop_hook_active`; any error means no block. It runs
  whether or not `[hooks] close` is on and shares one block with the open-run gate.
- **An ok run is labelled.** `run finish --outcome ok` reads the run's `changed`
  and `verified` events: `verified` when every changed instance passed after its last
  change, `claimed` when one did not, no label when nothing covered changed. Finishing
  is never refused; `run list --stats` prints the verified rate.

Rejected: reading completion claims out of the final message with language
patterns (language logic in the engine, false alarms); running the procedure's
`Success` text as a command (a separate step, E3).

## Consequences

- A host that wants the gate writes `verify.toml`; without it nothing changes.
- A coarse scope (any test clears it) is honest about its coarseness and is split
  when measurement shows false clears.
- `claimed` is a label, not a failure: it says what the run did not show.
- Evidence: `sessions.jsonl` `verify_gates`, `verify_after_gate`; `run list --stats`.
