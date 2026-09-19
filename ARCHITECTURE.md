# EOS Architecture

## Top-Level Topology

`eos-ui` tracks multiple `.eos` instances from a central SQLite database. Each instance lives inside a project folder and contains:

- `.eos/id.txt` — permanent UUID v4.
- `.eos/config.toml` — local ignore patterns and depth overrides.
- `.eos/runtime/` — deployed copy of the canonical `eos/core/` runtime.
- `.eos/data/` — immutable generated artifacts (cache, brain, graphs).

```text
┌─────────────────────────────────────┐
│            eos-ui                   │
│   SQLite  +  scan roots  +  views   │
└──────────────┬──────────────────────┘
               │ reads/writes
┌──────────────▼──────────────────────┐
│  ~/.eos-ui/eos.db (single SQLite)   │
│  eos_instances(id, path, name, ...) │
└─────────────────────────────────────┘
               │ scans / registers
    ┌──────────┴──────────┐
    ▼                     ▼
project-a/            project-b/
  .eos/                 .eos/
    id.txt                id.txt
    runtime/              runtime/
    data/                 data/
```

## Data Model

### `eos_instances` table

| Field | Type | Constraint | Purpose |
|-------|------|------------|---------|
| `id` | TEXT | PRIMARY KEY | Matches `.eos/id.txt` |
| `path` | TEXT | UNIQUE NOT NULL | Last known absolute path |
| `name` | TEXT | — | Display name |
| `engine_version` | TEXT | NOT NULL | Installed runtime version |
| `tech_stack` | JSON | — | e.g. `["python", "typescript"]` |
| `status` | TEXT | NOT NULL DEFAULT 'active' indexed | `active / missing / stale` |
| `created_at` | TIMESTAMP | NOT NULL | First registration |
| `last_scanned_at` | TIMESTAMP | indexed | Last scan timestamp |
| `last_updated_at` | TIMESTAMP | — | Last runtime update |
| `metadata` | JSON | — | Flexible extra fields |

Reconciliation rule: scanner reads `.eos/id.txt` first, then queries `WHERE id=?`. If found, `UPDATE path`; otherwise `INSERT`. This survives folder moves and renames.

## `.eos` Folder Layout

```text
.eos/
  id.txt
  config.toml
  runtime/                 # overwritten entirely by updates
    VERSION
    manifest.json
    eos.py
    scanner.py
    knowledge/
    plugins/
    generators/
    lib/
  data/                    # updates never touch
    cache/
    brain/
      _index.md
      Architecture.md
      TechStack.md
      EntryPoints.md
      AI_SUMMARY.md
      graph.json           # machine-readable graph (eos-ui reads this)
      evidence.jsonl       # provenance sidecar, streamed into eos.db (ADR-014)
```

## Knowledge Pipeline

```text
Scanner (filesystem walk + mtime/hash cache)
  → Plugin Detector (which languages?)
  → Plugin Parser (AST / regex imports)
  → Plugin Analyzer (extract symbols, exports, dependencies)
    → Semantic Model (File, Symbol, Import, Export)
      → Knowledge Model (Component, Service, EntryPoint, Dependency)
        → Linker (backlinks, cross-references)
          → Classifier (tags, types)
            → Generators
              ├─ markdown/  → brain/*.md
              ├─ graph/     → import.graph.json
              └─ json/      → graph.index.json
```

The Knowledge Model is the single source of truth. Markdown and JSON are derived artifacts; changing output format never changes the model.

## The Index and its Extensions

`eos index` folds the authored notes, the brain documents, the graph and git
history into one SQLite file at `.eos/data/eos.db`. It is derived and always
safe to delete: it is rebuilt from scratch, written to a temp file and moved
into place in one step.

A project can add to it. `[index] extensions` in `.eos/config.toml` names
Python modules that contribute tables, counts, staleness inputs and rows
against the published `index.BuildContext`. That is how to index an artifact
only one project knows how to read — without a second database, a second query
interface, or project-specific code in `core/`. `extensions/journeys.py` is
the reference implementation. Contract: `core/extensions.py`; rationale:
ADR-013.

```text
notes ─┐
brain ─┤
graph ─┼─→  .eos/data/eos.db  ←─ extension tables (same file, same queries)
 git  ─┘         meta.extensions records which extension wrote what
```

## Provenance

Every derived fact records how it came to be known: `origin` (extracted /
documented / inferred / verified), `confidence`, the `detector` that produced
it, the `source_ref` it was read from, and when it was observed. These live as
rows in the index, never as fields in `graph.json` — that artifact is 25 MB on
a real service and is returned whole by MCP. They travel in
`.eos/data/brain/evidence.jsonl` and are queried from SQLite.

Alongside them, `coverage` records what each detector was *asked* about. A
detector with no row was never run; one with `hits = 0` ran and found nothing.
Without that distinction the two print the same silence. `eos why <path>` shows
both. Rationale: ADR-014.

## Update Mechanism

Canonical source is this repository's `core/` folder. During `eos update`:

1. Compute manifest of local `.eos/runtime/`.
2. Compute manifest of canonical `core/`.
3. Replace only changed files.

`data/` is never modified by updates, and no backup of the previous runtime is
kept. The runtime is a copy of a canonical source tracked in git, so a rollback
is `git checkout` plus one `eos update` — the timestamped copies this used to
leave in every project's gitignored `.eos/` were never read.

## CLI Commands

| Command | Phase | Purpose |
|---------|-------|---------|
| `eos init <path>` | 0 | Bootstrap `.eos/` in a project, write the AI integration surfaces |
| `eos scan <path> [--full]` | 0 | Incremental or full scan |
| `eos update <path>` | 1 | Update runtime from canonical source |
| `eos doctor <path>` | 0 | Validate `.eos/` health |
| `eos info <path>` | 0 | Show instance metadata |
| `eos clean <path>` | 0 | Clear cache/brain/graph (preserve id/config) |
| `eos index <path>` | 1 | Rebuild `.eos/data/eos.db` from notes, brain, graph and git history |
| `eos query <path>` | 1 | Read-only SQL, or `--search`, against `eos.db` |
| `eos status <path>` | 0 | Project status and latest scan metadata |
| `eos graph <path>` | 3 | Export the generated project graph |
| `eos context <path>` | 0 | AI-oriented project context, budgeted |
| `eos compose <path> <task>` | 0 | Focused context for one task |
| `eos impact <path> <file> [--depth N]` | 0 | What a file reaches and what reaches it, answered from the index |
| `eos why <path> [file]` | — | Provenance of a file's facts, and which detectors found nothing (ADR-014) |
| `eos rules <path> [--untested]` | — | Behaviour codes thrown, and which no test names (ADR-016) |
| `eos mcp <path>` | 0 | Start the read-only stdio MCP server |
| `eos bench <path>` | — | Measure EOS's own tools against baselines, on this project (ADR-011) |
| `eos ui [port]` | 2–3 | Start the multi-project dashboard |
| `eos parents <path>` | — | List configured parent-project links |
| `eos note <subcommand> <path>` | — | Manage authored knowledge notes |
| `eos ai update <path>` | — | Refresh the AI integration surfaces (ADR-009) |

The full flag reference for each command is in the project README.

## Technology Choices

| Layer | Choice | Rationale |
|-------|--------|-----------|
| Runtime | Python stdlib | No `pip install` for the code dropped into random project folders |
| UI backend | FastAPI (Python) | Local-only, 127.0.0.1 bind |
| UI frontend | React + Cytoscape.js | Hierarchy/dependency graphs fit Cytoscape layouts |
| Database | SQLite | Single-user, local, serverless |
| Config | TOML | Python 3.11+ stdlib `tomllib`; no YAML dependency |
| Packaging | Browser-first local app; Tauri optional in Phase 4 | |

## ADRs

All architectural decisions are recorded in `docs/decisions/`.
