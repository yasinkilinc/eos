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
- **`core/verify.py` is pure.** Path → scope instance (`service:billing`),
  command → the waiting instances it checks, ordered events → instances changed
  after their last passing check. A command clears a scope only when it exits 0
  only if the check passed: the check starts a command (leading `bash`, `env`,
  `time`, `timeout N` and variable assignments allowed) in the last top-level command
  (split at unquoted `;` and newlines, comments and heredoc bodies removed), with no
  `||` or background `&` in it; after the check `&&` may run only `echo`, `printf`,
  `true`, `:`; a piped check counts only under an earlier `set -o pipefail`. A
  check inside `$(...)` or backticks never counts (`echo $(false; make test)`
  exits 0); `bash -c "<check>"` is read as the check it runs. Under an earlier
  `set -e`, a check ending its own `&&` chain counts wherever it is -- never in an
  `if`/`while` condition, after `!`, in a function body or `{ }` group, and a
  subshell's `set -e` stays in the subshell (`set +e`/`set +o errexit` turn it off);
  a subshell `( … )` is read as the check it runs; a shell named by its path
  (`/bin/bash`) passes through like `bash`.
- **Said at once, not only at Stop.** Replaying the host's 61 sessions since
  2026-09-15 through the rule: 36 of the 37 that changed a scoped file would be
  stopped, for 47 unchecked instances -- 24 of them had run their check, piped into
  `tail` or followed by `;`. So when a command runs a waiting check in a form whose
  status cannot count, the PostToolUse hook says so once per session, naming the
  check and how to run it; the Stop gate stays for what was never run. A `grep`
  or `echo` that mentions the check does not count. A placeholder is bounded only
  where nothing in the pattern follows it (`test-{name}\.sh` bounds itself).
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
