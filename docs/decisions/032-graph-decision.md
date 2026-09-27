# ADR-032: One code graph -- EOS's own, measured against Graphify

## Status

Proposed, 2026-09-27 (2.x roadmap L2). Measured on the branch `eos-2x`; the host
decides whether to stop building the second graph.

## Context

The host builds two code graphs per service: the EOS index (`node`, `edge` in
`eos.db`) and a Graphify `graph.json` (76-105 MB per service). The roadmap asked
which finds the tests affected by a change better, and whether EOS should consume
Graphify through the index or improve its own graph and retire the duplicate.

## Measurement

Ground truth: 353 pairs on three Java services where one `<X>.java` and one
`<X>Test|Tests|IT.java` share a name. Per pair: when the subject changes, is the
test among the affected files? EOS through `index.impact_rows` (reverse edges) on
copies of the live indexes; Graphify by reverse traversal of its graph.json over
its CLI's default relations (checked against `graphify affected` on four samples).

| | recall depth 1 | recall depth 2 | subject not in the graph | set size median / p90 | latency |
|---|---:|---:|---:|---|---|
| EOS | 98.9% | 99.4% | 1 / 353 | 1 / 4 | 0.16 ms (query) |
| Graphify | 83.3% | 83.6% | 49 / 353 | 1 / 3 | 0.7-1.0 s (CLI reloads the file) |

Where both tools index the subject, recall is close (EOS 99.7%, Graphify 97.0%);
Graphify's gap is coverage: about half of its missing files changed after its
snapshot was built, the rest are interface-only or annotation-heavy files its
parser leaves out. Its relations have no instantiation edge, which EOS records
(`new`) for exactly these pairs. EOS's one miss at depth 2 is a smoke test with no
reference to its namesake -- no edge exists for either tool.

## Decision (proposed)

Keep EOS's graph as the one code graph for impact questions; do not consume
Graphify through the index. Nothing in EOS changes. For the host: Graphify's
per-service build can stop once nothing else reads it (its community and
god-node views are the remaining users to check).

## Consequences

- One graph per service, refreshed with the index, instead of a second snapshot
  that goes stale between builds.
- Not measured: EOS through its CLI or MCP process (only the query); precision
  beyond the affected-set size; non-Java files.
