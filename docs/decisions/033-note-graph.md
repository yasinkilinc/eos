# ADR-033: Notes linked to notes -- the note graph

## Status

Accepted, 2026-09-27. Branch `eos-2x`, released in 1.16.0.

## Context

A brief found notes by the words they share with a task. What a note is
*connected* to -- the lesson learned while following a procedure, the note that
watches the same file, the one about the same ticket -- was invisible unless the
words happened to overlap. Measured on the host (238 notes): one note cites
another by name, while 40 share a ticket key with another. So explicit citations
cannot carry a graph; the edges must be derived.

The host's owner proposed using Graphify, which already builds a graph per
service. It was measured for code (ADR-032): its graph is a snapshot days old,
its text extraction is model-based, and each query reloads 76-105 MB. Notes change
daily and the brief runs on every prompt, so the graph lives in EOS.

## Decision

- `core/note_graph.py` derives edges, deterministically, from the notes alone:
  `cites` (a body names `note:<file stem>` or the file), `lesson` (a lesson note
  names the procedure it was learned in), `scope` (two notes watch the same file),
  `ticket` (two notes name the same key, by the project's ticket pattern). A file
  or key shared by more than 8 notes links nothing.
- The brief and `eos note related` compute them from the notes on each call (25
  ms on 238 notes), so they are current when the index is stale; the index keeps
  a copy in `note_edge` (schema 6) for `eos query` joins. (Review 13: the first
  text said the brief read the table; it never did.) A scope file is one file
  however it is written (relative, `./`, absolute); a ticket key quoted in a code
  block or a `>` quote links nothing.
- `eos note related <note>` lists a note's links, strongest first (cites and
  lesson 3, scope 2, ticket 1), each with its reason.
- The task brief adds `LINKED TO <procedure or nearest note>`: up to three notes
  linked to it that the brief did not already name, with the reason.

## Consequences

- Host: 178 edges (103 scope, 70 ticket, 4 lesson, 1 cites); 101 of 238 notes have
  a link; derived in 24 ms. A procedure's brief now names the lessons learned in it.
- Supersession (`supersedes`, M1) and near-duplicate clusters in `consolidate` can
  read the same edges; not done here.
- An export to Graphify's format, for its community and visual views, is possible
  and not done.

## Addendum: supersedes (1.17.0)

`eos note add --supersedes <note>` records that a note replaces an older one --
the additive part of roadmap M1, with no migration of existing notes. The old
note stays on disk as the record of what was believed; `note search` and every
brief stop offering it; `note show` on it names the replacement first; the graph
links the two (`supersedes`, weight 3). The rewrite is expected to read like the
note it replaces, so the duplicate and paraphrase guards do not compare the two.
Validity windows and provenance on every note (the rest of M1) are still a
decision.
