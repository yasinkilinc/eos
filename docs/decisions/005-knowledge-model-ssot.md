# ADR-005: Knowledge Model as Single Source of Truth

**Status:** Accepted  
**Date:** 2026-07-05  
**Deciders:** yasinkilinc

## Context

EOS produces several artifacts: Markdown notes, JSON graph indexes, and cached scan metadata. If each generator parsed files independently, the outputs could diverge and maintenance cost would grow.

## Decision

Define a single **Knowledge Model** (`core/knowledge/model.py`) produced by `KnowledgeBuilder` from the semantic model. All generators consume this model and never re-parse source files.

Pipeline:

```text
Scanner → Semantic Model → Knowledge Model → Linker → Classifier → Generators
```

Markdown and JSON are derived artifacts only.

## Consequences

- Changing output formats does not require changing parsers.
- Cross-references and backlinks are computed once in the model.
- The model can be serialized and reused by the UI without re-running the scanner.
- Generators must stay pure transformations; no business logic leaks into them.
