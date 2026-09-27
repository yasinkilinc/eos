# Ruflo 3.45.0 — memory, sessions, execution tracking, learning, knowledge graph, persistence, failure/recovery (source-level audit)

**Repo:** ruvnet/ruflo (formerly claude-flow), shallow clone at HEAD `88955d9` ("Merge PR #3414 fix/pin-memory-3392", 2026-09-24). CLI package `@claude-flow/cli@3.45.0`, memory package `@claude-flow/memory@3.0.0-alpha.25`, neural package `@claude-flow/neural@3.0.0-alpha.9`.
**Method:** code read only; README/CLAUDE.md claims were checked against the source. No `node_modules` exists in the clone, so the internals of the external `agentdb`, `ruvector`, `@ruvector/*` and `agentic-flow` npm packages were **not inspectable**. Statements about them come only from how Ruflo calls them and from Ruflo's own comments.
**Scope:** focus area B (memory, sessions, execution tracking, learning, knowledge graph, persistence, failure/recovery). Routing, swarm, skills, hooks and MCP are covered in the sibling report `ruflo-runtime-routing.md`. They appear here only where they write to or read from memory.

**Path legend.** All paths are relative to the repo root.
- `cli/` = `v3/@claude-flow/cli/`
- `mem/` = `v3/@claude-flow/memory/`
- `neu/` = `v3/@claude-flow/neural/`
- `hlp/` = `v3/@claude-flow/cli/.claude/helpers/`. This is the copy that `ruflo init` installs into user projects. `hook-handler.cjs` and `intelligence.cjs` are byte-identical to the repo's own `.claude/helpers/`.

---

## 0. Executive verdict

1. **"Unified memory" is not unified.** ADR-006 is marked "Implemented" and claims a single MemoryService. At runtime there are at least four independent memory implementations and 20+ on-disk stores (§7). The **primary store depends on the platform:**
   - Linux/macOS with the native deps: `.swarm/agentdb-memory.db`.
   - Windows, or without the native deps: `.swarm/memory.db`.

   The nightly backup and the distillation worker are hardwired to `.swarm/memory.db` (`cli/src/services/memory-backup.ts:1-14`, `cli/src/services/worker-daemon.ts:1701-1705,1747-1752`). On the default native path they therefore miss the store that actually receives the writes.
2. **The default Claude Code integration uses none of the SQLite/vector machinery.** `ruflo init` installs hooks that call `hlp/hook-handler.cjs`, `hlp/intelligence.cjs`, `hlp/session.js` and `hlp/auto-memory-hook.mjs` (`cli/src/init/settings-generator.ts:288-400`). That path is JSON files, word-trigram Jaccard and PageRank. The confidence that it "learns" is **never used in retrieval scoring** (`hlp/intelligence.cjs:621`).
3. **Vector search on the default (native) path is brute-force cosine, not HNSW.**
   - `bridgeSearchEntries` scans the newest 1,000 rows (`cli/src/memory/memory-bridge.ts:1245-1251`). The code now admits this (#2922, `cli/src/memory/memory-initializer.ts:919-947`).
   - `memory_search` still returns the literal `backend: 'HNSW + sql.js'` (`cli/src/mcp-tools/memory-tools.ts:771`).
   - The "150x/12,500x" speed-up printed by `memory search --build-hnsw` is looked up from the entry count (`cli/src/commands/memory.ts:530`).
4. **"Self-learning SONA / LoRA / EWC++" in the product path is confidence nudging on JSON-stored text snippets:**

   | Label | What the code does |
   |---|---|
   | "RL" | `confidence += reward*0.1` |
   | "LoRA-style" | `confidence += 0.001*reward` |
   | "Fisher information" | squared embedding magnitude, admitted in the code |
   | trajectories | kept in-process only |

   Evidence: `cli/src/memory/intelligence.ts:246-420`; `cli/src/memory/ewc-consolidation.ts:21-29`.
   - `neural_train` embeds text, stores it and sets `accuracy = 1.0` (`cli/src/mcp-tools/neural-tools.ts:451-508`).
   - `neural train` without `--data` "trains" on hardcoded template sentences (`cli/src/commands/neural.ts:145-197`).
5. **The RETRIEVE→JUDGE→DISTILL→CONSOLIDATE ReasoningBank exists as a class in `@claude-flow/neural`.** Its JUDGE is rule-based, its DISTILL emits `"Apply a -> b -> c"` strings, and it lives in memory (`neu/src/reasoning-bank.ts:394-583,1116-1125`). The CLI does not use it (`cli/src/memory/neural-package-bridge.ts:4-15`). AgentDB's `MemoryConsolidation` controller is a **no-op stub** that says so (`mem/src/controller-registry.ts:1292-1304`).
6. **Several "controllers" are null by construction.** SemanticRouter, MutationGuard, AttestationLog, GNNService, RVFOptimizer, GuardedVectorBackend, agentMemoryScope and federatedSession all return null (`mem/src/controller-registry.ts:899-953,1100-1111`). HierarchicalMemory has "no version of agentdb where the native path actually works" (`:922-938`). Yet `bridgeStoreEntry` returns `guarded: true, cached: true, attested: true` unconditionally (`cli/src/memory/memory-bridge.ts:1132-1139`).
7. **The declared schema is mostly dead.** Only `memory_entries`, `vector_indexes`, `graph_edges` and `metadata` receive production writes. `patterns`, `pattern_history`, `trajectories`, `trajectory_steps`, `sessions` and `migration_state` in `.swarm/memory.db` have none (`cli/src/memory/memory-initializer.ts:302-490`; a repo-wide search finds only a verification test that inserts and deletes one pattern). `applyTemporalDecay` for `patterns` has no caller (`:2160`).
8. **TTL, owner and metadata are write-only or never written.**
   - `expires_at` is stored but never filtered on read or swept. `memory_cleanup` reads an `expiresAt` field that `listEntries` never returns (`cli/src/mcp-tools/memory-tools.ts:1423-1430` vs `memory-initializer.ts:3517-3520`).
   - `owner_id` is never inserted. `metadata` is always the literal `'{}'` (`memory-initializer.ts:3056`, `memory-bridge.ts:1046`).
9. **Session restore is largely fabricated.**
   - The MCP `hooks_session-restore` invents `originalSessionId = session-${Date.now()-86400000}` and "restored" counts from key-name substring counts (`cli/src/mcp-tools/hooks-tools.ts:2630-2649`).
   - `session_save` snapshots only the legacy JSON stores, not the SQLite memory (`cli/src/mcp-tools/session-tools.ts:107-138`).
10. **What is genuinely good and transferable (all deterministic, no ML):**

    | Idea | Evidence |
    |---|---|
    | Typed write-time provenance (ADR-323) | `memory-initializer.ts:27-38,269-271` |
    | Outcome-provenance tiers where proxy evidence never promotes | `cli/src/services/memory-distillation.ts:23-26,331-333` |
    | `resolved_source` honesty field on run records | `cli/src/ruvector/run-transcript-recorder.ts:25-35` |
    | Versioned decision/outcome JSONL ledgers joined by a task hash, with size rotation | `cli/src/ruvector/router-trajectory.ts:43-214` |
    | Incremental per-namespace distillation cursor with per-batch transactions | `memory-distillation.ts:189-207,320-393` |
    | Temporal-validity store with supersede-not-delete | `mem/src/tiered-memory.ts:296-407` |
    | RRF + recency + MMR retrieval pipeline | `mem/src/smart-retrieval.ts:467-557` |
    | Discriminative keyword learner over outcomes | `cli/src/services/learned-routing.ts:45-123` |
    | "Never shrink a curated index" rule | `mem/src/auto-memory-bridge.ts:510-543` |
    | O_EXCL lock with stale takeover and AsyncLocalStorage reentrancy | `memory-initializer.ts:3928-3983` |
11. **The engineering record is unusually honest in comments.** Hundreds of `#NNNN` comments document silent data-loss bugs, and there is a self-audit (`docs/reviews/intelligence-system-audit-2026-05-29.md`). The architecture, however, accreted rather than consolidated. That is the main lesson for EOS.

---

## 0.1 What actually runs — four memory implementations

| # | Implementation | Entry points | Storage | Used by default? |
|---|---|---|---|---|
| A | **Hook "intelligence" layer** (ADR-050) | `hlp/hook-handler.cjs` → `hlp/intelligence.cjs`, `hlp/session.js`; `hlp/auto-memory-hook.mjs` | `.claude-flow/data/*.json(l)`, `.claude-flow/sessions/current.json`, Claude Code `~/.claude/projects/<key>/memory/*.md` | **Yes.** Wired into Claude Code SessionStart, UserPromptSubmit, PostToolUse, SubagentStop, Stop, SessionEnd and PreCompact (`cli/src/init/settings-generator.ts:288-400`) |
| B | **CLI/MCP memory** | MCP `memory_*`, `hooks_*`, `agentdb_*`, `neural_*`, `session_*`, `task_*` → `cli/src/memory/memory-initializer.ts` (bridge-first) → `cli/src/memory/memory-bridge.ts` → `@claude-flow/memory` `ControllerRegistry` → external `agentdb` | `.swarm/agentdb-memory.db` (native) or `.swarm/memory.db` (sql.js), plus many JSON sidecars | Only when the agent calls MCP tools or the user runs CLI commands |
| C | **`@claude-flow/memory` library** | `MemoryService`/`UnifiedMemoryService`, `AgentDBAdapter`, `HybridBackend`, `SQLiteBackend`, `HNSWIndex`, `MemoryConsolidator`, `LearningBridge`, `MemoryGraph`, `RvfLearningStore`, `PersistentSonaCoordinator` | Caller-chosen | Mostly **not** wired into the CLI. The CLI uses only `ControllerRegistry` (plus `TieredMemoryStore` through it) and `smartSearch` |
| D | **`@claude-flow/neural`** | `NeuralLearningSystem`, `ReasoningBank`, `SONAManager`, 7 RL algorithms, MoE, FlashAttention | In-memory, with serialize/deserialize | CLI imports only `getFlashAttention`/`getMoERouter` (`cli/src/mcp-tools/hooks-tools.ts:131-135,466-476`); `neural-package-bridge.ts` is "Phase 1 … just proves the wiring" (`cli/src/memory/neural-package-bridge.ts:4-15`) |

**The default hook handler has gaps.**
- It has no handlers for `post-bash`, `pre-edit`, `compact-manual` or `compact-auto`. They fall through to `console.log('[OK] Hook: …')` (`hlp/hook-handler.cjs:366-583`), yet the settings generator registers `post-bash` and `pre-edit` (`cli/src/init/settings-generator.ts:307-342`). As a result:
  - Bash outcomes are never recorded on the default path;
  - PreCompact does nothing but run session-end.
- It `require`s `router.js`, `session.js` and `memory.js` (`hlp/hook-handler.cjs:235-238`). The shipped helpers dir has those files, but the repo's own `.claude/helpers/` has only `.cjs` variants. In the repo itself those modules silently load as `null` (`safeRequire` swallows the error, `:214-233`).

---

## 1. Memory architecture

### 1.1 API surface (IMPLEMENTED)

**MCP tools** (`cli/src/mcp-tools/memory-tools.ts`):

| Tool | Lines |
|---|---|
| `memory_store` | 431-527 |
| `memory_retrieve` | 529-596 |
| `memory_search` (optional `smart`) | 598-783 |
| `memory_delete` | 785+ |
| `memory_list` | 833+ |
| `memory_stats` | 890+ |
| `memory_migrate` | 958+ |
| `memory_import_claude` | 1003-1171 |
| `memory_bridge_status` | 1174-1267 |
| `memory_search_unified` | 1270-1373 |
| `memory_detailed-stats` | 1378-1401 |
| `memory_cleanup` | 1406-1460 |
| `memory_compress` (reports "nothing to compress") | 1464-1482 |
| `memory_export` | 1486-1537 |
| `memory_import` | 1541-1580 |

**Engine functions** (`cli/src/memory/memory-initializer.ts`), each trying the AgentDB bridge first and falling back to raw sql.js:

| Function | Lines |
|---|---|
| `storeEntry` | 2882-3099 |
| `searchEntries` | 3105-3387 |
| `listEntries` | 3416-3589 |
| `getEntry` (bumps `access_count`) | 3594-3735 |
| `deleteEntry` (soft delete) | 3741-3891 |
| `purgeNamespace` (hard delete) | 3987-4069 |

**Input bounds:**
- key ≤1,024 characters, value ≤1 MB, query ≤4,096 characters;
- shell metacharacters and `../` are rejected in keys and namespaces (`memory-tools.ts:61-88`);
- namespaces for purge must match `^[A-Za-z0-9._-]{1,128}$` (`memory-initializer.ts:3985`).

**The library API is separate** (`mem/src/types.ts:308-356`, `IMemoryBackend`): store, get, getByKey, update, delete, query, search, bulkInsert, bulkDelete, count, listNamespaces, clearNamespace, getStats, healthCheck. `MemoryService` adds storeEntry, semanticSearch, findSimilar, getOrCreate, appendContent, addTags, shareWith and getSharedWith (`mem/src/index.ts:600-767`).

### 1.2 Namespaces

- A namespace is a flat string with `UNIQUE(namespace, key)` (`memory-initializer.ts:285`). There is no hierarchy and no per-namespace ACL.
- `accessLevel` and `ownerId` exist only in the library type (`mem/src/types.ts:25-30,80-84`). The CLI has no read-side check on them.
- **Namespaces in use:** `default`, `pattern` **and** `patterns`, `session` **and** `sessions`, `claude-memories`, `auto-memory`, `feedback`, `commands`, `trajectories`, `causal-edges`, `hive-memory`, `hive-consensus`, `learnings`, `restored`, `benchmark`, `pretrain`, `tasks`, `transcript-archive`.
- **The names drift, with a concrete consequence.** The SCM intent router maps episodic → `sessions`, `trajectories`, `commands`, `feedback` … and semantic → `patterns` … (`cli/src/memory/scm-classifier.ts:65-77`). But:
  - `bridgeSessionEnd` writes to `session` (`memory-bridge.ts:2893`);
  - the pattern-store fallback writes to `pattern` (`:2177`).

  Intent-routed retrieval therefore misses those writes.
- The SCM routing itself is **advisory only**: it prints a suggestion and does not filter (`cli/src/commands/memory.ts:493-513`).
- `memory_search_unified` enumerates namespaces dynamically by listing up to 100k rows (`memory-tools.ts:1303-1333`).

### 1.3 Memory types — episodic/semantic/procedural/working (CLAIMED, not behaviorally distinct)

- **The library** has `MemoryType = 'episodic'|'semantic'|'procedural'|'working'|'cache'` (`mem/src/types.ts:15-20`). It is used only as a filter (`sqlite-backend.ts:374-377`) and in stats. `createDefaultEntry` defaults to `'semantic'` (`types.ts:709`).
- **The CLI schema** CHECK allows `semantic|episodic|procedural|working|pattern` (`memory-initializer.ts:254`). Every writer hardcodes `'semantic'`:
  - `memory-initializer.ts:3039,3044`;
  - `memory-bridge.ts:1009,1014`;
  - the one exception is the causal-edge fallback, which writes `'procedural'` (`memory-bridge.ts:2484`).

  `memory_store` exposes no `type` parameter (`memory-tools.ts:433-452`).
- **`TieredMemoryStore`** tiers are `working|episodic|semantic` (`mem/src/tiered-memory.ts:143`). They are three Maps with identical logic. There is no promotion or demotion between tiers, and recall is substring matching (`:376-407`).
- **Verdict:** the terms appear in code, but no behavior differs by type.

### 1.4 Storage backends

| Store | Engine | Written by | Notes |
|---|---|---|---|
| `.swarm/memory.db` | **sql.js (WASM) whole-image read-modify-write:** load the file, mutate, `db.export()`, rewrite the whole file | sql.js fallback path, `ensureSchemaColumns`, `getEntry` access bump | Optional AES-256-GCM "RFE1" encryption when `CLAUDE_FLOW_ENCRYPT_AT_REST=1` (`cli/src/fs-secure.ts:13-17,167`; `cli/src/encryption/vault.ts:44,57-61`). Guarded by an advisory `<db>.lock` (`memory-initializer.ts:3942-3983`) |
| `.swarm/memory.db` (same file) | **better-sqlite3, WAL** | `graph-edge-writer.ts`, `repairVectorIndexes`, `recoverMemoryDatabase`, backup | Two engines on one file. This caused corruption (#2431) and is now gated by a WAL-sidecar check (`memory-initializer.ts:80-86`) |
| `.swarm/agentdb-memory.db` | **better-sqlite3 via the external AgentDB** (AgentDB may itself fall back to sql.js) | the bridge (`memory-bridge.ts:156-160,199-473`) | Split out by #2786/#3155. `sibling-store.ts:1-13`: "The danger is not the split. It is a count that describes one file as though it described the memory." |

- **Which path runs.** Store and search try the bridge first (`memory-initializer.ts:2916-2936,3145-3150`). The bridge is disabled on Windows by default (#3024, `memory-bridge.ts:76-82`), or when `agentdb`/`better-sqlite3` is absent.
- **A failed bridge init latches off for the life of the process** (`memory-bridge.ts:57-67,459-466`).
- **If sql.js is unavailable,** `initializeMemoryDatabase` writes a **4 KB zero-filled buffer carrying only a SQLite header** and reports success with every feature `true` and tables listed as `"(pending)"` (`memory-initializer.ts:2006-2055`).

### 1.5 Vector index

- **Embedding storage.** `memory_entries.embedding` holds a **JSON text array** (`memory-initializer.ts:256-259`; `JSON.stringify(embedding)` at `:2988`). 384 floats as text is roughly 7–8 KB per row. The code mentions 185 MB images (`fs-secure.ts:48-50`).
- **Default native path.** A full SELECT, then brute-force cosine in JavaScript:
  - search: newest 1,000 active rows (`memory-bridge.ts:1245-1251`);
  - vector-only helper: newest 10,000 rows (`:1899-1905`).
- **sql.js fallback path:**
  1. RaBitQ 1-bit WASM prefilter (`@ruvector/rabitq-wasm`) with exact cosine rerank (`memory-initializer.ts:3179-3236`). The in-memory index is rebuilt only when the entry count drifts more than 20% (`cli/src/memory/rabitq-index.ts:33`), so up to about 20% of new rows are invisible to the prefilter. Without a provenance filter, any non-empty reranked set is returned. The SQL scan runs only when that set is empty, or when a provenance filter under-fills the page (`memory-initializer.ts:3228-3234`).
  2. HNSW through the **external** `@ruvector/core` `VectorDb` (optional dependency), persisted to `.swarm/hnsw.index` plus `hnsw.metadata.json` (`memory-initializer.ts:589-731`). Metadata is saved non-atomically on every add (`:736-747`).
  3. Brute force over `LIMIT 1000` with a keyword-coverage fallback (`:3293-3366`).
- **Library.** `mem/src/hnsw-index.ts` is a **genuine** TypeScript HNSW: random levels with `levelMult = 1/ln M`, heap-based layer search, neighbor selection, remove/rebuild, a serialize format with a magic header, and binary/scalar/product quantization (`:242-389,547-735,1159-1480`). It is used by `AgentDBAdapter`/`MemoryConsolidator`, **not by the CLI**.
- **Measured reality.** The self-audit measured HNSW at "peak 1.48× at N=20k; slower than brute force below N≈5k" (`docs/reviews/intelligence-system-audit-2026-05-29.md:52`). After fixes it reports 1.9–6.5× at N=20k (`:120`).

### 1.6 Embedding providers (IMPLEMENTED, local-first, with a mock fallback)

**The chain** (`memory-initializer.ts:2285-2468`):
1. `@huggingface/transformers` / `@xenova/transformers` `Xenova/all-MiniLM-L6-v2` (384-d, local ONNX, model fetched from the Hugging Face CDN on first use);
2. `agentic-flow/reasoningbank` `computeEmbedding` (768-d);
3. `ruvector` ONNX embedder;
4. `agentic-flow` embeddings (768-d);
5. a **deterministic hash** fallback (128-d, `generateHashEmbedding`, `:2657-2675`).

There is no OpenAI or other remote embedding API on this path.

**Mislabeling history.**
- AgentDB silently used **mock embeddings** labeled `Xenova/all-MiniLM-L6-v2` when `sharp` failed to build. The self-audit measured synonyms at −0.988 and unrelated text at +0.775 (audit `:44`).
- Now `generateEmbedding` returns `backend: 'onnx'|'mock'` (`memory-initializer.ts:2474-2504`).
- The bridge rejects any embedding that is not 384-d (`memory-bridge.ts:1762-1764`).
- A "rescue" monkey-patches AgentDB's embedder to the local chain only when that chain is real ONNX (`memory-bridge.ts:771-831`).
- An opt-in strict "no stubs" policy is available (`cli/src/memory/embedding-policy.ts`).

**Dimensions conflict across the codebase:**

| Where | Dimension |
|---|---|
| CLI | 384 |
| Library `MemoryService` default | 1,536 (`mem/src/index.ts:346`) |
| `@claude-flow/neural` ReasoningBank | 768 (`neu/src/reasoning-bank.ts:106`) |
| `RvfLearningStore` | 64 (`mem/src/rvf-learning-store.ts:78`) |

### 1.7 Hybrid search

| Path | Mechanism | Status |
|---|---|---|
| Bridge (default) | Per-query JS "BM25" over the 1,000 candidates; term frequency counts **substring** matches (`w.includes(term)`, `memory-bridge.ts:511`), normalized `/10` and capped; `lexical = max(bm25, coverage)`; `score = max(0.6·semantic + 0.4·lexical, semantic)` (`:1257-1312`). The docstring says "reciprocal rank fusion" (`:1174`); it is a **weighted blend** | IMPLEMENTED (naming overstated) |
| Library `SQLiteBackend.searchKeyword` | FTS5 `porter unicode61` with BM25 rank, and a LIKE fallback (`mem/src/sqlite-backend.ts:308-346,737-745`). **Bug:** `bulkInsert` → `storeSync` never mirrors into FTS5, and `bulkDelete` never deletes from it (`:476-506,783-818`) | IMPLEMENTED (library only) |
| Library `hybridSearch` controller | Three-arm RRF (k=60) over dense, FTS5 and a regex entity arm, then MMR (`mem/src/controller-registry.ts:771-897`; `mem/src/entity-tagger.ts:31-80`) | IMPLEMENTED, auto-enabled only when a `memoryService` is registered (`mem/src/controller-registry.ts:628-632`), **which the CLI never does**: the bridge's `initialize` config passes none (`memory-bridge.ts:234-249`) |
| `smartSearch` (ADR-090) | Template query expansion (≤3 variants), RRF, recency boost (`score·(1+0.2·0.5^(age/30d))`), MMR (λ=0.7, cosine or token-Jaccard), session round-robin (`mem/src/smart-retrieval.ts:132-557`) | IMPLEMENTED; opt-in `memory_search {smart:true}`. The MCP raw search passes **no timestamps or session metadata**, so the recency and session phases are inert (`memory-tools.ts:666-689`) |
| CLI `neural_patterns` search | Multi-field BM25, cosine, MMR, optional cross-encoder (`Xenova/ms-marco-MiniLM-L-6-v2`) and a Lucene-style BM25 with a Porter stemmer (`cli/src/memory/hybrid-retrieval.ts`, `lucene-bm25.ts`, `cross-encoder-rerank.ts`) | IMPLEMENTED; measured on BEIR NFCorpus (ADR-085..091, including an honest negative RRF result in ADR-087) |

### 1.8 TTL, compression, dedup, confidence, provenance, timestamps

- **TTL is INERT.** `ttl` is accepted, and `expires_at = now + ttl·1000` is written (`memory-initializer.ts:3060`; `memory-bridge.ts:1049`). No read path filters `expires_at`: a repo-wide search finds `expires_at` only in DDL and INSERTs in the memory modules. `listEntries` does not select it (`memory-initializer.ts:3517-3520`, `memory-bridge.ts:1422-1428`), so `memory_cleanup`'s filter on `e.expiresAt` never matches (`memory-tools.ts:1425-1430`). The library `MemoryConsolidator.sweepExpired` does sweep TTL, but only on `AgentDBAdapter`'s in-process maps (`mem/src/consolidator.ts:117-158`).
- **Compression and summarization.**
  - None for memory rows. `memory_compress` states "nothing to compress" (`memory-tools.ts:1464-1482`).
  - Search responses **truncate content to 60 characters** and ids to 12 (`memory-initializer.ts:3214-3216,3357-3359`; `memory-bridge.ts:1321-1323`).
  - `memory_search_unified` truncates to 200 characters (`memory-tools.ts:1344`).
  - The only summarizer is the deterministic 4-field "structured distill" (§5.6).
- **Dedup.**
  - `UNIQUE(namespace,key)`: strict insert fails with a typed error; upsert is `INSERT OR REPLACE`. Upsert **resets `created_at` and `access_count`** because both are re-supplied or defaulted (`memory-bridge.ts:1004-1009,1042-1050`).
  - Soft-deleted tombstones are resurrected in place on strict insert (`memory-bridge.ts:987-1028`).
  - `memory_import_claude` dedups by file-content sha256 (`memory-tools.ts:1092-1108`).
  - AutoMemoryBridge dedups by section sha256 (`mem/src/auto-memory-bridge.ts:403-408,945-947`).
  - The hook store dedups by FNV-1a content fingerprint. The audit found 5,706 entries with ~20 unique (`hlp/intelligence.cjs:150-210`).
  - `LocalReasoningBank` dedups by exact content (`cli/src/memory/intelligence.ts:469-518,582-595`).
  - `compactPatterns` removes pairs with cosine ≥0.95 (`intelligence.ts:1506-1571`).
  - The library consolidator dedups by sha256 **across all namespaces**, plus embedding near-duplicates at cosine ≥0.95 (`mem/src/consolidator.ts:256-323`). That can collapse identical content living in different namespaces.
- **Confidence.** There is none on `memory_entries`. It exists only on the various pattern stores (§5).
- **Provenance** is covered in §9.
- **Timestamps.** `created_at`, `updated_at` and `last_accessed_at` are epoch milliseconds (`memory-initializer.ts:274-277`). `getEntry` bumps `access_count`; on the sql.js path that is a **whole-image rewrite for a read** (`:3649-3699`). Nothing ranks or prunes by `access_count`.

---

## 2. Exact schemas

### 2.1 `.swarm/memory.db` — `MEMORY_SCHEMA_V3` (`cli/src/memory/memory-initializer.ts:235-561`)

**`memory_entries`** (`:249-286`)

| Column | Type / default |
|---|---|
| `id` | TEXT PK |
| `key` | TEXT NOT NULL |
| `namespace` | TEXT DEFAULT 'default' |
| `content` | TEXT NOT NULL |
| `type` | TEXT DEFAULT 'semantic', CHECK in (semantic, episodic, procedural, working, pattern) |
| `embedding` | TEXT (JSON array) |
| `embedding_model` | TEXT DEFAULT 'local' |
| `embedding_dimensions` | INTEGER |
| `tags` | TEXT (JSON) |
| `metadata` | TEXT (JSON) |
| `owner_id` | TEXT |
| `provenance_type` | TEXT DEFAULT 'unknown', CHECK in (user_claim, agent_output, system_observation, tool_result, unknown) |
| `created_at`, `updated_at` | INTEGER ms |
| `expires_at`, `last_accessed_at` | INTEGER |
| `access_count` | INTEGER DEFAULT 0 |
| `status` | TEXT DEFAULT 'active', CHECK in (active, archived, deleted) |

Constraint: `UNIQUE(namespace,key)`. Indexes on namespace, key, type, status, created_at, last_accessed_at and owner_id (`:289-295`).

**`patterns`** (`:302-348`) — **never written in production.**
- Identity and definition: `id`, `name`, `pattern_type` (CHECK task-routing | error-recovery | optimization | learning | coordination | prediction | code-pattern | workflow), `condition`, `action`, `description`.
- Scoring: `confidence` REAL DEFAULT 0.5, `success_count`, `failure_count`, `decay_rate` DEFAULT 0.01, `half_life_days` DEFAULT 30.
- Embedding: `embedding`, `embedding_dimensions`.
- Versioning: `version`, `parent_id` → `patterns(id)`.
- Other: `tags`, `metadata`, `source`, `created_at`, `updated_at`, `last_matched_at`, `last_success_at`, `last_failure_at`, `status` (active | archived | deprecated | experimental).

**`pattern_history`** (`:357-374`) — never written. Columns: `id` AUTOINCREMENT, `pattern_id`, `version`, `confidence`, `success_count`, `failure_count`, `condition`, `action`, `change_type` (created | updated | success | failure | decay | merged | split), `change_reason`, `created_at`.

**`trajectories`** (`:383-405`) — never written. Columns: `id`, `session_id`, `status` (active | completed | failed | abandoned), `verdict` (success | failure | partial | NULL), `task`, `context` (JSON), `total_steps`, `total_reward`, `started_at`, `ended_at`, `extracted_pattern_id` → `patterns`.

**`trajectory_steps`** (`:408-422`) — never written. Columns: `id`, `trajectory_id`, `step_number`, `action`, `observation`, `reward`, `metadata`, `created_at`.

**`migration_state`** (`:431-464`) — never written; `checkAndMigrateLegacy` only *detects* legacy DBs, `:1405-1461,1881-1887`. Columns: `id`, `migration_type`, `status` (pending … rolled_back), `total_items`, `processed_items`, `failed_items`, `skipped_items`, `current_batch`, `last_processed_id`, `source_path`, `source_type`, `destination_path`, `backup_path`, `backup_created_at`, `last_error`, `errors`, `started_at`, `completed_at`, `created_at`, `updated_at`.

**`sessions`** (`:471-490`) — never written. Columns: `id`, `state` (JSON), `status` (active | paused | completed | expired), `project_path`, `branch`, `tasks_completed`, `patterns_learned`, `created_at`, `updated_at`, `expires_at`.

**`vector_indexes`** (`:497-520`) — metadata only. Columns: `id`, `name` UNIQUE, `dimensions`, `metric`, `hnsw_m` 16, `hnsw_ef_construction` 200, `hnsw_ef_search` 100, `quantization_type`, `quantization_bits`, `total_vectors`, `last_rebuild_at`, timestamps. It is seeded with `default` and `patterns` at 384 dimensions (`:1262-1264`), and one row is added per namespace on each store (`:3026-3031`).

**`graph_edges`** (`:530-545`, re-created by `cli/src/memory/graph-edge-writer.ts:164-183`)

| Column | Meaning |
|---|---|
| `id` | `edge-uuid` |
| `source_id`, `target_id` | domain-prefixed node ids `{mem\|agent\|task\|entity\|span\|pattern}:…` |
| `relation` | edge label |
| `weight` | REAL 1.0 |
| `confidence` | REAL 1.0; the comment says "updated by JUDGE step" |
| `decay_rate` | REAL 0; the comment says "per-day exponential decay applied at read time" |
| `last_reinforced` | TEXT; the comment says "set when CONSOLIDATE re-touches edge" |
| `witness_id` | TEXT |
| `embedding_ref` | `inline:{base64}` or `vector_indexes:{id}` |
| `metadata` | JSON |
| `created_at` | TEXT |

**Nothing ever UPDATEs or DELETEs `graph_edges`** (repo-wide search). The confidence, decay and reinforce semantics exist only in comments.

**`metadata`**: key PK, value, updated_at. Seeded with `schema_version='3.0.0'`, `backend`, `sql_js` and feature flags that are all `'enabled'` (`:1246-1256`).

### 2.2 `.swarm/agentdb-memory.db`

**Bridge `memory_entries`** (`memory-bridge.ts:671-714`):
- Same columns as §2.1, **without the CHECK constraints**.
- `provenance_type` is added by `ALTER` for older databases.
- Indexes on namespace, key and status only.
- Default `type` 'semantic'.

**AgentDB's own tables** are defined in the external package. As mirrored by Ruflo's tests and written by distillation (`cli/__tests__/memory-distillation.test.ts:24-50`; `cli/src/services/memory-distillation.ts:209-221`):

| Table | Columns |
|---|---|
| `episodes` | `id`, `ts`, `session_id`, `task`, `input`, `output`, `critique`, `reward`, `success`, `latency_ms`, `tokens_used`, `tags`, `metadata`, `created_at` |
| `episode_embeddings` | `episode_id`, `embedding` BLOB, `embedding_model` |
| `reasoning_patterns` | `id`, `ts`, `task_type`, `approach`, `success_rate`, `uses`, `avg_reward`, `tags`, `metadata` |
| `pattern_embeddings` | `pattern_id`, `embedding` BLOB |
| `causal_edges` | `id`, `from_memory_id`, `from_memory_type`, `to_memory_id`, `to_memory_type`, `similarity`, `confidence`, `mechanism`, `metadata`, `created_at` |
| `distill_state` | `namespace` PK, `last_rowid`, `last_run_at` (`memory-distillation.ts:190-194`) |
| `tiered_memory` | `id`, `key`, `value`, `tier`, `ts`, `valid_from`, `valid_until`, `superseded_by`, `archived` (`mem/src/tiered-memory.ts:101-115`) |

### 2.3 Other SQLite schemas

- **Library `SQLiteBackend`** (`mem/src/sqlite-backend.ts:693-746`):
  - `memory_entries` (with `version`, `access_level`, `"references"`, no provenance);
  - `memory_embeddings(entry_id, embedding BLOB)`;
  - `memory_fts` FTS5 (porter unicode61).
- **Dormant helper `learning-service.mjs`** (`hlp/learning-service.mjs:81-165`, file `.claude-flow/learning/patterns.db`; not wired into default settings):
  - `short_term_patterns` and `long_term_patterns`, each with strategy, domain, embedding BLOB, quality, usage_count and success_count; long-term adds promoted_at, source_pattern_id and quality_history;
  - `hnsw_index`, `trajectories`, `learning_metrics`, `session_state`.
  - Promotion rule: 3 uses and quality ≥0.6; short-term max age 24 h; consolidation every 30 minutes (`:43-75`).
  - Its "HNSW" is a single-layer greedy graph (`:265-292`).
- **Repo-dev-only transcript archive** (ADR-051), `.claude/helpers/context-persistence-hook.mjs:109-140`. It is **not installed by `ruflo init`**; no reference to it exists under `cli/` or `plugins/`.
  - `transcript_entries`: id, key, content, type 'episodic', namespace, tags, metadata, access_level, created/updated_at, version, access_count, last_accessed_at, `content_hash`, `session_id`, `chunk_index`, `summary`, plus `confidence` 0.8 and `embedding` BLOB.
  - Restore budget 4,000 tokens; retention 30 days (`:43-48`).

### 2.4 JSON record shapes

**`LocalReasoningBank` pattern** (`.claude-flow/neural/patterns.json`, `cli/src/memory/intelligence.ts:120-130`):
```
{id, type, embedding:number[], content, confidence, usageCount, createdAt, lastUsedAt, metadata?}
```
Its companion `stats.json` holds `{trajectoriesRecorded, patternsLearned, signalsProcessed, lastAdaptation}` (`:779-824`).

**`SONAOptimizer` pattern** (`.swarm/sona-patterns.json`, `cli/src/memory/sona-optimizer.ts:44-59`):
```
{keywords[], agent, confidence, successCount, failureCount, lastUsed, createdAt}
```
The key is sorted keywords plus the agent.

**Neural store** (`.claude-flow/neural/models.json`, `cli/src/mcp-tools/neural-tools.ts:216-245`):
```
models{id, name, type, status, accuracy, trainedAt, epochs, config}
patterns{id, name, type, embedding, content?, metadata, createdAt, usageCount}
```

**Routing outcome** (`.claude-flow/routing-outcomes.json`, `cli/src/services/learned-routing.ts:12-19`):
```
{task, agent, success, quality, keywords[], timestamp}
```
Capped at the last 500 (`cli/src/mcp-tools/hooks-tools.ts:205-211`).

**Hook store entry** (`.claude-flow/data/auto-memory-store.json`, created through `createDefaultEntry`, `mem/src/types.ts:703-723`): the full library `MemoryEntry`, i.e. id `mem_<ts36>_<rand>`, key `auto-memory:<file>:<heading>`, and `metadata{sourceFile, heading, importedAt, contentHash}`. Derived files:
- `graph-state.json`: `{version, updatedAt, nodeCount, contentFingerprint, nodes{id,category,confidence,accessCount,createdAt}, edges[{sourceId,targetId,type:'temporal'|'similar',weight}], pageRanks{}}`;
- `ranked-context.json`: `{version, computedAt, entries[{id,content,summary,category,confidence,pageRank,accessCount,words[]}]}`;
- `pending-insights.jsonl`: `{type:'edit', file, success, timestamp, sessionId}`;
- `intelligence-snapshot.json`: the last 50 snapshots (`hlp/intelligence.cjs:538-596,663-685,901-939`).

**Session file** (`.claude-flow/sessions/current.json`, `hlp/session.js:27-146`):
```
{id, startedAt, cwd, context{…}, metrics{edits,commands,tasks,errors}, restoredAt?, updatedAt?, endedAt?, duration?}
```
It is archived to `sessions/<id>.json` at end.

**MCP `SessionRecord`** (`cli/src/mcp-tools/session-tools.ts:21-37`):
```
{sessionId, name, description?, savedAt, stats{tasks,agents,memoryEntries,totalSize}, data?{memory,tasks,agents}}
```

**`TaskRecord`** (`.claude-flow/tasks/store.json`, `cli/src/mcp-tools/task-tools.ts:17-30`):
```
{taskId, type, description, priority, status(pending|in_progress|completed|failed|cancelled), progress, assignedTo[], tags[], createdAt, startedAt, completedAt, result?}
```

**MCP trajectory** (memory namespace `trajectories`, key `trajectory-<id>`, `cli/src/mcp-tools/hooks-tools.ts:494-509,3140-3156`):
```
{id, task, agent, steps[{action,result,quality,timestamp}], startedAt, success, endedAt, feedback}
```

**Router trajectory JSONL** (`.swarm/model-router-trajectories.jsonl`, opt-in, `cli/src/ruvector/router-trajectory.ts:43-109`):
- decision row: `{v:1, type:'decision', ts, task_hash, task≤500, embedding?, complexity, model, confidence, uncertainty, routed_by, …}`;
- outcome row: `{v:1, type:'outcome', ts, task_hash, quality, scores?, source?, tokens?, cost_usd?, model_id?}`.

**Run transcript JSONL** (`.swarm/run-transcripts.jsonl`, opt-in, `cli/src/ruvector/run-transcript-recorder.ts:46-99`):
```
{v:1, ts, instance_id, task_hash, model, tier, resolved, resolved_source('gold-oracle'|'output-verifier'|'api-success'|'external'), messages[], model_patch, sample?, source?, tokens?, cost_usd?}
```

**`RvfLearningStore`** (library only; the name notwithstanding, it is **JSON lines with a `RVLS` header, not the binary RVF format**, `mem/src/rvf-learning-store.ts:1-12,30-73`). Records `{type:'pattern'|'lora'|'ewc'|'trajectory', data}`; `PatternRecord` is `{id,type,embedding,successRate,useCount,lastUsed}`; `TrajectoryRecord` is `{id, steps[{type,input,output,durationMs,confidence}], outcome, durationMs, timestamp}`. The whole file is rewritten via tmp + rename (`:256-291`).

---

## 3. Session lifecycle

### 3.1 Default hook path (IMPLEMENTED, shallow)

| Claude Code event | What runs | What persists |
|---|---|---|
| SessionStart | `hook-handler.cjs session-restore` → `session.restore()` (or `start()`) → `intelligence.init()`; then `auto-memory-hook.mjs import` | `current.json` gets `restoredAt`. If a previous session never ended, **that stale session is "restored"** and keeps its id and counters (`hlp/session.js:50-70`). `init()` rebuilds the graph, PageRank and ranked-context (`hlp/intelligence.cjs:467-596`). The import copies Claude Code `MEMORY.md` sections into `auto-memory-store.json` with sha256 dedup (`mem/src/auto-memory-bridge.ts:377-451`) |
| UserPromptSubmit | `route` → `intelligence.getContext(prompt)` | Prints ≤5 lines of `[INTELLIGENCE] Relevant patterns…` (score = 0.6·trigram-Jaccard + 0.4·PageRank, threshold 0.05, 80-character summaries) into the prompt context (`hlp/intelligence.cjs:602-657`); writes `lastMatchedPatterns` to `current.json`; "implicit success" +0.03 for previously matched ids |
| PostToolUse Write/Edit | `post-edit` | `metrics.edits++`; appends `{type:'edit', file, success, timestamp, sessionId}` to `pending-insights.jsonl`. `sessionId` is read from `context.sessionId`, which nothing sets, so it is always null (`hlp/intelligence.cjs:663-685` vs `hlp/session.js:28-41`) |
| SubagentStop | `post-task` → `intelligence.feedback(!toolFailed)` | ±0.05 / −0.02 on `ranked-context.json` and `graph-state.json` for the last matched ids (`hlp/intelligence.cjs:691-725`) |
| Stop | `auto-memory-hook.mjs sync` | `syncToAutoMemory()` plus `curateIndex()`: **writes into the user's Claude Code memory dir** (`~/.claude/projects/<key>/memory/{patterns,debugging,…}.md` and `MEMORY.md`, or `MEMORY.generated.md` when the index would shrink) (`mem/src/auto-memory-bridge.ts:295-547`) |
| SessionEnd / PreCompact | `session-end` → `intelligence.consolidate()` then `session.end()` | Consolidate: frequent-edit (≥3) insight entries, a −0.005·⌊days⌋ decay for never-accessed nodes, graph and PageRank rebuild, snapshot history (`hlp/intelligence.cjs:731-897`). End: archives `current.json` to `sessions/<id>.json` and deletes it (`hlp/session.js:72-92`). **Every compaction therefore ends the session** |

**What a new session gets back:** re-injected, trigram-matched titles of the user's own Claude Code `MEMORY.md` sections (which Claude Code already loads natively), plus "frequent edit" notes. There is no summary of the previous session, no open tasks and no decisions.

### 3.2 MCP/CLI path (PARTIALLY FABRICATED)

- **`hooks_session-start`** (`cli/src/mcp-tools/hooks-tools.ts:2368-2503`):
  - initializes intelligence;
  - calls `bridgeSessionStart`, which calls `reflexion.startEpisode` if present and searches namespace `session` for `options.context || 'session patterns'` (limit 10, threshold 0.2). It only **counts** the results as `restoredPatterns` and injects nothing (`cli/src/memory/memory-bridge.ts:2802-2848`);
  - appends a `"Session started: <id>"` entry to `auto-memory-store.json`;
  - returns `previousSession: { id: session-${Date.now()-86400000}, … }`, a **fabricated id** (`hooks-tools.ts:2496-2500`).
- **`hooks_session-end`** (`:2506-2609`):
  - requires `.claude-flow/sessions/current.json` and throws without it;
  - computes activity: completed tasks in the window from `tasks/store.json`, pattern-like entries in `.claude-flow/memory/store.json`, and `metrics.*`;
  - builds the summary string `"N tasks completed; N patterns learned; N edits recorded; N commands recorded; N errors recorded; duration N minutes"` (`:637-649`);
  - `bridgeSessionEnd` upserts `{sessionId, summary, tasksCompleted, patternsLearned, endedAt}` into namespace `session` under key `session-<id>` (`memory-bridge.ts:2853-2915`), then calls `nightlyLearner.consolidate` if that controller exists.
- **`hooks_session-restore`** (`hooks-tools.ts:2612-2653`): `originalSessionId` is fabricated for "latest". `tasksRestored = min(#keys containing 'task', 10)` and `agentsRestored = min(#keys containing 'agent', 5)`, taken from the legacy JSON store. **Nothing is restored.**
- **`session_save` / `session_restore` / `session_export` / `session_import`** (`cli/src/mcp-tools/session-tools.ts:142-558`):
  - save snapshots `.claude-flow/memory/store.json` (the **legacy** JSON store), `tasks/store.json` and `agents/store.json` into `.claude-flow/sessions/session-<ts>-<rand>.json` (optionally encrypted);
  - restore writes those JSON files back and re-`storeEntry`s memory entries into namespace `restored`;
  - **the SQLite memory stores are not part of any session snapshot.**
- `session_list` reconciles the two shapes that share the same directory: `{sessionId, savedAt}` from `session_save` and `{id, startedAt}` from `session.js` (`:319-339`).

**Verdict.** Session persistence is metric counters plus a JSON-snapshot mechanism for legacy stores. "Restore" means reloading JSON or reporting invented counts. There is no context re-hydration from the vector store.

---

## 4. Execution tracking

There is **no unified execution ledger.** Fragments:

| Record | Where | Fields | Written by | Read back / used? |
|---|---|---|---|---|
| Routing outcome | `.claude-flow/routing-outcomes.json` (full JSON rewrite, last 500) | task, agent, success, quality, keywords, timestamp | MCP `hooks_post-task` when `task` and `agent` are given (`cli/src/mcp-tools/hooks-tools.ts:1775-1798`) | **Yes.** `buildLearnedRoutingPatterns` becomes learned keyword→agent intents in the semantic router (`hooks-tools.ts:228-245`), and `suggestAgentsForTask` uses keyword overlap ≥2 (`:841-861`). This is the one closed loop, but only on the MCP path |
| Feedback entry | memory namespace `feedback`, key `feedback-<taskId>` | JSON of the options: taskId, task, success, quality, agent, duration, patterns, parentAgentId, depth | `bridgeRecordFeedback` (`memory-bridge.ts:2433-2444`) | Daemon distillation treats the `feedback` namespace as `oracle:test-exec` ground truth (`memory-distillation.ts:297-306`). Post-edit writes quality 0.85 or 0.3 with success defaulting to true (`hooks-tools.ts:966,976-981`), so "execution-tier ground truth" is **mostly defaults** |
| Trajectory | memory namespace `trajectories` | §2.4 | MCP trajectory-start (pending) and trajectory-end (final) (`hooks-tools.ts:3009-3021,3140-3160`) | Steps live **only in the in-process `activeTrajectories` Map** between start and end (`:512`). A restart or a separate CLI process loses them, and end then reports "Trajectory not found" |
| Edit log | `.claude-flow/data/pending-insights.jsonl` | type, file, success, timestamp, sessionId (always null) | default post-edit hook | Consolidated at session end into "frequently edited file" entries, then **truncated** (`hlp/intelligence.cjs:748-786`) |
| Command outcome | memory namespace `commands`, key `cmd-<ts>` | `{command, exitCode, success}` | MCP `hooks_post-command` only (`hooks-tools.ts:1096-1118`) | Distilled as `proxy:structural`. The default hook path records nothing for Bash (§0.1) |
| Task status board | `.claude-flow/tasks/store.json` | §2.4 | `task_*` MCP tools (`cli/src/mcp-tools/task-tools.ts`) | Status only. No tool calls, no files, no timings besides timestamps. `task_retry` clones the record with tag `retry-of:<id>` (`:490-533`). No locking (load → mutate → `writeFileSync`). The descriptions claim "completion analytics in the .swarm/memory.db" and "dependency tracking" (`:73`); there is no such field or storage |
| Router decision/outcome | `.swarm/model-router-trajectories.jsonl` (opt-in `CLAUDE_FLOW_ROUTER_TRAJECTORY=1`) | §2.4 | model router | `pairTrajectoryRows` rebuilds training rows (latest wins per hash; drops unmatched and no-embedding rows) (`router-trajectory.ts:326-405`) for KRR/bandit retraining |
| Run transcript | `.swarm/run-transcripts.jsonl` (opt-in `CLAUDE_FLOW_RUN_TRANSCRIPTS=1`) | full messages, `model_patch`, `resolved` plus `resolved_source` | agent execution path | exported for SFT/DPO (weight-eft) |
| Hive-mind consensus history | `.claude-flow/hive-mind/state.json` plus memory namespace `hive-consensus` | proposals, votes, result | `hive-mind_consensus` | display |

**What is missing:** a single record that joins task → agent → tool calls → files touched → timings → verdict. None of the default paths records tool calls or files per task. Only the opt-in transcript recorder captures messages and patches.

**Where execution data feeds learning:**
1. routing outcomes → learned routing (real, deterministic, `cli/src/services/learned-routing.ts:45-123`);
2. trajectory-end → `SONAOptimizer` keyword patterns and Q-learning update (`cli/src/memory/sona-optimizer.ts:306-394`);
3. feedback/commands namespaces → daemon distillation into `reasoning_patterns` (opt-in daemon).

---

## 5. Learning and pattern storage

### 5.1 How many "pattern" stores? At least six, each with its own schema

1. `.claude-flow/neural/patterns.json`: `LocalReasoningBank`, the "SONA/ReasoningBank" of the MCP path (`cli/src/memory/intelligence.ts:446-732`). **If the project has no `.claude-flow` dir it falls back to `~/.claude-flow/neural`**, so projects share one global pattern file (`:29-40`).
2. `.swarm/sona-patterns.json`: `SONAOptimizer` keyword→agent patterns (`cli/src/memory/sona-optimizer.ts:124-131,306-394`).
3. `.claude-flow/neural/models.json` `patterns{}`: `neural_train` / `neural_patterns` (`cli/src/mcp-tools/neural-tools.ts:212-355`).
4. AgentDB `reasoning_patterns` + `pattern_embeddings` (via `ReasoningBank.storePattern` since #3327, or daemon distillation), with a fallback to memory namespace `pattern` (`memory-bridge.ts:2129-2221`).
5. `.claude-flow/data/ranked-context.json` / `graph-state.json`: the hook intelligence layer.
6. `.claude-flow/routing-outcomes.json`: learned routing intents.

In addition, the `patterns` SQL table (never written), `.claude-flow/learning/patterns.db` (dormant helper) and the library `RvfLearningStore`/`PersistentSonaCoordinator` (not used by the CLI).

### 5.2 How patterns are created

- **Default hook path.** Nothing "learns" new content except `consolidate()`'s "File X was edited N times" entries. Imports mirror Claude Code `MEMORY.md`.
- **MCP path.** `intelligence.recordTrajectory(steps, verdict)` (`intelligence.ts:1116-1199`) is called from post-edit, post-command, post-task, trajectory-end, bridge feedback, `hooks_task-completed` and `neural train`:
  - on `verdict === 'success'`, **every step is stored as a new pattern with confidence 0.8** (dedup by exact content, `:1153-1168`);
  - `recordStep` stores **every step at confidence 1.0** (`:1082-1091`);
  - post-edit synthesizes the step `"Edit <file> by <agent>: success"`, with success defaulting to `true` (`hooks-tools.ts:966,994-1002`);
  - `neural train` without `--data` records canned template sentences ("Route task to coder agent…") as steps and trajectories (`cli/src/commands/neural.ts:145-197,313-329`).
- **Explicit.** `hooks_intelligence_pattern-store`, `agentdb_pattern-store` and `neural_patterns store` (embed and store).
- **Daemon** (`consolidate` worker, every 30 minutes, 1,000 rows per tick, `cli/src/services/worker-daemon.ts:140-169`):
  - greedy cosine clustering (distance 0.2) of `memory_entries` per namespace;
  - the 4-field structural distill gives episode, pattern and a weak "co-occurrence" edge;
  - provenance `oracle:test-exec` for the `feedback` namespace and `proxy:structural` otherwise; proxy is **never promoted** (`cli/src/services/memory-distillation.ts:268-393`);
  - the cursor is `distill_state(namespace,last_rowid)`. This is the most carefully designed learning component in the repo.

### 5.3 How patterns are retrieved

- `findSimilar`: brute-force cosine over all patterns. Threshold 0.5, or 0.1 when the query embedding is 128-d, i.e. the hash fallback. **Retrieval increments `usageCount`/`lastUsedAt` (read-side mutation)** (`intelligence.ts:626-654,1235-1244`).
- AgentDB ReasoningBank `searchPatterns({task,k,threshold})`, mapped from `approach`/`similarity` (`memory-bridge.ts:2239-2259`).
- Hook path: `getContext` (§3.1).
- Library `LearningBridge.findSimilarPatterns` uses a **hash embedding** (`mem/src/learning-bridge.ts:353-375,502-527`).

### 5.4 "Training" — what actually happens

| Claim | Code | Verdict |
|---|---|---|
| SONA "RL updates" | `endTrajectory`: reward success 1.0 / partial 0.5 / failure −0.5; for each step embedding, the top-3 similar patterns (cos ≥0.3) get `confidence += reward·0.1` (`intelligence.ts:246-296`) | Heuristic confidence nudge |
| "LoRA-style distillation with EWC++" | `distillLearning`: for the last 10 successful in-process trajectories, `confidence += 0.001·reward` (loraLearningRate), damped by an EWC "penalty" (`:306-420`). EWC's Fisher is `Σ embedding²`, admitted as a "HEURISTIC IMPORTANCE PROXY" (`cli/src/memory/ewc-consolidation.ts:21-29`) | Named after ML; is scalar bookkeeping |
| Trajectory history | `LocalSonaCoordinator.trajectories` is an in-process array. The unified stats label it "in-memory, resets per process" (`intelligence.ts:162,962`). Hooks are separate processes, so distillation sees ~1 trajectory | Effectively no cross-session trajectory learning |
| SONA optimizer | success: `c += 0.1(1−c)`; failure: `c −= 0.15c`; clamp [0.1, 0.99]; max 1,000 patterns; also a Q-learning update with reward +1 / −0.5 (`sona-optimizer.ts:124-131,342-373`) | Real, simple, deterministic |
| Q-learning router | Tabular TD over feature-hashed states, ε from 1.0 to 0.01, replay buffer, `.swarm/q-learning-model.json` (`cli/src/ruvector/q-learning-router.ts:12,143-154,298-331`). Real mechanics; see the sibling report for routing quality | Real |
| `neural_train` | Embeds training texts and stores them as patterns; `accuracy = patternsStored > 0 ? 1.0 : 0` ("accuracy = data stored") (`neural-tools.ts:451-508`) | Not training |
| `neural train` (CLI) | InfoNCE contrastive loss where anchor, positive and negatives are chosen **by batch position**, not semantics, plus WASM MicroLoRA `trainPattern`. The self-audit found MicroLoRA `apply()` inert (Δ = 0) (`cli/src/commands/neural.ts:283-311`; audit `:42,121`) | Mechanically real; the supervision signal is meaningless |
| `@claude-flow/neural` RL algorithms | PPO, DQN, A2C and others with real (tiny) forward/backward in TypeScript (`neu/src/algorithms/dqn.ts:135-170`) | Real code, **not wired** to the CLI learning path |
| ReasoningBank 4-step (neural pkg) | RETRIEVE (MMR over cosine), JUDGE (`qualityScore ≥ 0.6 && positiveRatio > 0.6`, rule-based, "could be enhanced with LLM-as-judge"), DISTILL (`strategy = "Apply a -> b -> c"`), CONSOLIDATE (O(n²) dedup ≥0.95, "contradictions" at cos > 0.8 with Δquality > 0.4 marked `consolidated`, **which `retrieve()` never checks**) (`neu/src/reasoning-bank.ts:257-350,394-583,1116-1233`) | Toy; unused by the CLI |

### 5.5 RETRIEVE→JUDGE→DISTILL→CONSOLIDATE in the running product

| Step | Default hook path | MCP path | Notes |
|---|---|---|---|
| RETRIEVE | `getContext` trigram-Jaccard top-5 | `memory_search`, `findSimilarPatterns`, `agentdb_pattern-search` | — |
| JUDGE | `!toolFailed` heuristic on the hook payload (`hlp/hook-handler.cjs:352-364`) | caller-supplied `success` (default true) and constant quality 0.85/0.3 | No LLM judge. The `judge:'fable'` option is explicitly disabled (`memory-distillation.ts:145-148`) |
| DISTILL | — | `recordTrajectory` stores success steps as patterns; daemon structural distill | — |
| CONSOLIDATE | `consolidate()` at session end | `distillLearning`, the EWC proxy | AgentDB `memoryConsolidation` is a declared **no-op stub**; `nightlyLearner` is the external NightlyLearner or absent (`mem/src/controller-registry.ts:940-949,1032-1059,1292-1304`) |

**Verdict per concept:**
- **Verdict:** real as an enum (success/partial/failure), weakly sourced.
- **Trajectory:** real as a record, not persisted across processes on the SONA path.
- **Consolidation:** real in the hook layer and the daemon; stubbed in the AgentDB controller.

### 5.6 Deterministic distillation (transferable)

`cli/src/memory/structured-distill.ts:1-134` produces a 4-field schema:
- `summary`: first sentence, ≤200 characters;
- `detail`: ≤1,024 characters;
- `labels`: action vocabulary plus the top-5 CamelCase/CONSTANT tokens;
- `paths`: file paths with optional `:line`.

Serialization puts labels and paths first. Caveat: the vocabulary match is a substring match (`lower.includes(v)`), so `add` matches "address" (`:69-72`). The same class of bug was fixed elsewhere with word boundaries (`cli/src/mcp-tools/hooks-tools.ts:821-830`).

### 5.7 Success/failure feedback asymmetry

- Success increments are larger than failure decrements:
  - SONA: +0.1 vs −0.05 (`intelligence.ts:250-284`);
  - hook feedback: +0.05 vs −0.02 (`hlp/intelligence.cjs:691-697`).
- Many producers default to success:
  - post-edit `success !== false` (`hooks-tools.ts:966`);
  - hook `toolFailed` requires an explicit error signal (`hlp/hook-handler.cjs:352-364`).
- Combined with "store every step at 1.0", this **inflates confidence**.
- The hook layer's feedback-driven `confidence` is **not part of the `getContext` score** (score = 0.6·Jaccard + 0.4·PageRank, `hlp/intelligence.cjs:621`). It only affects the sort order of `ranked-context.json`, which `getContext` re-sorts anyway.

---

## 6. Knowledge graph

| Graph | Storage | Population | Query | Verdict |
|---|---|---|---|---|
| `graph_edges` table (ADR-130) | `.swarm/memory.db` via better-sqlite3 WAL (`cli/src/memory/graph-edge-writer.ts:115-266`) | Three writers, all with **synthetic node ids**: post-task `task:<id> → pattern:<id>` "reinforced-by" (`hooks-tools.ts:1758-1773`); trajectory-step `task:<trajId> → pattern:<stepId>` "trajectory-caused" (`:3078-3094`); `agentdb_causal-edge` with an embedded edge text `"<rel>: a -> b"` (`cli/src/mcp-tools/agentdb-tools.ts:440-479`). There is no node table, and the ids resolve to no pattern rows | `agentdb_graph-query`: k-hop via recursive CTE, depth ≤3, real traversal (`agentdb-tools.ts:1081-1119,1210-1231`); "semantic" = brute-force cosine over `embedding_ref` (`:1122-1167`); "pagerank" = simple personalized PageRank, 20 iterations (`:1170-1199,1258-1314`). `agentdb_graph-pathfinder`: **`dynamic-mincut`, `spectral-sparsify` and `connected-component-churn` are "simplified implementations: return k-hop neighbors with basic score" `1/(1+i)`** (`:1467-1482`); `temporal-centrality` uses a hardcoded `exp(-0.1·days)` and ignores the per-edge `decay_rate` (`:1424-1444`) | An insert-only edge log with real k-hop/PPR. The confidence, decay and reinforce columns are never updated (no UPDATE or DELETE anywhere) |
| AgentDB `causalGraph` / `causal_edges` | agentdb tables | `bridgeRecordCausalEdge` uses `causalGraph.addEdge` if present, else memory namespace `causal-edges` with key `src→tgt` (`memory-bridge.ts:2457-2499`); distillation adds weak "co-occurrence" edges at confidence 0.3 marked "must not justify autonomous action" (`memory-distillation.ts:369-388`) | Deletes: Cypher `DETACH DELETE` when the native graph adapter exists, else soft-delete of the fallback rows (`memory-bridge.ts:2621-2794`) | Real, but mostly co-occurrence |
| Library `MemoryGraph` | in-memory only | edges from `MemoryEntry.references`, which **no CLI or hook writer sets** (`mem/src/memory-graph.ts:90-106`) | PageRank (`:195-247`), label propagation (`:250-291`; the `'louvain'` option is declared but not implemented, `:25`), `rankWithGraph` α=0.7 | Real algorithms on an edgeless graph. In `curateIndex`, communities are never computed, so "graph-aware section ordering" is a no-op (`mem/src/auto-memory-bridge.ts:484-497`) |
| Hook graph (ADR-050) | `.claude-flow/data/graph-state.json` | nodes = store entries; "temporal" edges between consecutive entries of the same `sourceFile`; "similar" edges at trigram-Jaccard > 0.3 within a namespace, skipped above 100k comparisons (`hlp/intelligence.cjs:290-370`) | PageRank (damping 0.85, 30 iterations, skipped above 5,000 nodes) (`:238-286,540-548`) | Real but low-signal: PageRank ≈ 1/N contributes almost nothing to the 0.6/0.4 blend |
| Code dependency graph | in-memory, 5-minute cache | regex import/export extraction for JS/TS; extension resolution returns the first candidate (`.ts`) without an existence check (`cli/src/ruvector/graph-analyzer.ts:240-305`) | Stoer-Wagner mincut and Louvain fallbacks in TypeScript, cycle detection, DOT export (`:512-1100`) | Real, ephemeral, not linked to memory. The comparable code intelligence in EOS/Graphify is deeper |
| `ruflo-knowledge-graph` plugin | — | **Prompt-only.** The skill tells the LLM to Read files, extract entities and call `agentdb_hierarchical-store` / `agentdb_causal-edge` (`plugins/ruflo-knowledge-graph/skills/kg-extract/SKILL.md:1-40`) | — | No code |

---

## 7. Persistence model overall

### 7.1 Inventory of on-disk stores

| Path | Format | Writer concurrency discipline |
|---|---|---|
| `.swarm/memory.db` (+ `.lock`, `-wal`/`-shm`, `schema.sql`, `backups/memory-*.db`, `rabitq.meta.json`) | SQLite: sql.js whole image and/or better-sqlite3 WAL | sql.js paths serialize through an O_EXCL lock (10 s stale, 15 s timeout, reentrant via AsyncLocalStorage) (`cli/src/memory/memory-initializer.ts:3912-3983`) and **refuse** to write when WAL sidecars exist (`:80-86,2959-2972`). Native writers ignore the lock ("It is advisory, so it still cannot coordinate against a writer that bypasses this module", `:3938-3941`) |
| `.swarm/agentdb-memory.db` (+ `.swarm/attestation.db` if available) | SQLite via external AgentDB | WAL plus a PASSIVE checkpoint after each write/delete so that WAL-blind readers see rows (`cli/src/memory/memory-bridge.ts:1084-1110,1617-1630`) |
| `.swarm/hnsw.index`, `hnsw.metadata.json` | `@ruvector/core` native plus JSON | non-atomic `writeFileSync` (`memory-initializer.ts:736-747`) |
| `.swarm/sona-patterns.json`, `q-learning-model.json`, `ewc-fisher.json` | JSON | full rewrite |
| `.swarm/model-router-trajectories.jsonl`, `run-transcripts.jsonl` | JSONL | `appendFileSync`; rotation at 10 MB × 3 (`cli/src/ruvector/router-trajectory.ts:162-214`) |
| `.claude-flow/neural/{patterns,stats,models}.json` (or `~/.claude-flow/neural`) | JSON | **non-atomic `writeFileSync` of the whole array** (`cli/src/memory/intelligence.ts:541-553,816-824`; `cli/src/mcp-tools/neural-tools.ts:285-288`) |
| `.claude-flow/routing-outcomes.json` | JSON | full rewrite (`cli/src/mcp-tools/hooks-tools.ts:205-221`) |
| `.claude-flow/memory/store.json` | JSON (legacy) | still written by hook fallbacks (`hooks-tools.ts:1108-1117,1819-1831`), even though `memory-tools` migrates it once and drops a marker (`cli/src/mcp-tools/memory-tools.ts:213-245,398-423`) |
| `.claude-flow/data/{auto-memory-store,graph-state,ranked-context,intelligence-snapshot}.json`, `pending-insights.jsonl` | JSON/JSONL | non-atomic `writeFileSync` (`hlp/intelligence.cjs:106-109`); the JSON backend persists the whole map **on every store** (`hlp/auto-memory-hook.mjs:92-160`) |
| `.claude-flow/sessions/current.json`, `<id>.json`, `session-*.json` | JSON | `session.js` uses tmp + rename (`hlp/session.js:21-25`); `intelligence.cjs` `sessionSet` writes non-atomically (`hlp/intelligence.cjs:222-234`) |
| `.claude-flow/tasks/store.json`, `.claude-flow/agents/store.json` **and** `.claude-flow/agents.json` (two agent stores) | JSON | full rewrite, no lock (`cli/src/mcp-tools/task-tools.ts:65-68`; `cli/src/mcp-tools/agent-tools.ts:18-23`, "a *different* file from the canonical …") |
| `.claude-flow/hive-mind/state.json` | JSON | full rewrite, no lock (`cli/src/mcp-tools/hive-mind-tools.ts:14-16,239-243`) |
| `.claude-flow/learning/patterns.db` | SQLite (dormant helper) | — |
| `~/.claude/projects/<key>/memory/*.md` | Markdown (Claude Code's own memory) | written by AutoMemoryBridge sync at Stop (§3.1) |
| `~/.claude-flow/leases/<repoId>.json` | JSON | O_EXCL lock; 15-minute lease TTL (`cli/src/services/workspace-lease.ts:1-30`) |
| `agentdb.rvf` in CWD | RVF binary | §11 |

### 7.2 Concurrency, migration, size management

- **Concurrency.**
  - The only real protections are:
    - SQLite WAL for native writers;
    - the O_EXCL lock for sql.js read-modify-write;
    - a WAL-sidecar refusal gate;
    - tmp + rename in a few places.
  - Every JSON store is last-writer-wins, with no lock. Hooks run as concurrent separate processes: subagents, a daemon and an MCP server.
  - The #2878 regression test proves the sql.js path lost 11 of 12 concurrent writes before the lock (`cli/__tests__/memory-concurrent-write-loss-2878.test.ts:1-24`).
- **Migration and versioning.**
  - `ensureSchemaColumns` adds missing columns and backfills NULL status to `'active'` (`memory-initializer.ts:1303-1400`); the bridge has an equivalent (`memory-bridge.ts:669-720`).
  - `metadata.schema_version='3.0.0'` is never compared.
  - `checkAndMigrateLegacy` only detects legacy DBs (`memory-initializer.ts:1405-1461`).
  - `ReasoningBank.deserialize` checks `schemaVersion === 1` (`neu/src/reasoning-bank.ts:869-871`).
  - The JSONL recorders carry `v:1`, where "new required fields bump the version, additive optional fields do not" (`cli/src/ruvector/router-trajectory.ts:13-14`).
- **Size management.**
  - Caps: LocalReasoningBank FIFO at 5,000 (by insertion, not confidence) (`intelligence.ts:597-603`); SONA optimizer 1,000; routing outcomes 500; pending-insights trimmed to 2,000 lines above 512 KB (`hlp/intelligence.cjs:675-684`); snapshots 50; tiered memory 5,000 per tier and 5,000 archived; JSONL rotation 10 MB × 3; backups keep 7.
  - **Soft-deleted `memory_entries` are never garbage-collected.** On the bridge path they keep content and embedding (`memory-bridge.ts:1607-1611`). Only `purgeNamespace` hard-deletes.
  - The hook layer skips any JSON file larger than 10 MB (`hlp/intelligence.cjs:91-104`). **`init()` then treats the store as empty and overwrites it with a MEMORY.md bootstrap, silently discarding the whole store.** A corrupt-JSON parse does the same (`:471-483`).
  - The same shape exists in `LocalReasoningBank`: any load error means "start fresh", and the next flush overwrites `patterns.json` (`intelligence.ts:469-518,541-553`).

---

## 8. Failure and recovery

| Mechanism | Code | Status |
|---|---|---|
| Crash-safe write for the sql.js image | tmp + fsync + rename + dir fsync (`cli/src/fs-secure.ts:41-60+`), used for `memory.db` flushes | IMPLEMENTED (after #2584 corruption) |
| Corruption auto-recovery | `recoverMemoryDatabase`: `quick_check` → `BEGIN IMMEDIATE` (skip if a writer is active) → table-by-table copy with `INSERT OR IGNORE` → verify `integrity_check=ok` **and** `rows_after ≥ rows_before` → back up the corrupt original → atomic rename → drop sidecars; on failure, restore the newest integrity-ok backup (`cli/src/memory/memory-initializer.ts:1586-1711`) | IMPLEMENTED; runs on MCP start via `repairVectorIndexes(autoRecover:true)` (`:1896-1901`) |
| Backups | better-sqlite3 online `backup()`, byte-copy fallback for the encrypted file, keep 7, optional `gcloud storage cp` (`cli/src/services/memory-backup.ts:51-140`); nightly daemon worker | IMPLEMENTED, **but only `.swarm/memory.db`**, not `agentdb-memory.db` (§0 item 1) |
| Bridge failure latch plus diagnostics | `bridgeAvailable=false` for the life of the process; `bridgeFailureReason` appended to later fallback errors (`cli/src/memory/memory-bridge.ts:57-67,2064-2078`); `shutdownBridge` clears the latch (`:2106-2121`) | IMPLEMENTED |
| Durability warning | `persistWarning` when the PRAGMA checkpoint fails with "Invalid PRAGMA", meaning AgentDB silently used sql.js and the write may never reach disk (`memory-bridge.ts:1091-1110`) | IMPLEMENTED (an honest advisory) |
| Checkpoint/rollback | `CheckpointGate` over `agenticow` COW branches of `.rvf` memory: checkpoint before a risky loop tick, roll back on regression; degrades to unguarded when the package is missing (`cli/src/services/checkpoint-gate.ts:1-35`) | IMPLEMENTED as an optional dependency; not used by the memory paths above |
| Retry and circuit breakers | `cli/src/production/{retry,circuit-breaker,rate-limiter,error-handler,monitoring}.ts` — **re-exported from `cli/src/index.ts:780-826` only, with no internal caller** | Library only |
| Tool-loop breaker | pre-command blocks a command after repeated consecutive failures (`cli/src/mcp-tools/hooks-tools.ts:1049-1069,1094`) | IMPLEMENTED (MCP path) |
| Idempotent hooks | `claimSideEffectEvent`: sha256(project, family, `tool_use_id`/`session_id`, or payload + 2 s bucket) → O_EXCL marker under tmp; duplicates are skipped for `post-edit` and `session-end` (`hlp/hook-handler.cjs:289-333`) | IMPLEMENTED |
| Dead-agent detection / reassignment | Daemon worktree leases with a 15-minute TTL plus a `kill(pid,0)` liveness check and a supervisor election (`cli/src/services/workspace-lease.ts:1-30`; `repo-supervisor.ts`); orphaned headless children reaped (`cli/src/services/worker-daemon.ts:692-722`). **No agent- or task-level heartbeat or reassignment** in the task store | Daemon-level only |
| "Byzantine fault tolerance" | MCP `hive-mind_consensus` = vote tally in `state.json`: required votes are ⌊2n/3⌋+1 for bft, majority for raft, presets for quorum; a "Byzantine voter" is a voter id that votes inconsistently across same-type proposals; there is a deadlock reject rule (`cli/src/mcp-tools/hive-mind-tools.ts:84-169`). `hive-mind_status` returns hardcoded `health: {overall:'healthy', consensus:'healthy', memory:'healthy'}` and `memoryUsage = keys·2 KB` (`:474-482`). The swarm package has a PBFT class whose messages are "emitted into the void" unless a transport is wired (`v3/@claude-flow/swarm/src/consensus/byzantine.ts:42-50`) | Bookkeeping; no fault tolerance |
| Hive shared memory ("bounded conflict") | last-writer-wins JSON dict plus a mirror write to namespace `hive-memory` (`hive-mind-tools.ts:1048-1122`) | Not implemented as claimed |
| Library dual write | `HybridBackend.store` = `Promise.all([sqlite.store, agentdb.store])` with no compensation; a partial failure diverges the stores (`mem/src/hybrid-backend.ts:225-235`) | Weak (library only) |

---

## 9. Provenance, confidence, conflicts

- **Typed provenance (ADR-323, Accepted)** — IMPLEMENTED at the storage and filter level (`cli/src/memory/memory-initializer.ts:20-38,269-271,2905-2914,3133-3143`):
  - `provenance_type` ∈ {user_claim, agent_output, system_observation, tool_result, unknown};
  - validated before either backend is touched;
  - an upsert without a type **preserves** the existing label (`memory-bridge.ts:938-949`);
  - `provenance_filter` is honored by the bridge, RaBitQ, HNSW (fails closed on a lookup error) and brute-force paths, with ANN under-fill falling through to the SQL scan (`memory-initializer.ts:3228-3291`);
  - motivation: MemSyco-Bench memory-induced sycophancy (`:3111-3113`).
- **Only user-facing writes set it.** The MCP `memory_store` and CLI `memory store` accept it. **No internal writer** passes it: feedback, commands, trajectories, session summaries, hive memory and `memory_import_claude` (the only `provenanceType:` literals are in readers and the two user paths, per a search of `cli/src`). In practice nearly every row is `unknown`.
- **Who wrote it.** `owner_id` is never inserted; `metadata` is always `'{}'` (`memory-initializer.ts:3056`; `memory-bridge.ts:1046`). The library `shareWith`/`getSharedWith` uses metadata (`mem/src/index.ts:744-767`) but is not used by the CLI.
- **Outcome provenance** is the best design in the repo:
  - distillation tiers `oracle:test-exec | judge:fable | proxy:structural`, where "proxy NEVER promotes (ADR-171)", recorded in `metadata.provenance_tier/promoted/sourceIds` (`cli/src/services/memory-distillation.ts:33,331-338`);
  - run-transcript `resolved_source`, where a proxy can never be mistaken for gold (`cli/src/ruvector/run-transcript-recorder.ts:25-35`).
- **Search-score provenance.** Bridge results carry `provenance: "semantic:0.812+lexical:0.400"` (`memory-bridge.ts:1316-1318`), a useful explain-string.
- **Confidence semantics.** `findSimilar` used to overwrite `confidence` with the query cosine; it now returns a separate `similarity` (`intelligence.ts:614-654`). This is a named bug class: "confidence-similarity conflation" (`cli/__tests__/intelligence-confidence-similarity-conflation.test.ts`).
- **Conflicts and duplicates:**
  - key collisions: upsert (last writer wins) or a typed "already exists" error;
  - content duplicates: hashes on import only;
  - temporal conflicts: **only `TieredMemoryStore`** supports `supersedes`. It stamps `validUntil=now` and `supersededBy=<newId>`, archives rather than deletes, and recall filters invalid entries unless `includeExpired` is set (`mem/src/tiered-memory.ts:296-407`). With AgentDB's native HierarchicalMemory, supersede is "not supported … the referenced entry was left untouched" (`memory-bridge.ts:3110-3116`);
  - neural-pkg "contradiction detection" marks entries but retrieval ignores the mark (§5.4).
- **Verification status.** None on memory rows. ADR-354 (write verification and temporal supersession) and ADR-339 are "Proposed" (dream-cycle ADRs).

---

## 10. Token efficiency

- **Retrieval limits.**
  - `memory_search` default `limit=10`, `threshold=0.3`; content truncated to 60 characters in results (`memory-tools.ts:637-638`; `memory-initializer.ts:3216,3359`). Full values require `memory_retrieve`. The progressive disclosure is accidental but effective.
  - Hook `getContext`: top-5, 80-character summaries (`hlp/intelligence.cjs:612-655`).
  - `smartSearch`: `fanOutK = max(3·limit, 20)` per variant (`mem/src/smart-retrieval.ts:474`).
  - The neural ReasoningBank `retrievalK=3`.
  - **No token budget exists** anywhere in the memory → context path. The only budgeted restore is the repo-dev-only transcript archive (4,000 tokens, `.claude/helpers/context-persistence-hook.mjs:43`).
- **Summarization before injection.** None on the default path. The injected lines are entry keys/titles such as `auto-memory:MEMORY.md:<heading>`, re-surfacing content Claude Code already loads from `MEMORY.md`.
- **Measurement.** **"32.3% token reduction" is a hardcoded feature-list string** in `hooks --help` (`cli/src/commands/hooks.ts:5738`), next to "84.8% SWE-Bench", "Flash Attention (2.49x-7.47x)" and "150x faster search" (`:5733-5740`); it also appears in skill markdown. The measurement code tells a different story:
  - `hooks token-optimize` computes `tokensSaved = len(query)/4 − len(compactPrompt)/4` (`hooks.ts:4964-4969`). That baseline was declared meaningless and fixed in `v3/@claude-flow/integration/src/token-optimizer.ts:120-190` (#3289: `tokensSaved: null` unless the caller supplies `baselineTokens`), but **the CLI command still uses the old formula**.
  - The self-audit found the Flash Attention speed-up was once generated with `Math.random()` (audit `:53,64`, since removed).
  - `performance` benchmark: "speedup" = `entryCount·0.0005 ms` (an invented brute-force baseline) / measured mean, and the measured search itself runs through the brute-force bridge (`cli/src/commands/performance.ts:156-168`).
  - SONA "<0.05 ms adaptation" = the time of a circular-buffer array write (`cli/src/memory/intelligence.ts:176-191,1405-1442`).

---

## 11. Rust crates, `agentdb.rvf`, `data/`

- **Crates** (`Cargo.toml` members: `v3/crates/ruflo-federation-peer` and `v3/crates/ruflo-agntcy`; `v3/crates/ruflo-watermark` is standalone):

  | Crate | What it is | Wired to TS? |
  |---|---|---|
  | `ruflo-agntcy` (622 LOC) | CASA authorization envelopes; the `slim` feature is **a stub that returns `Unavailable`** (Cargo.toml comment) | No |
  | `ruflo-federation-peer` (742 LOC) | QUIC federation peer; the `native` feature is off by default | No |
  | `ruflo-watermark` (2,756 LOC) | SynthID-style text watermarking | Yes, **only this crate**, as WASM through `@claude-flow/watermark` (`v3/@claude-flow/watermark/package.json`) |

  **None implements memory, vector search, SONA or AgentDB.** All native memory acceleration comes from external npm packages (`@ruvector/core` VectorDb NAPI, `@ruvector/rabitq-wasm`, `@ruvector/sona`, `@ruvector/ruvllm`, `ruvector`, `agentdb`) loaded by dynamic `import()` with graceful null fallbacks (e.g. `cli/src/memory/memory-initializer.ts:613-627`). No bindings to the in-repo crates exist for memory.
- **`agentdb.rvf` (162 bytes) and `agentdb.rvf.lock` at the repo root.**
  - It is an **empty RVF (RuVector Format) vector-store container**: a segment header with magic bytes `SFVR` (the little-endian u32 `RVFS`), a 384-dimension field at offset 0x44 (`80 01` = 384), and an identity segment `IDIF` naming `agentdb.rvf` with a hash. It holds no vectors.
  - Origin: the native `ruvector` VectorDB defaults its `storagePath` to a shared file in CWD, "e.g. agentdb.rvf", which is "frequently already held by a running daemon/MCP server", so the lock fails and search silently degrades to brute force (`cli/src/ruvector/vector-db.ts:189-196`).
  - 162 bytes is exactly the fixed size of an agenticow COW branch (`docs/agenticow/findings.md:10,25`).
  - It is referenced only as a "bloat marker" in init (`cli/src/commands/init.ts:200,293`) and as a memory-exists probe (`cli/src/mcp-tools/system-tools.ts:331`). **Verdict: an accidentally committed runtime artifact; not a data store.**
- **`data/clone-data.rvf` + `.ledger.json` + `.proof.json`.** An RVF with dimension 10 (`0a 00` at 0x44) holding GitHub clone and npm download counters for 5 repos (`data/clone-data.ledger.json` `schema: ruflo-clone-tracker-ledger/v1`, `vector_layout` of 10 counters). It is a marketing and analytics "ecosystem proof" (`data/clone-data.proof.json`), **unrelated to agent memory**.

---

## 12. Tests — what they actually assert

- `cli/__tests__` has 249 top-level test files (268 including subdirectories). About 60 of them are memory, issue or doctor regression files (`memory-*`, `issue-NNNN-*`, `doctor-*`).
- **Integration-style (real sql.js or better-sqlite3 in temp dirs, no mocks):**
  - `memory-concurrent-write-loss-2878.test.ts`: 12 concurrent stores must all persist; 0 mocks, 16 expects;
  - `memory-durability-2584.test.ts`;
  - `memory-search-recall-2558.test.ts`;
  - `memory-distillation.test.ts`: builds the agentdb schema and seeds memory;
  - `self-learning-2245.test.ts`: EASY/MEDIUM/COMPLEX, a real `intelligence.ts` in a scratch CWD;
  - `tiered-memory.test.ts` (including durability across reopen).

  These prove the specific data-loss fixes.
- **Weak assertions:**
  - `self-learning-2245.test.ts:54-63`: `expect(signalsProcessed).toBeGreaterThanOrEqual(before)` is tautological, and the bridge call is wrapped in `try{}catch{}`;
  - `memory-ruvector-deep.test.ts` (300 expects) includes checks that the **schema string contains** `CREATE TABLE IF NOT EXISTS patterns|trajectories|sessions|migration_state` (`:28-65`). These are coverage of a constant, for tables nothing writes.
- **Mock-heavy:**
  - `hooks-intelligence-learning.test.ts` mocks EWC, the SONA optimizer, `memory-initializer` and `intelligence` (5 `vi.mock`, 11 `vi.fn`) to assert call shapes (`:1-70`);
  - `mem/src/learning-bridge.test.ts` uses a mock NeuralLearningSystem and a mock backend (30 `vi.fn`);
  - `mem/src/memory-graph.test.ts` (20 `vi.fn`).
- **Library suites without mocks:** `hybrid-backend` (52 expects), `consolidator` (49), `agentdb-backend` (37).
- **Gap:** no test exercises the default hook path end-to-end: `hook-handler` → `intelligence.cjs` with concurrent processes, the >10 MB clobber, or confidence actually changing retrieval. No test checks that TTL is enforced. No test checks that backups cover `agentdb-memory.db`.

---

## 13. Security package (brief)

`v3/@claude-flow/security` (`src/index.ts:1-80`) covers:
- bcrypt `PasswordHasher`;
- `CredentialGenerator`;
- `SafeExecutor` (command allowlisting, HIGH-1);
- `PathValidator` (HIGH-2);
- Zod `InputValidator` schemas;
- token generator;
- OAuth PKCE client and keychain adapter;
- policy engine and product-plane;
- plugin integrity verifier;
- MCP composition inspector;
- `ToolOutputGuardrail` (regex prompt-injection categories: instruction-override, role-hijack, exfiltration, jailbreak, hidden-unicode, …; `src/tool-output-guardrail.ts:1-45`).

**Memory-relevant wiring:**
- `AgentDbRetrievalGuard` wraps the guardrail for retrieved memory. It is **off by default** (`CLAUDE_FLOW_RETRIEVAL_GUARD=true`) and wired only into the library `agentdb-backend.ts`, **not the CLI `memory_search` path** (`mem/src/agentdb-retrieval-guard.ts:1-19`).
- The MCP content-boundary guardrail (`CLAUDE_FLOW_STRICT_GUARDRAIL=true`) loads the package with bare `require()` inside an ESM module (`"type":"module"`, `v3/tsconfig.base.json` `module: ESNext`, no `createRequire` in the file). It therefore throws, is caught, and **fails open**. It also scans only top-level string fields, while memory results are nested arrays (`cli/src/mcp-client.ts:302-343`).
- MCP memory keys and namespaces are validated (`cli/src/mcp-tools/memory-tools.ts:61-88`).
- Stores are written 0600 with opt-in AES-256-GCM at rest (`cli/src/fs-secure.ts:1-18`).

---

## 14. CHANGELOG and ADRs relevant to memory, learning and persistence

**Root `CHANGELOG.md`:**
- #2786: split AgentDB into `agentdb-memory.db` because of encryption;
- #2775: upsert and tombstone resurrect;
- #2785: routing outcomes persist;
- 3.5.0: "AgentDB v3 … 8 new controllers (HierarchicalMemory, MemoryConsolidation, SemanticRouter, GNNService, RVFOptimizer, MutationGuard, AttestationLog, GuardedVectorBackend)". The current `controller-registry.ts` shows all eight are null or stubs on installable agentdb ranges (#2977).

**`v3/CHANGELOG.md`** claims:
- "ADR-006 Unified memory service replacing 6+ fragmented systems";
- "150x-12,500x faster search";
- "<0.05ms adaptation";
- "Memory reduction: 83.1% achieved".

**ADRs** (`v3/implementation/adrs/`, older; `v3/docs/adr/`, newer; 184 files):

| Group | ADRs |
|---|---|
| Memory architecture | ADR-006 Unified Memory ("Implemented"); ADR-009 Hybrid backend; ADR-048 Auto-memory integration; ADR-049 Self-learning memory/GNN; ADR-050 Intelligence loop (the hook layer); ADR-051 Infinite-context compaction bridge (repo-dev only); ADR-053 AgentDB v3 controller activation; ADR-055 controller bug remediation; ADR-057 RVF native storage backend; ADR-076 Claude Code memory bridge. **ADR-125 ("MemoryService unification", cited throughout `mem/src/index.ts`) has no file in the tree** |
| Learning | ADR-074 Self-learning wiring (#2245); ADR-075 Unified learning stats; ADR-076 Structured distillation; ADR-077 Pretrain from history; ADR-078..091 retrieval quality work (hybrid BM25, cross-encoder, labelled corpus/nDCG, grid search, BEIR, Lucene BM25, an honest RRF negative result in ADR-087); ADR-086 ruvllm backend / silent-fallback story; ADR-142 per-task bandit priors; ADR-171 provenance-tiered evaluation oracle; ADR-174 Memory distillation and self-optimization |
| Graph, security, persistence | ADR-096 Encryption at rest; ADR-130 Graph intelligence (`graph_edges`); ADR-131/146/377 guardrails; ADR-145/337 memory namespace governance; ADR-170 agenticow COW memory; ADR-323 Typed memory provenance (**Accepted**) |
| Dream-cycle proposals | ADR-335 MV-HNSW; ADR-339 memory write integrity; ADR-341 multi-signal retrieval; ADR-344 KG index for ReasoningBank; ADR-347 trajectory JUDGE scoring; ADR-354 write verification and temporal supersession; ADR-358 AutoMem RL loop; ADR-365 recurrence-gated consolidation; ADR-367 epistemic working memory; ADR-368 selective persistence; ADR-372 cognitive mode router; ADR-373 budget operator selection. All are **"Proposed"** and mostly authored by "claude (dream-cycle agent)". They are research proposals, not implementation records |

---

## 15. Claims vs code — ledger

| Claim (source) | What the code does | Verdict |
|---|---|---|
| "Unified memory replacing 6+ systems" (ADR-006 "Implemented", `v3/CHANGELOG.md:20`) | 4 implementations and 20+ stores; the primary store depends on the platform; backup and distill target the other file | **False** |
| "HNSW 150x–12,500x faster" (`cli/src/commands/memory.ts:37,426,1201`; `v3/CHANGELOG.md:48`) | The default path is brute-force cosine (`memory-bridge.ts:1865-1942`); the speed-up string is chosen by entry count (`memory.ts:530`); measured 1.48–6.5× where HNSW is actually used (audit) | **Unsubstantiated / overstated** |
| `memory_search` `backend: 'HNSW + sql.js'` (`memory-tools.ts:771`) | Usually the bridge brute-force path | **Mislabel** (not yet fixed on this line) |
| "SONA <0.05 ms adaptation" | The time of a circular-buffer write (`intelligence.ts:176-191`) | Trivially true, meaningless |
| "LoRA / EWC++ continual learning" | Scalar confidence nudges; Fisher is an embedding-magnitude proxy (admitted) | **Mislabeled heuristics** |
| `neural_train` "Train a neural model" | Embed and store; `accuracy = 1.0` if anything was stored | **Not training** |
| "4-step RETRIEVE→JUDGE→DISTILL→CONSOLIDATE" | Toy class in the neural pkg (rule judge, `"Apply a -> b"`), unused; CLI equivalents are heuristics; AgentDB consolidation is a no-op stub | **Partially real, mostly relabeled** |
| "MutationGuard proof engine, AttestationLog" (3.5.0 CHANGELOG) | Controllers return null on installable agentdb; `guarded/attested: true` returned regardless | **Stub + false flag** |
| "Knowledge graph" with decay and reinforcement (schema comments) | Insert-only edges with synthetic ids; `decay_rate`, `confidence` and `last_reinforced` never updated; 3 of 6 pathfinder algorithms are k-hop placeholders | **Mostly claimed** |
| "Session restore" | Fabricated ids and counts on the MCP path; stale-session reuse on the hook path | **Fabricated / shallow** |
| "Byzantine-FT consensus, shared memory with bounded conflict" (hive-mind tool descriptions) | Vote tally in JSON; last-writer-wins dict; hardcoded "healthy" | **Claimed** |
| "32.3% token reduction" (`hooks.ts:5738`) | No computation behind it | **Unsubstantiated** |
| TTL (`memory_store {ttl}`) | Written, never enforced | **Inert** |
| Typed provenance (ADR-323) | Implemented end-to-end for user writes | **Real** (sparsely populated) |
| Temporal supersession (`TieredMemoryStore`) | Implemented and durable when given a SQLite handle | **Real** |
| Memory distillation (ADR-174) | Implemented: incremental, transactional, provenance-tiered | **Real**, but aimed at the possibly-wrong file |
| Learned routing from outcomes | Implemented, deterministic, discriminative | **Real** |
| DB corruption recovery and backups | Implemented and careful | **Real** (backup scope gap) |

---

## 16. Relevance to EOS

EOS, for reference: a local-first, stdlib-Python, SQLite-derived-index engine with file-first append-only ledgers (notes `.md`, `work.jsonl`, `executions.jsonl`). It has no embeddings by ADR, calls no LLM, and advises harnesses. Per the sibling `eos-core-audit.md`:
- notes have **no `supersedes`**, no TTL and only digest/title dedup;
- `executions.jsonl` has start/event/finish, with outcomes ok/failed/abandoned, and **no rotation**;
- procedure confidence is a word derived when read;
- **there is no locking**; procedure counters are a racy read-modify-write of a git-tracked file.

### 16.1 Transfer — fits EOS constraints (stdlib, deterministic, no model)

1. **Supersede-not-delete temporal validity for notes** (`mem/src/tiered-memory.ts:37-70,161-174,296-407`). Add front-matter `supersedes: <note-id>`, `valid_from`, `valid_until`, `superseded_by`, and have the fold stamp `superseded_by` on the target instead of editing it. Default recall filters invalid notes; `--include-expired` is the audit hatch. This directly fills the EOS gap. Keep Ruflo's detail: "unparseable timestamps are ignored rather than hiding the entry" (`:156-174`).
2. **Outcome provenance on `executions.jsonl` finish lines** (`run-transcript-recorder.ts:25-35`; `memory-distillation.ts:23-26,331-333`). Add `outcome_source` ∈ {`test-exec`, `ci`, `user`, `agent-claim`, `heuristic`}. Let only execution-tier outcomes move procedure counters and the `verified` rung; proxy outcomes are recorded but never promote. Ruflo's failure mode to avoid: defaulting `success=true` (`hooks-tools.ts:966`), then treating it as "oracle" ground truth.
3. **Typed write-time provenance for notes** (ADR-323 enum: user_claim / agent_output / system_observation / tool_result / unknown) plus a trust filter on retrieval. Lesson from Ruflo: **every internal writer must set it**, or the whole corpus becomes `unknown` (§9). Also record the writer (`agent`, `session`), which Ruflo drops (`owner_id` never written).
4. **Decision/outcome ledger discipline** (`cli/src/ruvector/router-trajectory.ts:13-14,43-109,162-214,326-405`):
   - a `v` field on every JSONL line; additive optional fields do not bump it;
   - a join key (a task hash) between separate decision and outcome lines;
   - `source` on outcomes;
   - size-based rotation (`file`, `.1`, `.2`, `.3`), which EOS's 592 KB, forever-growing `executions.jsonl` lacks;
   - a pairing function that reports `droppedNoMatch` counts.
5. **Incremental, idempotent folding** (`memory-distillation.ts:189-207,270-393`):
   - a `fold_state(ledger, byte_offset, head_sha)` cursor;
   - per-batch transactions;
   - never mutate sources;
   - a `quick_check` gate;
   - bounded work per tick (1,000 rows).

   EOS rebuilds `eos.db` from scratch on a digest change. A cursor allows cheap append-folds, with a full rebuild kept as the recovery path.
6. **A lock for read-modify-write of authored files** (`memory-initializer.ts:3912-3983`):
   - O_EXCL `<file>.lock` containing the pid, with 10 s stale takeover and a 15 s acquire timeout, 25 ms polling;
   - reentrancy via a held-set, since Python has no AsyncLocalStorage: a thread-local or context variable works;
   - applies to procedure-counter edits and telemetry tail-trims.

   Ruflo measured 11 of 12 concurrent writes lost without it (#2878). Also copy `fs-secure.writeFileAtomic` (tmp + fsync + rename + dir fsync) for every non-append write.
7. **Deterministic structured distillation** of run lessons and execution summaries (`cli/src/memory/structured-distill.ts`): summary / detail / labels / paths, with labels and paths first when serialized for FTS. Use word-boundary matching for the vocabulary; Ruflo's substring bug is at `:69-72`.
8. **Lexical retrieval upgrades that need no numerics:**
   - a Porter stemmer plus Lucene stop-list BM25 (`cli/src/memory/lucene-bm25.ts`, pure functions; EOS has "no stemmer");
   - a regex **entity arm** for exact paths, `Class.method`, quoted phrases and URLs (`mem/src/entity-tagger.ts:31-80`), fused with FTS by RRF k=60 (`mem/src/smart-retrieval.ts:175-213`);
   - MMR diversity with token-Jaccard (`:286-409`);
   - a **read-time** recency multiplier `score·(1+w·0.5^(age/half_life))` with w=0.2 and a 30-day half-life (`:217-237`).

   Heed ADR-087's measured negative RRF result: fuse only arms of comparable quality, and measure on EOS's own labeled queries.
9. **Deterministic outcome-driven relevance learning** (`cli/src/services/learned-routing.ts:45-123`). Per procedure or note: keyword `support × meanQuality × idf × discriminativeShare`, with defaults `minKeywordSupport` 2, `minAgentOutcomes` 2, `minOutcomeQuality` 0.65 and `minDiscriminativeShare` 0.6 (`:49-53`). It is explainable and derivable from `executions.jsonl` (title and target terms → procedure that finished ok). It could fill EOS's no-op `adjust_for_history` without ML.
10. **Reliability as a pure fold, not stored state.** Ruflo's rules are usable *formulas*:
    - asymmetric update `c += 0.1(1−c)` on success, `c −= 0.15c` on failure, clamped to [0.1, 0.99] (`cli/src/memory/sona-optimizer.ts:124-131,342-357`);
    - a per-bucket Beta posterior (ADR-142).

    Compute them in the fold from immutable ledger lines. EOS's "confidence word derived when read" is already the right shape; §16.2 item 3 shows why stored, mutated confidence goes wrong.
11. **"Never shrink a curated file"** (`mem/src/auto-memory-bridge.ts:510-543`). Any EOS generator that rewrites `MEMORY.md`, notes indexes or AGENTS.md blocks may grow or reorder but never lose lines or links. Otherwise it writes a `.generated.md` sidecar and reports that. This bug destroyed a 75-line hand index in Ruflo (#3224).
12. **Content-fingerprint dedup across sibling dirs** (`hlp/intelligence.cjs:150-210`): whitespace-normalized lower-cased FNV-1a, plus an aggregate store fingerprint to detect same-id content edits (#2920). For near-duplicate notes without embeddings, word-trigram Jaccard ≥ τ (`:111-132,357-362`) is a stdlib paraphrase detector that EOS lacks.
13. **Hook side-effect dedup** (`hlp/hook-handler.cjs:289-312`): a claim marker keyed by sha256(project, hook family, `tool_use_id`/`session_id`, or payload + time bucket) with O_EXCL. This stops duplicate `executions.jsonl` event lines when Claude Code double-fires a hook.
14. **Honest-status fields in every report**, and never a success flag that was not verified. Ruflo retrofitted them after audits:
    - `algorithm: 'brute-force-cosine'|'hnsw'`, `backend: onnx|mock`, `durable`, `persistence: sqlite|volatile` (`cli/src/memory/memory-initializer.ts:912-961`; `memory-bridge.ts:1826-1863`; `mem/src/tiered-memory.ts:216-239`);
    - `learningPath: 'recorded-only'|'trajectory-pipeline'`;
    - `newPatterns: null` rather than a guess (`hooks-tools.ts:1877-1911`).
15. **Intent → store routing** (`cli/src/memory/scm-classifier.ts:37-146`): cheap keyword intent detection that picks the store. Episodic intent goes to `executions.jsonl`, semantic to notes, procedural to procedures. EOS's stores already align with these three intents. Keep one canonical name per namespace; §16.2 item 1 describes Ruflo's `pattern`/`patterns` drift.
16. **Session-end summary line** (`cli/src/mcp-tools/hooks-tools.ts:637-649`): "N tasks; N patterns; N edits; N commands; N errors; duration". This is a compact, deterministic session episode EOS could append at Stop, joined by session id. Contrast with Ruflo's fabricated "restore" (§3.2): an EOS restore should be an explicit query — last N runs, open work and failing procedures for this session or branch — under a hard budget.
17. **Consolidation cadence ideas** (not a daemon):
    - fold at read time when a ledger offset changed;
    - a session-end fold;
    - bounded per-tick work.

    Ruflo turned its AI workers **off by default** after background `claude --print` sweeps leaked for days (#2661, `cli/src/services/worker-daemon.ts:117-128,360-367`). This validates EOS's no-daemon and no-model stance.

### 16.2 Avoid — infrastructure or ML that EOS deliberately excludes, or proven failure modes

1. **Several stores for one concept, and a platform-dependent primary store.** Ruflo has six pattern stores and two SQLite files whose "primary" depends on native-module availability, with backup and distill pointed at the wrong one. EOS's rule — ledger is the source, index is derived, one fold — is the antidote; keep it absolute.
2. **Two storage engines on one file, or whole-image rewrites.** This is Ruflo's corruption history: #2431, #2584, #2735, #2878, #3397. Also whole-array JSON rewrites on every write, with no rename and no lock (patterns.json, auto-memory-store.json, routing-outcomes.json). EOS should stay append-only for ledgers and use atomic replace for derived files.
3. **Mutating stored confidence with repeated decay passes.** Ruflo has five decay implementations:

   | Implementation | Defect |
   |---|---|
   | `patterns` SQL | linear formula labeled exponential; no caller (`memory-initializer.ts:2178-2188`) |
   | SONA optimizer | re-applies total elapsed days on every call; no production caller (`sona-optimizer.ts:533-558`) |
   | hook consolidate | subtracts 0.005·⌊age⌋ at **every** session end (`hlp/intelligence.cjs:788-799`) |
   | LearningBridge | per-hour linear (`mem/src/learning-bridge.ts:306-347`) |
   | `graph_edges.decay_rate` | never applied |

   All either compound incorrectly or never run. Decay belongs at read time, computed from immutable timestamps.
4. **Self-reinforcing learning signals.**
   - storing every step at confidence 1.0;
   - defaulting success to true;
   - asymmetric success-leaning increments;
   - "training" on canned template sentences (`cli/src/commands/neural.ts:145-197`);
   - read-side mutation of usage counters (`cli/src/memory/intelligence.ts:648-653`).

   EOS should count only explicitly finished runs, as it does now.
5. **Embeddings, ANN (HNSW, RaBitQ), cosine dedup, EWC/Fisher, LoRA/MicroLoRA, contrastive training, Q-learning with ε-exploration, MoE, SONA.** All need numerics or models that EOS excludes by ADR. In Ruflo the non-trivial ones are inert, unused, mislabeled or slower than brute force at realistic N. The silent mock-embedding fallback mislabeled as MiniLM (audit `:44`) is the strongest argument for EOS's C-10 decision: lexical retrieval cannot silently lose its semantics.
6. **Unverifiable success flags and fabricated counters:** `guarded/attested:true` (`memory-bridge.ts:1137-1139`), invented session ids and counts (`hooks-tools.ts:2496-2500,2630-2649`), hardcoded "healthy" (`hive-mind-tools.ts:476-482`), and performance numbers chosen by lookup table.
7. **Declared-but-dead schema.** Six tables are created and never written, yet tests assert their DDL. EOS's `memory_audit` (C-10 grep) style of self-check is better: assert "tables with zero writers" as an audit finding.
8. **Silent "start fresh" on a load error.** Ruflo wipes stores after a parse error or an over-10 MB file (`hlp/intelligence.cjs:471-483`; `cli/src/memory/intelligence.ts:515-517`). EOS's "line-skipping ledgers" is the right behavior; never let a derived-file read failure trigger a write over an authored source.
9. **Injecting context the host already has.** The default hook re-injects Claude Code `MEMORY.md` section titles on every prompt. EOS's budgeted brief should exclude what the harness already loads (dedup against `MEMORY.md`/CLAUDE.md).

---

## Appendix — key files read

**Memory engine (CLI):**
- `cli/src/memory/memory-initializer.ts` (4,090 lines);
- `cli/src/memory/memory-bridge.ts` (3,455);
- `cli/src/memory/intelligence.ts` (1,636);
- `cli/src/memory/sona-optimizer.ts`, `ewc-consolidation.ts`, `graph-edge-writer.ts`, `structured-distill.ts`, `scm-classifier.ts`, `sibling-store.ts`, `rabitq-index.ts`, `hybrid-retrieval.ts`, `lucene-bm25.ts`, `cross-encoder-rerank.ts`, `neural-package-bridge.ts`.

**MCP tools:** `cli/src/mcp-tools/{memory,session,task,hooks,agentdb,neural,hive-mind}-tools.ts`, `cli/src/mcp-client.ts`.

**Services:** `cli/src/services/{memory-distillation,memory-backup,learned-routing,worker-daemon,checkpoint-gate,workspace-lease}.ts`.

**Recorders and routing:** `cli/src/ruvector/{router-trajectory,run-transcript-recorder,q-learning-router,graph-analyzer,vector-db}.ts`.

**Commands and init:**
- `cli/src/commands/{memory,neural,hooks,performance}.ts`;
- `cli/src/init/{settings-generator,executor}.ts`;
- `hlp/{hook-handler.cjs,intelligence.cjs,session.js,auto-memory-hook.mjs,learning-service.mjs}`.

**Library and neural packages:**
- `mem/src/{types,index,sqlite-backend,hybrid-backend,database-provider,controller-registry,learning-bridge,memory-graph,auto-memory-bridge,consolidator,tiered-memory,smart-retrieval,entity-tagger,hnsw-index,rvf-learning-store,persistent-sona,agentdb-retrieval-guard,agent-memory-scope}.ts`;
- `neu/src/{reasoning-bank,index,algorithms/dqn}.ts`.

**Other:**
- `v3/@claude-flow/security/src/{index,tool-output-guardrail}.ts`;
- `v3/crates/*/Cargo.toml`, `Cargo.toml`;
- `agentdb.rvf`, `data/*`;
- `docs/reviews/intelligence-system-audit-2026-05-29.md`, `docs/agenticow/findings.md`;
- `CHANGELOG.md`, `v3/CHANGELOG.md`;
- `v3/docs/adr/*`, `v3/implementation/adrs/ADR-006,-050`;
- `.claude/helpers/context-persistence-hook.mjs`.
