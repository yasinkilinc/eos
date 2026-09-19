# ADR-014: Provenance as Rows, and Coverage as the Absence of Them

**Status:** Accepted
**Date:** 2026-09-19
**Deciders:** yasinkilinc

## Context

EOS states what it knows and never how it knows it. Measured across `core/`,
`ui/` and `extensions/`: no node, edge or fact carried an origin, a confidence,
a detector or an observation time. `Symbol.line` existed at the semantic layer
and was discarded on the way to the knowledge layer, so not even a line number
reached `graph.json`. `Dependency.weight` was the literal `1` at both of its
construction sites.

The one working counter-example is the journeys extension, whose `resolution`
and `candidates` columns record how a step was attributed and what else it
could have been — so an attribution can be disbelieved without re-deriving it.

Two facts decided the shape of the generalisation.

**Size.** On a real 255-file service with a 2,397-file linked parent,
`graph.json` is 25,466,819 bytes for 6,040 nodes and 72,139 edges. Roughly 154
bytes of provenance per edge is 10.6 MB raw and ~15 MB pretty-printed, on an
artifact that MCP `get_graph` returns whole and whose cost already carries a
warning in the generated skill.

**Silence.** The same service has zero `@Repository`, zero `@KafkaListener` and
zero `@FeignClient`: its persistence, messaging and remote calls go through
framework base classes and generated clients instead. A generic Spring detector
returns nothing there, and "this service publishes no events" and "nobody
looked for events" print identically. `ScanReport` already counts what a scan
did not index — and then throws it away on stdout.

## Decision

**Provenance is rows, not fields.** `KnowledgeGraph` gains `facts` and nothing
else; `to_dict()` is unchanged and pinned by a test, so `graph.json` keeps the
exact node and edge shape it has always had. Facts travel in a sidecar,
`.eos/data/brain/evidence.jsonl`, and land in SQLite as one generic table:

```
fact(subject_kind, subject, predicate, object,
     origin, confidence, detector, source_ref, observed_at)
```

Generic rather than columns on `node`/`edge`, because those are 1:1
projections of `graph.json` while one node carries a role, several annotations,
a bean name and an endpoint — and because a new detector then adds predicates
rather than a schema migration.

JSONL rather than JSON: structural extraction will produce tens of thousands of
facts on a project this size, and the index must stream them.

**`origin` is a four-value ladder** — `extracted`, `documented`, `inferred`,
`verified` — and the first distinction is the load-bearing one: a fact read out
of the source is not the same kind of thing as one a heuristic produced.
`confidence` is REAL for query ergonomics but producers may only use
`CERTAIN` / `LIKELY` / `POSSIBLE`; a free float invites a calibration nobody
performed.

**An edge is addressed by `src|dst|kind|imported`**, never by `node.nid`:
`_load_brain` assigns nid in `graph.json` iteration order, so every rebuild
renumbers every node and a stored number would silently come to mean a
different file.

**`observed_at` comes from the parse, not the scan.** An unchanged file is
reused from the cache, so a fact emitted today may have been observed several
scans ago. `FileSemantic` gains `parsed_at`; facts inherit it.

**Coverage is a table, and its absence is the answer.** `coverage(detector,
predicate, files_eligible, files_with_hits, hits)` records what each detector
was *asked about*. A project with no Java has no row for a Java detector
("not looked for"); a project with 2,652 Java files and `hits = 0` has one
("looked, found nothing"). `scan_exclusion` does the same for what the scan
skipped.

**`eos why` ships with the schema.** This project has already paid for the
alternative: a predecessor defined full schemas for `lessons/`, `patterns/`,
`history/` and `relationships/`, and four of them held nothing across five
snapshots — the only directory with data was the one with a CLI command. `why`
prints a file's facts, which of them are inbound, and the coverage of every
detector that ran, so "did you not find it or did you not look" is answered at
the moment it is asked.

## Consequences

- `graph.json` is byte-identical for the same inputs; the UI and every external
  reader are unaffected, and the 25 MB artifact does not grow.
- The SQLite index is now the only place provenance can be queried, which makes
  re-pointing the agent-facing surface at it the natural next step.
- A detector that produces nothing is now visible. That is a reporting change,
  not a capability one: the generic Spring probes still find nothing on a
  codebase that does not use those annotations — they now say so.
- `SCHEMA_VERSION` 1 → 2 and `_CACHE_FORMAT` 2 → 3. Neither needs a migration:
  the index is rebuilt from scratch and `_sources_digest` hashes
  `SCHEMA_VERSION`, and a cache format mismatch drops the cache.
- A half-written sidecar is refused rather than partially indexed. The header
  counts every row kind and is checked against both the file and
  `last_scan.json`; without that a killed scan would leave `eos why`
  confidently citing provenance for edges of a previous graph.
- Folder-hierarchy edges carry no facts by decision — they are a projection of
  the path, not a detection, and are 52,607 of 72,139 edges on the measured
  service. Their coverage row records that as a stated choice, not a gap.
