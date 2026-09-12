# ADR-004: Plugin Architecture for Language Support

**Status:** Accepted  
**Date:** 2026-07-05  
**Deciders:** yasinkilinc

## Context

EOS must support multiple languages (Python, JavaScript, TypeScript initially, with more to come). We want to avoid a single monolithic parser and make new languages easy to add without touching the core scanner.

## Decision

Adopt a plugin model where each language provides a class implementing `LanguagePlugin`:

- `detect(root: Path) -> bool` — decides whether the project uses this language.
- `parse_file(rel_path: str, content: str) -> FileSemantic` — extracts symbols, imports, exports, and docstrings.

Plugins are registered in `PluginRegistry`. The scanner asks the registry which plugin handles each file.

## Consequences

- Adding a language is mostly additive.
- Plugin implementations may be regex-based initially and replaced with AST parsers later without changing the contract.
- The scanner remains language-agnostic.
- Phase 0 supports Python and JavaScript/TypeScript; future languages follow the same interface.
