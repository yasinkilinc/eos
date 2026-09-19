# EOS

EOS is a code-intelligence engine you drop into a project so an AI coding
agent — or you — can actually find things in it. It scans a codebase into a
symbol cache, a dependency graph and a Markdown knowledge vault, keeps your
own hand-written notes alongside them, and serves all of it over MCP so an
agent gets grep-fast symbol lookup, real import-graph impact analysis, and a
budgeted context bundle instead of guessing from file names. If your project
is a thin layer on top of another codebase — a fork, a vendored dependency, a
platform repo you don't own — EOS can link that parent and search both
through one index.

I built it because pointing an agent at a large, real codebase and asking
"where is X defined" or "what breaks if I change Y" kept costing more tokens
and more wrong guesses than it should have. The runtime that does the actual
scanning is pure Python standard library, on purpose: it gets copied into
every project EOS manages, and a tool that needs `pip install` before it can
even look at your code is a tool people won't bother running. The optional
dashboard and file watcher are the only parts that need real dependencies,
and they're isolated behind their own install step.

## 60-second install

```bash
git clone https://github.com/<you>/eos.git
cd eos
./bin/install.sh              # installs the `eos` launcher to ~/.local/bin

eos --version
eos init /path/to/your/project
eos scan /path/to/your/project --full
eos context /path/to/your/project --budget 12000
```

Requires Python 3.11 or newer and nothing else. The installer copies the
canonical `core/` runtime to `~/.local/share/eos/<version>/` and installs an
executable launcher under `~/.local/bin/eos`. Override the locations with
`EOS_INSTALL_PREFIX`, `EOS_DATA_DIR`, `EOS_BIN_DIR`, or `EOS_PYTHON`.

## What `eos init` gets an agent

`eos init` does two things: it bootstraps `.eos/` (the scan cache, the
knowledge vault, the runtime copy), and it writes four integration surfaces
so a coding agent actually finds EOS without you wiring anything by hand:

1. **A decision-support skill** at `.claude/skills/eos/SKILL.md` — when to
   reach for `eos context`/`compose`/`impact` instead of grepping blind.
2. **A researcher agent profile** at `.claude/agents/eos-researcher.md` — a
   subagent whose whole job is answering "where/what/why" questions via EOS.
3. **An MCP server registration**, merged key-wise into `.mcp.json`
   (`mcpServers.eos`) so it sits next to whatever other MCP servers the
   project already has, without touching them.
4. **A section in `AGENTS.md`**, wrapped in `<!-- eos:begin -->` /
   `<!-- eos:end -->` markers so anything you wrote around it survives.

All four are re-runnable — `eos ai update` refreshes just the generated block
after an EOS upgrade, leaving everything else in each file alone. Pass
`--no-ai` to `eos init` to skip all four for a project that doesn't want
them. See [ADR-009](docs/decisions/009-ai-integration-layer.md).

## CLI

| Command | Purpose |
|---|---|
| `eos init <path> [--link-parent <path>] [--link-label <name>] [--no-ai]` | Bootstrap `.eos/`, write the AI integration surfaces |
| `eos scan <path> [--full] [--with-parents]` | Incremental or full scan; optionally index linked parents too |
| `eos update <path> [--dry-run]` | Update the runtime from canonical `core/` |
| `eos doctor <path>` | Validate `.eos/` integrity |
| `eos info <path>` | Show instance summary |
| `eos status <path>` | Project status and latest scan metadata |
| `eos clean <path>` | Remove generated artifacts (cache/brain/graph); id and config are kept |
| `eos context <path> [--budget N]` | Generate an AI-oriented project context, budgeted in approximate tokens |
| `eos compose <path> <task> [target] [--budget N]` | Compose focused context for one task, optionally anchored on a file |
| `eos impact <path> <file> [--depth N] [--include facts\|coverage\|history]` | What a file reaches and what reaches it, to N hops |
| `eos why <path> [file] [--predicate P] [--format json]` | Provenance of a file's facts, and which detectors found nothing |
| `eos graph <path> [--type import\|all] [--output -]` | Export the generated project graph as JSON |
| `eos index <path>` | Rebuild `.eos/data/eos.db` from notes, brain, graph, and git history |
| `eos query <path> [sql \| --search "..."] [--limit N]` | Read-only SQL, or full-text search, against `eos.db` |
| `eos parents <path>` | List configured parent-project links |
| `eos note add\|list\|search\|skip\|amend\|audit <path> ...` | Manage authored knowledge notes (see below) |
| `eos ai update <path>` | Refresh the AI integration surfaces for the current EOS version |
| `eos mcp <path>` | Start the read-only stdio MCP server |
| `eos bench <path> [--samples N]` | Measure EOS's own tools against baselines, on this project |
| `eos ui [port] [--yes] [--no-install]` | Start the multi-project dashboard |

## What the index does and does not do

Read this before trusting an artifact.

| Language | Symbols | Import edges | Entry points |
|---|---|---|---|
| Python | real `ast` | resolved, including relative imports | `main`/`app`/`cli` filename heuristics |
| Java | regex | package-qualified imports | Spring annotations (`@RestController`, `@Service`, …) |
| JavaScript / TypeScript | regex | path specifiers, `import type`, re-exports | `index.*` filename heuristics |

Anything else — C, SQL, Rust, Go, HTML — is not parsed. Every scan prints how
many files it skipped and why, so silence is never mistaken for absence:

```text
Scanned 803 files across ['python']
Skipped 731 path(s) by ignore rules: build (730), bin (1)
  Un-ignore a default with [scan] unignore in .eos/config.toml
314 file(s) in languages EOS does not index: .sql (314)
```

`build`, `bin`, `dist`, `target`, `node_modules` and the usual caches are
ignored by default. For a project whose real source lives in one of them, add
`unignore` to `.eos/config.toml`:

```toml
[scan]
unignore = ["build"]
```

`eos index` also reads two configurable patterns for mining git history, both
under `[index]` in `.eos/config.toml`:

```toml
[index]
ticket_pattern = "\\b[A-Z][A-Z0-9]+-[0-9]+\\b"   # default; JIRA-style keys
merge_branch_pattern = "^Merge in \\S+ from (\\S+) to \\S+"
```

`merge_branch_pattern` is unset by default — GitHub's merge-commit subject
line is already handled without configuration. The example above is
Bitbucket's merge-commit body format (`Merge in <project> from <source> to
<target>`); a project on a host with its own merge-commit convention sets its
own pattern here to get source-branch extraction for free. It is matched
line-by-line against the commit body (`re.MULTILINE`), not the whole body at
once.

### Where a fact came from

Every derived fact records how it came to be known — `origin` (extracted,
documented, inferred, verified), `confidence`, the detector that produced it,
and the `path:line` it was read from. `eos why` prints them:

```bash
eos why . src/main/java/com/example/OrderService.java
```

The last lines of that answer are the ones worth reading. `coverage` records
what each detector was *asked* about, so a detector that examined 2,652 files
and produced nothing is distinguishable from one that never ran — otherwise
"this project has no message producers" and "nobody looked for message
producers" print the same silence.

Provenance is not part of `graph.json`: that artifact reaches 25 MB on a real
service and is returned whole over MCP. Facts travel in
`.eos/data/brain/evidence.jsonl` and are queried from `eos.db`. See ADR-014.

### Indexing something only your project has

Some knowledge is not in the code. A system that implements its business flows
as a configured chain of named steps, looked up at run time, cannot be
understood from the source alone — the configuration *is* the program. EOS
indexes that kind of artifact through **extensions**: small Python modules the
project supplies, which add tables to the same index everything else lives in.

```toml
[index]
extensions = ["tools/eos-ext/flows.py"]
```

A module may define `SCHEMA` (its tables), `COUNTS` (numbers `eos index`
prints), `sources(root, notes_dir)` (what it reads, hashed for staleness) and
`load(build)` (the rows). The contract is documented in `core/extensions.py`.

`extensions/journeys.py` in this repository is the reference implementation
and is usable as-is. It indexes journey documents and exported step-chain
snapshots, and resolves each configured step to the service that runs it —
recording *how* it was resolved and what else it could have been, so an
attribution can be checked rather than believed:

```bash
eos query <project> "SELECT flow, sort_id, bean_name, owner, resolution
                     FROM journey_step WHERE owned = 1 ORDER BY sort_id"
```

A configured extension that is missing or unimportable stops the build and
keeps the previous index; one that fails while running costs only its own
tables and is reported in `build_issue`. See ADR-013.

## MCP

```bash
eos mcp /path/to/project
```

EOS exposes a read-only MCP server over stdio for local code intelligence.
It's self-contained and doesn't need the UI dependencies. Registered tools:

`get_project`, `get_structure`, `get_context`, `get_file`, `find_symbol`,
`compose`, `impact_analysis`, `get_graph`, `get_history`,
`get_parent_implementation`, `search_notes`, `add_note`.

All are read-only except `add_note`, which writes a note into the configured
knowledge directory and nowhere else — it also refuses bodies containing
credential-shaped text and scope entries naming sensitive files or paths
outside the project.

## Notes

Notes record what a scan cannot re-derive — why something is the way it is:

```bash
eos note add /path/to/project --kind finding --title "..." --body "..." --tags a,b
eos note add /path/to/project --kind defect --title "..." \
  --cause "..." --solution "..." --metric "..."
eos note list /path/to/project [--tag a]
eos note search /path/to/project "query"
eos note skip /path/to/project --reason "..." --session <id>
eos note amend <note-file> /path/to/project [--body "..." | --reaffirm "..."] [--scope a,b]
eos note audit /path/to/project
```

`skip` records that a session had nothing durable to write down, so a
Stop-hook gate stops asking; it never writes a note.

`amend` is the only way a note that has gone stale comes back into agreement
with the code: it rewrites the note and re-hashes its `--scope`. Use `--body`
when the claim itself changed, `--reaffirm` when the file moved on but the
claim still holds, and `--scope` when the scoped file was renamed or deleted
(`--body` and `--reaffirm` re-hash the recorded path, so for a file that is
gone they can only refuse). When one note scopes both a removed file and a
changed one, `--scope` and `--body` go in the same command: a scope-only
amend may neither re-hash nor drop an entry that still exists and changed. It
refuses an unchanged body, a placeholder value, and any amend that would
leave the note with no scope entry resolving to a file.

`audit` reports every note whose scoped files have moved, split into
hand-written (revise with `amend`) and generated (re-run the generator), and
prints the command for each. It's a report and always exits 0.

Notes live in `.eos/knowledge/` by default, or wherever `[knowledge] dir` in
`.eos/config.toml` points — useful when the project's own repository isn't a
good home for them (a workspace of repos that share one knowledge directory,
for instance). Notes are never touched by `scan`, `clean` or `update`.

`--scope` records which files a note is about and hashes them at write time,
so `eos doctor` can later flag notes whose code has moved on since. Notes
matching the current task are injected into `get_context` output, capped at
15% of the budget.

## Linked parent projects

A project that is a thin overlay on another codebase can link it, so both are
searchable through one index:

```bash
eos init /path/to/project --link-parent /path/to/parent
eos scan /path/to/project --with-parents
eos parents /path/to/project
```

Parent files are indexed under `@parent:<label>/` keys in the same cache;
`get_parent_implementation` returns their real source. Parent trees are only
ever read.

## eos-ui (optional)

`eos ui` is a small local dashboard: a project list, a drill-down import
graph per project, and (if you point `EOS_GRAPHIFY_ROOT` at one) a viewer for
Graphify artifacts. It's entirely optional — nothing else in EOS needs it.

`eos ui` installs its own Python dependencies (FastAPI, uvicorn, watchdog)
into a venv EOS owns on first run, and starts the server — but it does not
build the React frontend, since that's a one-time step, only needed once per
pull of frontend changes:

```bash
cd ui/frontend
npm ci
npm run build      # -> ui/static/, served by the FastAPI app at /
cd ../..
eos ui              # or: bin/start.sh
```

Until the frontend is built, `ui/static/` doesn't exist and `/` returns a
JSON hint (`{"status": "frontend-not-built", ...}`) instead of the page; the
API itself (`/api/...`) works either way.

### Frontend dev loop

For actual frontend work, run the two servers side by side instead of
rebuilding on every change:

```bash
eos ui 8000              # terminal 1: FastAPI + API on :8000
cd ui/frontend && npm run dev   # terminal 2: Vite on :5173
```

Vite serves the app on `:5173` and proxies `/api` requests to the FastAPI
server on `:8000` (see `ui/frontend/vite.config.ts`), so the browser only
ever talks to `:5173` and hot-reloads on save. `npm run build` is only needed
again once you're done and want a static `ui/static/` for `eos ui` to serve
directly.

### Workspace view — one thing it deliberately doesn't do

The dashboard's cross-project view links two projects when one's graph
mentions the other's name, matched two ways: a separator-delimited form
(token-boundary match) and a compact form (substring match, dropped below 6
characters to avoid matching unrelated short words). An earlier version also
derived an extra alias for any dash-separated name with three or more
segments by dropping its first segment — a generic "strip the org/team
prefix" heuristic. That generic multi-segment alias derivation has been
removed: it produced more false cross-project links than it was worth. If you
expected two projects with a shared multi-part name prefix to auto-link and
they don't, this is why — link them explicitly instead of relying on name
matching.

## Environment

| Variable | Overrides | Default | Read by |
|---|---|---|---|
| `EOS_PYTHON` | interpreter used to run EOS | `python3` | `bin/eos`, `bin/install.sh` |
| `EOS_INSTALL_PREFIX` | install root | `$HOME/.local` | `bin/install.sh` |
| `EOS_DATA_DIR` | runtime copies | `$PREFIX/share/eos` | `bin/install.sh` |
| `EOS_BIN_DIR` | launcher location | `$PREFIX/bin` | `bin/install.sh` |
| `EOS_UI_DB` | central instance registry | `~/.eos-ui/eos.db` | eos-ui |
| `EOS_UI_ALLOWED_HOSTS` | Host header allowlist | `127.0.0.1,localhost` | eos-ui server |
| `EOS_CORE_PATH` | core source for the UI | discovered | eos-ui |
| `EOS_GRAPHIFY_ROOT` | Graphify artifact root(s) to scan, `os.pathsep`-separated | unset (feature off) | eos-ui |
| `EOS_GRAPHIFY_REFRESH_CMD` | graph refresh command template (`{project}`, `{key}`) | unset | watcher queue |
| `EOS_GRAPHIFY_COORDINATOR_ROOT` | candidate coordinator script path(s), `os.pathsep`-separated | unset (feature off) | watcher queue |
| `EOS_GRAPHIFY_QUEUE_STATE` | queue state file | `~/.eos-ui/graphify-queue.json` | watcher queue |
| `EOS_PARENT_SEGMENT` | path segment name that marks a "parent" repository | unset (nothing is a parent) | eos-ui |

## Dependency tiers

EOS treats what it depends on as three tiers, not one requirements file,
because they fail differently (`core/preflight.py`, [ADR-010](docs/decisions/010-dependency-tiers.md)):

- **T1 — Python itself.** EOS can't install its own interpreter, so instead
  of a traceback it prints the exact command for your platform
  (`brew install python@3.12`, `apt install python3.12`, `pyenv install`).
- **T2 — the UI's dependencies** (FastAPI, uvicorn, pydantic, watchdog).
  Installed by EOS, after asking, into a venv EOS owns — never your system
  interpreter, never the project's own environment. `eos ui --yes` skips the
  prompt; `eos ui --no-install` reports what's missing and stops instead.
- **T3 — optional integrations**, like Graphify. Unset configuration means
  the feature is off, not broken.

The CLI core itself (everything except `eos ui`) has zero third-party
dependencies, on any tier.

## `eos bench`

```bash
eos bench /path/to/project --samples 50
```

Measures EOS's own tools against plain alternatives, **on the project you run
it against** — never a bundled fixture, so the number is always about your
code, not a demo. It measures two different kinds of claims differently
([ADR-011](docs/decisions/011-bench-ground-truth.md)):

- `find_symbol` has real ground truth: the language plugins already know
  which symbol is defined in which file, so `eos bench` samples symbols from
  that parsed set and checks whether `find_symbol` finds the right one.
- Impact analysis has no independently-known correct answer, so `eos bench`
  compares against `grep` as a labeled baseline, not a correct answer — and
  the report says so explicitly.

The report is scoped to the project it ran against and says so: a result on
a small Python codebase is not a claim about EOS on a large Java one.

## Design principles

1. **Zero external dependencies in the core runtime** — every project gets a
   self-contained Python stdlib runtime.
2. **Separation of runtime and data** — updates replace `runtime/` but never
   touch `data/`.
3. **Knowledge model as single source of truth** — plugins produce a semantic
   model; generators derive Markdown/Graph/JSON from it.
4. **Permanent ID** — each instance is identified by a UUID stored in
   `.eos/id.txt`, surviving path moves and renames.

## Architecture and decisions

`ARCHITECTURE.md` covers the data model and the pipeline in more depth.
Every non-trivial architectural choice — including the extraction into an
independent repository, the AI integration layer, dependency tiers, `eos
bench`'s ground-truth boundary, and the MIT license — is recorded as an ADR
in `docs/decisions/`, in context/decision/consequences form.

## Contributing

Issues and pull requests are welcome. A few things that keep contributions
easy to review:

- `core/` stays pure Python standard library — no third-party `import` under
  it, ever (see [ADR-001](docs/decisions/001-runtime-vs-core.md) and
  [ADR-010](docs/decisions/010-dependency-tiers.md)). New optional
  functionality goes in `ui/` or behind a T3 environment variable instead.
- Run `python3 -m pytest -q` before opening a PR; `bash tools/check-clean.sh .`
  also runs in CI and fails on any leftover internal identifier.
- New language support is a new plugin under `core/plugins/`, following the
  existing detector/parser/analyzer split — see ADR-004.
- A change to an architectural decision gets its own ADR, not a silent
  rewrite of an old one.

## License

MIT — see [LICENSE](LICENSE).
