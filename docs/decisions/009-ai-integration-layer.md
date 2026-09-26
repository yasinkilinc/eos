# ADR-009: A Dedicated AI Integration Layer, Written by `eos init`

**Status:** Accepted; delivery to Claude Code amended by ADR-026 (the plugin,
`--claude plugin`), 2026-09-26
**Date:** 2026-09-12
**Deciders:** yasinkilinc

## Context

EOS's value to a coding agent is proportional to how easily that agent
discovers it. An MCP server nobody registers, a skill nobody installs, and an
agent profile nobody writes are all just unused code. Different agent hosts
(Claude Code, and anything else that reads `AGENTS.md` or an MCP config) each
expect their own file in their own place, and a project that already has a
`.mcp.json` or `AGENTS.md` with other content must not have it clobbered by
`eos init`.

## Decision

`eos init` writes four integration surfaces, all generated from templates
under `core/ai/templates/` and all re-runnable via `eos ai update`:

1. A decision-support skill at `.claude/skills/eos/SKILL.md`.
2. A researcher agent profile at `.claude/agents/eos-researcher.md`.
3. An MCP server registration merged into `.mcp.json` under `mcpServers.eos`
   (existing servers are preserved; only the `eos` key is written).
4. A section in `AGENTS.md`, wrapped in `<!-- eos:begin -->` / `<!-- eos:end
   -->` markers so everything outside the block survives regeneration.

`--no-ai` on `eos init` skips all four for a project that does not want them.
`.claude/` is added to the scanner's default ignore list, since host-agent
configuration is never project source.

## Consequences

- An agent working in a freshly-initialized project can discover EOS through
  whichever surface its host reads, without the user hand-writing any of it.
- Re-running `eos ai update` after an EOS upgrade refreshes the generated
  block without touching anything a human added around it.
- The four surfaces are templates, not hardcoded strings in `core/eos.py`,
  so wording changes do not require touching CLI plumbing.
- A fifth agent host with a different convention means a fifth writer
  function, not a redesign — `write_all()` in `core/ai/writer.py` already
  treats each surface as an independent, idempotent write.
