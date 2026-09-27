# EOS core audit — what version 1.2.2 actually implements

- **Audit date:** 2026-09-26
- **Tree audited:** `<host>/tools/eos/` (engine `core/VERSION` = `1.2.2`). Byte-identical to the upstream clone `<eos-repo>` at `921cb6c release: 1.2.2` (`diff -rq core extensions` is empty).
- **Method:** every module under `core/`, `extensions/`, `bin/`, the four hook templates and three Markdown templates, all 25 ADRs, both plans, `docs/phases.md`, `README.md` and `ARCHITECTURE.md` were read in full. The UI was read at the level of schema, routes and data sources. The test inventory was taken with `pytest --collect-only -p no:cacheprovider` (no test executed). Behaviour probes ran against a scratch copy, or through read-only imports with routing recording monkeypatched off. **No file in the repository was modified.** Real-usage numbers come from read-only reads of the nexus instance (`.eos/data/*`, `.devin/knowledge/nexus/*`) and of the service indexes under `../microservices/*/.eos/data/eos.db`.
- **Citation format:** `core/<file>.py:<start>-<end>` refers to line ranges in the audited tree.

---

## 0. Summary

### 0.1 What EOS is, in one paragraph

EOS is a stdlib-only, file-first, **fully deterministic** CLI that combines project memory with a static code index. It does four things:

1. **Code index.** It parses Java (a masking lexer plus a brace-depth scope scanner), Python (`ast`) and JS/TS (regex) into a per-file semantic cache (`file_cache.json`). It then projects that into a file-level graph (`graph.json`) and a provenance sidecar (`evidence.jsonl`).
2. **Authored and append-only stores.** Beside the code index it keeps Markdown notes of five kinds, plus three append-only JSONL ledgers in the knowledge directory: `work.jsonl` (intent), `executions.jsonl` (episodes) and `verifications.jsonl` (runs). Three local logs live under `.eos/data/`: `telemetry.jsonl`, `routing.jsonl` and `routing-usage.jsonl`.
3. **Derived SQLite index.** It folds all of that, plus up to 2,000 commits of `git log`, into a throwaway SQLite file `.eos/data/eos.db`. That file is rebuilt from scratch whenever its inputs' digest changes.
4. **Surfaces.** It exposes everything through:
   - a 33-command CLI;
   - a 12-tool stdio MCP server, off by default;
   - generated Claude Code hooks (SessionStart, UserPromptSubmit, Stop, and an optional PreToolUse). The hooks put a budgeted "brief" into the session so the agent never has to decide to call EOS.

What EOS does **not** do:

- **It calls no model, executes no task and does no planning.**
- **Retrieval is lexical only.** Notes are ranked by IDF-weighted coverage; the index uses FTS5 BM25 with a LIKE fallback. There are no embeddings; that was decided out (C-10).
- **"Learning" is limited.** It consists of procedure run counters, auto-written lesson notes, a conditional `verified` rung, and a confidence word derived when read.

Model and effort routing (1.2.x) is a keyword classifier plus a 7-factor weighted score plus a capability table. It **advises** the harness; the only code path that acts on it is an opt-in hook that fills in a subagent's missing `model`.

### 0.2 Scorecard

| # | Dimension | Status | One-line mechanism |
|---|---|---|---|
| 1 | Code scanning | **IMPLEMENTED** | `os.walk` with ignore rules; mtime/size, then sha256 change detection; FileSemantic cache as one JSON file |
| 2 | Parsing per language | **IMPLEMENTED (uneven)** | Java is structural (lexer + scope stack); Python is `ast` at top level only; JS/TS is regex; nothing else |
| 3 | Semantic model | **IMPLEMENTED** | 9 dataclasses (`FileSemantic` with 15 fields) plus `ScanReport` |
| 4 | Knowledge model | **PARTIALLY IMPLEMENTED** | Generic `KnowledgeNode` and `Dependency` only; no Component/Service classes; no Linker; SSOT bypassed by several readers |
| 5 | Code facts / provenance | **IMPLEMENTED** (narrow) | `fact` + `coverage` + `scan_exclusion` rows; 11 predicates from 6 detectors; every fact is `origin=extracted` |
| 6 | Business workflow knowledge | **PARTIALLY IMPLEMENTED** (extension, not core) | `extensions/journeys.py` resolves configured step chains to classes; only one service has rows |
| 7 | Retrieval / search | **IMPLEMENTED** (lexical); embeddings **MISSING by decision** | IDF coverage (title .5 / tags .3 / body .2) with floors; FTS5 OR-BM25 with a 0.5 relative floor; LIKE fallback |
| 8 | Context assembly | **IMPLEMENTED** | `build_context`: greedy whole-section fit, notes 15% and work 8% caps. Task brief: 1,500 tok at 3.0 chars/tok with exempt lines |
| 9 | Semantic memory (notes) | **IMPLEMENTED** (no `supersedes`) | Five kinds, front matter, scope hashes, staleness audit, guarded `amend`, dedup, credential and placeholder guards |
| 10 | Procedural memory | **IMPLEMENTED** | `kind: procedure` notes with parsed `## Steps`; counters written only by `run finish`; confidence derived when read |
| 11 | Episodic memory | **IMPLEMENTED** (capture is host-dependent) | `executions.jsonl` start/event/finish, folded on read; 7 event kinds; refs only |
| 12 | Session history / id threading | **PARTIALLY IMPLEMENTED** | One id across stores (env → config vars → Claude id); no per-session history view; the "hook exports EOS_SESSION" claim does not work |
| 13 | Tool/action history | **IMPLEMENTED** | `eos-event` (POSIX sh) and `eos run event`; pointer file per session; `run tools`, `run diff` |
| 14 | Planning / decomposition | **MISSING** (by design) | Nothing schedules, decomposes or estimates; procedures are human-authored static lists |
| 15 | Execution | **MISSING** (by design) | Records only; the only subprocesses are git, grep (bench), venv/pip (ui) and hook→`eos` |
| 16 | Agent routing | **MISSING** | One generated agent profile; no dispatch between agents; the PreToolUse hook only picks a *model* |
| 17 | Model/effort selection | **IMPLEMENTED** (advisory, no learning) | 12-type keyword classifier, 7 weighted factors, 4 levels, cheapest-sufficient registry pick, effort clamp; `adjust_for_history` is a no-op |
| 18 | Skills / plugins | **IMPLEMENTED** (one runtime defect) | Static `LanguagePlugin` registry; index extensions (SCHEMA/COUNTS/QUESTIONS/sources/load); generated skill, agent, hooks, AGENTS.md block |
| 19 | MCP | **IMPLEMENTED** (off by default) | Hand-rolled newline JSON-RPC over stdio; 12 tools; only `add_note` writes |
| 20 | Impact analysis | **IMPLEMENTED** (direction-limited) | Recursive CTE over 6 edge kinds, depth 1–5, capped at 500 rows per direction; graph.json fallback is import-only at depth 1 |
| 21 | Persistence | **IMPLEMENTED** | Files are the source of truth; `eos.db` is always rebuilt (temp file, fsync, `os.replace`); 21 core tables + FTS; central UI registry |
| 22 | Learning | **PARTIALLY IMPLEMENTED** | Counters, lessons (+`## Seen again`), `verified` rung, audit; no adaptive policy, ranking or confidence storage |
| 23 | Verification | **IMPLEMENTED** (recording) | `eos verify` records runs; a person may add a verdict; `draft-test` grounds a Java skeleton against the source |
| 24 | Telemetry / cost | **IMPLEMENTED** (3 defects) | Command, flag names, ms, chars/4, session. `rebuilt` is never set; `ok` ignores exit codes; "median tokens" is a mean |
| 25 | Token efficiency | **IMPLEMENTED** (with gaps) | Brief budget, context caps, brain deduplication, `rules --limit`, one surface. Gaps: branch brief unbudgeted, pretty-printed JSON |
| 26 | Failure / recovery | **IMPLEMENTED** (no locking) | Atomic index swap; line-skipping ledgers; exit-0 hooks; no locks; orphaned `*.tmp` files observed |
| 27 | UI | **PARTIALLY IMPLEMENTED** (peripheral) | FastAPI + React/Cytoscape over `~/.eos-ui/eos.db` and graph.json; reads none of the memory stores |

### 0.3 The findings that matter most for an architectural decision (all verified)

1. **There is no vector or semantic layer, and it is excluded by decision.**
   - `memory_audit.check_c10` greps `core/` for numpy, faiss, torch, openai and the like, and reports `DECIDED` (`core/memory_audit.py:60-62,255-261`).
   - Every recall mechanism is word matching. There is no stemmer, no synonyms and no cross-language support. The only exception is configurable routing keywords.
2. **The knowledge model is thinner than ARCHITECTURE/ADR-005 describe.**
   - Only `KnowledgeNode` and `Dependency` exist (`core/knowledge/model.py:10-93`). There are no Component/Service/EntryPoint types and no Linker or backlinks stage.
   - `symbol_index` is built and never read (`core/knowledge/builder.py:68,91-95`).
   - Three of the edge kinds listed in the model's comment (`monorepo-parent|shared-dep|manual-link`) are never produced.
3. **Defect: `eos ai update` / `eos init` cannot run from a project's `.eos/runtime/`.**
   - The runtime updater copies `*.py` only (`core/lib/updater.py:30-38`).
   - So `skill.md`, `agent.md` and `agents_section.md` are missing from the runtime.
   - Reproduced on a scratch copy: `FileNotFoundError: .../.eos/runtime/ai/templates/skill.md`.
   - The PATH launcher (`bin/install.sh` uses `copytree`) is not affected.
4. **Three telemetry fields are not what the documents say.**
   - `Timer.rebuilt` is never set to `True` anywhere (`core/telemetry.py:134,150`). The "rebuilds" column of `eos cost` is therefore always 0: 0 of 5,054 real nexus lines.
   - `ok` is `exc_type is None` (`:145`), so a command that returns exit 1 is recorded as ok: 0 failures in 5,054 lines.
   - "median tokens" is actually a **mean**: `tokens // calls` (`:252`). ADR-019 says "Medians, not means".
5. **The README's MCP tool list is wrong.** It names `get_history` (`README.md:337`). No such tool exists. The 12th tool is `search_index` (`core/mcp_server.py:216-232`), and history moved to `impact_analysis include=["history"]`.
6. **The README says a Stop hook runs `eos route --usage-from`** (`README.md:683-687`). EOS's generated Stop hook (`core/ai/templates/session_stop.py`) does not. Only the host's own `automation/hooks/eos-usage.py` does.
7. **The "hook exports `EOS_SESSION` so every later call is attributed" claim** (`README.md:543-545`; comment in `session_start.py:90-95`) cannot hold.
   - The hook sets the variable only in its child `eos brief` process.
   - Attribution actually works because Claude Code exports `CLAUDE_CODE_SESSION_ID`.
8. **In practice, routing nearly always gives the same answer.**
   - 58 of the 67 decisions recorded in nexus are `sonnet/medium`, and the MEDIUM level accounts for 58 of 67.
   - The type mix (planning 11, code_review 9, test_generation 13) suggests keyword collisions with domain words such as "rate plan", "PR" and "test env". That cause is a hypothesis: the trace stores only a hash of the task.
9. **Prompt-hook false positive, reproduced.** `eos brief --task "ok, continue" --task-only` on nexus prints a 478-character block, because one note title contains "Save & Continue". The README promises "nothing when nothing is recorded".
10. **The SessionStart (branch) brief has no size budget.**
    - It is capped only by item counts (5 work items, 4 notes, 3 runs); `last:` and `blocked on:` bodies print unclipped.
    - On nexus it measured 3,055 characters (~1,018 tokens at 3.0 chars/token), against "a handful of lines" (ADR-021).
11. **Impact and trace follow edges in one direction only.**
    - A call through an interface-typed field lands on the interface file. Implementations are never reached, because `implements` edges point from implementation to interface.
    - On Spring constructor-injection code this truncates forward traces structurally. This is independent of the runtime-wiring caveat that ADR-017 prints.
12. **Planning, execution, agent orchestration and adaptive learning are absent by explicit decision** (ADR-018, ADR-020, ADR-025; `core/work.py:30-34`; `policy.adjust_for_history` returns its input, `core/routing/policy.py:126-136`).
13. **There is no locking anywhere.**
    - Concurrency relies on single-line `O_APPEND` writes.
    - Procedure counters are a read-modify-write of a git-tracked Markdown file (`core/notes.py:1682-1711`).
    - The telemetry and trace tail-trims are read-then-rewrite.
    - An orphaned `eos.db.s733dymu.tmp` from 2026-09-15 is still in nexus `.eos/data/`: a hard-killed build bypasses the cleanup in `_build`.
14. **Scale is real.**
    - Service indexes are about 56 MB, with ~48–50k facts and ~31k edges.
    - `graph.json` is written pretty-printed (`indent=2`) and reaches 33.8 MB (svc-pcm-product-catalog).
    - MCP `get_graph` returns it whole.
15. **Maturity and soak time are short.**
    - 83 commits between 2026-09-12 (0.10.0, the extraction) and 2026-09-25 (1.2.2).
    - Operational memory (0.39 → 1.0.0) landed on a single day (09-24, 24 commits); routing 1.2.0–1.2.2 landed on a single day (09-25, 14 commits).
    - 931 tests exist; 13 service indexes still run a 1.2.0 runtime.

---

## 1. Numbers

| Item | Value |
|---|---|
| Core Python | **57 files, 18,058 LOC** under `core/`, plus 3 Markdown templates (264 lines) and `core/lib/sqlite_schema.sql` (35) |
| Largest modules | `eos.py` 2,896 · `notes.py` 1,766 · `index.py` 1,358 · `inspector.py` 973 · `knowledge/builder.py` 853 · `work.py` 682 · `bench.py` 676 · `executions.py` 633 · `plugins/java/structure.py` 523 · `memory_audit.py` 496 · `brief.py` 480 · `testgen.py` 473 · `mcp_server.py` 403 · `scanner.py` 337 · `telemetry.py` 328 |
| Mid/small modules | `ai/writer.py` 259 · `generators/markdown/brain.py` 250 · `knowledge/semantic.py` 230 · `ai/templates/session_stop.py` 211 · `routing/classify.py` 210 · `plugins/java/plugin.py` 207 · `generators/ai/summary.py` 191 · `extensions.py` 186 · `routing/policy.py` 179 · `lib/cache_store.py` 169 · `verification.py` 167 · `routing/score.py` 164 · `plugins/javascript/plugin.py` 160 · `routing/trace.py` 153 · `retrieval.py` 146 · `plugins/java/lexer.py` 146 · `ai/templates/session_start.py` 146 · `routing/registry.py` 141 · `preflight.py` 140 · `routing/__init__.py` 138 · `ai/templates/prompt_submit.py` 131 · `routing/types.py` 124 · `knowledge/evidence.py` 124 · `ai/templates/pretooluse_task.py` 118 · `routing/usage.py` 115 · `lib/updater.py` 110 · `routing/config.py` 103 · `plugins/python/plugin.py` 95 · `knowledge/model.py` 93 · `lib/config_io.py` 82 · `generators/json/evidence.py` 77 · `knowledge/classifier.py` 75 · `links.py` 73 · `routing/defaults.py` 60 · `plugins/registry.py` 56 · `routing/adapters.py` 54 · `lib/paths.py` 49 · `plugins/base.py` 31 · `generators/json/graph_index.py` 17 |
| Routing package | 11 files, 1,441 LOC |
| Extensions (repo root, not deployed) | `extensions/journeys.py` 743 · `extensions/procedures.py` 167 |
| bin | `eos` 17 · `eos-event` 72 · `install.sh` 154 · `start.sh` 114 · `stop.sh` 71 |
| UI | Python 3,052 LOC (`workspace.py` 724, `server.py` 434, `graphify_queue.py` 419, `graphify.py` 307, `watcher.py` 287, `models.py` 245, `reconcile.py` 243, `cli.py` 205, `db.py` 188); frontend TS/TSX 1,700 |
| CLI | **33 top-level commands** (`core/eos.py:2795-2829`). 6 are groups with 32 subcommands: note 8, work 10, run 7, procedure 4, ai 1, ui 2. 62 `cmd_*` handler functions |
| Tests | **70 files** (69 test modules + `conftest.py`), **15,490 LOC**, **931 collected tests**. Routing: 189. Largest: `test_routing_policy` 70, `test_note_amend` 57, `test_knowledge` 37, `test_index` 34, `test_api` 32 |
| ADRs / plans | **25 ADRs** (001–025, 1,582 lines) · 2 plans (`operational-memory.md` 509 lines, `model-routing.md` 823) · `phases.md` 67 · README 866 · ARCHITECTURE 196 |
| SQLite (per project) | **21 core tables + `search`** (an FTS5 virtual table, which adds 5 shadow tables) and 22 explicit indexes. Extensions add 4 tables (journeys) and 3 (procedures): nexus `eos.db` shows 34 `sqlite_master` tables. `SCHEMA_VERSION = 4` |
| SQLite (central, UI) | 2 tables (`eos_instances`, `eos_scan_roots`) + 2 indexes, `~/.eos-ui/eos.db` |
| Provenance vocabulary | 11 fact predicates, 6 detectors, 11 coverage rows, 4 origins defined (1 used), 3 confidence constants (2 used) |
| MCP tools | 12 (`tools/list` measured at 4,892 chars, about 1.2k tokens at chars/4) |
| Generated hooks | 3 always (SessionStart, UserPromptSubmit, Stop) + 1 optional (PreToolUse, matcher `Agent\|Task`) |
| Upstream history | 83 commits, 2026-09-12 → 2026-09-25 (11 · 28 · 1 · 5 · 24 · 14 per active day) |

---

## 2. The 27 dimensions

### 2.1 Code scanning — IMPLEMENTED

**Evidence**

- `core/scanner.py`: `24-60` (`DEFAULT_IGNORE`), `74-110` (config and `.gitignore`), `112-146` (`ignore_reason`, `_matches_gitignore`), `156-193` (`_walk`), `195-212` (`scan`), `214-242` (`_carry_cached_parents`), `244-278` (`scan_with_links`), `280-338` (`_process_file`).
- `core/lib/cache_store.py`: `27` (`_CACHE_FORMAT = 5`), `67-74` (`is_changed`), `97-119` (`remove_missing`).
- `core/plugins/registry.py:12-56`.
- `core/eos.py:208-288` (`cmd_scan`).
- `core/knowledge/semantic.py:175-217` (`ScanReport`).

**Mechanism**

- **Walk.** `os.walk(followlinks=False)` down to `max_depth` (default 15, `[scan] max_depth`).
- **Directory pruning:**
  - by path segment against `DEFAULT_IGNORE` (22 names, including `.git`, `.claude`, `node_modules`, `build`, `target`, `bin`, `.worktrees`, `graphify-out`, `logs`);
  - plus `[scan] ignore` entries, minus `[scan] unignore` entries;
  - plus every non-comment line of the root `.gitignore`, matched with `fnmatch` against the full relative path, the basename and every segment.
  - `.eos` is never entered.
- **Plugin choice and change detection.**
  - Each file goes to the first detected plugin that claims its extension.
  - The cache entry's `mtime` and `size` are compared first; if both match, the sha256 of the bytes is compared.
  - A changed file is re-parsed and its `FileSemantic` serialized into `.eos/data/cache/file_cache.json`: a single JSON document, `indent=2`, rewritten whole on every scan. It is stamped `parsed_at`.
  - An unchanged file's cached `FileSemantic` is reused.
- **Linked parents.** `--with-parents` walks every `[links.<label>] role = "parent"` root and stores its files under `@parent:<label>/<path>` keys in the same cache. A plain scan re-adds those cached parent entries so the parent's facts are not silently dropped.
- **After parsing**, the whole `KnowledgeGraph` and every artifact are regenerated, and `index.build()` runs.
- **Scan report.** `ScanReport` counts files skipped per ignore rule (a pruned directory is counted by the number of files under it), unsupported extensions, and unresolved imports by specifier kind. These are printed and persisted to `evidence.jsonl` as `exclusion` rows, and from there into the `scan_exclusion` table.

**Limits**

- Incremental scanning saves *parsing*, not I/O. `abs_path.read_bytes()` is evaluated as an argument on every file on every scan (`core/scanner.py:311`), and the file is read again when it has changed. Plugin detection also does a second bounded walk of the whole tree (`core/plugins/registry.py:12-30,44-48`).
- `.gitignore` support is approximate:
  - root file only; nested `.gitignore` files are ignored;
  - `!` negations become patterns that never match;
  - no anchoring and no `**` semantics.
- `--full` re-parses but never prunes stale cache entries (acknowledged at `core/scanner.py:270-273`).
- The graph, brain, evidence and `eos.db` are always rebuilt in full. Only the per-file parse is incremental.
- **Nested projects:** only their `.eos` directory is skipped, so a nested project's sources are scanned as the parent's (no monorepo or nested-instance support; Phase 4 "planned").
- Symlinks are not followed.
- The cache format is versioned (`_CACHE_FORMAT`). A mismatch drops the whole cache, which costs one full parse.

### 2.2 Parsing per language — IMPLEMENTED (strongly uneven)

**Evidence**

- `core/plugins/python/plugin.py:14-95`
- `core/plugins/java/lexer.py:62-146`
- `core/plugins/java/structure.py:72-523`
- `core/plugins/java/plugin.py:60-207`
- `core/plugins/javascript/plugin.py:20-160`
- Import resolution: `core/knowledge/builder.py:121-212,653-853`
- Type edges: `core/knowledge/builder.py:325-419`

**Java: structural, by position (ADR-015)**

- **Lexer.** Replaces string, char and text-block literals and both comment forms with same-length filler, so offsets and newlines are preserved. It keeps a `literals{offset: value}` table.
- **Scanner.** Makes one pass over the masked text with a brace-depth counter and a stack of open type and method scopes.
- **What it extracts:**
  - `package`, and imports (simple name → FQN);
  - type declarations (`class/interface/enum/record`) with modifiers and pending annotations;
  - `extends` and `implements` references, with generic arguments stripped;
  - methods: recognised by `(...)` directly before `{` or `;`, so **no modifier is required**. Each carries `signature`, `returns`, `modifiers`, `annotations`, `parent` and `end_line`. Interface/abstract signatures (ending in `;`) are kept;
  - fields at type scope: simple type name, owner, modifiers and annotations, with annotations stripped first;
  - annotations with argument values read from the literal table (the unnamed argument under `""`);
  - `new X` type references;
  - calls, as `receiver.name(`. The receiver is resolved to a declared field type or a capitalized static receiver; a call with no receiver is attributed to the file's own type.
  - behaviour codes: SCREAMING_SNAKE literals, and the throw sites that carry one.
- **Role**, from type-level Spring annotations: `@Service`→service, `@RestController/@Controller`→entry-point, `@Repository/@Component`→component, `@Configuration`→config.
- **Endpoints:** class-level `@RequestMapping` base joined with `@Get/Post/Put/Delete/PatchMapping` paths (`value | path | unnamed`).
- **`doc`:** the first Javadoc plus a "REST endpoints" list.
- **`parent_ref`:** `<product.version>` read from `pom.xml`.

**Java: not extracted**

- local variable and parameter types (the known 1-in-345 gap in ADR-015);
- resolution of wildcard imports (collected into `wildcards`, never used) and of static imports;
- generics semantics, lambdas and method references;
- receivers of chained calls;
- **dispatch from interface to implementation**;
- annotation values that are constants (`@RequestMapping(Paths.X)` yields no path);
- array-valued mappings (`{"/a","/b"}` keeps only the last literal per key);
- enum constants as symbols, and record components as fields;
- **behaviour codes referenced through a constant or enum** (only a literal between `throw` and `;` counts);
- Kotlin and Groovy.

**Python: `ast`, top level only**

- **Extracts:**
  - `Import` and `ImportFrom` (with `level`);
  - top-level functions, async functions and classes as exports and symbols, with docstrings and line numbers;
  - one level of class methods (`Class.method`, `kind=method`).
- **Not extracted:**
  - calls, decorators (so a Python file never gets a `role`), and type annotations;
  - base classes (no `extends` edges);
  - module-level variables and constants, nested definitions, `__all__` and privacy.
- A `SyntaxError` returns an empty `FileSemantic` silently. Entry points come only from filename heuristics.

**JS/TS: regex on unmasked text**

- **Imports:** `import … from '…'` (default, named, namespace, type and mixed forms); `export … from`; and `const x = require('…')`.
- **Symbols:** `function`, `export const/let/var`, `class`, `export interface`, `export type`, `enum`, `namespace`.
- **Builder resolution:**
  - relative paths are resolved as POSIX paths with extension probing (`.ts .tsx .js .jsx .mjs .cjs .d.ts`, index files, ESM `.js`→`.ts`);
  - tsconfig/jsconfig `paths` aliases are supported in the `prefix/*` form only, using the first target;
  - a bare specifier is treated as a package and produces no edge.
- **Not extracted:**
  - dynamic `import()`, `import x = require()`, and `require` outside a const assignment;
  - class members (`_RE_METHOD` is defined at `:58-60` and never used);
  - JSX components, calls and type references;
  - comments and strings are **not masked**, so a commented-out import still produces an edge.
- Entry points come from filenames only (`index.ts`/`index.js`, or "main" in the name).

**Everything else** (Go, Rust, C, SQL, Kotlin, HTML, …) is not parsed; it is counted in `unsupported_extensions`. Manifests are read only for the tech stack (§2.4).

### 2.3 Semantic model — IMPLEMENTED

**Evidence:** `core/knowledge/semantic.py:10-230`.

**Exact fields**

- `Import(module, name=None, is_relative=False, alias=None, line=0, level=0)`
- `Export(name, kind, line=0, doc=None)`
- `Symbol(name, kind, line=0, doc=None, parent=None, signature=None, returns=None, modifiers=[], annotations=[], end_line=0)`
- `Annotation(name, values: dict[str, str], line=0, target=None)`, where `values[""]` holds the unnamed argument
- `Field(name, type, owner=None, line=0, modifiers=[], annotations=[])`
- `TypeRef(name, relation ∈ {extends, implements, field, new, annotation}, line=0, owner=None)`
- `Thrown(code, exception=None, owner=None, method=None, line=0)`
- `Call(method, receiver=None, receiver_type=None, from_type=None, from_method=None, line=0)`
- `FileSemantic(path, language, imports, exports, symbols, doc, package, annotations, fields, type_refs, calls, thrown, codes, parsed_at, role)`: 15 fields
- `ScanReport(files_parsed, skipped_by_ignore{rule:n}, unsupported_extensions{ext:n}, unresolved_imports{kind:n}, coverage{(detector, predicate): Coverage})`
- `ProjectSemantic(files, detected_languages, report)`

**Limits**

- Only Java fills `package`, `annotations`, `fields`, `type_refs`, `calls`, `thrown`, `codes` and `role`. An empty list means "this detector did not look", and that is recorded by coverage.
- The model is serialized as JSON inside the cache (`core/lib/cache_store.py:121-170`). A deserialization error returns `None`, which forces a re-parse.

### 2.4 Knowledge model — PARTIALLY IMPLEMENTED

**Evidence**

- `core/knowledge/model.py:10-93`
- `core/knowledge/builder.py:55-217` (build), `:223-316` (facts), `:325-419` (type edges), `:443-486` (folder hierarchy), `:488-651` (tech stack)
- `core/knowledge/classifier.py:23-67`

**Exact fields**

- `Dependency(source_id, target_id, kind, weight: int = 1, metadata: dict)`
- `KnowledgeNode(id, type, label, path=None, language=None, tags=[], metadata={}, doc=None)`
- `KnowledgeGraph(nodes, edges, languages, tech_stack, entry_points, facts)`. `facts` is deliberately **not** part of `to_dict()`.

**How it is derived**

- **Nodes.** One node per file, with id `file:<path>`. `metadata` holds `exports` and `top_symbols[:8]`; `doc` comes from the plugin; `label` is the file stem. The node type comes from `Classifier`:
  1. a test path wins, then the plugin's role;
  2. otherwise path heuristics: `config`, `service`, `util`, then `entry-point` (`"main"` in the filename or `app.py`/`index.ts`/…), else `component`.
- **Folders.** Folder nodes (`folder:<path>`) and `folder-hierarchy` edges are added for every ancestor directory.
- **Edges** produced:
  - `import`: resolved per language, including Java FQN fallback resolution;
  - `extends`, `implements`, `field`, `new`: from declaration-site type refs, resolved by explicit import first, then the same package (Java);
  - `calls`: only when the receiver type resolves; deduplicated per target and method;
  - `folder-hierarchy`.
- **`weight` is always 1.**
- **Tech stack.** Language presence, plus the direct dependencies declared in `package.json`, `pyproject.toml`, `requirements.txt`, `pom.xml` and gradle files (root and its direct children), matched against a whitelist of 19 framework tokens.

**SSOT claim (ADR-005)**

- **True for scan artifacts.** `cmd_scan` builds one `KnowledgeGraph`, and BrainGenerator, GraphIndexGenerator, AISummaryGenerator and EvidenceGenerator all derive from it (`core/eos.py:228-238`).
- **Not true across the system:**
  - The SQLite index is built from the *serialized* `graph.json` and `evidence.jsonl` plus the brain Markdown, not from the model.
  - `find_symbol` and `eos parent` read `file_cache.json`, the semantic layer (`core/inspector.py:146-223`).
  - `testgen` (`core/testgen.py:37-44,258-366`), the journeys extension (`_bean_index`, `extensions/journeys.py:547-580`) and the parent-source reader re-read raw source with regexes.
  - Notes and ledgers are separate file-level sources of truth.

**Designed but absent**

- the "Linker (backlinks, cross-references)" stage (`ARCHITECTURE.md:87`, ADR-005 line 18);
- Component, Service and EntryPoint as entity types (they are only `type` strings);
- the edge kinds `monorepo-parent`, `shared-dep` and `manual-link` (comment at `model.py:15`);
- `symbol_index` (`builder.py:68,91-95`), which is built but dead.

**Limits**

- The graph is file-granular: there are no symbol-level nodes or method-to-method edges.
- `KnowledgeGraph.get_node` is a linear scan, called in loops by the generators.
- Hubs and leaves in `Architecture.md` and `AI_SUMMARY.md` count `import` edges only, not the richer Java edges.

### 2.5 Code facts / provenance — IMPLEMENTED (narrow vocabulary)

**Evidence**

- `core/knowledge/evidence.py:35-124`
- `core/knowledge/builder.py:184-194` (import-edge), `:228-266` (annotation, bean-name), `:268-316` (codes), `:325-390` (type and call edges)
- `core/generators/json/evidence.py:27-77`
- `core/index.py:270-309` (tables), `:1138-1224` (`_load_evidence`)
- `core/inspector.py:536-676` (`why`, per-file verdict), `:703-842` (`rules`, coverage ladder)
- ADR-014, ADR-016, ADR-017

**Mechanism**

- **Fact row:** `Fact(subject_kind ∈ {node, edge}, subject, predicate, object, origin, confidence, detector, source_ref="path:line", observed_at=parsed_at)`.
  - A node is addressed by its id; an edge by `src|dst|kind|imported`, never by `nid`, because `nid` is renumbered on every build.
- **Coverage row:** `Coverage(detector, predicate, files_eligible, files_with_hits, hits)`. It records what each detector *examined*: no row means "never looked"; `hits=0` means "looked, found nothing".
- **Sidecar.** Facts travel in `evidence.jsonl` (a header with counts, then fact, coverage and exclusion lines), never in `graph.json`.
- **Loading.** `_load_evidence` streams the file and **refuses** it when:
  - the header counts differ from the file's actual counts; or
  - the header's fact count differs from `last_scan.json`.
- **Detectors and predicates** (the 11 coverage rows):

  | Detector | Predicates |
  |---|---|
  | `folders@1` | `folder-hierarchy` (coverage only, no facts) |
  | `imports@1` | `import-edge` |
  | `java.typeref@1` | `extends-edge`, `implements-edge`, `field-edge`, `new-edge` |
  | `java.calls@1` | `calls-edge` |
  | `codes@1` | `throws-code`, `throws-code-at` (`CODE\|Owner.method\|Exception`), `names-code` |
  | `annotations@1` | `annotation` (`@Name\|Target`, type-level only), `bean-name` (unnamed value of `@Component/@Service/@Repository/@Controller/@RestController/@Qualifier/@Named/@Bean`) |

- **Behaviour codes (ADR-016).** A code is a literal matching `^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+$`. The *first* such literal after `throw` and before the next `;` is the thrown code. Every other code-shaped literal in a Java file becomes `names-code`.
- **`eos rules` grades each code:**
  - `verified`: the latest recorded run passed, **and** a test reaches the class, **and** a test names the code;
  - `asserted`: reached and named;
  - `reachable`: reached, not named;
  - `named`: named, not reached;
  - `none`.
  - "Reached" means a test-path file has a `calls/new/field/import` edge into the throwing file.
- **`eos why <file>`** prints the file's facts (outbound, plus inbound edge facts), and for every coverage row a per-file verdict `applies_here ∈ {yes, no, structural, unknown}`, derived from the languages each detector has produced facts on.

**Limits**

- **Only `origin=extracted` is ever produced.** Confidence is `1.0`, except `calls-edge` at `0.8`. `documented`, `inferred`, `verified` and `POSSIBLE` are defined but unused; ADR-024 admits this. Nexus has 675 facts, all extracted.
- Codes are Java-only and literal-only. `reachable` is class-level, not branch-level; `rules` says so on every run.
- Folder edges deliberately carry no facts.
- `why` uses leading-wildcard `LIKE` on `fact.subject` (a table scan). That is fine at about 50k rows.
- Provenance can only be queried through SQLite; it needs `eos index` or `eos scan`.

### 2.6 Business workflow knowledge — PARTIALLY IMPLEMENTED (index extension, not core)

**Evidence**

- `extensions/journeys.py:93-155` (schema), `:167-288` (5 questions), `:359-400` (sources, load), `:403-437` (implementation resolution), `:547-580` (bean index), `:628-657` (owner resolution), `:660-720` (step insertion)
- `extensions/procedures.py:43-167`
- `core/inspector.py:845-935` (trace, runtime-wired count)
- ADR-013, ADR-017

**Mechanism**

- **Tables:** `journey_doc`, `journey_snapshot`, `journey_step`, `flow_step`.
- **Documents.** `journey_doc` holds Markdown files from `<repo>/<docs>/*.md` whose front matter `services` lists this project: fields `journey`, `bis`, `flows`, `verified_on/at/by`, and the body.
- **Snapshots.** `journey_snapshot` and `journey_step` come from host-exported `_snapshots/<env>/{chains.json, flow-steps.json, _meta.json}`.
- **Resolving a step to a service.** A step names a *bean*. A bean index is built by walking `<workspace>/<implementations>/*` and `<workspace>/<upstream>/*` for files ending in `class_suffix` (default `Command.java`, excluding `src/test`). Each file is keyed by its stem, the stem with a lowercase initial, and any explicit `@Component("x")`. `impl_cls` package segments are a second signal.
- **Resolution recorded per step:** `bean`, `impl_cls`, `shared`, `ambiguous`, `elsewhere`, `unresolved` or `no-bean`, with the `candidates` it could have been.
- **Flow-level rules:**
  - A flow where at least as many steps name classes that no checked-out tree declares as are credited to a checked-out project is re-marked `elsewhere`.
  - A step that resolves only to a shared library is credited to the flow's majority runner.
- **Implementation path.** `impl_path` joins the step's bean to core `bean-name` facts; the first one wins, and an overlay sorts before its parent.
- **Questions:** `flow-steps`, `flow-rules` (codes the flow's steps can raise, graded like `rules`), `flow-untested`, `flows`, `unresolved-steps`.
- **Procedures extension.** `extensions/procedures.py` indexes a host-exported JSON catalogue into `catalogue_procedure`, `procedure_step` and `procedure_state`. The task brief reads the catalogue's last state (`core/brief.py:446-480`).
- **Core's only workflow signal** is `trace`'s "N of M named components have no caller" (the runtime-wired count).

**Real usage**

- Of the 20 service indexes, **only svc-cpq-ordercapture has journey steps**: 465 steps, 436 owned, 423 resolved to a class.
- 11 services have 1–3 journey docs. Nexus itself has 0 steps and 0 docs.

**Limits**

- It depends entirely on host exports: a DB snapshot of chain configuration, and journey docs with front matter.
- The bean index is a regex walk of raw source outside the model.
- `class_suffix` and the directory layout are conventions of a specific host. They are configurable, but the design is shaped around one company.
- ADR-017 explicitly refuses to introduce a core "workflow" entity. There is no generic workflow model and no cross-service flow graph in core.

### 2.7 Retrieval / search — IMPLEMENTED (lexical); embeddings MISSING by decision

**Evidence**

- `core/notes.py:1186-1336` (tokenizer, IDF, relevance, `search_notes`)
- `core/index.py:36-42` (`SCORE_FLOOR`), `:312-320` (FTS schema), `:610-671` (`search`)
- `core/brief.py:210-259` (procedure picker), `:341-352` (related notes)
- `core/executions.py:431-485` (run ranking)
- `core/inspector.py:146-160` (`find_symbols`), `:163-223` (parent lookup)
- `core/retrieval.py:60-146` (golden-set evaluation)
- `core/memory_audit.py:60-62,255-261` (C-10)
- ADR-001, ADR-010; operational-memory plan §2 and §7

**There are six retrieval paths, all lexical.**

**(a) Note search** (`notes.search_notes`). Used by `note search`, context injection, the brief, MCP `search_notes` and `note eval`.

- **Tokenizer `_words`:**
  - words are Unicode letters and digits (`[^\W_]+`), casefolded, kept if longer than 2 characters;
  - 2-letter tokens are kept only if all-caps (an acronym) or non-ASCII;
  - issue keys `NAME-123` are one word, and their bare prefix is removed.
- **IDF** over the whole corpus: `w(word) = log(N / df)`, unsmoothed. A word found in every note, or in no note, weighs 0. If every weight is 0, equal weights are used instead.
- **Score:**
  ```
  relevance = min(1, 0.5·cov(title) + 0.3·cov(tags) + 0.2·cov(body))
  cov(S)    = Σ w(matched query words in S) / Σ w(all query words)
  ```
  This is coverage of the **query**, not Jaccard.
- **Cut and order:**
  - absolute threshold `0.15`;
  - relative floor `SCORE_FLOOR = 0.4` × the best score;
  - sort by `(-score, filename)`.
- **Cost:** every call loads and parses every note file from disk (0.10 s for 236 notes on nexus).
- **Measured history:** recall@1 went from 0.70 to 0.95 with IDF plus title weighting, on a 20-question golden set (recorded in the EOS note shown during this audit and in the `notes.py` comments).

**(b) Index search** (`eos query --search`, MCP `search_index`).

- **Table:** FTS5 `search(source UNINDEXED, ref UNINDEXED, title, body, terms)` with `tokenize='unicode61'`. There is no porter stemmer. The `terms` column holds identifiers split from camelCase.
- **Query:** the words (whitespace-split) are each quoted as an FTS5 phrase and **ORed**.
- **Ranking:** `ORDER BY rank`, i.e. default BM25 with equal column weights; titles are not up-weighted. Rows are kept when `rank <= best_rank × 0.5` (rank is negative BM25).
- **Snippet** from `body`.
- **Without FTS5**, a LIKE fallback counts how many query words hit title or body, keeps rows with `hits >= best × 0.5`, and uses a 160-character prefix as the snippet.
- **Sources indexed:** `note`, `work`, `execution`, `verification`, `brain`, plus extension sources (`journey`, `procedure`).

**(c) Procedure picker** (`brief.best_procedure`).

- `relevance(body=False)`: title and tags only.
- A single matched word counts only when the prompt has at most 2 "wording" words (issue keys and bare numbers are not counted).
- Otherwise the procedure is accepted if `score >= 0.15`, **or** if at least 2 words matched and `Σw(matched) >= log(min(7, √N))`.

**(d) Related notes in the task brief.** `search_notes(limit=9)`, then filtered to exclude procedures and bulk indexes (`source ∈ {api-inventory, repo-topology}`), and the note **must share a word with the task in its title or tags**. At most 3 are shown.

**(e) Run ranking** (`executions.ranked`).

- **Candidates:** the procedure's runs; else runs whose title, target or procedure shares a word with the task, after removing "ubiquitous" words (found in more than half the records); else all runs.
- **Order:** target-matching runs first, then most recent first (ledger order breaks ties).
- **`last_lesson`** surfaces an older failed run with a lesson.

**(f) Symbol lookup.**

- `find_symbols`: case-insensitive substring over every cached symbol name, capped at 100, **unranked**.
- `get_parent_implementation`: sorts non-test paths, then exact name, then path, before truncating, and inlines 30 lines of live source.

**Embeddings.** None. `core/notes.py:1186-1189` states "Deliberately not embeddings". C-10 is `DECIDED`.

**Limits**

- There is no stemming, lemmatization or synonym handling, and it is acknowledged: "fails" does not match "failure".
- There is no cross-language matching, except the procedure-picker heuristics (1.1.x).
- FTS does not up-weight titles.
- Every note search is O(N) disk reads and tokenization.
- The observed false positive: "ok, continue" matched a note title containing "Continue" (§0.3 item 9).
- There is no cross-project index. `work list --across` reads sibling ledgers, and the UI's workspace view reads Graphify artifacts.
- No retrieval result carries a score to the caller, except `rank` inside SQL.

### 2.8 Context assembly — IMPLEMENTED

**Evidence**

- `core/inspector.py:15-32` (brain file list, dropped sections, `GOING_DEEPER`), `:335-376` (brain orientation and deduplication), `:379-425` (target facts), `:428-444` (`_fit_sections`), `:447-533` (`build_context`, `compose`)
- `core/notes.py:1355-1432` (notes section)
- `core/work.py:600-648` (work section)
- `core/brief.py:29-61,96-178,283-443`
- `core/eos.py:617-643`

**`build_context(root, budget=12000 tokens, task, target)`.** Used by `eos context`, `eos compose` and MCP `get_context`/`compose`.

1. **Ceiling:** `max_chars = max(1000, budget × 4)`, i.e. **4 chars/token**.
2. **Sections, in order:**
   1. header (project, path, engine version);
   2. **Work In Flight**, capped at **8%** of `max_chars`. Items go in whole; the remainder is named.
   3. `## Task` (the task text, verbatim);
   4. `## Target File`: up to 12,000 characters, with a truncation notice;
   5. `## What Is Known About The Target`: impact at depth 2 (dependents, with the tests counted, and the dependency count), plus up to 8 behaviour codes thrown in the file with their coverage grade;
   6. the **brain orientation** (see below).
3. **Brain orientation.** The 5 brain docs are concatenated, then:
   - sections headed `## Components`, `## Module Structure` or `## Folder Structure` are dropped;
   - repeated headings are **deduplicated**, keyed on the lowercased heading text with any parenthesised aside removed and level ignored; **the first document wins**.
4. **Notes** (`## Accumulated Knowledge`), capped at **15%**.
   - With a task, notes are ranked by `search_notes`.
   - Without one, the newest come first and bulk-index notes are sorted last.
   - Notes go in whole. Up to 25 titles of notes that did not fit are listed.
   - If the rendered block still exceeds 15% (one giant note), it is replaced by a pointer.
5. **Fitting.** Room for `GOING_DEEPER` and the notes block is reserved first. `_fit_sections` then keeps a **prefix**: the first section always, then sections in order until one does not fit, at which point it **stops**. Everything after that section is dropped, even smaller sections. An `_N section(s) omitted_` line is added.

**Task brief (`eos brief --task`).**

- **Budget:** `TASK_BUDGET = 1500` tokens at `CHARS_PER_TOKEN = 3.0`, a 4,500-character cap.
- **Sections, in priority order:**
  1. `PROCEDURE` (title, slug, counters, last verified, CONFIDENCE), `RULE` lines, steps (each clipped to 240 characters), prerequisites and success (each clipped to 100);
  2. `LAST RUNS` (up to 3, plus catalogue state, plus "earlier failure worth reading");
  3. `KNOWN FAILURES` (last 3, clipped to 140);
  4. `RELATED NOTES` (up to 3 titles);
  5. the "Record this run" command;
  6. `ROUTE` (2 lines, only if `[model_routing]` is configured and enabled);
  7. the branch brief (unless `--task-only`).
- **Budget loop:** adding a line that would exceed the cap sets `trimmed`. After that only **exempt** lines are appended: those starting with `PROCEDURE  `, `  RULE  `, `ROUTE  ` or `  Apply: `. A trimmed notice ends the block.
- **Bounds on the exemption:** a `## Rules` section is capped at 600 characters when written or amended (`notes.RULES_MAX_CHARS`), and the ROUTE reason is clipped to 120.
- **`--task-only`** returns `""` when no section "found" anything. The ROUTE line counts as found only with `brief = "always"`.

**Branch brief (SessionStart).**

- `IN FLIGHT`: up to 5 items, each with a `last:` or `blocked on:` line.
- `KNOWN HERE`: up to 4 notes matched against a query built from the branch name and its ticket keys, with noise words removed (`feature`, `fix`, `main`, …).
- `RUNS OPEN`: up to 3.
- The ledger's sync sentence, but only when the ledger is not pushed or committed.
- **There is no character budget.** 3,055 characters measured on nexus.

**Limits**

- Token estimates are inconsistent across modules: context uses 4 chars/token, the brief 3.0, telemetry and bench 4.
- Context deduplication is heading-level only: nothing deduplicates between notes and brain, or between notes.
- The greedy prefix fit can drop the brain orientation because a large target file came before it.
- The task text is echoed into the context. It is not stored.

### 2.9 Semantic memory (notes) — IMPLEMENTED (no `supersedes`)

**Evidence**

- `core/notes.py`: `29-47` (notes dir), `89` (`KINDS`), `110-300` (credential guards), `230-283` (generated vs bulk), `305-366` (required sections), `369-435` (scope anchoring and hashing), `438-523` (skips), `526-638` (duplicate and placeholder guards), `641-743` (`add_note`), `777-1080` (`amend_note`), `1083-1183` (`Note`, `parse_note`, `load_notes`), `1435-1475` (`stale_notes`)
- `core/eos.py:1098-1399` (note CLI), `:330-422` (doctor)

**Mechanism**

- **Storage.** One Markdown file per note, `<YYYYMMDD>-<slug>.md`, in `notes_dir`. The default is `.eos/knowledge`; `[knowledge] dir` can point elsewhere.
- **Front matter.** A hand-rolled YAML subset, with JSON-quoted scalars when a value is unsafe:
  - `kind`, `title`, `created`, `updated` (written by amend), `source`, `tags[]`, `scope[]`, `scope_hashes[]` (sha256 per scope entry, or `null`), `session`;
  - for procedures: `procedure`, `runs_ok`, `runs_failed`, `last_verified`, `last_execution`;
  - for lessons: `execution`.
- **Guards on write.** A note is refused when:
  - a value is an unsubstituted placeholder (`<…>`, `...`, `…`);
  - the text contains a credential (PEM block, JWT, AWS key id, a password in a connection URI, an opaque `Authorization:` header value, or a `password/secret/token/…` assignment with a run of 12+ credential characters);
  - a scope entry escapes the project or linked-parent root, or names a sensitive file (`.env*`, `*.key`, `*.pem`, `*.p12`, `*.jks`, `*secret*`, `*credential*`);
  - it duplicates another note: same whitespace-normalised body digest, **or** same normalized-title token set (digits masked, words longer than 2 characters);
  - the file already exists for the same day and slug.
- **Staleness.** `stale_notes` re-hashes each scoped file and reports `removed` or `changed`. `eos doctor` warns about stale notes. `eos note audit` prints ready-to-run `amend` commands, split into hand-written and generated notes (generated = `source ∈ {api-inventory, journey-map, repo-topology}`).
- **`amend`.** The only way to clear a stale flag, and heavily guarded:
  - `--body` or `--reaffirm` (appends `[date] Still holds: …`), and/or `--scope`;
  - it refuses an unchanged body, placeholders, an empty scope, the same scope *set*, and a scope-only amend that would retire a still-existing changed entry or leave the note unmonitored;
  - it keeps procedure counters and a lesson's `execution`.
- **Skips.** `.skips.jsonl` (date, session, reason) records "nothing worth noting" for a host Stop gate. `was_skipped` is not called anywhere in core.

**Limits**

- There is no `supersedes` field. It appears only in the plan (`docs/plans/operational-memory.md:324`).
- There is no expiry, TTL or archive, and no link between notes other than a lesson's `execution` and `procedure`.
- The `note` table has no `procedure`, `execution` or counter columns, so lesson↔run and procedure↔run links are not SQL-joinable except through the ledger tables.
- Deduplication is exact-digest or title-token based only: paraphrases pass.
- The parser understands only the front matter this module writes.
- Hand edits that break the front matter are skipped with a `build_issue`.
- `GENERATOR_SOURCES` hard-codes three generator names from one host.

### 2.10 Procedural memory — IMPLEMENTED

**Evidence**

- `core/notes.py:1528-1767` (section parsing, counters, confidence)
- `core/executions.py:231-284` (finish moves counters), `:383-425` (audit)
- `core/eos.py:1933-2052`
- `core/brief.py:210-362`
- `extensions/procedures.py`
- ADR-023, ADR-024

**Mechanism**

- **A procedure is a note** with `kind: procedure` and a required `## Steps` section: a list, numbered (`1.` / `1)`) or bulleted (`-*+`).
- **Optional sections**, parsed case-insensitively at any heading level: `## Prerequisites`, `## Success`, `## When not to use this`, `## Rules` (≤ 600 characters), and `## Known failures`, to which the engine appends.
- **Tools.** A step names its tool with `(tool: X)`; `procedure_tools` extracts them.
- **Slug.** Given explicitly, or derived from the title.
- **Counters.** Only `executions.finish` moves them, through `notes.record_procedure_run`, and only on an **exact** slug match:

  | Outcome | Effect |
  |---|---|
  | `ok` | `runs_ok += 1`, `last_verified = finish time` |
  | `failed` | `runs_failed += 1`, and a `## Known failures` bullet `YYYY-MM-DD <run id>: <lesson first line>` |
  | `abandoned` | `last_execution` only |

  The edit is a targeted rewrite of the front-matter lines.
- **Confidence** is derived when read (`procedure_confidence`):

  | Word | When |
  |---|---|
  | `failing` | the latest finished run failed |
  | `unverified` | no run has finished ok |
  | `fresh` | last verified ≤ 30 days ago |
  | `aging` | last verified ≤ 90 days ago |
  | `stale` | older than that |

- **`procedure audit`** recomputes the counters from the ledger. It reports mismatch (exit 1), "latest failed", "never verified", "last verified more than 30 days ago", and "a scoped file changed".
- **Brief.** The best procedure is shown with its steps, rules and last runs.
- **Catalogues.** Host catalogues are indexed separately and **never** move note counters.

**Real usage (nexus):** 15 procedures. 4 have runs: `deliver-config-sql-script` 7 ok / 1 failed, `run-a-scenario` 7/2 (FAILING), `open-a-pr` 2/0, `commit-and-push` 1/0. 11 are `unverified`.

**Limits**

- Steps are prose. There is no parameterization, typing, preconditions as code, or step-level outcome tracking; "which step failed" exists only in catalogue state.
- No versioning of procedures other than git.
- Counters are a racy read-modify-write of a git-tracked file.
- The ledger passed to `procedure_confidence` is `note.path.parent/executions.jsonl`, so it depends on notes and ledger sharing a directory.

### 2.11 Episodic memory — IMPLEMENTED (capture depends on the host)

**Evidence**

- `core/executions.py`: `30-45` (constants), `47-87` (`Event`, `Record`), `97-164` (pointer), `183-284` (start, event, finish), `293-360` (fold, `by_session`), `454-493` (`ranked`, `last_lesson`), `501-532` (lesson notes), `540-633` (tools, diff)
- `bin/eos-event`
- `core/index.py:141-176,998-1032`
- ADR-022

**Mechanism**

- **Storage.** One append-only `executions.jsonl` in `notes_dir`, with three line types:
  - `start`: `id = x-<3-word-slug>-<4hex>`, `title`, `procedure`, `work_item`, `session`, `agent`, `started_at`, `target`, `branch`, `commit_start`;
  - `event`: `execution`, `ord` (null on disk; assigned by position in the fold), `at`, `kind`, `tool` (normalized), `target` (lowercased), `ref`, `exit_code`, `ms`, `body`, `session`;
  - `finish`: `id`, `at`, `outcome`, `lesson`, `commit_end`.
- **`Record` fields:** `id, title, procedure, work_item, session, agent, started_at, finished_at, outcome, target, branch, commit_start, commit_end, lesson, events[]`.
- **`load_path` folds** lines in order:
  - an event with no start creates the record;
  - an event without a session inherits the run's session;
  - the first `finish` wins;
  - bad lines are skipped.
- **Finish rules:**
  - idempotent for the same outcome;
  - a different outcome is refused;
  - `failed` without `--lesson`, and with no existing lesson note for the run, is refused **before** anything is written;
  - with a lesson, the lesson note is written first.
- **Queries and views:** `ranked()` (§2.7e), `tools()` (§2.13), `diff()`, `audit_procedures()`, `by_session()`.
- **Indexing.** `execution` and `execution_event` are rebuilt through the same fold. The search body is outcome, target, procedure, tools and lesson.

**Event kinds:** `ran`, `read`, `changed`, `called`, `verified`, `noted`, `decided`. **Outcomes:** `ok`, `failed`, `abandoned` (open = `null`).

**What is NOT recorded (by ADR-019/022):**

- payloads, command output and diffs;
- prompts or task text;
- tool arguments.

`body` is allowed as one free-text sentence; the Python path guards it for credentials and placeholders, the shell helper does not. Anything done outside the wrappers leaves no trace.

**Real usage (nexus):**

- 51 runs: 45 ok, 3 failed, 3 open. 21 name a procedure; all 51 carry a session.
- 1,929 events: `called` 1,806, `ran` 81, `decided` 39, `changed` 2, `verified` 1.
- Top tools: `bb` 761, `db` 303, `bssapi` 295.
- **Service projects have 0 executions**: the ledger lives at the host root.

**Limits**

- There are no durations unless the wrapper passes `--ms`, and timestamps have one-second resolution.
- `ord` is positional and so depends on file order; concurrent writers interleave.
- There is no retention or rotation: the file grows forever (592 KB on nexus).
- Run ids are a 3-word slug plus 16 bits of randomness, with no collision detection.
- An event only "belongs" to a run through a pointer file or environment variables.

### 2.12 Session history / session-id threading — PARTIALLY IMPLEMENTED

**Evidence**

- `core/telemetry.py:66,73-98`
- `core/eos.py:2055-2060` (`_FILL_SESSION`), `:2831-2865`
- `core/executions.py:112-143`
- `core/brief.py:181-199`
- `core/work.py:204-229`
- `core/notes.py:641-743`
- hook templates
- ADR-021, ADR-022

**Mechanism**

- **Order of resolution for a session id:**
  1. `--session`;
  2. the `EOS_SESSION` environment variable;
  3. variables declared in `[telemetry] session_env`;
  4. `CLAUDE_CODE_SESSION_ID`.
- **Filled-in writes.** For these commands a missing session is filled in automatically: `work add/claim/log/block/unblock/done/drop`, `note add/amend/skip`, `procedure new`, `route`.
- `run start/event/finish` resolve the session inside `executions.session_for`; `verify` reads it from the environment.
- **Stores that carry the id:** notes (`session`), work events, execution start and event lines, verification records, telemetry (truncated to 64 characters, with `session_from` recording which variable), routing trace and routing usage.
- **Hooks** pass `--session <payload.session_id>` to their child processes.

**What is missing**

- **There is no "session history" view.**
  - `eos brief --session` only marks items "(yours)".
  - `run list --session` and `work list --session` are separate filters.
  - `executions.by_session` exists but only the doctor probes it.
  - A cross-store view is possible only by writing SQL against `eos.db` (the `note.session`, `work_holder.session`, `work_event.session`, `execution.session`, `execution_event.session` and `verification.session` columns).
- The claim that the hook "exports `EOS_SESSION` so every later call is attributed" cannot work, because the variable is set only in the hook's child process.
- `bin/eos-event` reads only `EOS_SESSION` and `CLAUDE_CODE_SESSION_ID`, not `[telemetry] session_env`, so a harness with its own session variable cannot use the shell helper's pointer lookup.
- Per-session state files under `~/.local/state/eos/{current,prompted}/<session>` are never garbage-collected (`prompted` grows by one file per session).
- Nexus telemetry: 2,167 of 5,054 calls carry no session.

### 2.13 Tool / action history — IMPLEMENTED (capture is in host wrappers)

**Evidence**

- `bin/eos-event:1-72`
- `core/executions.py:42-44,121-164,206-228,537-599`
- `core/eos.py:1841-1852,2063-2106`
- `bin/install.sh` (installs `eos-event` beside the launcher)
- ADR-022

**Mechanism**

- **Two capture paths**, both appending one `event` line:
  - `eos-event` (POSIX `sh`, no interpreter; the ADR measures a 37 ms median). It escapes quotes and backslashes and drops control characters.
  - `eos run event` (Python, 104 ms). It applies the credential and placeholder refusals.
- **Resolving the run.** Either `EOS_EXECUTION` plus `EOS_EXECUTION_LEDGER`, or the pointer file `${EOS_STATE_DIR:-~/.local/state/eos}/current/<session>` holding `execution\tledger`. `run start` writes the pointer and `finish` removes it.
- **Failure mode.** Capture never fails the wrapped command: when there is no run, no session, or the ledger cannot be written, the helper exits 0.
- **Normalization.** Tool names are lowercased and stripped of path and `.sh/.py/.js/.ts/.exe/.cmd/.bat`; targets are lowercased.
- **`eos run tools`** reports `ToolUse(tool, count, failures(non-zero exits), runs, last_outcome, last_at, targets)`, most used first.
- **`eos run diff <id>`** reports paths from `changed` events, `commit_start..commit_end`, and the commits and files in that range from `git log`/`git diff --name-only`.
- **Host side.** In nexus, capture is wired into `automation/lib/eos-capture.sh`.

**Limits**

- Only actions routed through wrappers are seen. Raw commands, file edits made by the agent's editor tools, and reads are not captured unless a wrapper reports them.
- There are no argument shapes, only tool and target.
- The shell helper performs no credential guard.
- Tool names are not normalized against a registry.

### 2.14 Planning / task decomposition — MISSING (by design)

**Evidence of absence**

- `core/work.py:30-34`: "It is deliberately not a planner. Nothing here schedules, assigns, estimates or closes anything on its own."
- `work.Event` and `work.Item` have no parent/child, dependency, priority or estimate fields (`core/work.py:84-136`).
- The brief refuses improvisation: "Do not present improvised steps as this project's" (`core/brief.py:310-311`).
- `planning` exists only as a routing *classification* label (`core/routing/types.py:20-33`).
- Procedures are human-authored static lists; no code generates or decomposes steps.
- No module creates sub-tasks, orders work or proposes next actions. The "Next:" line in `doctor --memory` is the plan tracker's own first open row (`core/memory_audit.py:490-495`).

### 2.15 Execution — MISSING (by design; records only)

**Evidence**

- ADR-018: "An adapter executes; EOS records the evidence."
- ADR-025: "EOS advises; the harness executes."
- `core/verification.py:1-24`.
- **Every subprocess call site:**
  - git: `index.py:1290,1311`, `work.py:176,568`, `verification.py:148`, `executions.py:623-626`;
  - `grep` in bench (`bench.py:401`);
  - venv and pip for the UI (`preflight.py:122,132`);
  - `bin/start.sh` (`eos.py:2271`);
  - hook templates → `eos`;
  - UI → `eos init/scan` and a configured Graphify refresh command (`ui/server.py:133-181`, `ui/watcher.py:214`, `ui/app/graphify_queue.py:112`).

**Mechanism**

- No provider SDK, no model call (C-10 greps `openai` among others).
- No test runner: `eos verify` only records.
- `draft-test` writes a skeleton to `.eos/data/candidates/`, never into the source tree, and it is not compiled.

### 2.16 Agent routing — MISSING

**Evidence**

- `core/ai/templates/agent.md` (one profile, `eos-researcher`, with no `model` or `tools` front matter)
- `core/ai/writer.py:216-259`
- `core/ai/templates/pretooluse_task.py:32-109`

**Mechanism**

- EOS generates a single read-only research agent profile.
- `--agent claude|devin` is a label on work, runs and the brief. It selects the brief's `Apply:` line format and nothing else.
- The optional PreToolUse hook rewrites **only the `model`** of an `Agent`/`Task` call that has no model and whose `subagent_type` is empty or `general-purpose`. Named agents are untouched.
- There is no agent registry, no dispatch between agents, no orchestration and no hand-off protocol.

**Also noted:** `agent.md` has no `mcp-only` fence, so on the default `cli` surface the generated agent is told to call MCP tools (`search_notes`, `add_note`, `get_context`, `compose`) that are not registered.

### 2.17 Model / effort selection — IMPLEMENTED (deterministic, advisory, no learning)

**Evidence**

- `core/routing/__init__.py:25-138`
- `types.py:18-124`
- `defaults.py:27-60`
- `classify.py:41-210`
- `score.py:34-164`
- `policy.py:31-179`
- `registry.py:29-141`
- `config.py:20-103`
- `trace.py:26-153`
- `usage.py:27-115`
- `adapters.py:19-54`
- `core/brief.py:365-406`
- `core/eos.py:1665-1737,1816-1838`
- `core/mcp_server.py:319-336`
- `core/ai/templates/pretooluse_task.py`
- ADR-025 and the model-routing plan

**Pipeline** (`route()`): config → reuse the open run's decision → classify → score → policy → `adjust_for_history` (no-op) → record.

**Classification** (`classify.py`)

- **12 types:** `trivial_edit, simple_implementation, normal_implementation, debugging, refactoring, code_review, test_generation, investigation, architecture, planning, complex_reasoning, repository_wide_change`.
- **Keyword tables:** 11 non-empty tables of `(token, weight)`, 146 tokens in total; `simple_implementation`'s table is empty.
  - A single token is matched as `\bTOKEN(s|es|ed|ing|d)?\b`.
  - A phrase is matched literally with boundaries, and its last word may inflect.
  - Each distinct token counts once.
- **Config extension:** `[model_routing.keywords]` adds words with weight 1.0.
- **Score:** the sum of matched weights per type.
  - The best type wins; ties go to the earlier type in `TASK_TYPES`.
  - Confidence `= min(0.95, best / (best + second + 1))`.
  - No match at all gives `normal_implementation` at 0.30.
- **Ordered override rules:**
  1. A repository-wide phrase → `repository_wide_change`.
  2. Text starting with a question opener (`why/where/what causes/investigate/find out/how does/what is causing`) → `investigation`. If debugging or investigation won and failure words are present, a change verb (`fix/resolve/repair`) makes it `debugging`; without one it is `investigation`.
  3. `trivial_edit` stands only if no other type scored above 1.0.
  4. For the implementation types: exactly one implementation verb, no "and <verb>", and at most 12 words → `simple_implementation`; otherwise `normal_implementation`.
- A rule override reports `max(0.80, scored)` confidence.

**Complexity** (`score.py`)

- **7 factors** (each 0..1) and their weights:

  | Factor | Weight | How it is computed |
  |---|---|---|
  | `scope` | 0.15 | by type: trivial 0.10 … repository-wide 1.00 |
  | `file_count` | 0.15 | `--file` count, else "N files/modules/classes" in the text, else 1; divided by 8 |
  | `dependency_count` | 0.10 | Σ `inspector.impact(file).dependents` over the `--file` list; divided by 20; 0 without an index |
  | `architectural_impact` | 0.20 | by type (architecture and repository-wide 1.0, refactoring 0.6); +0.3 for schema, migration, public api, contract, interface or boundary |
  | `reasoning_required` | 0.20 | by type (complex_reasoning 1.0 … trivial 0.1, default 0.3) |
  | `failure_risk` | 0.15 | 0.4 for production, prod, payment, billing, auth…, security, data loss, migration or irreversible; +0.3 if debugging |
  | `uncertainty` | 0.05 | `1 − class confidence`, +0.2 for hedge words |

- **Thresholds** (recalibrated from the plan's original .30/.55/.80): score < 0.15 → LOW, < 0.35 → MEDIUM, < 0.55 → HIGH, otherwise CRITICAL.
- **Floors and caps:** architecture, repository_wide_change and complex_reasoning are at least HIGH; `trivial_edit` stays LOW unless `failure_risk ≥ 0.5`. Uncertainty has no gate of its own.

**Policy** (`policy.py`)

- **Level requirements:**

  | Level | Min reasoning | Min coding | Effort band (preferred first) |
  |---|---|---|---|
  | LOW | 1 | 2 | `(low, medium)` |
  | MEDIUM | 3 | 3 | `(medium, high)` |
  | HIGH | 4 | 4 | `(high, xhigh)` |
  | CRITICAL | 5 | 4 | `(xhigh, max)` |

- **Type adjustments:** `code_review` and `test_generation` add 1 to min coding; `investigation`, `planning` and `complex_reasoning` add 1 to min reasoning (capped at 5).
- **Model choice.** `registry.candidates(min_reasoning, min_coding[, effort])` sorted by `(cost, -reasoning, id)`, taking the first.
  - If none qualifies, the type adjustment is relaxed.
  - If an explicit effort is not accepted by any candidate, the level is kept and the effort is clamped.
  - Otherwise the strongest available model is used, and the reason says so.
- **Effort choice.** `clamp(spec, requested)` returns `requested` if the model accepts it, else the highest accepted effort below it, else the lowest accepted effort. Auto effort is the first effort in the band that the model supports.
- **Confidence** `= round(0.5·class_conf + 0.5·min(1, distance_to_nearest_threshold/0.10), 2)`.
- **Overrides**, in precedence order: flag > `EOS_ROUTE_MODEL`/`EOS_ROUTE_EFFORT` > `[model_routing] default_model/default_effort` > auto. An explicit `auto` stops the search. An unknown or unavailable model raises `OverrideError`, which is exit 2.
- **The reason** is one sentence: type, level, the two strongest weighted factors, the file count, and any clamp or fallback notes.

**Registry defaults** (`defaults.py`)

| Model | Reasoning | Coding | Cost | Efforts |
|---|---|---|---|---|
| haiku | 2 | 3 | 1.0 | all 5 |
| sonnet | 4 | 5 | 3.0 | all 5 |
| opus | 5 | 5 | 5.0 | all 5 |

- Every default has a 200k context window.
- Config merges entries by id (unknown keys raise). A new model must state `reasoning`, `coding`, `cost` and `efforts`.

**Reuse, recording and output**

- **Reuse inside a run:** the latest `decided` event (tool `route`) with a 7-token body `type level model effort source score confidence` is rebuilt with `reused=True`, unless `--fresh` or an explicit model/effort is given, or the model is no longer valid.
- **Recording:**
  - `routing.jsonl` (trimmed to 5,000 lines) holds `at, session, execution, work_item, task_hash (FNV-1a 32-bit), type, level, model, effort, reason, confidence, override_source, reused, score, factors{7}, effort_in_use (EOS_EFFORT | CLAUDE_EFFORT)`;
  - a `decided` event is appended to the open run;
  - the brief records with `record=False`, except when `record_prompts` is on (once per session per task hash, and only for prompts with classifier matches);
  - `run start` routes the run's title when routing is configured and a session exists.
- **`routing-usage.jsonl`** holds one line per session per model from `--usage-from <transcript>`: messages (deduplicated by message id) and four token counts, with subagents tallied separately. Up to 2,000 sessions, written as temp file + replace.
- **Surfaces:**
  - `eos route` (human-readable or `--json`), `--stats` (decisions joined to run outcomes);
  - the brief's ROUTE lines;
  - MCP `get_context route:true` (`record=False`);
  - the PreToolUse hook, which writes a model only if it is one of `haiku`, `sonnet`, `opus`, `fable`.
- **`adjust_for_history` returns its input** (`policy.py:126-136`). Nothing reads outcomes to change a decision.

**Real usage (nexus, `record_prompts = true`):**

- 67 decisions: `sonnet/medium` 58, `haiku/low` 4, `opus/high` 3, `sonnet/high` 2.
- Levels: MEDIUM 58, HIGH 5, LOW 4.
- Types: normal_implementation 21, test_generation 13, planning 11, code_review 9.
- 39 of the decisions are inside runs; transcript usage exists for 16 sessions.

**Limits**

- The classifier is English keyword matching. In a domain whose own vocabulary overlaps the taxonomy ("rate plan", "PR", "test environment"), classification is likely distorted.
- Scores cluster at MEDIUM without `--file` input.
- Capability numbers are unvalidated defaults.
- Effort is only advisory: the harness cannot take it per call.
- The model actually used must be joined from the harness transcript.
- There is no feedback loop at all.

### 2.18 Skills / plugins — IMPLEMENTED (one runtime defect)

**Evidence**

- `core/plugins/base.py:9-31`
- `core/plugins/registry.py:33-56`
- `core/extensions.py:11-186`
- `core/index.py:361-384,729-761,820-837`
- `core/ai/writer.py:26-259`
- `core/ai/templates/*`
- `core/lib/updater.py:30-38`
- ADR-004, ADR-009, ADR-013, ADR-021

**Language plugin contract**

- `LanguagePlugin(name, extensions)` with:
  - `detect(root) -> bool` (marker files);
  - `parse_file(rel_path, content) -> FileSemantic`;
  - optional `parent_ref(root)`.
- **The registry is a static list** `[Python, TypeScript, JavaScript, Java]`. A new plugin means editing `registry.py`; there is no discovery.
- A plugin is detected when a matching extension exists (bounded walk) or `detect()` returns true. A file goes to the first detected plugin whose extension matches.
- There is **no** separate detector/parser/analyzer split, contrary to `ARCHITECTURE.md:80-85` and the README's contributing section.

**Index extension contract**

- **Configuration:** `[index] extensions = ["path.py", …]`, resolved against the root and required to exist.
- **Loading:** the module is imported as `eos_ext_<stem>_<digest12>`.
- **Optional members:**
  - `SCHEMA` (`executescript`);
  - `COUNTS` (label → SQL);
  - `QUESTIONS` (name → `{help, sql}`; each `?` receives the same single CLI argument);
  - `sources(root, notes_dir)` (field tuples added to the staleness digest);
  - `load(build)`, where `BuildContext` offers `conn, root, notes_dir, meta, read(path) → (bytes, sha256), issue(source, ref, problem), search(source, ref, title, body)`.
- **Failure handling:**
  - a configuration error (missing file, not importable, wrong shape) raises `IndexBuildError`, and the previous index is kept;
  - a `SCHEMA` or `load` failure becomes a `build_issue`, and that extension's tables are skipped or left empty;
  - `meta.extensions = name@digest12,…`.
- Extensions are trusted in-process code with no sandbox (a documented choice).

**Generated AI surfaces** (`writer.write_all`)

- `.claude/skills/eos/SKILL.md` (one template; the MCP half is fenced off on the `cli` surface);
- `.claude/agents/eos-researcher.md`;
- hooks `eos-brief.py` (SessionStart), `eos-prompt.py` (UserPromptSubmit), `eos-close.py` (Stop), each registered in `.claude/settings.json`. Registration merges by key; an existing entry is recognised by script path;
- optional `eos-route.py` (PreToolUse, matcher `Agent|Task`), installed or removed according to `[model_routing] hook`;
- `.mcp.json` `mcpServers.eos` (only on the `mcp`/`both` surface);
- an `AGENTS.md` block between `<!-- eos:begin -->` and `<!-- eos:end -->` (skipped with `--no-agents-md`).
- The surface choice is persisted in `[ai] surface`.

**Defect (verified)**

- `Updater.compute_manifest` copies only `*.py`, so the three `.md` templates never reach `.eos/runtime/ai/templates/`.
- `python .eos/runtime/eos.py ai update <root>` raises `FileNotFoundError …/ai/templates/skill.md`, reproduced on a scratch copy of the nexus runtime.
- The same applies to `core/lib/sqlite_schema.sql`, which only the UI uses.

**Limits**

- There is no plugin API for detectors beyond `parse_file`: a new fact predicate requires changing `builder.py`.
- Extensions cannot hook into scanning, only into indexing.
- Rewriting `.claude/settings.json` re-serializes the JSON, so any formatting outside the managed hooks is not preserved; comments are not possible in JSON anyway.

### 2.19 MCP — IMPLEMENTED (off by default)

**Evidence**

- `core/mcp_server.py:12-403`
- `core/eos.py:2215-2227`
- `core/ai/writer.py:74-86`
- `tests/test_mcp_index_tools.py` (`test_tool_roster_did_not_grow`)
- ADR-021

**Mechanism**

- **Transport.** A hand-rolled server reading **newline-delimited JSON-RPC** on stdin (one object per line) and writing to stdout.
- **Methods:**
  - `initialize` returns `protocolVersion "2024-11-05"` (hard-coded) and `capabilities.tools.listChanged=false`;
  - `notifications/initialized` and `notifications/cancelled` get no response;
  - `ping`;
  - `tools/list`;
  - `tools/call`.
- **Errors:**
  - an unknown method returns `-32601`;
  - an unknown tool returns `-32602`;
  - an exception inside a handler returns a *result* with `isError:true`;
  - a malformed line returns `-32603` with `id: null`.
- There are no resources, no prompts and no batching; requests are handled sequentially.
- **The 12 tools:** `get_project`, `get_structure`, `get_context` (with `task`, `target`, `budget` and `route`/`files`/`model`/`effort`), `get_file` (sensitive-file and containment checks), `find_symbol`, `compose` (deprecated alias), `impact_analysis` (with `include` facts/coverage/history), `get_graph` (the whole graph.json), `search_index`, `get_parent_implementation`, `add_note` (**the only write**), `search_notes`.
- Results are returned as pretty-printed JSON text (`indent=2`).
- **The roster is pinned at 12 by a test.** New capability arrives by widening an existing tool (for example `get_context route:true`).

**Cost**

- ADR-021 cites a host measurement: 2,194 tokens of roster per session across 18 registered services.
- This audit measured EOS's own `tools/list` at 4,892 characters (about 1.2k tokens at chars/4).
- `--surface cli` is the default, so no MCP registration is written.

**Limits**

- `add_note` passes no session, procedure or execution, so MCP-written notes are unattributed.
- MCP calls never refresh the index; impact reports staleness with a `stat` comparison.
- `get_graph` can return more than 30 MB.

### 2.20 Impact analysis — IMPLEMENTED (direction-limited)

**Evidence**

- `core/index.py:533-597` (`IMPACT_KINDS`, recursive CTE, caps)
- `core/inspector.py:238-320` (index path, graph fallback, staleness), `:679-700` (history), `:845-935` (trace)
- `core/eos.py:646-672,1041-1083`
- `core/mcp_server.py:61-81`
- ADR-015, ADR-017

**Mechanism**

- **Query.** For each direction, one recursive CTE with `UNION` (to terminate on cycles):
  ```sql
  reachable(nid, depth) … JOIN edge e ON e.{src|dst} = r.nid
  WHERE r.depth < :depth AND e.kind IN (import, extends, implements, field, new, calls)
  ```
  grouped by `path` with `MIN(depth)`.
- **Directions:** `dependencies` follows src→dst; `dependents` follows dst→src.
- **Bounds:** depth is clamped to 1–5 (default 1 for `impact`, 4 for `trace`). Each direction is capped at `MAX_IMPACT_ROWS = 500` (+1 row to detect truncation).
- **Result fields:** `path`, `depth`, `origin` (`own` or the parent label), `source` (`index` or `graph.json`), `index_built_at`, and `stale` (graph.json mtime is more than 1 s newer than `built_at`).
- **Fallback.** With no index, or the path not in it, the answer comes from `graph.json` using **import edges only, depth 1** (the depth argument is ignored).
- **`--include`:**
  - `facts` and `coverage` via `why()`, after an `index.refresh`;
  - `history` via `git_commit_file`: up to 20 commits for the file, newest first (`ORDER BY c.ord`, where `ord` 0 is HEAD).
- **`trace <file>`:**
  - forward dependencies;
  - routes parsed from the node's `doc` lines `- **VERB** \`path\``;
  - codes thrown in the file and in everything it reaches;
  - `runtime_wired`: `bean-name` facts with no inbound `calls/new/field` edge (total, uncalled, 5 examples). Reachability is **never** unioned through them; a test pins this.

**Limits**

- **Single-direction traversal:** an interface-typed dependency does not lead to its implementations.
- There are no call-site precision edges: calls are recorded only when the receiver type resolves, and then only once per target and method.
- Edges carry no weights or ranking. Results are sorted by depth then path; there is no notion of "likely impacted".
- The 500-row cap makes hub files truncate quickly.
- Python and JS/TS have only import edges.
- The `folder-hierarchy` kind is excluded (correctly).
- `eos bench` measures dependent recall only for Java same-package `<Name>Test` pairs (ADR-015 cites 339 of 345 pairs).

### 2.21 Persistence / SQLite architecture — IMPLEMENTED

**Evidence**

- `core/index.py:56-320` (schema), `:400-491` (build/refresh/atomic swap), `:682-726` (populate order), `:771-853` (sources digest)
- `core/lib/sqlite_schema.sql`
- `ui/app/db.py:12-59`
- ADR-003, ADR-005, ADR-013, ADR-014, ADR-020, ADR-022

**Principle.** Files are the source of truth; `eos.db` is derived and "always safe to delete".

**Build**

1. `_sources_digest` is computed **before** anything is read. It covers:
   - `SCHEMA_VERSION` and the engine version;
   - the notes directory path and each note's sha256;
   - the sha256 of `work.jsonl`, `executions.jsonl` and `verifications.jsonl`;
   - the sha256, size and mtime of `last_scan.json`, `graph.json`, `evidence.jsonl` and the 5 brain docs;
   - `ticket_pattern` and `merge_branch_pattern`;
   - git HEAD;
   - each extension's file digest and declared sources.
2. `refresh()` rebuilds only if this digest differs from `meta.inputs_sha256`. It is called by `query`, `why`, `rules`, `trace`, `ask`, `draft-test` and `impact --include`. `scan` and `index` always build.
3. `_build` creates a temp file (`mkstemp` in the same directory) with `PRAGMA journal_mode=OFF; synchronous=OFF`, creates the schema, and loads in this order: notes → work → executions → verifications → brain → evidence → extensions → git history.
4. It writes `meta` and `build_issue`, commits, `fsync`s, and swaps the file in with `os.replace` (Windows `PermissionError` → `IndexBuildError`, keeping the old index). On any exception the temp file is unlinked.

**Reading.** Reads go through `connect_read_only`: a `mode=ro` URI plus an authorizer that allows only `SELECT`, `READ`, `FUNCTION`, `RECURSIVE` and `PRAGMA`. Each call opens and closes its own connection.

**Every table** is listed in Part D.

**What lives as files and what lives in SQLite**

| Store | Location | Nature | In `eos.db`? |
|---|---|---|---|
| Notes `*.md` | `notes_dir` (default `.eos/knowledge`) | authored, git-tracked | `note`, `note_tag`, `note_scope`, `search` |
| `.skips.jsonl` | `notes_dir` | append-only | no |
| `work.jsonl` | `notes_dir` | append-only ledger | `work_item`, `work_holder`, `work_event`, `search` |
| `executions.jsonl` | `notes_dir` | append-only ledger | `execution`, `execution_event`, `search` |
| `verifications.jsonl` | `notes_dir` | append-only | `verification`, `search` |
| `file_cache.json` | `.eos/data/cache/` | derived semantic cache | no (read directly by `find_symbol` and `parent`) |
| `graph.json`, 5 brain `.md` | `.eos/data/brain/` | derived | `node`, `node_symbol`, `edge`, `brain_doc`, `search` |
| `evidence.jsonl` | `.eos/data/brain/` | derived | `fact`, `coverage`, `scan_exclusion` |
| `last_scan.json` | `.eos/data/` | derived (scan completion marker) | `meta.last_scan` |
| `llm_context.md` | `.eos/data/brain/` | output of `eos context` | no |
| `telemetry.jsonl` | `.eos/data/` | local log, ≤ 10,000 lines | no |
| `routing.jsonl` | `.eos/data/` | local log, ≤ 5,000 lines | no |
| `routing-usage.jsonl` | `.eos/data/` | per-session totals, ≤ 2,000 | no |
| `bench.md`, `candidates/*.java.txt` | `.eos/data/` | outputs | no |
| `id.txt`, `config.toml`, `runtime/` | `.eos/` | identity, configuration, engine copy | `meta` (partial) |
| git history | the repository | external | `git_commit`, `git_commit_file`, `git_commit_ticket` |
| Session pointer and prompt digests | `~/.local/state/eos/{current,prompted}/` | machine-local | no |
| Central registry | `~/.eos-ui/eos.db` (`EOS_UI_DB`) | UI only | separate database |

**The central registry** (`core/lib/sqlite_schema.sql`, WAL): `eos_instances(id PK = .eos/id.txt, path UNIQUE, name, engine_version, tech_stack JSON, status active|missing|stale, created_at, last_scanned_at, last_updated_at, metadata JSON)` and `eos_scan_roots(id, path UNIQUE, added_at, last_scan_at)`. It is reconciled by id, not path (ADR-003).

**Limits**

- A full rebuild on every change: a 56 MB service index is rebuilt whenever one note or one ledger line changes, at the next reader call.
- There is no incremental index or migration path. That is by design: the schema version is part of the digest.
- `eos clean` does not remove the logs.
- There is no retention on the ledgers.
- The central registry holds no memory data.

### 2.22 Learning — PARTIALLY IMPLEMENTED

**Evidence**

- `core/notes.py:1682-1750`
- `core/executions.py:231-284,387-425,501-532`
- `core/inspector.py:765-842` (the `verified` rung)
- `core/routing/policy.py:126-136`
- `core/routing/trace.py:119-143`
- ADR-018, ADR-023, ADR-024, ADR-025

**What is learned (recorded, then reused)**

1. Procedure counters and `last_verified`, moved only by `run finish`, plus `## Known failures` bullets.
2. Lessons: `run finish --outcome failed --lesson` writes a `kind: lesson` note (`## What went wrong` with the run's non-zero exits, `## What was learned`, `## Next time`, and `execution:`/`procedure:` front matter). A duplicate title appends `## Seen again: <date> <run id>` instead of creating a second note.
3. Behaviour-code coverage is promoted to `verified` only when the latest run **passed and** the code is reached **and** named. A failing run never demotes.
4. Confidence words are derived from counters and age every time they are read.
5. `procedure audit` recomputes counters from the ledger and flags drift.
6. Routing decisions are recorded against run outcomes (`route --stats`).

**What is NOT learned**

- nothing changes the router (`adjust_for_history` is identity);
- nothing changes retrieval ranking (IDF is recomputed per query, but no parameter is ever fitted from usage);
- nothing auto-promotes or deprecates a procedure;
- nothing writes a verdict;
- no stored confidence;
- no fact origin other than `extracted`;
- nothing about notes' usefulness (which notes were read or used is not tracked).

`eos note eval` measures retrieval against golden files but never tunes anything.

### 2.23 Verification — IMPLEMENTED (recording and grounding)

**Evidence**

- `core/verification.py:37-167`
- `core/eos.py:815-866` (verify), `:936-961` (findings), `:964-1000` (draft-test)
- `core/testgen.py:65-473`
- ADR-018, ADR-024

**Mechanism**

- **`eos verify <code>`** takes `--outcome passed|failed|errored`, `--command` (required), and optionally `--exit-code`, `--log`, `--output` (only its sha256 is stored), `--verdict` and `--note`. It appends to `verifications.jsonl`:
  `code, outcome, command, exit_code, recorded_at, commit (HEAD), log, output_sha256, verdict, note, session, execution`.
  - Inside an open run it also appends a `verified` event with `ref = code:outcome`.
- **Verdicts** are set only by a person: `expected-behaviour`, `test-defect`, `environment-failure`, `potential-defect`, `confirmed-defect`. A failing run without a verdict prints the reason it has none.
- **`eos findings`** lists runs newest first, with a summary of the latest run per code.
- **`eos draft-test <code>`:**
  1. Finds the throw sites. A site only in a linked parent is refused.
  2. Picks the test that already reaches the class, or proposes `src/test/…Test.java`.
  3. Reads that test's style: package, imports, AssertJ vs JUnit, Mockito, `@Nested`, `@DisplayName`.
  4. **Grounds** the class and method against the source by regex, checks visibility, and if the method is private or inaccessible drives it through a public entry point (preferring `execute`).
  5. Finds the exception type at the throw site, and an accessor for the code up to 3 levels up the hierarchy (including Lombok `@Getter`).
  6. Renders one `@Test` method marked `DRAFT`, and with `--write` saves it to `.eos/data/candidates/<CODE>.java.txt`.
  - Anything that cannot be grounded is refused.

**Limits**

- Nothing is executed or checked for compilation.
- Grounding is regex-based, and Java/JUnit-only.
- The arrangement that drives the code down the failing branch is left to the person.
- Nexus has 1 verification record in total.

### 2.24 Telemetry / cost / observability — IMPLEMENTED (three defects)

**Evidence**

- `core/telemetry.py:46-328`
- `core/eos.py:869-933` (cost), `:1740-1777` (work stats), `:2841-2892` (Timer wrapper)
- `core/work.py:356-434`
- `core/bench.py:39-676`
- `core/routing/usage.py`
- hook `_record_failure` / `_record` functions
- ADR-011, ADR-019, ADR-021

**Mechanism**

- **Telemetry** is off unless `[telemetry] enabled = true`.
  - Every command that takes a path is wrapped by `Timer`, except `init`, `ui`, `mcp` and `cost`.
  - Output is counted by a pass-through stream (never buffered). `declare_answer_size` lets `eos context` report the file it wrote.
  - **Line fields:** `at, command, flags (names only), session (≤64), session_from, ms, chars, tokens (chars//4), rebuilt, ok`.
  - The log is trimmed to 10,000 lines, and failures are swallowed.
  - Hooks append `brief ok:false` when EOS is unreachable, and `close ok:<clean>` lines — but only into a log that already exists.
- **`eos cost`** reports per command: calls, median ms, "median tokens" (**computed as the mean**, `tokens // calls`, `core/telemetry.py:252`), rebuilds, failed. For sessions it reports: identified sessions, sessions "beyond opening", median and max calls, failed openings, Stop-hook asked vs left work open, `attributed_by`, and unattributed calls.
- **`eos work stats`** replays events: items, events, claimed, contested (a second concurrent claimer), went quiet (a gap of ≥ 24 h while held), closed/done/dropped, blocks, open now/stale/contested, and median and longest claim→close hours.
- **`eos bench`** (ADR-011), on this project only, with seed 0:
  - `find_symbol` recall against symbols parsed by the plugins (grep -rn -F as baseline);
  - `get_context` vs Read (size and time only);
  - brief vs `get_context` (size, with the ledger size disclosed);
  - dependent recall on Java same-package test/subject pairs (the rule is printed);
  - it rescans and reindexes as a side effect, and writes `.eos/data/bench.md`.
- **Routing usage** is folded from transcripts (§2.17).

**Defects**

- `rebuilt` is never set, so the "rebuilds" column is always 0.
- `ok` means "no Python exception", not "exit 0", so the `failed` column misses every handled error. Nexus: 0 of 5,054 lines.
- "median tokens" is a mean.

**Limits**

- Tokens are estimated as chars/4.
- There are no per-call tracing spans, and nothing is exported externally.
- There are no metrics for retrieval quality in production: only offline golden sets.

**Real usage (nexus):** 5,054 lines since 2026-09-19. Calls: `brief` 2,175, `scan` 2,126, `note` 312, `route` 170, `run` 115, `work` 44. 141 identified sessions.

### 2.25 Token efficiency — IMPLEMENTED (with gaps)

**Evidence:** see the constants cited in §2.8, and `core/eos.py:772-801` (`rules --limit 20`), `core/inspector.py:17-32`, `core/mcp_server.py:61-67,319-321`, `core/ai/writer.py:9-18`, `core/knowledge/model.py:41-48`.

**Measures that exist**

- **Brief:**
  - 1,500 tokens at 3.0 chars/token, with bounded exemptions;
  - clipping: steps 240, lessons 100, known failures 140, prerequisites and success 100, ROUTE reason 120;
  - caps: 3 runs, 3 related notes, 3 failures, 5 work items, 4 notes;
  - `--task-only` returns an empty string when nothing matched;
  - the prompt hook sends at most 2,000 characters of prompt, and skips a block whose digest was already delivered in the session.
- **Context:**
  - ceiling of budget × 4 characters;
  - brain heading deduplication (the README cites 45% duplication removed on one service);
  - file-by-file enumeration sections dropped;
  - notes capped at 15% and work at 8%;
  - whole-section drops, with omitted note titles listed;
  - target file capped at 12,000 characters.
- **One surface:** MCP is off by default, and the roster is pinned at 12 tools.
- **`eos rules`:** 20 codes by default (a measured 37,874 tokens unbounded), with the full list available through `--format json`.
- **Provenance** is kept out of `graph.json`. The index excludes folder nodes and edges. Impact is answered from SQLite in about 1 ms, instead of re-parsing graph.json (0.14 s).

**Gaps**

- The branch brief is unbudgeted (§2.8).
- `graph.json` and MCP results are pretty-printed JSON (`indent=2`), which is avoidable bloat.
- `get_graph` returns up to 33.8 MB.
- `file_cache.json` is pretty-printed and rewritten whole on every scan.
- Nothing measures the tokens a session actually saved.

### 2.26 Failure / recovery — IMPLEMENTED (no locking)

**Evidence**

- `core/index.py:446-491,1054-1224`
- `core/executions.py:231-284`
- `core/work.py:253-330`
- `core/telemetry.py:123-206`
- `core/routing/trace.py:52-91`
- `core/routing/usage.py:77-99`
- hook templates
- ADR-013, ADR-020, ADR-021, ADR-022

**Mechanism**

- **Index:**
  - atomic swap; a configuration error keeps the old index;
  - a runtime failure of one source becomes a `build_issue`;
  - a half-written scan is detected: brain files newer than `last_scan.json`, count mismatches between `graph.json` and `last_scan`, or `evidence.jsonl` header mismatches all cause that section to be skipped.
- **Readers.**
  - When a refresh fails, `eos query` answers from the stale index with a warning.
  - Other commands warn and continue.
- **Ledgers:**
  - append-only; unparseable lines are skipped;
  - an event or line for an unknown id creates the item or run, so a lost `start` or `open` does not hide work;
  - finish is idempotent, and a contradicting outcome is refused;
  - a failed finish without a lesson is refused before any write;
  - the lesson is written before the finish line;
  - counters are moved *after* the ledger line, so `procedure audit` can repair by recomputing.
- **Collisions and staleness.** Work collisions (two sessions holding one item) are reported and never resolved. A claim with no event for 24 h is flagged stale and never closed.
- **Hooks:**
  - SessionStart, prompt and PreToolUse always exit 0 and stay silent on failure. They try the `eos` on PATH, then `.eos/runtime/eos.py` with Python 3.14 down to 3.11.
  - The Stop hook exits 2 only to ask about held work or open runs. It asks once (`stop_hook_active`) and never without a session id; when EOS cannot be reached it lets the session end.
- **Logs.** Telemetry, trace and usage never raise. Usage writes are atomic (temp file + replace).

**Limits**

- **No locking:**
  - concurrent appends rely on `O_APPEND`;
  - `record_procedure_run`, `append_to_note_section`, `_set_front`, and the telemetry and trace `_trim` are read-modify-write and can lose concurrent updates.
- **Config rewrites.** `ConfigIO.write_toml` (used by `init --link-parent` and the surface setting) re-serializes `config.toml` with a minimal writer, which **drops comments and formatting**.
- A hard kill leaves orphaned `eos.db.*.tmp` files; one is present in nexus.
- A scan's artifact writes are not atomic; they are detected afterwards rather than prevented.

### 2.27 UI — PARTIALLY IMPLEMENTED (peripheral)

**Evidence**

- `ui/server.py` (FastAPI, about 24 routes, `TrustedHostMiddleware` 127.0.0.1/localhost, a strict CSP for the Graphify HTML)
- `ui/app/db.py`, `reconcile.py`, `watcher.py`, `workspace.py`, `graphify.py`, `graphify_queue.py`
- `ui/frontend/src/*` (React 19, Cytoscape, Vite)
- `docs/phases.md` (Phase 2 "in progress", Phase 4 Tauri/nested "planned")
- ADR-003, ADR-007, ADR-010

**Mechanism**

- A central registry of `.eos` instances, reconciled by the id in `id.txt`, with scan-roots management.
- An instance list, and a drill-down Cytoscape graph built from `graph.json`.
- A Graphify artifact viewer and workspace projections (relations by name mention, BFS paths, shared dependencies, communities).
- A watchdog watcher that runs an incremental `eos scan` on change (1.5 s debounce, 120 s timeout).
- Dependencies are installed into EOS's own venv after asking (T2).

**Limits**

- It reads **none** of the memory stores: no notes, work, executions, procedures, routing or `eos.db`.
- The API is unauthenticated and can run `eos init/scan` on arbitrary paths (it binds locally only).
- The frontend must be built separately.
- ADR-007 abandoned a separate dashboard; this in-repo UI is the replacement, not a central part of the system.
- Nothing in `core/` depends on it.

---

## Part B — Every CLI command: handler → modules → stores

Every command that takes a path is wrapped by telemetry (`telemetry.jsonl`, when enabled), except `init`, `ui`, `mcp` and `cost` (`core/eos.py:2841-2877`).

**Setup, scan and index**

| Command | Handler (`core/eos.py`) | Core modules | Reads | Writes |
|---|---|---|---|---|
| `init` | `cmd_init` 73-155 | `lib/updater`, `links`, `ai/writer`, `routing/config` | `config.toml`, canonical `core/*.py` | `.eos/{id.txt, config.toml, runtime/**, data/cache, data/brain}`; `[links]`, `[ai] surface`; `.claude/skills/eos/SKILL.md`, `.claude/agents/eos-researcher.md`, `.claude/hooks/eos-{brief,prompt,close}.py` (+`eos-route.py`), `.claude/settings.json`, `AGENTS.md` block, `.mcp.json` (mcp/both) |
| `scan` | `cmd_scan` 208-288 | `scanner`, `lib/cache_store`, `knowledge/builder`, generators, `index` | source tree (+ linked parents), `.gitignore`, `config.toml`, `file_cache.json` | `data/cache/file_cache.json`, `data/brain/{_index,TechStack,EntryPoints,Architecture,AI_SUMMARY}.md`, `graph.json`, `evidence.jsonl`, `data/last_scan.json`, `data/eos.db` |
| `update` | `cmd_update` 291-327 | `lib/updater` | canonical `core/*.py`, `runtime/manifest.json` | `.eos/runtime/**.py`, `manifest.json`, `VERSION` |
| `doctor` | `cmd_doctor` 330-422 | `preflight`, `notes`, `work`; `--memory`: `memory_audit`, `brief`, `executions` | `.eos/*`, notes, ledgers, `eos.db`, `.claude/settings.json`, git | nothing directly (`--memory` runs `brief.build`, which can write a routing trace line when `record_prompts` is on) |
| `info` | `cmd_info` 461-480 | — | `id.txt`, `runtime/VERSION`, `last_scan.json` | — |
| `status` | `cmd_status` 593-596 | `inspector.project_summary` | the same | — |
| `clean` | `cmd_clean` 483-506 | `index.db_path` | — | deletes `data/cache/*`, `data/brain/*`, `last_scan.json`, `eos.db` (logs kept) |
| `index` | `cmd_index` 535-543 | `index`, `extensions`, `notes`, `work`, `executions`, `verification` | notes, ledgers, brain, `graph.json`, `evidence.jsonl`, extensions, `git log` | `eos.db` (temp file + replace) |
| `query` | `cmd_query` 554-590 | `index.refresh`, `run_query` / `search` | `eos.db` + digest inputs | `eos.db` if stale |
| `graph` | `cmd_graph` 599-614 | `inspector.load_graph` | `graph.json` | optional `--output` file |

**Context and code intelligence**

| Command | Handler (`core/eos.py`) | Core modules | Reads | Writes |
|---|---|---|---|---|
| `context` | `cmd_context` 617-638 | `inspector.build_context`, `work`, `notes`, `index` | brain, notes, `work.jsonl`, `eos.db` | `data/brain/llm_context.md` (unless `--stdout`) |
| `compose` | `cmd_compose` 641-643 | `inspector.compose` | as `context` + target file | — |
| `impact` | `cmd_impact` 646-672 | `inspector.impact` / `why` / `file_history`, `index.refresh` (with `--include`) | `eos.db` or `graph.json` | `eos.db` if refreshed |
| `why` | `cmd_why` 675-723 | `index.refresh`, `inspector.why` | `eos.db` | `eos.db` if stale |
| `rules` | `cmd_rules` 726-812 | `index.refresh`, `inspector.rules`, `verification` | `eos.db`, `verifications.jsonl` | `eos.db` if stale |
| `trace` | `cmd_trace` 1041-1083 | `index.refresh`, `inspector.trace` | `eos.db` | `eos.db` if stale |
| `ask` | `cmd_ask` 1003-1038 | `inspector.questions` / `ask`, `extensions` | extension modules, `eos.db` | `eos.db` if stale |
| `draft-test` | `cmd_draft_test` 964-1000 | `index.refresh`, `testgen` | `eos.db`, source files, linked-parent sources | `--write`: `data/candidates/<CODE>.java.txt` |
| `parents` | `cmd_parents` 2189-2212 | `links` | `config.toml`, parent `.git/HEAD` | — |
| `parent` | `cmd_parent` 2146-2186 | `inspector.get_parent_implementation` | `file_cache.json`, live parent sources | — |

**Verification, cost and tooling**

| Command | Handler (`core/eos.py`) | Core modules | Reads | Writes |
|---|---|---|---|---|
| `verify` | `cmd_verify` 815-866 | `verification`, `executions`, `telemetry.detect_session` | pointer, `executions.jsonl` | `verifications.jsonl`; `verified` event in `executions.jsonl` |
| `findings` | `cmd_findings` 936-961 | `verification` | `verifications.jsonl` | — |
| `cost` | `cmd_cost` 869-933 | `telemetry.summary` | `telemetry.jsonl` | — (not itself recorded) |
| `mcp` | `cmd_mcp` 2215-2227 | `mcp_server` → `inspector`, `notes`, `index`, `routing` | as the tools do | `add_note`: a new note |
| `bench` | `cmd_bench` 2230-2240 | `bench` → `scanner`, `builder`, generators, `index`, `inspector`, `brief`, `work`, `notes` | source tree, cache | cache, brain, `graph.json`, `evidence.jsonl`, `last_scan.json`, `eos.db`, `data/bench.md` |
| `ui [start\|uninstall]` | `cmd_ui` 2243-2271 | `preflight` → `bin/start.sh` | — | `~/.local/share/eos/venv` (`EOS_VENV`); UI writes `~/.eos-ui/eos.db` |
| `ai update` | `cmd_ai` 2274-2285 | `ai/writer`, `routing/config` | `config.toml`, templates | as `init`'s AI surfaces |

**Notes**

| Command | Handler (`core/eos.py`) | Core modules | Reads | Writes |
|---|---|---|---|---|
| `note add` | `cmd_note_add` 1098-1137 | `notes.add_note` | the whole notes corpus (deduplication), scoped files | new `<date>-<slug>.md` |
| `note list/show/search` | 1151-1217 | `notes` | notes | — |
| `note eval` | 1220-1240 | `retrieval` | golden TSV, notes | — |
| `note skip` | 1243-1255 | `notes.record_skip` | — | `.skips.jsonl` |
| `note amend` | 1258-1285 | `notes.amend_note` | the note, scoped files, the corpus | rewrites the note |
| `note audit` | 1297-1386 | `notes.stale_notes` | notes, scoped files | — |

**Work, brief and routing**

| Command | Handler (`core/eos.py`) | Core modules | Reads | Writes |
|---|---|---|---|---|
| `work add/claim/log/block/unblock/done/drop` | 1463-1526 | `work.append` / `open_item` | `work.jsonl`, git | `work.jsonl` |
| `work list/show/stats` | 1529-1645, 1740-1777 | `work`, `index` (show) | `work.jsonl` (+ siblings with `--across`), `eos.db`, git | — |
| `brief` | `cmd_brief` 1648-1662 | `brief`, `notes`, `work`, `executions`, `index` (catalogue), `routing` | ledgers, notes, `eos.db`, `config.toml`, git | `routing.jsonl` + `decided` event only when `record_prompts` is on and `--task` is given |
| `route` | `cmd_route` 1665-1737 | `routing/*`, `inspector.impact` (with `--file`), `executions` | `config.toml`, index, ledger | `routing.jsonl`; `decided` event; `--usage-from`: `routing-usage.jsonl` |

**Runs and procedures**

| Command | Handler (`core/eos.py`) | Core modules | Reads | Writes |
|---|---|---|---|---|
| `run start` | 1794-1838 | `executions.start`, `routing` (if configured) | git | `executions.jsonl`; `~/.local/state/eos/current/<session>`; `routing.jsonl` + `decided` |
| `run event` | 1841-1852 | `executions.event` | pointer / env | `executions.jsonl` |
| `run finish` | 1855-1866 | `executions.finish`, `notes` | ledger, notes | `executions.jsonl`; lesson note or `## Seen again`; procedure front matter + `## Known failures`; clears the pointer |
| `run list/show/tools/diff` | 1869-1930, 2063-2106 | `executions`, `verification` | ledgers, git | — |
| `procedure list/show/audit` | 1933-2004, 2029-2043 | `notes`, `executions` | notes, ledger | — |
| `procedure new` | 2006-2026 | `notes.add_note` | corpus | new procedure note |

---

## Part C — Enumerations (exact)

### C.1 Note kinds and required content (`core/notes.py:89,305-366`)

| Kind | Required | Optional / engine-owned |
|---|---|---|
| `defect` | `--cause`, `--solution`, `--metric` (CLI flags), rendered as `## Root cause`, `## Solution`, `## Metric`; `amend --body` must keep all three headings (any level, case-insensitive) | leading body prose |
| `finding` | a non-empty body | — |
| `procedure` | `## Steps` with ≥ 1 list item (numbered or bulleted); `## Rules` ≤ 600 characters if present | `## Prerequisites`, `## Success`, `## When not to use this`, `## Rules`; engine-appended `## Known failures`; front matter `procedure` (slug), `runs_ok`, `runs_failed`, `last_verified`, `last_execution` |
| `lesson` | `## What went wrong`, `## What was learned`, `## Next time` (each non-empty) | front matter `execution:`, `procedure:` (not enforced); engine-appended `## Seen again` |
| `decision` | `## Why`, `## When`, `## Component` (each non-empty) | — (**no `supersedes`**) |

Common front matter: `kind, title, created, updated (amend), source, tags[], scope[], scope_hashes[], session`.

### C.2 Execution ledger (`core/executions.py:30-37`; `bin/eos-event:35-38`)

- **Line types:** `start`, `event`, `finish`.
- **Event kinds** (closed vocabulary): `ran`, `read`, `changed`, `called`, `verified`, `noted`, `decided`.
- **Run outcomes:** `ok`, `failed`, `abandoned`; an unfinished run has `outcome = null`, which reads as "open".
- **`eos run list --outcome`** also accepts `open`.

### C.3 Other enumerations

| Vocabulary | Values | Source |
|---|---|---|
| Work events | `open, claim, log, block, unblock, done, drop` | `core/work.py:52-60` |
| Work statuses | `open, active, blocked, done, dropped` (live = active, blocked, open) | `core/work.py:62-73` |
| Verification outcomes | `passed, failed, errored` | `core/verification.py:39-42` |
| Verdicts (people only) | `expected-behaviour, test-defect, environment-failure, potential-defect, confirmed-defect` | `:47-53` |
| Procedure confidence | `failing, unverified, fresh (≤30 d), aging (≤90 d), stale` | `core/notes.py:1716-1750` |
| Behaviour-code coverage | `none, named, reachable, asserted, verified` | `core/inspector.py:816-842` |
| Fact origins | `extracted` (used); `documented, inferred, verified` (unused) | `core/knowledge/evidence.py:35-40` |
| Fact confidence | `CERTAIN 1.0` (used), `LIKELY 0.8` (calls only), `POSSIBLE 0.5` (unused) | `:45-49` |
| Routing task types | 12 (§2.17) | `core/routing/types.py:20-33` |
| Routing levels / efforts | `LOW MEDIUM HIGH CRITICAL` / `low medium high xhigh max` | `:18-19` |
| Override sources | `auto, flag, env, config, run` | `:36` |
| Brief modes | `with-brief, always, never` | `core/routing/config.py:21` |
| Surfaces | `cli, mcp, both` | `core/ai/writer.py:29` |
| Journey resolutions | `bean, impl_cls, shared, ambiguous, elsewhere, unresolved, no-bean` | `extensions/journeys.py:35-40,628-657` |
| Coverage `applies_here` | `yes, no, structural, unknown` | `core/inspector.py:627-676` |
| Ledger sync states | `ignored, untracked, uncommitted, unpushed, pushed, committed, no-git, unknown, absent` | `core/work.py:522-591` |
| Doctor matrix statuses | `IMPLEMENTED, PARTIAL, MISSING, DECIDED` (21 checks C-01…C-21) | `core/memory_audit.py:43-48,428-450` |

---

## Part D — Every table in `.eos/data/eos.db`

Schema: `core/index.py:56-320`. The "Filled from" column names the loader function.

**Build metadata**

| Table | Columns | Filled from |
|---|---|---|
| `meta` | `key PK, value` | `_populate` (`schema_version`, `built_at`, `engine_version`, `sqlite_version`, `fts5`, `project_root`, `service`, `inputs_sha256`, `notes_dir`, `work_ledger`, `execution_ledger`, `verifications`, `languages`, `tech_stack`, `entry_points`, `last_scan`, `evidence_generated_at`, `with_parents`, `git_head`, `extensions`, plus extension keys such as `journeys_dir` and `procedure_catalogue`) |
| `build_issue` | `source, ref, problem` | `BuildContext.issue` from every loader |

**Notes**

| Table | Columns | Filled from |
|---|---|---|
| `note` | `id PK, file UNIQUE, kind, title, created, updated, source, session, generated, body, sha256` | `_load_notes` ← `notes_dir/*.md` |
| `note_tag` | `note_id, tag` (PK both) | the same |
| `note_scope` | `note_id, ord, entry, hash` | the same |

**Work ledger**

| Table | Columns | Filled from |
|---|---|---|
| `work_item` | `id PK, title, status, ticket, opened_at, updated_at, holder_count, reason, last, events` (**no `stale` column** by design) | `_load_work` ← `work.fold(work.jsonl)` |
| `work_holder` | `item, session, agent, claimed_at` (PK `item, session`) | the same |
| `work_event` | `item, ord, event, at, session, agent, body, branch, commit_sha` | the same |

**Execution ledger and verifications**

| Table | Columns | Filled from |
|---|---|---|
| `execution` | `id PK, title, procedure, work_item, session, agent, started_at, finished_at, outcome, target, branch, commit_start, commit_end, lesson, events` | `_load_executions` ← `executions.load_path` |
| `execution_event` | `execution, ord, at, kind, tool, target, ref, exit_code, ms, body, session` | the same |
| `verification` | `ord PK, code, outcome, command, exit_code, recorded_at, commit_sha, log, verdict, note, session, execution` | `_load_verifications` ← `verifications.jsonl` |

**Brain and graph**

| Table | Columns | Filled from |
|---|---|---|
| `brain_doc` | `name PK, content, sha256` | `_load_brain` ← the 5 brain `.md` files |
| `node` | `nid PK (renumbered each build), id UNIQUE, type, label, path, dir, language, origin (own \| parent label), is_entry, doc` | `_load_brain` ← `graph.json` (file nodes only) |
| `node_symbol` | `nid, role (export \| top), name` | `graph.json` node metadata |
| `edge` | `src, dst, kind, imported` (PK all four) | `graph.json` edges, excluding `folder-hierarchy` |

**Git history**

| Table | Columns | Filled from |
|---|---|---|
| `git_commit` | `sha PK, ord, parents, author_name, author_email, authored_at, committed_at, subject, body, is_merge, pr, files_total` | `_load_history` ← `git log --max-count=2000` |
| `git_commit_file` | `sha, path` (≤ 200 per commit) | the same |
| `git_commit_ticket` | `sha, key, source (subject \| branch \| body)` | `ticket_pattern` / `merge_branch_pattern` |

**Provenance**

| Table | Columns | Filled from |
|---|---|---|
| `fact` | `fid PK, subject_kind, subject, predicate, object, origin, confidence, detector, source_ref, observed_at` | `_load_evidence` ← `evidence.jsonl` |
| `coverage` | `detector, predicate, files_eligible, files_with_hits, hits` | the same |
| `scan_exclusion` | `kind (ignore \| unsupported \| unresolved), rule, files` | the same |

**Search**

| Table | Columns | Filled from |
|---|---|---|
| `search` | FTS5 `(source UNINDEXED, ref UNINDEXED, title, body, terms)`, `tokenize='unicode61'`; a plain table with the same columns when FTS5 is missing. Shadow tables: `search_config`, `search_content`, `search_data`, `search_docsize`, `search_idx` | `_add_search` from notes, work, executions, verifications, brain, and extensions (`BuildContext.search`) |

**Extension tables** (present only when configured)

| Table | Columns | Filled from |
|---|---|---|
| `journey_snapshot` | `env PK, generated_at, scope, schema_name, generated_by` | `journeys.load` |
| `journey_step` | `env, cmd_config_id, bi, flow, state, phase, sort_id, bean_name, cmd_short_code, impl_cls, config_active, def_active, next_struct, managed_by, cmd_def_id, updated_at, updated_by, owner, resolution, candidates, owned, impl_path` | the same |
| `flow_step` | `env, config_id, bi, flow, state, sale_channel_id, sort_id, active, optional, checkout, next_struct, visibility_class, managed_by` | the same |
| `journey_doc` | `name PK, journey, title, services, bis, flows, verified_on, verified_at, verified_by, body, sha256` | the same |
| `catalogue_procedure` | `name PK, summary, source, steps` | `procedures.load` ← host catalogue JSON |
| `procedure_step` | `procedure, ord, name, tool` | the same |
| `procedure_state` | `procedure, env, status, updated_at, failed_step` | the same |

**Indexes (22):** `note_tag_by_tag`, `note_scope_by_entry`, `work_item_by_status`, `work_item_by_ticket`, `work_holder_by_session`, `work_event_by_session`, `execution_by_session`, `execution_by_procedure`, `execution_by_target`, `execution_event_by_tool`, `execution_event_by_session`, `verification_by_code`, `verification_by_execution`, `node_by_path`, `node_by_label`, `node_symbol_by_name`, `edge_by_dst`, `git_commit_by_ord`, `git_commit_file_by_path`, `git_commit_ticket_by_key`, `fact_by_subject`, `fact_by_predicate`. The extensions add `journey_step_by_flow` and `flow_step_by_flow`.

**Row counts in the nexus instance (2026-09-26):**

- note 236 · note_tag 1,666 · note_scope 141
- work_item 14 · work_event 32 · work_holder 1
- execution 51 · execution_event 1,929 · verification 1
- node 279 · edge 548 · node_symbol 4,338 · fact 675 · coverage 11 · scan_exclusion 51
- git_commit 1,109 · git_commit_file 5,212 · git_commit_ticket 95
- search 334 · catalogue_procedure 27 · procedure_step 184 · procedure_state 33
- journey_* 0–1 · build_issue 0
- Database size 6.2 MB.

**Service indexes:** svc-cpq-ordercapture is 55.9 MB (3,782 nodes, 31,693 edges, 48,357 facts, 407 distinct codes, 485 bean names); svc-pcm-product-catalog is 56.2 MB.

---

## Part E — Designed (in ADRs, plans or docs) but not implemented

| Item | Where it is designed | State in code |
|---|---|---|
| Linker stage (backlinks, cross-references) | `ARCHITECTURE.md:87`, ADR-005 | absent; `symbol_index` built and unused (`builder.py:68,91-95`) |
| Component / Service / EntryPoint entity types | `ARCHITECTURE.md:86`, ADR-005 | only a generic `KnowledgeNode.type` string |
| Edge kinds `monorepo-parent`, `shared-dep`, `manual-link` | `core/knowledge/model.py:15` comment | never produced |
| Detector / parser / analyzer split per plugin | `ARCHITECTURE.md:82-84`, README "Contributing" | plugins implement only `detect` + `parse_file` |
| Generators `graph/ → import.graph.json`, `json/ → graph.index.json` | `ARCHITECTURE.md:90-92` | only `graph.json` + `evidence.jsonl`; no `generators/graph/` |
| Runtime backups on update | ADR-001 consequences | explicitly removed (`core/lib/updater.py:7-11`); ADR-001 not amended |
| `decision` front matter `supersedes:` | operational-memory plan OM-40 (`:324`) | not in `notes.py` |
| `policy.adjust_for_history` reading `trace.outcomes_by(type, level, model)` | ADR-025, model-routing plan M4 / §11a | identity function (`policy.py:126-136`); no `outcomes_by` |
| Analysis of routing outcomes to recalibrate | model-routing plan §11a ("The analysis itself is not built yet") | only raw `routing.jsonl`, `routing-usage.jsonl`, `route --stats` |
| A dedicated MCP `route` tool | ADR-025 text ("an MCP tool") | folded into `get_context route:true` (plan M7 "as built") |
| C-10 semantic / vector retrieval | operational-memory plan §4 / §7 | **decided out** (ADR-001/010); `memory_audit.check_c10` returns `DECIDED` |
| Tauri desktop packaging | `ARCHITECTURE.md:192`, `phases.md:9,53,67` | none |
| Nested / monorepo `.eos` support | `phases.md:9,39,54,67` | none (the scanner only skips `.eos` dirs) |
| "Additional language plugins" | `phases.md:54` | four plugins only |
| A per-session cross-store listing (`brief --session` returns the session's records from all stores) | operational-memory plan OM-14 | `brief --session` only marks "(yours)"; SQL join possible |
| Automatic verdicts, trusted procedures, promotion | explicitly excluded (ADR-018, plan §7) | absent by design |
| Enforcement gates (a commit gate for unclaimed work) | ADR-020 "deferred" | absent by design (the Stop hook asks, it does not gate commits) |
| Local-variable types in Java edges | ADR-015 "not worth the trade" | absent by decision |
| Stemming | EOS note on retrieval ("a stemmer is a guess until a second golden set says otherwise") | absent by decision |

---

## Part F — Documentation claims not matched by code

| Claim | Where | Reality |
|---|---|---|
| MCP tools include `get_history` | `README.md:336-338` | no such tool; the list has `search_index`; history is `impact_analysis include:["history"]` (`core/mcp_server.py:188-232`) |
| "Two hooks" (`eos-brief.py`, `eos-close.py`) | `README.md:50-54` | three are always written (+ `eos-prompt.py` UserPromptSubmit), plus optional `eos-route.py` (`core/ai/writer.py:96-100,151-201`) |
| The hook exports `EOS_SESSION` so every later call in the session is attributed | `README.md:543-545`; `session_start.py:90-95` | the environment is set only for the child `eos brief`; attribution works because of `CLAUDE_CODE_SESSION_ID` |
| A Stop hook calls `eos route --usage-from` | `README.md:683-687`, plan §11a | the EOS-generated Stop hook does not; only the host's `automation/hooks/eos-usage.py` |
| Telemetry records "whether the index had to be rebuilt"; `eos cost` "rebuild count" | ADR-019, `core/telemetry.py:32-34`, `core/eos.py:890-893` | `Timer.rebuilt` is never set (always `False`; 0 of 5,054 real lines) |
| `eos cost` reports failures | ADR-019 | `ok` = no Python exception; handled errors that return exit 1 count as ok |
| "Medians, not means" / "median tokens returned" | ADR-019, `eos cost` header | `median_tokens = tokens // calls`, a mean (`core/telemetry.py:252`); only `median_ms` is a median |
| The UserPromptSubmit hook "prints nothing when nothing is recorded" | `README.md:575-578`, `prompt_submit.py:16-18` | true only when no note title or tag shares a word; "ok, continue" printed 478 characters on nexus (title match on "Continue") |
| The brief "is capped at a handful of lines" | ADR-021 | count-capped only; 3,055 characters measured with 13 items in flight |
| `eos init` writes "four integration surfaces" | ADR-009 | partly superseded by ADR-021 (no MCP by default) and by hooks; ADR-009 not amended |
| Runtime updates "keep timestamped backups" | ADR-001 | no backups (`updater.py:7-11`); only ARCHITECTURE was corrected |
| `.eos/data/` holds "immutable generated artifacts" | `ARCHITECTURE.md:10` | also holds the mutable `eos.db`, telemetry, routing and usage logs, `llm_context.md`, candidates, `bench.md` |
| "Knowledge Model is the single source of truth" and "Generators … never re-parse source files" | ADR-005, `ARCHITECTURE.md:95` | true for scan artifacts; `find_symbol` and `parent` read the semantic cache; testgen, the journeys bean index and parent lookup re-read source |
| The eos-researcher agent uses `search_notes` / `add_note` | `core/ai/templates/agent.md:10-39` | on the default `cli` surface those MCP tools are not registered (no `mcp-only` fence in `agent.md`) |
| README CLI table / ARCHITECTURE CLI table | `README.md:73-107`, `ARCHITECTURE.md:148-179` | README omits `run`, `procedure`, `note eval`; ARCHITECTURE omits `brief`, `work`, `run`, `procedure`, `parent` |
| `eos update` / `eos ai update` work from the deployed runtime | implied by ADR-001/009 | `ai update` fails from `.eos/runtime` (missing `.md` templates) |
| PreToolUse hook: "Four rules keep it harmless" | `pretooluse_task.py:12` | five bullets follow (cosmetic) |
| `graph.json` "25 MB on a real service" | ADR-014, README | now up to 33.8 MB (svc-pcm-product-catalog), still pretty-printed |

---

## Part G — Defects and anomalies verified during this audit

1. **`eos ai update` / `init` fails from `.eos/runtime/eos.py`** with `FileNotFoundError: …/.eos/runtime/ai/templates/skill.md`. Cause: `Updater.compute_manifest` includes only `*.py` (`core/lib/updater.py:30-38`). Reproduced on a scratch copy of the nexus runtime.
2. **`Timer.rebuilt` is never assigned** (`core/telemetry.py:134,150`). Observed: 0 of 5,054 nexus telemetry lines have `rebuilt: true`.
3. **Telemetry `ok` ignores exit codes** (`core/telemetry.py:145`). Observed: 0 failures in 5,054 lines. Also, `median_tokens` is computed as a mean (`:252`).
4. **False-positive task brief for conversational prompts:** "ok, continue" → `RELATED NOTES` (the title of note "env0 sanity: … Save & Continue disabled"), so the brief counts as found (`core/brief.py:341-356`).
5. **The branch brief has no character budget** (`core/brief.py:120-199`); `last:` and `blocked on:` print unclipped.
6. **The generated agent profile references MCP tools on the CLI surface** (`core/ai/templates/agent.md`, no fence).
7. **The shell capture helper ignores `[telemetry] session_env`** (`bin/eos-event:42`), unlike the Python path (`core/telemetry.py:82-97`).
8. **An orphaned `eos.db.s733dymu.tmp`** (2026-09-15) sits in nexus `.eos/data/`: a hard kill bypasses `_build`'s cleanup (`core/index.py:477-482`).
9. **Runtime version drift across projects:** 13 of 20 service indexes report engine 1.2.0 while core is 1.2.2 (the `meta.engine_version` values).
10. **Minor:**
    - `_impact_from_graph` ignores `depth` (`core/inspector.py:277-296`).
    - `file_history` orders by `c.ord` ascending, which is newest first because `ord` 0 is HEAD. The docstring says so; there is no bug, but the ordering is implicit.
    - `_RE_METHOD` in the JS plugin is unused.
    - `wildcards` in Java parsing are unused.

---

## Part H — Architectural strengths and weaknesses

### Strengths

- **Honest, deterministic, explainable.**
  - No model sits in any decision path.
  - Every derived number is printable (routing factors, coverage, `why` verdicts).
  - "Silence is not absence" is systematically engineered: coverage rows, `scan_exclusion`, "no index" versus "no match" messages.
- **File-first with a disposable SQLite projection.**
  - Git-friendly (one note per file; append-only JSONL).
  - Rebuildable and crash-safe (digest-gated rebuild, atomic swap, header-count checks on sidecars).
- **Memory separated by lifetime.** Notes (durable, audited for staleness), work (intent, expires), executions (episodes, permanent), verifications (evidence). Each has a clear writer and fold, and the index joins them.
- **Strong guards on the write paths:** credential detection, placeholder refusal, scope containment and sensitivity, duplicate detection, the amend invariants. Evidence of learning from real misuse.
- **Java structural parsing is genuinely good** for what it is: the masking lexer, position-based members, declaration-site edges, bean names and behaviour codes. The provenance and coverage model is well conceived.
- **ADR discipline and measurement culture:**
  - 25 ADRs;
  - plans with executable acceptance checks (`doctor --memory`);
  - golden-set retrieval evaluation;
  - a bench against ground truth or labelled baselines;
  - document-vs-CLI tests;
  - 931 tests.
- **Zero-dependency core** that runs anywhere with Python 3.11+. Clean optional tiers (the UI is isolated).
- **An extension point** (index extensions plus `QUESTIONS`) that kept host-specific knowledge out of core.

### Weaknesses

- **Monolithic CLI.** `core/eos.py` is 2,896 lines: 62 handlers plus a single 500-line `main()` argparse builder mixing presentation, policy (for example `rules` output grading text) and orchestration. `notes.py` (1,766 lines) mixes storage, validation, credential scanning, retrieval, context rendering, procedures and confidence.
- **Retrieval ceiling.** It is purely lexical: no embeddings (by decision), no stemming, no synonyms, English-leaning tokenization with ad-hoc multilingual patches.
  - Relevance and precision depend on the title and tag vocabulary that authors choose.
  - FTS does not weight titles.
  - Every note search re-reads the whole corpus.
- **The keyword classifier for routing is brittle in practice.** Real data shows 87% of decisions landing on one model and effort. The "learning" seam is intentionally inert, so nothing will correct it without a human.
- **The knowledge model is file-granular and thin.** There are no symbol-level graph nodes, no weights, no interface→implementation dispatch, and only import edges for Python and JS/TS. Several documented stages (Linker, typed entities) do not exist.
- **Full rebuilds everywhere:** a whole-graph regeneration per scan, a whole-index rebuild per input change (56 MB service indexes), whole-file JSON cache rewrites, every file read on every scan.
- **Duplicated rule sets that drift:** four ignore lists (scanner `DEFAULT_IGNORE`, inspector `IGNORED_DIRS`, UI watcher, UI reconcile), three token estimators (3.0, 4, 4), two credential guards (core versus the host pre-commit hook), two copies of the amend classification (core versus the host gate).
- **Concurrency by convention.** There is no locking. Read-modify-write on git-tracked Markdown (counters) and on log tails.
- **Capture depends on the host.** Episodic and tool memory is only as complete as the host's wrappers: service projects have 0 executions. The engine can record but cannot observe.
- **Distribution friction:**
  - a per-project runtime copy of `*.py` only (the template defect);
  - version drift across projects;
  - a minimal TOML writer that drops comments when EOS rewrites `config.toml`.
- **Maturity.** Large feature slices (operational memory, routing) were designed, built and released within a single day each, with acceptance mostly through presence and usage checks (`memory_audit` checks that functions exist and records are present, not that they are good). The plan itself records the gap: "21/21 in the one project the work was seeded in, 6/21 in the projects the work is meant to serve".
- **The UI is disconnected from the memory features.** The dashboard shows code graphs and Graphify artifacts only.

---

## Appendix A — Real-usage snapshot (nexus instance, read-only, 2026-09-26)

- **Config.**
  - `[knowledge] dir = ".devin/knowledge/nexus"`;
  - extensions: journeys and procedures;
  - `ticket_pattern (PROJ…|FM|BASE)-N`;
  - `[telemetry] enabled`;
  - `[model_routing] enabled, brief = with-brief, record_prompts = true`.
- **Notes:** 236 (finding 214, procedure 15, lesson 4, decision 2, defect 1). 85 have a scope; 193 carry a session.
- **Work:** 14 items (blocked 8, open 4, active 1, done 1). The branch brief shows 13 in flight, 8 of them stale.
- **Executions:** 51 (45 ok, 3 failed, 3 open), 1,929 events; lessons 4, 3 of them linked to a run; verifications 1.
- **Routing:** 67 decisions (58 sonnet/medium); usage recorded for 16 sessions.
- **Telemetry:** 5,054 lines (`brief` 2,175, `scan` 2,126); 141 sessions; `session_from`: `--session` 2,250, none 2,167, `CLAUDE_CODE_SESSION_ID` 337, `EOS_SESSION` 305.
- **Brief sizes (at 3.0 chars/token):**
  - branch brief: 3,055 characters;
  - task brief "deliver a config sql script to env0": 2,870 characters (FRESH procedure picked);
  - "open a PR on svc-rim": 2,033 characters (4 RULE lines printed whole);
  - "run the topup scenario on env1": 2,414 characters (FAILING procedure);
  - "ok, continue": 478 characters (false positive).
  - Each took about 0.2 s.
- **`build_context(budget=12000)`:** 14,354 characters in 0.01 s.
- **`search_notes("rate plan change fails 500")`:** 1 result in 0.10 s (the correct note).

## Appendix B — Test inventory (collected, not run)

931 tests in 69 modules. The files that best evidence behaviour:

| Area | Test files |
|---|---|
| Operational-memory acceptance | `test_operational_memory.py` (24; one per C-row, plus volume test C-20 with 1,000 executions) |
| Brief | `test_task_brief.py` (25; budget, exemptions, naming heuristics, prompt hook, "task text reaches no file"), `test_brief.py` (19) |
| Executions and procedures | `test_executions.py` (21), `test_procedures.py` (15), `test_lessons.py` (11), `test_tool_memory.py` (11) |
| Routing (189 total) | `test_routing_policy.py` (70; including the (level × registry) sweep) and 8 other routing files |
| Notes | `test_note_amend.py` (57), `test_note_cli.py` (22), `test_note_audit.py` (10), `test_note_skip.py`, `test_note_dedup.py` |
| Retrieval | `test_retrieval.py` (10; IDF, golden-set evaluation) |
| Telemetry | `test_telemetry.py` (16; including "a flag is recorded by name and never by value") |
| MCP | `test_mcp_index_tools.py` (7; roster pinned at 12), `test_mcp_notes.py`, `test_mcp_parent.py` |
| Index and extensions | `test_index.py` (34), `test_extensions.py` (15), `test_evidence.py` (8) |
| Java | `test_java_structure.py` (17), `test_rules.py` (16), `test_trace.py` (7) |
| Documents vs CLI | `test_documents_match_reality.py` (15; checks CLI commands and flags named in README and skill, **not** MCP tool names, which is why `get_history` slipped through) |
