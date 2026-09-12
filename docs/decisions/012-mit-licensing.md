# ADR-012: License EOS under MIT

**Status:** Accepted
**Date:** 2026-09-12
**Deciders:** yasinkilinc

## Context

A published repository with no license is, by default, "all rights reserved"
— nobody may legally fork, modify or redistribute it, no matter how open the
source looks. EOS's own design goal is the opposite: a project drops the
`core/` runtime into its own tree and modifies its plugins freely, and other
developers are expected to fork it, adapt a language plugin, or lift pieces of
it into their own tooling. A copyleft license would require every downstream
project embedding `core/` to itself become open source, which is a much
stronger constraint than EOS needs and would deter exactly the kind of casual
adoption ("drop this folder into your project") the tool is designed for.

## Decision

License EOS under the MIT License, `Copyright (c) 2026 Yasin Kilinc`. MIT
permits use, modification, and redistribution — including inside proprietary
projects — with attribution as the only real obligation.

## Consequences

- A project can vendor `core/` into a closed-source codebase without any
  licensing conflict.
- Contributions accepted into this repository are implicitly MIT-licensed
  unless a contributor states otherwise up front.
- EOS carries no obligation on downstream users to publish their own changes,
  matching how it is actually used: copied into a project's own tree via
  `eos init`, not linked as a library.
