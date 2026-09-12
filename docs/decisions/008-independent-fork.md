# ADR-008: Publish EOS as an Independent Fork

**Status:** Accepted
**Date:** 2026-09-12
**Deciders:** yasinkilinc

## Context

EOS was built and hardened inside a private corporate workspace, developed
against real multi-service projects so its scanning, indexing and context
generation had to work on non-trivial code, not toy examples. That workspace
also contains the company's own service names, ticket prefixes, internal
hostnames and absolute paths, none of which belong in a tool released to the
public. The alternative to extraction was to keep EOS private indefinitely,
which would deny it any use, feedback or contribution outside that one
workspace.

## Decision

Extract EOS into its own repository, owned and published independently of the
workspace it was built in. The extraction removed every workspace-coupled
test, every domain-specific code path (the "journey" and "bean" indexing
built for one company's service topology), and every literal identifier —
company name, ticket prefixes, service names, hostnames, absolute paths —
replacing them with neutral, generic equivalents or configuration knobs where
the underlying capability is still worth keeping (`merge_branch_pattern`,
`EOS_PARENT_SEGMENT`). `tools/check-clean.sh` encodes the resulting denylist
as an executable gate so a future contributor cannot silently reintroduce a
company-specific string.

## Consequences

- EOS is generic: every project-specific concept that survived is a
  configuration value with a documented example, not a hardcoded default.
- The private workspace's own tooling continues to use EOS as a git
  dependency, unaffected by the split.
- History was rebuilt into a single commit at the extraction boundary (see
  the project's publishing notes) — the pre-extraction commits are development
  history of a codebase that no longer exists in public form, not a useful
  audit trail for the published one.
- Any future capability that is specific to one company's workflow again must
  either become a configuration knob or stay out of this repository.
