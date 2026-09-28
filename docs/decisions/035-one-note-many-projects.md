# ADR-035: One note about many projects

## Status

Accepted, 2026-09-28. Released in 1.40.0. Roadmap item M4 (phase 2).

## Context

A business journey spans services, and a project reads only its own store. So
the host fanned each journey out: 18 hand-written journeys became 56 files in 13
service stores, one of them in 12 stores at once. The copies are a generator's
output, but a search across stores returned the same journey three times for
one question, and each copy was audited and deduplicated separately.

The research (§17, M4) proposed multi-service scope prefixes and one file.

## Decision

- A note in a workspace's own store may name the projects it is about:
  `projects: [a, b]` (`eos note add --projects a,b`). Every name must be a
  project of the workspace's `projects.toml`; outside a workspace the field is
  refused.
- Its scope names a project's file as `@project:<name>/<path>`, the shape of
  `@parent:<label>/<path>`. It is hashed when written and audited from the
  workspace like any scope entry (`note audit`, the index).
- `notes.shared_notes(project)` gives a project (one whose `[workspace] root`
  leads to a workspace listing it) the workspace's notes that name it. Its own
  `@project:<name>/` entries become project-relative, and the other entries are
  left out. The path stays the workspace's file.
- The readers that ask "what is known about this project" read them as the
  project's own: `search_notes` (so the brief, `eos note search`, the MCP tools
  and `note eval`), the queryless context section, touched-file injection, and
  `note show`.
- Injection delivers a shared note once per session, like any note: a batch
  touching files of two projects it names gets it once, not once per project.
- Writing, auditing and deduplicating stay with the store that holds the file.
  A shared note is amended in the workspace.

## Consequences

- The host writes each journey once, in the workspace store. 56 files become 18.
- A project outside any workspace, or a workspace note without `projects`, sees
  no change.
- `@project:` names are resolved through `projects.toml` on every read. A
  project not cloned on this machine resolves nothing, and its entries read as
  removed files in the audit.
