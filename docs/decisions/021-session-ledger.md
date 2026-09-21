# ADR-021: One surface, reached without a decision

## Status

Accepted, 2026-09-21.

## Context

Two measurements from a real 18-service workspace decide this one.

**EOS was reached for in 4 of 25 sessions.** Not because it was undocumented —
it had a generated skill, an agent profile and an AGENTS.md section in every
service. Because calling it was a decision, and on most questions the honest
answer to that decision is "no": `Grep` finds a symbol as fast as
`find_symbol`, `Read` returns the same bytes as `get_file`, and the skill
itself says so, tool by tool. A tool that loses to grep *should* be skipped.

**Registering it as MCP cost 2,194 tokens per session** — one server per
service, 39% of that workspace's entire MCP tool surface, charged on every
request whether or not a tool was called. Those registrations were removed by
hand, which `eos ai update` would have quietly put back.

Together they say the adoption problem was never a documentation problem. It
was the shape of the surface: a menu that costs something to carry and
requires a decision to use.

## Decision

**One surface per project, chosen at init.** `--surface cli` is the default
and writes no MCP registration; `mcp` and `both` write one. The choice is
recorded in `.eos/config.toml` under `[ai] surface`, so a later `eos ai
update` cannot silently re-add what a project removed. The generated skill
renders from one template with the MCP half fenced off, so a project without
an MCP server is not handed a table of tools it cannot call — and there is
still only one document to keep true.

**The part that must be reached is reached by a hook, not a decision.**
`eos init` writes `.claude/hooks/eos-brief.py` and registers it as a
SessionStart hook. It runs `eos brief`, whose output becomes context the
session starts with. There is no tool to choose and no call to remember, which
is the only mechanism that has ever moved this number: the same workspace's
own note on hookless platforms records that a rule saying "run this first" was
already measured as ineffective.

**The brief answers only what nothing else can.** What another session is
doing right now (claimed, blocked, stale, contested — ADR-020) and which
recorded notes match this branch. It derives its query from the branch name
and its ticket keys, because at session start nobody has typed a task yet. It
is capped at a handful of lines: a session-start block that costs as much as
reading two files is one somebody turns off inside a week.

**The hook fails silently and always exits 0.** No `eos` on PATH, an
interpreter too old, a slow filesystem — none of them may disturb a session.
It tries the `eos` on PATH and then the project's own deployed runtime, which
is what keeps the loop working in the ordinary state of a workspace a week
after an engine upgrade, when the two are different versions.

**Silent failure is still recorded.** The first version of this hook was
silent in a way that would have corrupted the measurement below: `eos cost`
counts sessions by the calls they made, so a session where the hook failed
would not have been in the denominator at all, and a project where EOS could
not be reached in any session would have reported 100% adoption of the
sessions that worked. When every runner fails the hook appends one `ok: false`
record itself — and only to a telemetry log that already exists, because the
engine creates that file only where telemetry was turned on, and a hook is not
a licence to start one.

**The other end of the loop is a Stop hook**, `eos-close.py`, which asks a
session once what happened to the items it claimed. Unclosed claims are the
failure mode the ledger creates: to the next session a claim reads as an agent
still working, so nobody takes it, and a day later it is stale — the claim
then costing more than it saved. It is narrow deliberately: only items this
session claimed, only once (`stop_hook_active`), never when EOS could not be
asked, never when no session id was passed, and every answer accepted —
`done`, `block` and `drop` all end it. A gate with no way through gets
deleted, and a deleted gate records nothing at all. This is not the commit
gate that was deferred: it is session-local, costs no distribution across
repositories, and is removed by deleting one entry from `hooks.Stop`.

**Telemetry records the session id.** Until now this engine could count calls
and not sessions, so "4 of 25" was counted by hand, once. The id is read from
the harness's own environment — Claude Code exports `CLAUDE_CODE_SESSION_ID`
into every command it runs, so this works on a plain install with no hook and
no configuration; `--session`, `$EOS_SESSION` and a project-declared
`[telemetry] session_env` cover harnesses that export nothing or something
else. Only variables that *are* a session id are ever read:
`DEVIN_PERMISSION_MODE` was observed set inside a Claude Code session on a
machine with Devin's editor extension installed, and a marker read that way
attributes one agent's work to another. Every record carries which variable it
came from, because "read from the harness" and "nobody passed one" otherwise
print as the same count.

**Usage is not the measurement.** Once a hook runs in every session, "how many
sessions reached for EOS" approaches 100% by construction and stops meaning
anything. The number that still means something is what changed: collisions,
claims that went quiet, time from claim to close, sessions that tried to end
holding work. `eos work stats` reports the first three by replaying the ledger
— a collision settled an hour later leaves no trace in the current state and
is exactly the event worth counting — and `eos cost` reports the fourth,
recording both halves so that a hook leaving a trace only when it fired could
not make the ratio look worst precisely when sessions started closing their
work.

## Consequences

The session id is the one value-shaped thing telemetry records, against a
module whose first rule is that it records what was called and never what was
asked. It is an opaque marker a harness generated rather than anything a
person typed, and the alternative — the number this system is judged on being
uncountable — is worse. It is truncated to 64 characters and nothing joins it
to anything outside the file.

The denominator is honest about what it is: sessions that identified
themselves. A human at a terminal passes no session id, and those calls are
reported separately rather than folded into an imaginary session.

Devin gets the brief without the hook, because Devin has no hooks. There the
seam is the workspace's own start-of-task step, and the thing that makes it
work is that it is now *one command* rather than a table of twelve to choose
from. That is a weaker mechanism than a hook and is expected to show a lower
number; `eos cost` will say by how much, which is the point.

MCP is not deprecated. A single-project setup where the roster is one server
may reasonably prefer it, and `eos mcp` is unchanged. What is gone is a
project carrying both surfaces and paying for one of them in every request.
