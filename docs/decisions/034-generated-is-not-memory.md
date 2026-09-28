# ADR-034: Generated is not memory

## Status

Accepted, 2026-09-28. Released in 1.39.0. Roadmap item M3 (phase 2).

## Context

The host's note stores held 873 notes, and 405 of them were written by a script:
206 endpoint tables (`api-inventory`), 196 imported AGENTS.md sections
(`agents-md`), 3 directory listings (`repo-topology`). Every reader treated them
as memory: the duplicate guards compared against them, the brief and injection
had to rank them last one reader at a time (`is_bulk_index` at eight sites), and
`note audit` reported them stale whenever a pom changed.

The architecture research (§6.3) put generated artifacts in their own store. Its
first count, 591 of 841, also listed `context-budget` (106), `H-05` (31) and
`journey-map` (56). Measured again on 2026-09-28, those are not generated:

- `context-budget`/`H-05` were sections moved out of the host's CLAUDE.md and
  skills. The notes are now the only copy, and 87 golden answers point at them.
- `journey-map` notes are prose a person wrote in a journey document, copied in
  by a script (the reasoning behind `BULK_INDEX_SOURCES`). Five golden answers
  point at them; M4 decides their form.

## Decision

- A store's generator notes live in its `generated/` subdirectory
  (`notes.generated_dir`). `load_dir` reads a store flat, so `load_notes` (and
  through it the brief, injection, audit, Stop gate, consolidate and duplicate
  guards) never sees them.
- `add_note(provenance="generated")` writes there and compares the new body only
  with other generator notes. `amend_note` works in place and compares the same
  way.
- They are offered only when asked for: `eos note search --generated`, the MCP
  `search_notes` tool's `generated: true`, and `notes.search_notes(...,
  generated=True)`, ranked with the memory by the one scorer. `eos note show`
  finds them by name.
- The index keeps them in `note` as `generated/<file>`, with `generated = 1`,
  in `search` under source `generated`, and outside the note graph. The column's
  meaning is unchanged (`is_generated`); a note in `generated/` is always 1.
  `add_note` refuses a file name the other directory already holds.
- `eos note move-generated [--apply]` moves what belongs there: `provenance:
  generated`, or a bulk-index source whatever its front matter says. It moves
  byte for byte, appends `from`/`to`/`sha256` to `generated/moved.tsv` as each
  file moves (under the record's lock, so a move cut short still says what it
  moved), refuses to overwrite, and on a second run finds nothing. Without `--apply` it only
  lists.

## Consequences

- The research's acceptance "notes store ≤ 300" does not hold for the host: 468
  authored notes remain after the move. The gate becomes "no generator note in a
  memory store" (`move-generated` lists nothing).
- A project that never moves its notes sees no change. Generator notes still in
  the memory store are read as before.
- Hosts that read a store directory themselves (a guide built from the imported
  AGENTS.md sections, a search across stores) read `generated/` for those notes.
