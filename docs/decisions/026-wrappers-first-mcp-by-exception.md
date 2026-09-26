# ADR-026: Wrappers first, MCP by exception; hooks from one plugin

## Status

Accepted, 2026-09-26. Extends ADR-009 (how the integration is delivered) and
ADR-021 (one surface, reached without a decision); measured against Claude Code
2.1.281.

## Context

Three measurements from one real workspace (18 services, 135 session
transcripts) decide this.

**What a wrapper saves is in its output, not its roster.** The host routes its
external systems through shell wrappers that filter what they return. Measured
there: an issue search 40,275–233,021 tokens raw against ~605 through its
wrapper; one issue ~10,745 against ~175; a failing build ~13k against five
lines; a parent-span diff ~957k against ~160. Across 135 transcripts the
wrapper calls returned 542 tokens per call on average, raw commands a wrapper
covers 629, MCP tools 1,040 (tokens calibrated below). An MCP server that
returns an API's JSON has no such filter: its output is capped at
`MAX_MCP_OUTPUT_TOKENS` (25,000), not shaped to the question.

**Roster cost is smaller than it was, not zero.** ADR-021 measured 2,194 tokens
per session for eighteen servers before the harness deferred tool schemas.
With tool search on, a session is handed tool *names* (the deferred-tools
listing was a median 2,543 tokens per session in that workspace, most of it
MCP servers a person still had registered); when tool search is off — a custom
gateway, an older model on some clouds — the full schemas return to every
request. EOS's own roster was 4,881 characters of `tools/list` (twelve tools)
and is 4,388 (ten) after this decision.

**The copies were never run.** `eos init` wrote a SessionStart, a prompt and a
Stop hook into each project's `.claude/`. In the workspace above they were in
thirteen services and ran in none — 0 invocations in 135 transcripts, no
harness project directory for any service — because sessions start one
directory up, and a nested `.claude/settings.json` is never loaded. The skill
and agent copies *were* loaded (from the added directories), thirteen times
over the same name.

## Decision

**Wrappers are the default way to reach an external system; MCP is the
exception.** An MCP server is justified only when:

- no practical CLI exists;
- OAuth or session integration is materially easier through it (hosted SaaS
  connectors);
- persistent protocol or session semantics are needed (a server holding a
  large graph in memory between calls);
- tool-level permission isolation is materially valuable; or
- it provides something that would cost disproportionately more to rebuild.

Otherwise a wrapper, and no existing wrapper is rewritten as an MCP server.
EOS's own MCP roster shrinks to ten: `get_graph` (the whole graph; `eos graph
--output` exports it) and `compose` (an alias of `get_context` kept "for one
release" for eight) are removed. What MCP offers at no roster cost is added:
two resources (`eos://brief`, `eos://capabilities`) and one prompt (`brief`),
which a client lists only when a person asks.

**The wrappers are declared, so the engine can point at them.** A project
lists them in `capabilities.toml` beside its notes — name, the command to run,
what it answers, the words a task uses for it, and the raw forms it covers
(`block`: a host guard refuses them; `hint`: allowed, answered once). Every
surface reads that one file: `eos capabilities`, the task brief's WRAPPERS
block, the hook that notices a raw command a capability covers and records it
on the run as a bypass, and any host guard. Nothing in the engine runs a
wrapper or decides one was the right call (ADR-018).

**Claude Code gets EOS as a plugin; the per-project copies go.** `plugin/`
registers each hook once and runs `eos hook <event>`, one entry point with one
function that reads the harness's field names (`core/hooks.py:normalize`).
`eos init/ai update --claude plugin` writes nothing into `.claude/` and removes
what `files` mode wrote — only EOS's own scripts, entries and documents. `files`
stays the default for projects without the plugin. Every hook exits 0 and is
silent on failure; the Stop hook blocks through its JSON answer, never an exit
code, so a missing or older `eos` cannot stop a session.

**Every hook earns its place with something measured.** Added: run capture
with `source`, `status`, the acting subagent and the harness's call id
(deduplicated in the ledger's tail); instruction-load and per-session logs;
compaction resetting the prompt dedup; the routing hook, dry-run by default in
plugin mode. Not added, with the reason recorded in the Claude integration
plan: task-list sync (did not fire in 2.1.281, and would fill a committed
ledger with to-dos), model-switch capture (the transcript fold already has
model and effort per message), working-directory and maintenance hooks
(nothing to feed yet), and `if` filters on the host's guard (27 ms per call
saved, at the price of rules that match anywhere in a command line).

## Consequences

A project that uses the plugin has nothing EOS-generated under `.claude/` and
one version of the hooks for all its sessions; one that does not keeps today's
behaviour. The ledger's events carry four optional fields (`agent`, `source`,
`status`, `tool_use_id`); older readers ignore them, and older lines read as
before.

Token figures in this ADR use a ratio calibrated on the harness's own counts:
the prompt growth across 536 turns dominated by one large tool result gave 2.22
characters per token (prose read from markdown 2.27, code 2.41, MCP JSON
1.94). Estimators written for older tokenizers — 3.0 and 3.5 in this
repository's history — understate the current models' counts by roughly a
third.

MCP is not deprecated. `--surface mcp` remains for a project that wants it,
and the resources and prompt make that surface cheaper to keep. What changed is
the default reasoning: an integration has to show why it cannot be a wrapper.
