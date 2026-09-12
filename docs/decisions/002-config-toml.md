# ADR-002: TOML for Project Configuration

**Status:** Accepted  
**Date:** 2026-07-05  
**Deciders:** yasinkilinc

## Context

Each `.eos` instance needs local configuration: project name, scan depth overrides, extra ignore patterns, and possibly monorepo flags. We want the configuration to be human-editable and parseable without third-party libraries.

YAML was considered because it is common in DevOps tooling, but it requires an external dependency (`PyYAML`) which conflicts with the zero-dependency runtime goal.

## Decision

Use **TOML** (`.eos/config.toml`) as the project configuration format.

Python 3.11+ includes `tomllib` in the standard library, and `tomli_w` is small enough to vendor if write support is needed on older versions. For Phase 0 we use a minimal hand-written TOML writer.

## Consequences

- No extra package installation is required.
- The format is readable by both humans and machines.
- Arrays and tables map naturally to our nested config schema.
- YAML users will need to adjust, but the trade-off is justified by the dependency constraint.
