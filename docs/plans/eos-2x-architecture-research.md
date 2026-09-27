# EOS 2.x — architecture research: EOS vs RAGFlow vs Ruflo

**Date:** 2026-09-26 · **EOS audited:** 1.2.2 (`tools/eos`, byte-identical to upstream
`921cb6c`) in its real deployment (nexus, 20 services, 134 session transcripts) ·
**RAGFlow:** `infiniflow/ragflow` @ `313ca90` (2026-09-24, v0.27.2 era) · **Ruflo:**
`ruvnet/ruflo` @ `88955d9` (claude-flow 3.45.0, 2026-09-24).

**Method.** Source-level investigation, not README comparison. Six parallel code audits
(EOS core, EOS host integration, RAGFlow retrieval, RAGFlow agents/memory/MCP/workflow,
Ruflo runtime/routing/hooks/skills/MCP with sub-audits of swarm/consensus, agents/
decomposition and MCP/providers/workflows, Ruflo memory/learning/persistence), each reading
whole files and citing `path:line`; the shipped Ruflo model router was executed against
sample prompts; 418 Ruflo citations were re-checked against the clone. EOS numbers come
from read-only reads of `.eos/data/*`, `.devin/knowledge/*`, the service indexes and the
transcripts on 2026-09-26. Verdicts distinguish **implemented**, **partially implemented**,
**designed but not implemented** and **missing**, and "live" from "dead" code. The raw
audits (≈ 500 KB, six files) are kept in `docs/eos-plans/eos-2x-research/`; every claim
here traces to one of them.

**Reading order.** §1 is the decision; §2–§4 are what the three systems actually do; §5–§8
compare; §9–§13 decide; §14–§19 specify EOS 2.x and how to get there.

## Contents

1. Executive summary
2. Current EOS reality
3. RAGFlow architecture findings
4. Ruflo architecture findings
5. EOS vs RAGFlow vs Ruflo matrix
6. Memory architecture comparison and the unified model
7. Context engineering comparison
8. Model / effort routing comparison
9. Major architectural insights
10. KEEP / CHANGE / REMOVE
11. Adopt from RAGFlow
12. Adopt from Ruflo
13. Do not adopt
14. Proposed EOS 2.x architecture
15. Target data model
16. Target execution lifecycle
17. Implementation roadmap
18. Risks and trade-offs
19. Final architectural principles

---
## 1. Executive summary

**Question.** If EOS were redesigned today from the strongest architectural ideas in EOS,
RAGFlow and Ruflo, what should it look like?

**Answer.** The same engine — stdlib-only, file-first, deterministic, no model, no
execution — with **five properties added to the stores it already has** (temporal validity,
typed write provenance, outcome source, a fold cursor, a lock), **one retrieval scorer**
built from RAGFlow's lexical machinery, **one workspace instance** so twenty unfed project
ledgers fill themselves, a **task envelope** instead of an unapplied model pick, and a hard
boundary between **learned memory and generated inventory**. Nothing from RAGFlow's
infrastructure (Elasticsearch, Redis, MinIO, GPU parsers, an LLM per chunk) or Ruflo's
runtime (swarms, consensus, bandits, 472 tools, "neural" learning) transfers as a component;
what transfers is data shapes and disciplines, all deterministic.

**What the source code showed.**

- *EOS 1.2.2* (57 files, 18,058 LOC, 931 tests, 25 ADRs) implements a static code index with
  provenance rows, five note kinds, three append-only ledgers folded into a disposable SQLite
  index, a budgeted task brief delivered by hooks, and a deterministic model/effort router.
  Planning, execution and agent routing are absent by decision; embeddings are decided out.
  Measured in its real deployment: 841 notes of which **70% are generated or moved
  sections** and 257 are stale; recall@1 fell **0.94 → 0.67** in one day as the corpus grew;
  **51 runs and 1,929 events exist in one project and 0 in the twenty it serves** (7/21 on
  its own memory matrix); **67 routing decisions, 58 identical, 0 applied**; file-touch
  injection at **p95 7,621 tokens against a 4,000 target**; 40% of sessions ask EOS anything
  beyond the brief; three telemetry columns are wrong; no lock anywhere; a 56 MB index is
  rebuilt when one note changes.
- *RAGFlow* is a multi-tenant RAG platform whose default "hybrid" search is **lexical-gated
  dense ranking re-scored in Python by query-term coverage — EOS's own formula** — with
  field boosts, binary-TF short fields, stemming, filler stripping, bigram proximity,
  synonyms and min-should-match that EOS lacks; its newest code fuses by RRF "because RRF
  needs no threshold calibration." Its memory subsystem has the right row shape
  (`raw|semantic|episodic|procedural`, `source_id`, `valid_at/invalid_at/forget_at`) and
  never filters on `invalid_at`. Its canvas planner is dead code; its live decomposition is
  a budgeted LangGraph slot table; its citations have a 1-based/0-based bug and a
  `sha1 % 500` collision; GraphRAG is deprecated in its own UI. Verified-quote evidence gates
  and deterministic `narrow_by_terms` are its best transferable ideas.
- *Ruflo* ships two codebases that do not talk: real Raft/PBFT/gossip, a DAG orchestrator and
  a weighted agent scorer that **nothing reachable imports**, and live MCP tools that write
  JSON files and count votes. Its model router is a Thompson bandit with a reward equal to
  "the API answered" and a cold-start uncertainty gate that sends a typo fix to opus 19% of
  the time; a second router disagrees with it; **no effort dimension exists**; the decision
  is applied by CLAUDE.md persuasion. Its memory is four implementations and 20+ stores;
  "HNSW" is brute force over the newest 1,000 rows; six declared tables are never written;
  TTL is inert; "SONA/LoRA/EWC" are confidence nudges; session restore fabricates ids. Its
  genuinely good parts are disciplines: a benchmark gate with committed receipts (its own
  router scores 36% top-1), provenance tiers where proxy evidence never promotes, supersede-
  not-delete validity, an O_EXCL lock added after 11 of 12 concurrent writes were lost,
  incremental distillation with a cursor, `_stub`/`_real` honesty flags, and hook dedup by
  `tool_use_id`.

**Decisions.** KEEP the decomposition, determinism, provenance, hook delivery, budgeted
brief, procedures-as-notes, derived confidence, "a failed run leaves a lesson", extensions,
Java parsing and the measurement culture. CHANGE corpus composition (generated out),
retrieval (one scorer, RAGFlow's techniques, RRF), index build (fold cursor), concurrency
(locks, atomic writes), memory properties (validity, provenance, outcome source),
procedures (typed steps, lint), context (one estimator, every block budgeted, kind + age +
id, narrowing, dedup against loaded files), routing (labelled corpus, envelope, hook on,
offline learner), verification (structural checks on agent output), the workspace shape
(one instance, hooks once, a resolver), the graph (symbol rows, reverse dispatch, weights, a
measured Graphify decision), telemetry, CLI structure and documentation honesty. REMOVE two
of three retrieval implementations, inventories as notes, journey duplicates, per-service
hooks, dead code, duplicated ignore lists and estimators, pretty-printed 33 MB artifacts,
the UI from the core roadmap, and README claims that do not hold. DO NOT ADOPT embeddings,
LLM-extracted memory, bandits, uncertainty gates, planners, swarms, consensus, agent routers
by embedding, MCP as the channel, whole-state snapshots, per-event process hooks, daemons,
stored confidence, auto-written notes, or rules as notes.

**Roadmap.** Six releasable phases — Foundation (defects, locks, one scorer, CLI split,
ledger discipline, evaluation), Memory (five properties, generated boundary, priors, session
line), Context (one loop, render contract, narrowing, workspace instance, subagent handoff,
session-level measurement), Routing (corpus and gate, envelope, hook on, capability table,
offline learner), Execution (PostToolUse capture, typed steps, verification depths, work↔run
links, incremental fold, compact artifacts), Learning (consolidate report, graph decision,
symbol rows, honest numbers, recalibration receipts) — each with named files, dependencies,
migration, risks and a gate a fresh session can run. The gate that matters most is the one
the 1.0.0 audit wrote down: the matrix must hold **in the projects the work is meant to
serve, not in the one it was seeded in.**
## 2. Current EOS reality (engine 1.2.2, host nexus, measured 2026-09-26)

Source of truth: `tools/eos/` in nexus, byte-identical to upstream `921cb6c release: 1.2.2`.
Every claim below was checked against code (`core/<file>.py:<lines>`), against the
nexus instance (`.eos/data/*`, `.devin/knowledge/*`, 134 session transcripts) or
against the 20 service indexes. Where the documentation and the code disagree, the
code is reported and the mismatch is listed in §2.8.

### 2.1 What EOS is, in one paragraph

A stdlib-only, file-first, fully deterministic CLI (57 files, 18,058 LOC under `core/`,
931 tests, 25 ADRs) that combines a **static code index** with **project memory** and
hands both to a coding-agent harness through hooks. It parses Java structurally
(masking lexer + brace-depth scope scanner), Python with `ast` (top level only) and
JS/TS with regexes into a per-file semantic cache, projects that into a file-granular
graph plus a provenance sidecar, keeps Markdown notes of five kinds and three
append-only JSONL ledgers (work, executions, verifications) beside them, and folds all
of it — plus 2,000 commits of git history — into a throwaway SQLite index
(`.eos/data/eos.db`) that is rebuilt from scratch whenever its inputs' digest changes.
It **calls no model, executes nothing and plans nothing** (ADR-018, ADR-025). Its one
active decision, model/effort routing, is a keyword classifier plus a seven-factor
weighted score that *advises* the harness.

### 2.2 Capability matrix

Status legend: **IMPL** implemented and used · **PARTIAL** implemented in part or used in
one project only · **DESIGNED** in an ADR/plan/doc, absent in code · **MISSING** absent,
and where so by decision, the ADR is named.

| # | Capability | Status | Mechanism (evidence) | Hard limit |
|---|---|---|---|---|
| 1 | Code scanning | IMPL | `os.walk` + 22-name ignore list + root `.gitignore` (`scanner.py:24-146`); mtime/size then sha256 change detection; one pretty-printed `file_cache.json` rewritten whole per scan (`cache_store.py:27-119`) | Incremental parse only — graph, brain, evidence and `eos.db` are always rebuilt in full; every file is read on every scan (`scanner.py:311`); nested `.gitignore`, `!` negation, `**` unsupported |
| 2 | Parsing | IMPL (uneven) | Java: classes/methods/fields/annotations/`extends`/`implements`/`new`/field-typed calls/behaviour codes (`plugins/java/structure.py:72-523`, ADR-015: 339/345 dependent recall). Python: imports, top-level defs, one level of methods. JS/TS: regex imports and top-level symbols on **unmasked** text | No interface→implementation dispatch; no local-variable or parameter types; codes only as literals between `throw` and `;`; Python has no calls/decorators/bases; JS commented-out imports still produce edges; Go/Rust/SQL/Kotlin not parsed |
| 3 | Semantic model | IMPL | 9 frozen dataclasses; `FileSemantic` has 15 fields incl. `package, annotations, fields, type_refs, calls, thrown, codes, parsed_at, role` (`knowledge/semantic.py:10-230`) | Only the Java plugin fills the structural fields; empty list = "did not look" (recorded by coverage) |
| 4 | Knowledge model | PARTIAL | Generic `KnowledgeNode(id,type,label,path,language,tags,metadata,doc)` and `Dependency(source,target,kind,weight=1,metadata)`; one node per **file**; 7 edge kinds (`import, extends, implements, field, new, calls, folder-hierarchy`) (`knowledge/model.py:10-93`, `builder.py:55-419`) | No symbol-level nodes, no weights (always 1), no Linker/backlinks stage, no Component/Service/EntryPoint types — all three are DESIGNED (ADR-005, `ARCHITECTURE.md:86-87`) and absent; `symbol_index` is built and never read (`builder.py:68,91-95`) |
| 5 | Code facts / provenance | IMPL (narrow) | `fact(subject_kind, subject, predicate, object, origin, confidence, detector, source_ref, observed_at)` + `coverage(detector, predicate, files_eligible, files_with_hits, hits)` + `scan_exclusion`, streamed via `evidence.jsonl` (ADR-014; `index.py:270-309,1138-1224`). 11 predicates from 6 detectors. `eos why` prints "looked and found nothing" vs "never looked" | Every fact is `origin=extracted`; `documented/inferred/verified` and `POSSIBLE` are defined and never produced (ADR-024 admits it). Nexus: 675 facts, all extracted; a service: ~48–50k |
| 6 | Business workflow knowledge | PARTIAL (extension) | `extensions/journeys.py` (743 LOC) resolves configured step chains (host DB snapshot) to owning classes with `resolution ∈ {bean, impl_cls, shared, ambiguous, elsewhere, unresolved, no-bean}` and `candidates` (ADR-013); `extensions/procedures.py` indexes a host scenario catalogue (27 procedures / 184 steps / 33 states); core's only workflow signal is `trace`'s "N named components have no caller" (ADR-017) | Only svc-cpq-ordercapture has journey steps (465); nexus journey tables are **empty** since journey docs became notes (W-04). ADR-017 deliberately refuses a core "workflow" entity |
| 7 | Retrieval / search | IMPL (lexical only) | Six paths, all word-matching (§2.4). No stemming, synonyms or embeddings; C-10 is `DECIDED` out (ADR-001/010; `memory_audit.py:255-261`) | recall@1 on the nexus golden set fell 0.94 → 0.67 within one day as ~160 procedure/moved-section notes joined the store; r@3 holds at 0.97 ("fails" ≠ "failure") |
| 8 | Context assembly | IMPL | `build_context`: 4 chars/token, work ≤ 8%, notes ≤ 15%, brain heading dedup, greedy **prefix** fit (`inspector.py:428-533`). Task brief: 1,500 tok at 3.0 chars/token, sections in priority order, exempt `PROCEDURE/RULE/ROUTE/Apply` lines, `--task-only` returns "" when nothing matched (`brief.py:283-443`) | Branch (SessionStart) brief has **no** character budget: 3,055 chars measured; three token estimators (3.0 / 4 / 4) drift; prefix fit can drop the orientation behind a large target file |
| 9 | Semantic memory (notes) | IMPL | One Markdown file per note, hand-rolled front matter (`kind, title, created, updated, source, tags, scope, scope_hashes, session`), 5 kinds with required sections (§2.3), sha256 scope hashes → `note audit`, guarded `amend`, credential/placeholder/duplicate/scope guards (`notes.py:110-1080`) | No `supersedes` (DESIGNED, OM-40), no `invalid_at`/expiry/archive, no inter-note links; dedup is exact digest or title tokens; every search re-reads the whole corpus |
| 10 | Procedural memory | IMPL | `kind: procedure` note with parsed `## Steps` (+ Prerequisites, Success, When not to use, Rules ≤ 600 chars, engine-appended Known failures); counters `runs_ok/runs_failed/last_verified/last_execution` moved only by `run finish` (ADR-023); confidence word derived at read (ADR-024) | Steps are prose: no parameters, typed I/O, per-step outcome or preconditions as data; counters are a racy read-modify-write of a git-tracked file; 15 procedures in nexus, 4 ever run, 11 `unverified` |
| 11 | Episodic memory | IMPL (host-fed) | `executions.jsonl`: `start/event/finish` lines folded on read; `Record` 15 fields, `Event` 11 fields; 7 event kinds (`ran, read, changed, called, verified, noted, decided`); outcomes `ok/failed/abandoned`; references only, never payloads/prompts/diffs (ADR-019/022) | Capture is only as complete as host wrappers: 51 runs / 1,929 events **all in nexus**, 0 in any of 20 services; 34% of events are CI polling; `ms` has 1-second resolution; `work_item` set on 0/51 runs |
| 12 | Session history | PARTIAL | One id across all stores (`--session` → `EOS_SESSION` → `[telemetry] session_env` → `CLAUDE_CODE_SESSION_ID`); filled into notes, work events, runs, verifications, telemetry, routing | No per-session view (`brief --session` only marks "(yours)"); README's "hook exports `EOS_SESSION`" cannot work — attribution works only because Claude Code exports its own id; 2,167 of 5,054 telemetry lines carry no session; state files under `~/.local/state/eos/` never garbage-collected |
| 13 | Tool / action history | IMPL | `bin/eos-event` (POSIX sh, 37 ms) and `eos run event` (Python, 104 ms) append one event; run id via `EOS_EXECUTION` env or pointer file `~/.local/state/eos/current/<session>`; `run tools`, `run diff`; never fails the wrapped command | Only wrapper-routed actions are seen; no argument shapes; shell helper has no credential guard and ignores `session_env`; subagents inherit the parent's session id so their events land on the parent's run |
| 14 | Planning / decomposition | MISSING (by design) | "It is deliberately not a planner" (`work.py:30-34`); the brief refuses to improvise steps (`brief.py:310-311`) | `planning` exists only as a routing label |
| 15 | Execution | MISSING (by design) | ADR-018 "an adapter executes; EOS records"; the only subprocesses are git, `grep` (bench), venv/pip (UI) and hook→`eos` | `eos verify` records a run someone else made; `draft-test` writes a skeleton to `.eos/data/candidates/`, never compiled |
| 16 | Agent routing | MISSING | One generated profile (`eos-researcher`); `--agent claude|devin` is a label; the optional PreToolUse hook rewrites only the **model** of an untyped/`general-purpose` subagent | No agent registry, dispatch, capability matrix or hand-off protocol; the generated agent tells the agent to call MCP tools that are not registered on the default `cli` surface |
| 17 | Model / effort selection | IMPL (advisory, no learning) | 12 task types from 146 weighted keywords + 4 ordered rules → 7 factors (`scope .15, file_count .15, dependency_count .10, architectural_impact .20, reasoning_required .20, failure_risk .15, uncertainty .05`) → cuts `.15/.35/.55` → cheapest registry model meeting `(min_reasoning, min_coding)` → effort clamped to what the model accepts; one decision per open run (`decided` event); trace with task hash, never text (ADR-025; `routing/*`, 1,441 LOC, 189 tests) | In practice one answer: 58 of 67 nexus decisions are `sonnet/medium`; main sessions actually ran opus (2,058 of 2,816 msgs); nothing applies the advice (`hook = false`); `adjust_for_history` is an identity function; English keywords collide with domain words ("rate plan", "PR", "test env") |
| 18 | Skills / plugins | IMPL (one defect) | Static `LanguagePlugin` list (`detect`, `parse_file`); index extensions (`SCHEMA/COUNTS/QUESTIONS/sources/load` against `BuildContext`, ADR-013); generated `SKILL.md`, agent, 3 hooks (+1 optional), `AGENTS.md` block, `[ai] surface` | No plugin discovery; a new fact predicate requires editing `builder.py`; extensions hook indexing only, not scanning. **Defect:** the runtime updater copies `*.py` only, so `eos ai update` from `.eos/runtime/` fails with `FileNotFoundError` on the `.md` templates (`lib/updater.py:30-38`) |
| 19 | MCP | IMPL (off by default) | Hand-rolled newline JSON-RPC over stdio; 12 tools pinned by a test; only `add_note` writes; roster 4,892 chars (~1.2k tokens) (`mcp_server.py`) | Off since 0.34 because 18 per-service servers cost 2,194 tokens per session and were reached for in 4 of 25 sessions (ADR-021); `get_graph` returns up to 33.8 MB; README lists a `get_history` tool that does not exist |
| 20 | Impact analysis | IMPL (direction-limited) | Recursive CTE over 6 edge kinds, depth 1–5, 500 rows per direction (`index.py:533-597`); `--include facts|coverage|history`; `trace` prints routes, forward reach, codes and the runtime-wired count (ADR-017) | A call through an interface-typed field stops at the interface — implementations are never reached (`implements` edges point the other way), which truncates forward traces on Spring constructor-injection code; graph.json fallback is import-only depth 1; no edge weights or "likely impacted" ranking |
| 21 | Persistence | IMPL | Files are the truth; `eos.db` derived, digest-gated (`_sources_digest` covers notes, ledgers, brain, graph, evidence, extensions, git HEAD), built into a temp file with `journal_mode=OFF`, fsynced, `os.replace`d (`index.py:400-491`); read-only connections with an authorizer | Full rebuild of a 56 MB service index whenever one note or ledger line changes; no incremental path; no ledger retention; orphaned `eos.db.*.tmp` from a hard kill present in nexus |
| 22 | SQLite schema | IMPL | 21 core tables + FTS5 `search(source, ref, title, body, terms)` + 22 indexes; extensions add 7 (§2.3) | `note` has no `procedure/execution/counter` columns, so lesson↔run and procedure↔run join only through the ledger tables; FTS does not weight titles |
| 23 | Learning | PARTIAL | Procedure counters; `run finish --failed` refused without a lesson and writes a `kind: lesson` note (`## Seen again` on repeat); `verified` rung only when a run passed **and** a test reaches and names the code; confidence derived, never stored; `procedure audit` recomputes counters (ADR-018/023/024) | Nothing changes the router, ranking, or a procedure's status; no note-usefulness signal (which notes were read is not tracked); `route --stats` joins decisions to outcomes and stops there |
| 24 | Verification | IMPL (recording) | `eos verify <code> --outcome passed|failed|errored --command …` appends to `verifications.jsonl` with HEAD, log path and output sha256; a **person** may add one of 5 verdicts; `draft-test` grounds class/method/exception/accessor against source by regex and refuses when it cannot | Nothing executed or compiled; Java/JUnit only; nexus holds 1 verification record |
| 25 | Telemetry / cost | IMPL (3 defects) | Per command: name, flag **names**, ms, chars/4, session (ADR-019); `eos cost`, `work stats`, `bench` (ground truth or labelled baseline, ADR-011) | `Timer.rebuilt` never set (0 of 5,054 lines); `ok` = "no Python exception" so exit-1 commands count as ok; "median tokens" is a mean (`telemetry.py:252`); 36% of nexus calls are eval traffic |
| 26 | Token efficiency | IMPL (gaps) | Brief budget and clipping, context caps, brain dedup (45% duplication removed on one service), `rules --limit 20` (37,874 tokens unbounded), provenance kept out of `graph.json`, one surface | Branch brief unbudgeted; `graph.json`, `file_cache.json` and MCP results pretty-printed; nothing measures tokens a session actually saved |
| 27 | Failure / recovery | IMPL (no locking) | Atomic index swap; half-written scans detected by count/mtime/header checks; ledgers skip bad lines, an event for an unknown id creates the run; hooks exit 0 on every failure; Stop hook asks once | No locks anywhere: counters, `## Seen again`, telemetry and trace trims are read-modify-write; `config.toml` rewrites drop comments |
| 28 | UI | PARTIAL (peripheral) | FastAPI + React/Cytoscape over `~/.eos-ui/eos.db` and `graph.json`; watchdog watcher runs incremental scans (and drives Graphify in nexus) | Reads **none** of the memory stores; nothing in `core/` depends on it |

### 2.3 The stores, exactly

**Files (source of truth)** — all in the knowledge directory (`[knowledge] dir`; in nexus
`.devin/knowledge/<project>/`, one shared git tree for 22 projects):

| Store | Format | Lifetime | Writer |
|---|---|---|---|
| Notes `<date>-<slug>.md` | Markdown + front matter; kinds `defect` (Root cause/Solution/Metric), `finding`, `procedure` (Steps…), `lesson` (What went wrong/What was learned/Next time; `execution:`), `decision` (Why/When/Component) | durable, audited for staleness | `eos note add`, `run finish --lesson`, MCP `add_note`, host generators |
| `work.jsonl` | events `open, claim, log, block, unblock, done, drop`; status is the fold | hours–days | `eos work *` |
| `executions.jsonl` | `start / event / finish`; 7 event kinds; 3 outcomes | permanent, append-only | `eos run *`, `eos-event` from wrappers |
| `verifications.jsonl` | one object per recorded run; verdict by a person | permanent | `eos verify` |
| `.skips.jsonl` | "nothing to note" per session | — | `eos note skip` (0 files exist in nexus) |

**Derived, under `.eos/data/`:** `cache/file_cache.json` (semantic cache), `brain/{_index, TechStack, EntryPoints, Architecture, AI_SUMMARY}.md`, `brain/graph.json` (file nodes + edges, pretty-printed, 25–34 MB on a service), `brain/evidence.jsonl` (facts, coverage, exclusions), `last_scan.json`, `eos.db`, and three logs that are **not** indexed: `telemetry.jsonl` (≤ 10,000 lines), `routing.jsonl` (≤ 5,000), `routing-usage.jsonl` (≤ 2,000 sessions).

**`eos.db` tables (21 core + FTS + 7 from extensions):**
`meta`, `build_issue` · `note`, `note_tag`, `note_scope` · `work_item`, `work_holder`, `work_event` · `execution`, `execution_event`, `verification` · `brain_doc`, `node` (nid renumbered each build), `node_symbol`, `edge(src, dst, kind, imported)` · `git_commit`, `git_commit_file`, `git_commit_ticket` · `fact`, `coverage`, `scan_exclusion` · `search` (FTS5, `unicode61`, no stemmer) · extensions: `journey_snapshot`, `journey_step`, `flow_step`, `journey_doc`, `catalogue_procedure`, `procedure_step`, `procedure_state`.
Nexus: 6.2 MB (note 236, execution 51, execution_event 1,929, fact 675, git_commit 1,109, search 334). A service: ~56 MB (fact 48,357, edge 31,693, node 3,782, execution 0).

### 2.4 Retrieval, exactly (six paths, all lexical)

1. **Note search** (`notes.search_notes`, used by `note search`, injection, brief, MCP, `note eval`): tokens = Unicode letters/digits, casefolded, > 2 chars (2-letter kept if all-caps or non-ASCII; `NAME-123` is one word, bare prefix dropped); unsmoothed IDF `log(N/df)`;
   `relevance = min(1, 0.5·cov(title) + 0.3·cov(tags) + 0.2·cov(body))` where `cov(S) = Σw(matched query words in S)/Σw(all query words)` — **query coverage, not Jaccard**; absolute threshold 0.15; relative floor 0.4 × best; O(N) disk reads per call (0.10 s for 236 notes).
2. **Index search** (`eos query --search`, MCP `search_index`): FTS5 BM25, words quoted as phrases and **OR**ed, equal column weights, kept when `rank ≤ 0.5 × best`; LIKE fallback without FTS5.
3. **Procedure picker** (`brief.best_procedure`): title and tags only; accepts `score ≥ 0.15`, or ≥ 2 matched words with `Σw ≥ log(min(7, √N))`; a single matched word counts only when the prompt has ≤ 2 wording words (the 1.1.x fixes for "PR aç", "planı incele", "koş").
4. **Related notes in the task brief**: `search_notes(limit=9)` minus procedures and bulk sources, must share a title/tag word with the task, ≤ 3 shown.
5. **Run ranking** (`executions.ranked`): the procedure's runs, else runs sharing a non-ubiquitous word with the task, else all; target-matching first, then recency; an older failed run with a lesson is surfaced on its own line, never promoted.
6. **Symbol lookup**: case-insensitive substring over cached symbol names, capped at 100, unranked; `get_parent_implementation` inlines 30 lines of live parent source.

Measured: nexus.tsv r@1 0.667 / r@3 0.970 / r@5 1.0 (was 0.94 / 1.00 the morning the corpus was 80 notes smaller); svc-cpq-ordercapture 0.95 after IDF + title weight (from 0.70); nexus-procedures r@1 0.69. The host has a **third** retrieval implementation, `automation/eos-query.sh notes` (rg + awk IDF, title × 3), the one agents actually use (159 calls in 134 transcripts) and the one that is not measured by the golden sets.

### 2.5 Context assembly, exactly

- **Task brief** (`eos brief --task`, run by the UserPromptSubmit hook on every prompt): `TASK_BUDGET = 1500` tokens × 3.0 chars = 4,500 chars. Priority: PROCEDURE (counters, CONFIDENCE, RULE lines whole, steps clipped 240, prerequisites/success 100) → LAST RUNS (3 + catalogue state + "earlier failure worth reading") → KNOWN FAILURES (3, clipped 140) → RELATED NOTES (3 titles) → "Record this run" → ROUTE (2 lines) → branch brief. Once over budget only exempt lines pass. Measured 2,033–2,870 chars on real tasks; 478 chars on "ok, continue" (false positive on a title containing "Continue").
- **Branch brief** (SessionStart): IN FLIGHT ≤ 5 items with unclipped `last:`/`blocked on:` lines, KNOWN HERE ≤ 4 notes matched from branch name + ticket keys, RUNS OPEN ≤ 3, ledger sync sentence. **No budget**; 3,055 chars measured.
- **`build_context(budget=12000)`** (`eos context`/`compose`/MCP `get_context`): header → Work In Flight ≤ 8% → Task → Target file ≤ 12,000 chars → "What Is Known About The Target" (impact depth 2, codes with coverage grade) → brain orientation (5 docs, `## Components/Module Structure/Folder Structure` dropped, repeated headings deduplicated first-wins) → Accumulated Knowledge ≤ 15% (ranked by task, else newest first; bulk sources last; 25 unfitted titles listed). Fit is a greedy **prefix**: the first section that does not fit ends the document.
- **Host injection** (`automation/hooks/note_inject.py`, PostToolBatch on Read/Edit/Write): exact-scope notes get ≤ 3 full bodies (hand-written before bulk; api-inventory/repo-topology never), everything else in the store a digest of ≤ 8 titles; 3,000 tok × 2.7 = 8,100 chars per batch; per-session dedup; instruction files skipped. Measured median 1,863 / p95 7,621 tokens per session against a p95 target of 4,000 — **the target is missed and moving the wrong way** (09-24: 1,501 / 6,689).

### 2.6 The real system is engine + host glue

EOS alone records nothing and delivers nothing; the host makes it live. Nexus adds ~5.8k
lines of Python in `automation/hooks` + `automation/lib` beside EOS's 18.1k:

| Host part | What it does | Measured |
|---|---|---|
| `eos-brief.py` (SessionStart) | nexus brief + any service with an open run or claim | 23 sessions, median 657 tok |
| `eos-prompt.py` (UserPromptSubmit) | `eos brief --task --task-only` for nexus + ≤ 3 named services; NOTES ELSEWHERE scans **every** store's front matter per prompt because the index is per project; cap 6,000 chars; per-session block dedup | 18 sessions, median 2,100 / p95 6,085 tok |
| `note_inject.py` (PostToolBatch) | the only automatic push of knowledge (above) | 52 sessions, 159,492 tok total |
| `note_gate.py` (Stop, exit 2) | blocks a session that edited FM source and wrote no note — "instruction-driven note writing produced 9 notes on 2 calendar days; a Stop hook exiting 2 does" | 99 organic session-stamped notes from 34 sessions |
| `wrapper_guard.py` (PreToolUse Bash, exit 2) | refuses raw psql/mongo/kubectl/mvn/cat-on-wrappers/`rg`… with the wrapper to use instead | **264 `# guard:allow` overrides in 5 days** (~70/day), mostly "read"/"cat"/"rg" |
| `eos-usage.py` (Stop) | folds the transcript into `routing-usage.jsonl` | 16 sessions |
| `automation/lib/eos-capture.sh` → `eos-event` | EXIT-trap capture in 20 of 73 scripts (19 of the 26 wrappers in `CLAUDE.md`) | 1,929 events; `bb` 761 (CI polling 34%), `db` 303, `bssapi` 295 |
| `eos-query.sh notes|parent` | the retrieval agents actually reach for; bypasses `eos` telemetry | 159 + 54 calls in 134 transcripts |
| `context-budget.sh` | always-loaded set gate; rule ledger (159 rules, K/Z/P/O/R/T classes); arrival tests | 13,418 tok loaded (target 16,000; was 113,493 before H-05 and 38,114 before C-01…C-06) |

The engine's own per-service hooks (`eos-brief/eos-prompt/eos-close.py`, installed in 13
services) **never fire**, because sessions start in nexus. `eos-close.py` has no nexus
equivalent, and 8 of 13 in-flight work items are stale. `eos-route.py` is not installed
(`hook = false`).

### 2.7 Measured reality

| Question | Number |
|---|---|
| Notes | 841 in 22 stores, 2.05 MB; `finding` 809 (96%), procedure 15, defect 10, lesson 4, decision 3 |
| …of which learned by a session | **99 organic, session-stamped (16.6% of bytes)**; 591 (70%, 73.5% of bytes) are generated or verbatim-moved sections (api-inventory 206, agents-md 182, context-budget 106, journey-map 56, H-05 31) |
| Stale notes (`note audit`) | 257 = 60 hand-written + 197 generated (195 of 206 api-inventory) |
| Java files covered by any note | 102 of 2,402 (**4.2%**; spec target ≥ 15%) |
| Runs | 51 (45 ok / 3 failed / 3 open), 1,929 events, 9 lessons — **all in nexus; 0 in any service**; `eos doctor --memory` nexus 21/21, svc-cpq-ordercapture 7/21 |
| Work | 14 items, 13 in flight, 8 stale; 0 runs reference a work item |
| Routing | 67 decisions (sonnet 60, haiku 4, opus 3); 0 applied; main sessions ran opus-5-5 |
| Sessions that asked EOS beyond the brief | 54 of 134 transcripts (40%); 15 of 62 real telemetry sessions (24%) |
| Largest context cost in the workspace | harness "file changed on disk" notices: **779,053 tokens**, median 10,719 per affected session — larger than every hook combined |
| Version drift | all 20 service runtimes at 1.2.0 against engine 1.2.2; Graphify graphs a week stale; store README describes superseded ranking |

### 2.8 Designed but not implemented; documentation not matched by code; defects

**Designed, absent:** Linker/backlinks stage and typed Component/Service/EntryPoint
entities (ADR-005, ARCHITECTURE) · edge kinds `monorepo-parent/shared-dep/manual-link`
(`model.py:15`) · detector/parser/analyzer plugin split (ARCHITECTURE, README) · `decision`
`supersedes:` (OM-40) · `policy.adjust_for_history` reading `trace.outcomes_by` (ADR-025,
identity today) · the routing-outcome analysis ("not built yet", plan §11a) · a per-session
cross-store listing (OM-14) · Tauri, nested/monorepo `.eos`, more languages (phases.md) ·
C-10 semantic retrieval (decided out) · stemming (decided out until a second golden set).

**Documentation claims that do not hold:** MCP `get_history` (does not exist; 12th tool is
`search_index`) · "two hooks" (three always + one optional) · hook "exports `EOS_SESSION`"
· a Stop hook running `eos route --usage-from` (host-only) · telemetry `rebuilt`/`failed`
columns (never set) · "medians, not means" (tokens are a mean) · prompt hook "prints
nothing when nothing is recorded" · brief "capped at a handful of lines" · runtime
"timestamped backups" (removed, ADR-001 not amended) · `.eos/data/` "immutable" (holds
`eos.db` and three logs) · SSOT "generators never re-parse source" (testgen, journeys bean
index and parent lookup re-read source; `find_symbol` reads the cache, not the model).

**Defects verified:** `eos ai update` fails from `.eos/runtime/` (templates not copied) ·
`Timer.rebuilt` never assigned · `ok` ignores exit codes · false-positive task brief on chat
("ok, continue") · unbudgeted branch brief · agent template names unregistered MCP tools ·
`eos-event` ignores `session_env` · orphaned `eos.db.*.tmp` · 13/20 runtimes at 1.2.0 ·
`_impact_from_graph` ignores `depth`.

### 2.9 What is architecturally strong, and what is not

**Strong.** Honest and deterministic (no model in any decision path; every number printable;
"silence is not absence" engineered through coverage rows). File-first with a disposable
SQLite projection (git-friendly, crash-safe, digest-gated). Memory separated by lifetime with
one writer per store. Real write guards (credentials, placeholders, scope, duplicates, amend
invariants). Genuinely good structural Java parsing. Measurement culture: ADRs, executable
acceptance checks, golden sets, `bench` against ground truth, document-vs-CLI tests, 931
tests. Zero-dependency core. An extension point that kept host knowledge out of core.

**Weak.** A 2,896-line CLI file and a 1,766-line `notes.py` that mixes storage, validation,
credential scanning, retrieval, rendering, procedures and confidence. A lexical retrieval
ceiling with recall@1 decaying as the corpus grows and three unmeasured implementations.
A keyword router that gives one answer 87% of the time and whose learning seam is inert.
A file-granular, weightless graph with no interface dispatch and only import edges outside
Java. Full rebuilds of everything on any change. Four ignore lists, three token estimators
and two credential guards that drift. Concurrency by convention. Capture that depends on
the host, so episodic memory exists in one project of twenty-one. Feature slices of a
thousand lines designed, built and released within a day, accepted by presence checks
(`memory_audit` verifies that functions exist and rows are present, not that they are good).
And the deployment's own honest sentence: "21/21 in the one project the work was seeded in,
6/21 in the projects the work is meant to serve."
## 3. RAGFlow architecture findings (infiniflow/ragflow, commit `313ca90`, 2026-09-24)

Method: source read at the module level (`rag/`, `deepdoc/`, `agent/`, `memory/`, `api/db`,
`mcp/`, `common/`), tests used as evidence of what runs; READMEs used only to find code.
RAGFlow is a multi-tenant SaaS-shaped RAG platform: MySQL/Postgres for metadata (Peewee), a
document engine (Elasticsearch, Infinity, OpenSearch or OceanBase) for chunks and vectors,
MinIO for blobs, Redis/Valkey for queues and caches, GPU-optional OCR/layout models, and an
LLM in every loop. That posture is the opposite of EOS's, which is why only *shapes*
transfer, never components.

### 3.1 Retrieval, ingestion, chunking, embeddings, hybrid search, reranking, GraphRAG

**Ingestion.** `queue_tasks` splits a document into tasks (PDF 12 pages per task, 22 for
`paper`; tables 3,000 rows) and stamps each with `xxh64(chunking_config ∥ doc_id ∥
from_page ∥ to_page)`; a previous task with the same digest has its chunks **adopted as-is**
(`task_service.py:544-637`). Chunk `id = xxh64(content + doc_id)` — content-addressed and
idempotent (`chunk_service.py:241`). Redis Streams (`XADD`, `XREADGROUP count=1 block=5`,
ack after handling), loop-local semaphores (tasks 5, chunk builders 1, embedding 1, MinIO
10, KG 2), heartbeats to a ZSET, a lock-elected reaper. A shadow-run comparator
(`TE_RUN_MODE=1`) diffs the legacy and refactored executors. Parsers are a `FACTORY` by
`parser_id` (`naive, book, laws, paper, manual, qa, table, tag, presentation, one, email,
picture, resume, audio`); **code files are plain text split on `"\n!?;。；！？"`** — no
AST-aware chunking anywhere (`naive.py:1324-1329`). DeepDoc is ONNX OCR/layout (11
classes)/table-structure (6 classes) plus an XGBoost box-join classifier, KMeans column
detection and inline coordinate tags `@@page\tx0\tx1\ttop\tbottom##` carried through
chunking.

**Chunking.** `naive_merge` groups delimiter-split sections under `chunk_token_num` (512
default) with `OVER_CAP` (one boundary overflow allowed) or `UNDER_CAP`; overlap is the
previous chunk's tail prepended; tokens counted with `tiktoken cl100k_base`
(`rag/nlp/__init__.py:1286-1510`). `hierarchical_merge` links bullet/title levels by binary
search; `tree_merge` emits each body block **prefixed with its full title path**
(breadcrumb chunking). Parent/child ("mom") chunks: the parent is stored once with
`available_int=0`, children carry `mom_id`, and retrieval swaps children for the parent
scored by the mean child similarity (`search.py:1085-1139`). LLM enrichment per chunk —
`important_kwd` keywords, `question_kwd` questions (which then **replace the content in the
embedding**), document metadata, `tag_feas` (a lift ratio `P(tag|hits)/P(tag)` over a tag
KB, no LLM) — cached in Redis 24 h. A hidden TOC chunk (`toc_kwd`, `available_int=0`) is
LLM-built even where the PDF outline exists. RAPTOR (deprecated in the UI since v0.27.0)
clusters by an O(N) 1-D **watershed over adjacent-chunk cosine**, summarises LLM-extracted
**claims whose verbatim quotes are validated as substrings of the cited chunk with recorded
offsets** (`structure.py:931-1034`), and carries `source_chunk_ids` provenance.

**The chunk record** (`rag/nlp/__init__.py:422-429,1022-1034`): `id, doc_id, kb_id,
docnm_kwd, content_with_weight, content_ltks, content_sm_ltks, title_tks, title_sm_tks,
important_kwd/tks, question_kwd/tks, page_num_int[], top_int[], position_int[], img_id,
doc_type_kwd, mom_id, chunk_order_int, create_time, available_int, pagerank_fea, tag_feas,
tag_kwd, q_<dim>_vec` plus row-kind discriminators (`knowledge_graph_kwd, raptor_kwd,
toc_kwd, compile_kwd, source_chunk_ids`). ES maps by suffix (`*_tks` scripted similarity
`idf·min(tf,1)` — binary TF, normalised IDF; `*_ltks` BM25; `*_kwd` keyword; `*_fea`
rank_feature; `*_<dim>_vec` dense_vector HNSW cosine for 512/768/1024/1536 only).

**Embeddings.** ~50 providers registered by `_FACTORY_NAME`; `encode_queries` separate for
asymmetric models; the stored vector is `0.1·title + 0.9·content` with the title vector
encoded **once per document** (`embedding_utils.py:49,160-199`); no re-normalisation; a
model-switch **drift check** re-encodes sampled chunks and allows the switch only when the
average cosine to stored vectors is ≥ 0.9 (`dataset_api_service.py:1330-1423`). Jina
multi-vector outputs are mean-pooled; `MatchSparseExpr`/`MatchTensorExpr` are unused.

**Full text.** The tokenizer is `infinity.rag_tokenizer` from `infinity-sdk` (not in the
repo; darts trie, OpenCC, Snowball stemmer in 16 languages, WordNet lemmatizer, POS/NER).
`FulltextQueryer.question()` has two paths: **"Chinese" for any query of ≤ 3 tokens**
(fine-grained sub-tokens, proximity `~2`, synonyms `^0.2`, `minimum_should_match` 0.3 →
0.1 on retry → 0 when vector weight ≥ 0.8) and English for longer queries (per-token weight
`(tk^w "syn"^(w/4))`, **every adjacent pair as a phrase `"t1 t2"^(2·max(w))`, no
minimum-should-match**). `rmWWW` strips question words, stop words and fillers first.
Field boosts: `title_tks^10, title_sm_tks^5, important_kwd^30, important_tks^20,
question_tks^20, content_ltks^2, content_sm_ltks` with `best_fields`. Term weights
(`term_weight.py:192-274`): `w = (0.3·idf(freq, 1e7) + 0.7·idf(df, 1e9)) · ner · pos`,
normalised to Σ = 1, with type multipliers (numeric 2, 1–2-letter 0.01, org/place 3) and an
OOV prior `max(10, 300/2^((letters−3)/2))` — **`rag/res/term.freq` is not shipped, so the
df half falls back to priors**; the weights are dictionary statistics, not corpus IDF.
Synonyms: dictionary → Redis override → WordNet (`[a-z]+` only), ≤ 8 per token.

**Hybrid search.** One façade (`Dealer.search/retrieval`, `rag/nlp/search.py:243-928`)
over seven engines whose semantics differ. **Default ES path: lexical-gated dense
ranking, not a union** — the kNN leg is filtered by the same bool query that carries the
`query_string`, the text leg's boost is zeroed (fusion weights `"0.001,1"`, pinned by a
test), the top `rerank_candidates_count` (64) are re-scored in Python:
`sim = (1−w)·token_coverage + w·knn_score + rank_features` with `w = vector_similarity_weight
= 0.3` (`search.py:604-629`). `token_coverage` is **weighted query-term coverage**
(unigrams × 0.4, adjacent bigrams × 0.6; `query.py:180-209`), not BM25 and not symmetric;
chunk-side field repetition (`title×2 / important×5 / question×6`) is a no-op because the
weights are commented out. Infinity gates the same way with atan-normalised fusion;
OpenSearch and SereneDB do a real union (`min_max` + static `[0.5,0.5]`; SQL FULL OUTER
JOIN). Empty-result fallbacks: dense-only at 0.17, doc-scoped filter, min_match 0.1. The
newest code (`navigation.py:1529-1549`) fuses BM25 and kNN over claim rows with **RRF
k=60** "because RRF needs no threshold calibration"; an internal note records flat
scoring beating tree descent 39.3% vs 20.2%.

**Reranking and rank features.** With a reranker, `sim = 0.7·coverage + 0.3·rerank_score +
rank_fea` and the **embedding similarity is not used at all**; 25 providers with min-max
normalisation only when a provider leaves [0,1]; built-in rerankers were removed in v0.18
("minimal impact, significantly slower"). Rank features are **additive and unscaled**: KB
`pagerank` (0–100) is added **raw** to a [0,1] similarity — a priority tier by another name
— and tag-feature cosine is multiplied by 10; both bypass `similarity_threshold`. A bounded
**feedback loop** (`chunk_feedback_service.py`, off by default) moves `pagerank_fea` by an
integer budget of 1 per thumbs up/down, split across cited chunks by largest-remainder in
proportion to their retrieval score, atomically.

**GraphRAG.** Build per KB (`rag/graphrag/general/index.py`): chunks concatenated into ≤
4,096-token batches, extractors `general` (MS GraphRAG prompt, two gleaning passes),
`light` (LightRAG), `ner` (spaCy + sentence co-occurrence, **no LLM**); records
`("entity"<|>NAME<|>TYPE<|>DESC)` / `("relationship"<|>SRC<|>TGT<|>DESC<|>KEYWORDS<|>STRENGTH)`;
merge by majority type, `<SEP>`-joined descriptions truncated to 512 tokens, summed weights;
global merge under a Redis lock, `nx.pagerank` on every node; **entity resolution** blocks
by type, rejects candidate pairs whose differing bigrams contain a digit, Levenshtein ≤
⌊min(len)/2⌋ for English, then LLM yes/no in batches of 100; **Leiden on the largest
connected component only** (`max_cluster_size=12`), community reports as JSON
`{title, summary, findings[], rating}`. Persistence: **rows in the chunk index** with
`available_int=0`, discriminated by `knowledge_graph_kwd ∈ {graph, subgraph, entity,
relation, community_report}`; entities carry `rank_flt` = pagerank and precomputed
**`n_hop_with_weight` 2-hop paths**; entity vectors embed the **name only**; legacy
provenance stops at the **document** (`chunk_key = doc_id`). Retrieval
(`graphrag/search.py:139-275`): MiniRAG rewrite → entity kNN (≥ 0.3, N=56) and relation kNN
→ `P(E|Q) ∝ pagerank · sim` (×2 if also found by type), edges on 2-hop paths get
`sim/(2+i)`, top 6 entities + 6 relations within 8,196 tokens, one community report → **one
synthetic chunk of CSV tables** with `doc_id=""` inserted at position 0 of the context —
uncitable. The `answer_type` pool (`ty2ents`) has readers and no writers. GraphRAG and
RAPTOR are **deprecated in the UI since v0.27.0**; the successor "knowledge compilation"
(`knowlege_compile/structure.py`) writes entity/relation rows with `source_chunk_ids`
(chunk-level provenance) and verified evidence quotes, deduplicated by exact name → kNN →
batched LLM judgment.

**Configuration surface (defaults):** `similarity_threshold 0.2` (used twice: kNN cosine
floor and fused-score floor), `vector_similarity_weight 0.3`, `top_n 6`, `top_k 1024`,
`knn_num_candidates 2048`, `rerank_candidates_count 64` (page 1 only when reranking),
`chunk_token_num 512`, `filename_embd_weight 0.1`, `DOC_BULK_SIZE` 32 in code / 4 in env,
`EMBEDDING_BATCH_SIZE 16`, `MAX_CONCURRENT_TASKS 5`.

**Benchmarks.** `rag/benchmark.py` indexes MS MARCO/TriviaQA/MIRACL passages directly and
scores nDCG@10 / MAP@5 / MRR@10 with a worst-first per-query report — but **deletes zero-hit
queries from the qrels**, inflating the scores. `test/benchmark/` measures latency
percentiles and QPS, not quality. Unit tests pin the `0.001,1` fusion weights, the 10.0 tag
score, pagination limits and chunking contracts; nothing tests graph routing or GraphRAG
quality.

**Defects found:** the 1-based/0-based citation mismatch (§3.6); kNN lexical gating makes
semantic-only matches unreachable except via the zero-hit fallback; unscaled priors added to
normalised similarities; field repetition no-op; query semantics change at the 3-token
boundary; `term.freq` missing; `ty2ents`, `MatchSparseExpr`, `MatchTensorExpr`, `_loop_args`
dead; ES rank-feature clauses neutralised by `bool.boost = 0`; auto metadata filters fail
open; the benchmark drops zero-hit queries.

**Strengths.** Content-addressed chunks with digest reuse; one chunking contract; title-path
breadcrumbs; parent/child; rich typed chunk metadata with layout provenance; field-aware
boosts and binary-TF short fields; adjacent-bigram proximity; a bounded, atomic feedback
prior; the graph reusing the chunk index with checkpoints at every phase; verified-quote
evidence gates in the successor. **Weaknesses.** Backend-dependent semantics behind one
parameter name; an LLM call per chunk for every enrichment; no structural chunking for code;
dictionary rather than corpus IDF; noisy WordNet synonyms; a KG output that cannot be cited;
Leiden on the LCC only; seven engines drifting apart.

### 3.2 Agent architecture (Canvas)

- **The engine is a data-driven batch scheduler over a JSON DSL**, not a topological or
  event-bus engine. `Canvas._run_impl` keeps a growing `path` worklist; each pass runs the
  nodes whose `{cpn@var}` data references have finished (`_schedulable`,
  `agent/canvas.py:472-529`), ≤ 5 at a time (`ThreadPoolExecutor(5)`), then appends
  successors from `downstream`, branch `_next` or loop back-edges. Cycles fail
  deterministically; a node whose input was never scheduled is dropped; human-in-the-loop
  pauses by **persisting `path`** and returning a `user_inputs` event (`canvas.py:904-920`).
  Readiness is computed from actual data references, not from edges — pinned by
  `test_canvas_batch_readiness.py`.
- **DSL shape** (`canvas.py:50-88`): `components{id: {obj{component_name, params},
  downstream, upstream, parent_id}}`, `history` (unbounded), `path` (the resumable cursor),
  `retrieval` (per-turn citation pool), `globals` (`sys.query, sys.user_id, sys.files,
  sys.history, sys.date, env.*`), typed `variables`, a dead `memory` list, and `graph` (UI
  layout). **State and definition are mixed**: `Canvas.__str__` dumps params *with outputs*,
  history, path and retrieval, and that string becomes `API4Conversation.dsl` after every
  turn — a session row is a full snapshot rewritten whole.
- **Component contract** (`agent/component/base.py`): `ComponentParamBase` carries
  `message_history_window_size=13, max_retries=0, delay_after_error=2.0,
  exception_method (comment→default value | goto), exception_default_value, exception_goto,
  inputs, outputs`; outputs are `{value, type}` with reserved `_ERROR, _created_time,
  _elapsed_time, _next, _references, _ARTIFACTS`; variable references `cpn_id@var[.path]`
  walk dicts, lists and JSON strings; `invoke` stamps times and catches every exception into
  `_ERROR`; **tools return `str(e)` as the result** so the model can react. Registry by
  reflection over `agent.component`, `agent.tools`, `rag.flow`; the same resolver serves the
  ingestion pipeline (`rag/flow/pipeline.py`, a sequential walk over the same `Graph`).
- **The "ReAct agent" is native function calling** in `rag/llm/chat_model.py:616-927`: up to
  `max_rounds` (5) rounds, tool calls run in parallel with `asyncio.gather`, results appended
  **verbatim**, a "terminal tool" (`rag`) short-circuits, and after `max_rounds` one final
  call with "Exceed max rounds: N". Component-tool timeouts are **not enforced** (only MCP
  calls get the 10 s deadline). A retryable error restarts the **entire** loop and
  re-executes tools that already ran (`:645-647`).
- **Multi-agent = agents as tools.** A lead `Agent` lists child `Agent` components in
  `params.tools`; each child exposes `{user_prompt, reasoning, context}` and gets id
  `parent-->child`. The shipped `deep_research.json` is a lead with three sub-agents.
  Sub-agents never cite.
- **The explicit planner is dead code.** `analyze_task_async`, `next_step_async`,
  `reflect_async`, `rank_memories_async`, `tool_call_summary` (`rag/prompts/generator.py:439-524`)
  and `Canvas.add_memory` have **no callers**; `ReActMode` is unused; only the
  `<CITATION_GUIDELINES>` block of the old prompt format is still consumed.

### 3.3 Task decomposition and planning: the agentic RAG harness

The real decomposition lives in chat, not in the canvas: `rag/advanced_rag/` is a
**LangGraph state machine** (`agentic_rag_graph.py`, `harness/*`) reached from
`dialog_service.rag_agent`:

- `AgenticState` (TypedDict) holds `question, keywords, plan, current_queries, slot_table,
  slot_draft, unresolved_slots, research_feedback, kbinfos, draft, verdict, sca, deadline,
  search_rounds, attempted, fills_found, no_progress`.
- `build_agentic_graph`: `formalize_question → [planner / fan-out] → prefetch → rag_agent
  (slot research) → draft → sca → query_rewrite ↺ → formalize_answer`; exits on
  `no_progress`, pool saturation (60 chunks), `sca_max_rounds`, or < 50 s of budget left.
- **Budgets as constants:** total 180 s, 50 s headroom per round, pass 120 s, prefetch 90 s,
  draft 60 s, SCA 60 s, rewrite 45 s; `_bounded()` returns `None` on expiry;
  `recursion_limit=60`.
- **Modes = effort** (`harness/config.py:44-120`, `ModeSpec{agentic, enable_sca,
  sca_max_rounds, use_fanout, action_max_turns, tools}`): `low` no agent/no tools; `medium`
  SCA ≤ 3 rounds; `high` + planner + fan-out; `ultra` 5 SCA rounds, 6 action turns,
  `graph_explore`; `NAIVE` one retrieve + one compose.
- **Decomposition:** `_expand_fanouts` is one strict-JSON LLM call; sub-questions form a
  **slot table** capped at depth 3 and 8 slots; reflection is the SCA ("sufficient
  context") judge returning structured `sub_queries[{sub_query, satisfied, missing_fact,
  search_hint}]` — not free text.
- **Action session** (`harness/action_session.py`): native tool calling over 9 tools
  (`retrieve, search_chunks, list_chunks, navigate_tree, navigate_structure, calculate,
  graph_explore, web_search, metadata_search`), ≤ 4 turns (6 in ultra); near-duplicate calls
  suppressed at Jaccard 0.8 with a same-call cache; a 400,000-**character** context budget;
  `REDUNDANT` hits collapse to one line; a tool is disabled after 2 empty results; each
  result carries `ToolOutcome.status ∈ {OK, EMPTY, MISS, POOR, REDUNDANT, ERROR}` and
  `evidence_ids`; on budget exhaustion a forced tools-off call, then a deterministic "loose
  clue harvest" so a round never returns empty.
- **Deterministic narrowing** (`harness/grep_sed_narrow.py:245-382`): `grep -n -C`-like
  spans over chunk text — word-boundary regexes (CJK-aware), whole lines, 600 chars of
  context, 1,200 per chunk, 16,000 total, a fallback chain, **tables never narrowed**
  because row position decides table answers.
- A "memory" ledger (`harness/memory.py`) has `add()` callers and **no `search()`/`grep()`
  callers**; the prompt receives the whole pool instead.

Tests (9 files, 883 lines) cover tool-schema contracts and "no ghost args" (every documented
flag is consumed), not graph routing, termination, narrowing or memory.

### 3.4 Memory

RAGFlow's "memory" is two unrelated things. `Canvas.memory` is a dead list. The new
`memory/` package is an LLM-extracted, embedded, per-tenant store in the document engine:

- **Config row** `Memory` (`api/db/db_models.py:1816-1838`): `memory_type` bit flags
  (1 raw, 2 semantic, 4 episodic, 8 procedural), `storage_type table|graph` (graph is a UI
  facet only), `memory_size` default 5,242,880 bytes, `forgetting_policy` whose enum holds
  **only FIFO** (help text says LRU|FIFO), `embd_id/llm_id`, editable prompts, `permissions
  me|team`.
- **Message schema** (index `memory_{uid}`, doc id `{memory_id}_{message_id}`):
  `message_id` (Redis `INCR`), `message_type raw|semantic|episodic|procedural`, `source_id`
  (0 for raw; the raw id for extracted items), `memory_id, user_id, agent_id, session_id,
  content, content_embed (q_<dim>_vec), tokenized_content_ltks, valid_at, invalid_at,
  forget_at, status`.
- **Write path:** the `Message` component's `_save_to_memory` synchronously embeds and stores
  the raw turn, then queues a `Task(task_type="memory")`; the executor's `extract_by_llm`
  makes one call producing `{"semantic":[{content, valid_at, invalid_at}], "episodic":[…],
  "procedural":[…]}` (≤ 5 per type), parsed by stripping fences and `json.loads` (failure →
  `{}`); `embed_and_save` evicts by FIFO — **forgotten first, then oldest `valid_at`** — when
  `current + new > memory_size`, sized by `sys.getsizeof` over content + vector.
- **Read path:** `Retrieval` with `memory_ids` → `MsgTextQuery` (term weighting, synonyms at
  weight/4, adjacent-bigram phrase boosts at 2× max weight) fused with a dense match via
  `FusionExpr("weighted_sum", weights="kw,1-kw")`; filters `status=1`, hides `forget_at`;
  **`invalid_at` is stored and never filtered on**; results are packed by `memory_prompt`
  as raw content **without type, dates or ids**.
- **No reconciliation:** no ADD/UPDATE/DELETE/NOOP pass against existing memories, so
  contradictory facts accumulate; the only policy is FIFO; the raw turn is embedded on the
  request path.
- **Conversation memory:** chat sends the **entire** history every turn and, over budget,
  drops all middle history at once (`message_fit_in`); agents window to `2 × 13` messages;
  `history` and `sys.history` are parallel representations that can drift; runtime replicas
  in Redis with a 3 h TTL and an **unlocked read-modify-write** on commit.

### 3.5 Context engineering

Covered comparatively in §7. The mechanisms: one tokenizer for every model (`tiktoken
cl100k_base`); `message_fit_in` keeps everything or collapses to `[system, last user]` and
**head-truncates** (cutting the knowledge block's tail); `kb_prompt` renders `ID, Title,
URL, document_metadata, Content` and budgets the **whole rendered block** at 97%, skipping
empty chunks without shifting numbering (pinned by `test_kb_prompt_metadata.py`); chat
knowledge is packed to the **entire** model context before history is considered; tool
results are appended verbatim with a 200,000-token retrieval budget (effectively none);
`full_question` rewrites multi-turn queries and resolves relative dates; `decorate_answer`
appends a per-stage timing and token breakdown to every answer.

### 3.6 Provenance and citations

Every turn appends a `{chunks, doc_aggs}` pool; agent retrieval keys chunks by
`sha1(chunk_id) % 500` — **silent overwrite on collision** (~32% at 20 chunks, 58% at 30)
and an `if cid not in r` check against the wrong dict that is always true. Chat numbers
`kb_prompt` blocks from **1** (`generator.py:176`) while both resolvers index from **0**
(`dialog_service.py:881-888`, `reference-utils.ts:79-80`); the Go port documents the
mismatch (`internal/service/kb_prompt.go:92-95`). With ≥ 7 messages the agent buffers the
answer and runs a **second LLM pass** (`citation_plus`) to insert citations. Chat has a
deterministic fallback (`insert_citations`: per-sentence token + vector similarity) and
`repair_bad_citation_formats` (`(ID: 12)`, `【ID:12】`, `ref12` …). Citations resolve to a
structured reference (chunk id, document id/name, dataset, positions, similarity) and
`doc_aggs` narrows to documents actually cited.

### 3.7 MCP

- **As a server** (`mcp/server/server.py`): a separate Starlette/uvicorn process proxying
  the REST API via `httpx`; two auth modes (`self-host` single tenant with `--api-key`;
  `host` multi-tenant bearer); SSE and stateless streamable-HTTP, **no stdio**; **three
  tools** (`ragflow_retrieval` with page/`page_size ≤ 100`/`top_k ≤ 1024`/rerank/keyword,
  `ragflow_list_datasets`, `ragflow_list_chats`); a fixed 512-candidate rerank window keeps
  pagination stable; 300 s ± 30 TTL caches with LRU 32. `list_tools` **embeds the caller's
  live dataset and chat lists in the tool descriptions**, so the listing grows with the
  tenant.
- **As a client** (`common/mcp_tool_call_conn.py`): tool schemas are **snapshotted at
  registration** into `MCPServer.variables["tools"]` and copied into the agent DSL; each
  session owns a daemon thread with its own loop; SSE/streamable-HTTP only; requests carry a
  deadline and an abandoned flag; `tool_call` returns strings on timeout/error; only
  `result.content[0]` as `TextContent` is used (images, resources, `structuredContent`
  dropped); SSRF DNS pinning at registration but **not** at run time (TOCTOU);
  `strip("Bearer")` strips characters, not the prefix.

### 3.8 Workflow, task executor, persistence, observability, failure

- **Agent runs are in-process**: the API drives `canvas.run()` and relays SSE; the session
  row is written **after** the run, so a client disconnect loses the turn.
- **Task executor** (`rag/svr/task_executor.py` + `task_executor_refactor/`, the live
  `TE_RUN_MODE=0` path): pure asyncio; Redis Streams one per `(priority, suffix)`,
  `XREADGROUP count=1 block=5 ms` (polling); ack **after** the handler whatever the outcome;
  heartbeats every 30 s to a ZSET, a locked reaper at 120 s; **no XCLAIM** — a crashed
  worker replays its own pending entries under the same consumer name, an orphaned name
  leaves them stuck; `retry_count ≥ 3` → FAIL; xxh64 digest of chunking config + page
  range skips unchanged work before queueing; SETNX "credit" keys prevent double counting;
  cancel via a Redis key with a **3,600 s TTL shorter than the 3 h task timeout**; the
  `@timeout` decorator is a **no-op unless `ENABLE_TIMEOUT_ASSERTION`**; LLM retry delay is
  `base × U(10,150)` = 20–300 s; a shadow-run diff harness (`TE_RUN_MODE=1`) compares legacy
  and refactored executors.
- **Persistence** (Peewee): `UserCanvas` (definition, `release`), `UserCanvasVersion`,
  `CanvasTemplate`, `API4Conversation` (`message, reference, dsl, round, errors, tokens,
  duration, thumb_up`), `Conversation`, `Dialog` (`top_n=6, top_k=1024,
  similarity_threshold=0.2, vector_similarity_weight=0.3, rerank_candidates_count=64`),
  `MCPServer`, `Memory`, `Task` (`progress, retry_count, digest, chunk_ids`),
  `PipelineOperationLog`, `TenantLLM` (`max_tokens=8192, used_tokens`), `TenantLangfuse`,
  `Search`. **Analytics columns exist and are never written** (`tokens, duration, thumb_up,
  used_tokens`). Memory messages live in a different store than their config.
- **Observability:** typed per-node events `node_started{thoughts}`, `node_finished{inputs,
  outputs, error, elapsed_time}`, `workflow_finished{usage}`; a per-run `token_usage_sink`
  ContextVar (single chokepoint, thread-locked) whose totals are **not persisted**; tool
  traces in Redis with a **600 s TTL** under a key that the logs endpoint looks up by a
  different id for session runs; Langfuse per tenant with an `auth_check()` network
  round-trip per bundle and a deliberately never-flushed `close()`.
- **Failure handling as data:** `exception_method` (`comment` → default value; else `goto`
  branch), cooperative cancel checks, human-in-the-loop as the only intra-run recovery point;
  no per-node checkpoint.
- **Sandbox** (`agent/sandbox/`): code execution isolated from the API process (details in
  the retrieval report's scope; not load-bearing for EOS).

### 3.9 What RAGFlow teaches EOS

**Transfer (deterministic shapes, no LLM, no infrastructure):**
1. The **typed memory row** — `kind ∈ {raw, semantic, episodic, procedural}`, `source_id`
   from derived fact to raw event, `valid_at / invalid_at / forget_at / status` — *and the
   discipline RAGFlow forgot*: filter on `invalid_at` and render kind and dates.
2. **A byte budget per store with deterministic eviction** (forgotten first, then oldest).
3. **`narrow_by_terms`** for large bodies, with the table carve-out, as EOS's injection and
   `get_file` narrowing.
4. **`ToolOutcome.status`** (`ok, empty, miss, poor, redundant, error`) for wrapper results
   and run events; near-duplicate query suppression by Jaccard; disable a probe after N
   empty results.
5. **Budgeted, phase-structured control flow with a deadline per phase** and four
   independent stop conditions — the model for EOS's own verification/consolidation jobs.
6. **DSL-as-data procedures with typed step I/O and `{step@field}` references**, linted the
   way `_schedulable` works: unknown reference, cycle, never-scheduled step — with no
   execution.
7. **Readiness from data references**, **"emit started with a teaser, finished with inputs,
   outputs, error, elapsed"**, and **"pause = persist cursor + pending inputs"** for blocked
   work items.
8. The **sub-agent handoff schema** `{user_prompt, reasoning, context}` for briefs handed
   to subagents.
9. **One numbering authority for citations**, stable ids, a post-hoc "cited ids exist"
   check, and a tolerant citation-format repair table.
10. **Fixed-window pagination**, `list_*` tools instead of live inventories in descriptions,
    deadline + abandoned flag per request, errors as tool text — for the MCP surface.
11. The negative lessons: persist run records **incrementally**; never keep columns nothing
    writes; deadlines that actually fire; never re-execute non-idempotent tools on a loop
    retry.

**Do not adopt:** Elasticsearch/Infinity, Redis queues, MinIO, MySQL, LLM-extracted memory,
embeddings, LLM planner/reflection/citation passes, SSE/HTTP MCP transports, whole-state
snapshots per turn, a canvas engine, deep-research templates.
## 4. Ruflo architecture findings (ruvnet/ruflo = claude-flow 3.45.0, commit `88955d9`)

Method: the TypeScript under `v3/@claude-flow/*` was read from the entry points a user
install actually runs (`settings.json` hooks → `.claude/helpers/*.cjs`; `ruflo mcp start`
→ `cli/src/mcp-server.ts` → `cli/src/mcp-tools/*`; CLI commands), the shipped model router
was executed under Node against sample prompts, and claims were checked against tests,
benchmark receipts and the project's own ADRs. Verdicts: **IMPL** (real, on a default path)
· **OPT-IN** (real, behind a flag or optional dependency) · **DEAD** (real code nothing
reachable imports) · **STUB** (canned or constant output) · **DOC** (described only).

### 4.1 The finding that governs everything else

Ruflo ships **two codebases that do not talk to each other**:

1. `v3/@claude-flow/cli/src/{mcp-tools,commands,services,ruvector}` — what `bin/cli.js`
   and `bin/mcp-server.js` run. Flat JSON-file stores under `.claude-flow/` and `.swarm/`,
   exposed as ~472 MCP tool definitions across 44 files, all loaded eagerly.
2. `v3/@claude-flow/swarm`, `@claude-flow/hooks`, `@claude-flow/providers`,
   `@claude-flow/guidance` — real algorithms (Raft, PBFT, gossip, a DAG orchestrator with DFS
   cycle detection, a five-term weighted agent scorer, a typed 20-event hook registry, a
   provider layer with circuit breakers). **None is a dependency of the CLI package**
   (`v3/@claude-flow/cli/package.json` lists `cli-core, codex, mcp, neural,
   plugin-agent-federation, security, shared, memory`); the CLI imports none of them, and
   `mcp-server.ts` always constructs the server with `orchestrator: undefined`. The
   consensus classes are imported only by their own tests.

Ruflo's own CLAUDE.md states the architecture honestly: *"Ruflo is the coordination ledger
and policy decision point. Claude Code executes code … A Ruflo coordination call records
work; it does not perform the implementation."* Everything below is read in that light.

### 4.2 Agent runtime and lifecycle

| Mechanism | Verdict | Evidence |
|---|---|---|
| 108 agent definitions in `.claude/agents/*.md` (+ 59 in plugins, 11 YAMLs in two other schemas) | DOC as an execution mechanism | prose system prompts with `name`/`description`/`tools`; **no loader anywhere** — 11 files touch the path and all treat it as opaque bytes (`init/executor.ts:157,836-844` copies; migration scripts do filename checks); `agent_spawn`'s `agentType` is checked only against a 12-entry model-routing map (`agent-tools.ts:124-140`) |
| `agent_spawn` | STUB as execution, IMPL as registry write | writes `{agentId, agentType, status:'idle', health:1.0, taskCount:0}` to `.claude-flow/agents/store.json`; its own response says "Three execution paths: (1) agent_execute (2) Task tool (3) claude -p" (`agent-tools.ts:450-453`) |
| `agent_execute` | IMPL | direct `fetch('https://api.anthropic.com/v1/messages')` with OpenRouter/Ollama fallback (`agent-execute-core.ts:246,607-661`) — a **separate runtime from Claude Code** |
| `HeadlessWorkerExecutor`, `ContainerWorkerPool` | IMPL | real `spawn('claude', ['--print','--output-format','json'])` process pool with SIGTERM→SIGKILL; real `docker run -d` with graceful degradation (`services/headless-worker-executor.ts:1397-1520`, `container-worker-pool.ts:364-468`) |
| `hive-mind spawn --claude` (CLI) | IMPL, **one** process | `childSpawn(claudeLaunch.command, …)` spawns a single `claude` session prompted to role-play "the Queen" over MCP tools (`commands/hive-mind.ts:327-335`) |
| `hive-mind_spawn` (MCP) | STUB | writes N idle records into `agents.json` and `state.workers[]`; no process |
| `agent_health`, `agent_logs`, `agent_pool scale` | STUB (self-admitted) | `health` set once at spawn and never recomputed; "entries are synthetic (ruvnet/ruflo#1916)" |
| DDD `Agent` aggregate (6-state guarded transitions) | DEAD | `swarm/src/domain/entities/agent.ts:180-279`; its only consumer is referenced by nothing |

Live lifecycle states: `idle → busy → idle | terminated`. Strength: the real executors
(`agent_execute`, headless pool, container pool, Managed Agents REST) are hardened — retry
and provider fallback, process-group kill, Docker-absent degradation. Weakness: the tool
named for the job spawns nothing; four agent-config schemas coexist without a loader.

### 4.3 Task decomposition and orchestration

| Path | Verdict | Evidence |
|---|---|---|
| `QueenCoordinator.decomposeTask` | DEAD, and a **fixed template** even if reached | `switch (task.type)`: `coding` → always Design/Implement/Test (`queen-coordinator.ts:664-737`); `executeDelegation` computes `parallelAssignments` and never reads them (`:1384-1399`) |
| `TaskOrchestrator` dependency DAG | DEAD | real `dependencyGraph/dependentGraph` maps, DFS `wouldCreateCycle`, online readiness (`coordination/task-orchestrator.ts:102-104,524-548`) |
| live `TaskRecord` | DOC for "dependency tracking" | no `dependencies` field (`task-tools.ts:17-30`); the CLI's `--dependencies` flag is sent and silently dropped |
| `task_orchestrate` | STUB | deprecated alias that creates **one** task (`v3/mcp/tools/v2-compat-tools.ts`) |
| `coordination_orchestrate`, `daa_workflow_execute` | STUB (self-admitted) | `executor: 'none' … Real executor tracked in issue #2140`; "Steps are tracked but not auto-executed" |
| `workflow_execute` | IMPL | the one real multi-step runner: sequential array order, `task` steps call `agent_execute` with `{{stepId.output}}` interpolation, `wait` and `condition` steps real, store saved after every step (resume from `currentStep`); `parallel`/`loop` are **honestly** `status: 'skipped'` (`workflow-tools.ts:302-427`) |
| `workflow_run` templates | STUB | hard-coded stage lists per template name, `agentsSpawned: 0` always |
| SPARC | DOC/PROMPT only | executable footprint is `mkdir -p … && touch 1-specification.md … 5-completion.md` (21 lines) and an `[ -f ]` check; `sparc.md` tells the LLM to call `mcp__claude-flow__sparc_mode`, which does not exist |
| `gaia-decomposer.ts` | IMPL, out of scope | one Haiku call to split a benchmark question; never touches the tool surface |

Consequence: **there is no live planner**. Decomposition is either a template keyed by type
or the calling LLM's own turn, and the only real executor runs steps in array order.

### 4.4 Swarm, hive-mind, consensus

- **Consensus algorithms are real and dead.** `raft.ts` (randomized 150–300 ms election
  timeout, `floor((n+1)/2)+1` quorum, heartbeats, majority commit, Raft §5 receiver rules
  when a transport is wired; an acknowledged gap in `prevLogIndex/prevLogTerm` conflict
  resolution), `byzantine.ts` (pre-prepare/prepare/commit, `f = max(1, floor((n−1)/3))`,
  `2f+1`, SHA-256 digests replacing a toy hash; a self-documented bug where the raw
  commit-count path bypasses weighted voting), `gossip.ts` (fanout 3, TTL/hop bounds,
  anti-entropy, 100k-entry bounded seen-set, convergence-gated weighted vote). Default
  transport is **in-process**; `FederationTransport` (Ed25519, correlation ids) is
  instantiated only by its own test. Importers of the three classes: tests and fixtures only.
- **The live tools re-implement a shallower version under the same names.** `swarm_init`
  stores `topology` and `consensusMechanism: 'majority'` as strings in
  `.claude-flow/swarm/swarm-state.json` (atomic write, lock file, PID-liveness orphan
  reconciliation — genuinely good bookkeeping) and constructs no `TopologyManager`;
  `ring`/`star` are label-only (zero `case 'ring'` anywhere). `hive-mind_consensus` does
  real quorum arithmetic (`bft floor(2n/3)+1`, `raft floor(n/2)+1`), timing-safe capability
  tokens and Sybil/double-vote exclusion — **vote counting, not a protocol**: no rounds,
  terms or leader election. `hive-mind_init` "elects" a queen by assigning `queenId`.
- **Result merging does not exist.** `executeParallel` is `Promise.allSettled` returning an
  array; `attention-coordinator.computeWeightedConsensus` returns the single highest-weight
  agent's output annotated with metadata. Nothing combines several drafts.
- **What is live and good:** ADR-330 "pheromone-adaptive" —
  `raw = 0.5·success + 0.2·(1−latency) + 0.3·alignment`, EMA 0.85, role-local baseline,
  protected roles, `minActiveAgents=3`, `maxSuspendFraction=0.25`, deterministic
  exploration, `dryRun` default — wired as an eligibility gate on `agent_execute`
  (`agent-tools.ts:497-505`) and fed from the post-task hook. It is a **circuit-breaker on
  agent reputation**, not consensus; the name is the only place doc and code diverge.
- ADR-334 (hierarchical consensus, shards) and ADR-345 (Shapley credit routing): **DOC**;
  target files absent; "Shapley" appears in zero `.ts` files.

### 4.5 Agent routing

Four routers, four label sets, none sharing a model (§8 covers *model* routing):

| Router | Path | Algorithm | Verdict |
|---|---|---|---|
| A. `router.cjs` (UserPromptSubmit) | every prompt | first-match regex over 8 token lists → 8 agents; confidence constant 0.6, default `coder` 0.3; order-dependent ("add tests" → coder) | IMPL; the header says "NOT a learned model" |
| B. `hooks_route` | when the LLM calls it | AgentDB pre-route (duck-typed) → per-keyword "semantic" index (default embedder is a **character hash**: `sin/cos(charCode·(i+1))`; ADR-390 admits it "measures spelling, not meaning") → keyword fallback with hand-set confidences | IMPL |
| C. Q-learning router | `ruflo route` only | tabular Q over 64 hashed features, ε-greedy from 1.0, `.swarm/q-learning-model.json` | IMPL, off the hot path |
| D. typesafe router | `CLAUDE_FLOW_ROUTER_TYPESAFE=1` | `@ruvector/typesafe` with abstain/lift/margin | OPT-IN |

Their own benchmark (ADR-391, 197 blind-labelled prompts, 113-prompt test split): best
candidate **36.3% top-1**, default hash embedder 25.7%; no candidate promoted because the
p95-latency AND-gate failed on a ~1 ms baseline; the pattern table **cannot return
`researcher`, `reviewer` or `none`**, capping accuracy near 70% before any embedder question.
The learned-pattern layer (`services/learned-routing.ts`) is a sound discriminative-keyword
learner (`support × mean quality × ln(1+A/df) × discriminative share`, support ≥ 2, quality
≥ 0.65) — fed only when the LLM remembers to call `hooks_post-task` with `agent` and `task`.

### 4.6 Skills

361 `SKILL.md` files (39 copied by `init`, 605 KB, up to 31 KB each; 146 across 40+
plugins at ≤ 8 KB with `REFERENCE.md` sidecars). Standard Claude Code frontmatter; **no
parser, no runtime selection, no skill routing** — discovery and progressive disclosure are
Claude Code's (`skillListingBudgetFraction: 0.06` is Ruflo's only lever). Several skills
describe behaviour the code no longer has (`cost-booster-route` greps for a marker
`hooks_route` no longer emits; `sparc-methodology` advertises "17 modes"). The one
transferable artifact is `capability-brain.ts`: a **data-only** taxonomy of 20+ domains →
`prefixes, taskSignals, commands, skills, agents, maturity, authority, risk, useWhen,
verifyBeforeUse`, with a six-state truth model per capability (catalogued / registered /
configured / reachable / healthy / authorized, health defaulting to `'unknown'`).

### 4.7 Hooks

Four hook sets are shipped; the real one is what `ruflo init` writes into
`.claude/settings.json` (`init/settings-generator.ts:288-492`): every event → a new Node
process running `.claude/helpers/hook-handler.cjs <cmd>`:

| Event | Handler | Verdict |
|---|---|---|
| UserPromptSubmit `route` | prints `intelligence.getContext(prompt)` (top-5 memory lines ≤ 80 chars) + the router box; may print a **sponsored-capacity nudge** into model context when `~/.ruflo/rate-limit-status.json` is flagged | IMPL |
| PreToolUse Bash `pre-bash` | 4-string denylist (`rm -rf /`, fork bomb…) then `process.exit(1)` — in Claude Code only exit **2** or a JSON deny blocks, so this "BLOCKED" path does not block | broken |
| PostToolUse Write/Edit `post-edit` | dedup by event id, `session.metric('edits')`, `intelligence.recordEdit` → append to `.claude-flow/data/pending-insights.jsonl` (≤ 512 KB / 2,000 lines); failure detection from `tool_response` | IMPL |
| SessionStart `session-restore` | `intelligence.init()`: dedupe store by id and content fingerprint, build graph, PageRank, write `ranked-context.json`; detached `hooks refresh-funnel` / `refresh-advisor` (the latter may make a consent-gated `claude -p` call once per 24 h) | IMPL |
| SessionEnd `session-end` | `intelligence.consolidate()`: files edited ≥ 3× become "insight" entries; −0.005/day confidence decay for never-accessed nodes older than 24 h; rebuild edges; PageRank | IMPL |
| SubagentStop `post-task` | `intelligence.feedback(!toolFailed)` → ±0.05 / −0.02 on the last matched patterns | IMPL |
| `pre-edit`, `post-bash`, `compact-*`, `status`, `notify` | no handler → prints `[OK] Hook: <cmd>` | STUB |

The intelligence layer on the hot path (`intelligence.cjs`, 1,169 lines) is simple, bounded
and real: nodes = memory entries, edges = temporal (same source file, 0.5) + trigram Jaccard
> 0.3 within a category (skipped above 100k comparisons), PageRank d=0.85 (skipped above
5,000 nodes), retrieval `0.6·Jaccard + 0.4·pageRank`, threshold 0.05, top-5, implicit +0.03
when an entry shown last prompt is not shown this one. It is **not** SONA/LoRA/EWC; those
live behind MCP tools. The typed `@claude-flow/hooks` package (20-event enum, priorities,
a bridge that emits proper `hookSpecificOutput.permissionDecision`) is **DEAD** for
installs. The marketplace plugin's hooks spawn the full CLI synchronously per event for
subcommands (`modify-bash`, `modify-file`) that do not exist. Latency budgets in comments
(init < 200 ms, getContext < 15 ms) are not measured in CI; every hook pays a Node cold
start. 36 of 41 `hooks_*` tool descriptions end with the same 258-character suffix, paid in
every session's tool listing.

### 4.8 MCP

- **Three MCP implementations**, one live: `cli/src/mcp-server.ts` hand-rolls JSON-RPC
  over stdio (10 MB buffer; no `@modelcontextprotocol/sdk` anywhere) and dispatches through
  an in-process `TOOL_REGISTRY` Map populated eagerly from ~54 modules (`mcp-client.ts`,
  misnamed — it is not a client). `@claude-flow/mcp` (typed registry with category/tag
  indices, http/websocket transports with CORS/helmet/rate limit, resources, prompts,
  sampling, OAuth) is reached only for `--transport http|websocket`. `v3/mcp/` is a third,
  orphaned copy with compiled artifacts and zero importers.
- **Tool census:** 472 `name:` definitions in 44 files (`hooks-tools` 67, `capability-brain`
  34, `wasm-agent-tools` 27, `browser-tools` 23, `guidance-tools` 22, `agentdb-tools` 20 …).
  `mcp start` prints a hard-coded "27 enabled"; `.harness/mcp-policy.json` claims "314
  tools"; none agree. `tools/list` always returns the whole catalogue; there is no lazy or
  deferred loading. `initialize` advertises `resources: {subscribe: true}` that the stdio
  dispatcher does not implement.
- **Policy:** `policy-enforcer.ts` is real (sliding-window rate limit, fail-closed audit
  log) and **off by default** (`RUFLO_MCP_ENFORCE_POLICY=1`); `.harness/mcp-policy.json`
  declares `defaultDeny`, `allowShell:false`, `allowNetwork:false` that no code reads, and
  asserts the server has no shell/network primitives while `terminal-tools.ts` (`execSync`)
  and `http-fetch-tools.ts` (`fetch`, SSRF-guarded) expose both. `tool-loop-guardrail.ts`
  (exact-string consecutive-failure streak, warn ≥ 3 / block ≥ 5) is real and narrow.
- **Honesty convention (ADR-073, 2026-04):** the project's own audit removed
  `Math.random()` metrics from `agent_health`, `system_health`, `coordination_metrics`,
  wired `neural_predict/compress/optimize` to real cosine/Int8 code, and introduced
  `_stub: true` / `_real: true` result flags. It holds where checked (`neural_train` reports
  `_realEmbedding` and a `platformNote` when degraded to a hash), is not universal
  (`runOptimizeWorkerLocal` returns `cacheHitRate: 0.78, avgResponseTime: 45` with no flag),
  and still lists "WASM agents: Stub — echo-based, no WASM runtime". `mcp logs` returns four
  hard-coded objects with fresh timestamps; `mcp toggle` prints "Enabled N tools" and mutates
  nothing.

### 4.9 Providers, model catalogue, budget

`@claude-flow/providers` is real and well-engineered — five providers with real HTTP
clients, a closed/open/half-open circuit breaker, health checks, per-request cost, and a
manager with round-robin / least-loaded / latency-EMA / cost strategies and fallback-on-error
(`providers/src/base-provider.ts:37-84,211-244`, `provider-manager.ts:214-398`) — and **the
CLI does not depend on it**; the live path is `agent-execute-core.ts`'s own client. Two
disconnected price tables exist (`base-provider` `capabilities.pricing` vs
`ruvector/model-prices.ts`). `catalog-manifest.json` is a 208-byte count fingerprint
(`agents: 167, tools: 418, skills: 34, benchmark: null`), not a catalogue. The genuinely
valuable safety piece is `services/global-ai-budget.ts`: an `O_EXCL` file-locked,
fail-closed ledger at `~/.claude-flow/ai-budget.json` capping AI launches user-wide across
daemons and worktrees (`maxConcurrentGlobal: 1`, hourly/daily caps, quota-pause on 429),
with receipts. `worker-queue.ts` documents itself as "Redis-based … distributed" and
contains no Redis client — it is a `Map`-backed in-memory queue.

### 4.10 Context and token efficiency

What shapes a default install's context: a generated CLAUDE.md of 7.5–11.3 KB (the repo's
own is 69.6 KB), the ≤ 5-line hook block with no token budget or dedup against what is
already in context, the full MCP tool listing, and Claude Code's skill listing. Real but
unwired: `message-compressor.ts` (extractive TF-IDF under `budgetTokens`, preserves code
fences/paths/URLs), `context-persistence-hook.mjs` (SQLite transcript archive, prune at
85%), guidance shard retrieval. The advertised −32% / −15% / 352× figures are marked
"Claimed upstream, not yet verified" by the repo's own cost-tracker plugin; after #3289 the
library reports `tokensSaved: null` when unmeasured, while the CLI's inline copy still
computes "saved" as `query tokens − compact tokens`. **No benchmark in the repo measures
end-to-end token use against a baseline.**

### 4.11 Memory, session, learning, persistence

**Four memory implementations, 20+ on-disk stores, and the primary store depends on the
platform.** (A) the hook "intelligence" layer — JSON files under `.claude-flow/data/`,
word-trigram Jaccard + PageRank — is the only one wired into Claude Code by default; (B) the
CLI/MCP memory (`memory-initializer.ts` 4,090 lines → `memory-bridge.ts` 3,455 → external
`agentdb`) writes `.swarm/agentdb-memory.db` on Linux/macOS with native deps and
`.swarm/memory.db` (sql.js whole-image rewrite) otherwise — **the nightly backup and the
distillation worker are hard-wired to `memory.db` and miss the store that receives the
writes**; (C) the `@claude-flow/memory` library (a genuine TypeScript HNSW, FTS5 `porter
unicode61` backend, three-arm RRF + MMR `hybridSearch`, `TieredMemoryStore`) is mostly not
wired into the CLI; (D) `@claude-flow/neural` (ReasoningBank, SONA, PPO/DQN/A2C in
TypeScript) is imported only for two helpers. ADR-006 "Unified memory — Implemented" is
false by the code.

**Schema (`MEMORY_SCHEMA_V3`, `memory-initializer.ts:235-561`).** `memory_entries(id, key,
namespace, content, type CHECK(semantic|episodic|procedural|working|pattern), embedding TEXT
(a JSON array — ~7–8 KB per 384-d row), embedding_model, embedding_dimensions, tags,
metadata, owner_id, provenance_type CHECK(user_claim|agent_output|system_observation|
tool_result|unknown), created_at, updated_at, expires_at, last_accessed_at, access_count,
status CHECK(active|archived|deleted))`, `UNIQUE(namespace, key)`. **Six tables are
created and never written**: `patterns` (confidence, success/failure counts, decay_rate,
half_life_days, version, parent_id, status), `pattern_history`, `trajectories`
(`verdict success|failure|partial`), `trajectory_steps`, `sessions`, `migration_state`;
tests assert their DDL. `graph_edges(source_id, target_id, relation, weight, confidence,
decay_rate, last_reinforced, witness_id, embedding_ref)` is **insert-only** — nothing ever
UPDATEs or DELETEs it, so the confidence/decay/reinforce semantics exist only in comments.
Every writer hard-codes `type='semantic'`; `owner_id` is never inserted; `metadata` is
always `'{}'`; **`expires_at` is written and never filtered or swept** (TTL is inert).
Namespaces drift (`pattern` vs `patterns`, `session` vs `sessions`) so the intent-routed
retrieval misses its own writes.

**Vector search.** `memory_search` reports `backend: 'HNSW + sql.js'`; the default native
path is **brute-force cosine over the newest 1,000 rows** (`memory-bridge.ts:1245-1251`).
The "150×/12,500× faster" string is looked up from the entry count
(`commands/memory.ts:530`). The self-audit measured real HNSW at 1.48× at N=20k and slower
than brute force below N≈5k. Embeddings: local MiniLM ONNX → agentic-flow → ruvector → a
**deterministic 128-d hash fallback**; AgentDB once served **mock embeddings labelled
MiniLM** (synonyms scored −0.988, unrelated text +0.775) — the strongest argument on record
for EOS's C-10: lexical retrieval cannot silently lose its semantics. Hybrid on the default
path is `score = max(0.6·semantic + 0.4·lexical, semantic)` with a per-query "BM25" that
counts **substring** matches; the docstring says RRF. Search results truncate content to 60
characters — accidental but effective progressive disclosure.

**Sessions.** The default hook path persists `{id, startedAt, cwd, metrics{edits, commands,
tasks, errors}}`, "restores" a stale session that never ended, and re-injects trigram-matched
titles of the user's own `MEMORY.md` sections — content Claude Code already loads. **Every
PreCompact ends the session.** The MCP `hooks_session-restore` **fabricates**
`originalSessionId = session-${Date.now()-86400000}` and derives "restored" counts from key
substrings; `session_save` snapshots the legacy JSON stores, never the SQLite memory.

**Execution tracking.** No unified ledger. Fragments: `routing-outcomes.json` (the one closed
loop — feeds the learned keyword router, MCP path only), a `feedback` namespace whose
`success` **defaults to true** and quality is 0.85/0.3 constants (then treated as
`oracle:test-exec` ground truth by distillation), trajectories whose steps live only in an
in-process Map (lost across processes), `pending-insights.jsonl` edits with `sessionId`
always null, `commands` recorded only on the MCP path (the default hook has no `post-bash`
handler, so **Bash outcomes are never recorded**), a `TaskRecord` board with status only
and no dependencies despite eight tool descriptions claiming "dependency tracking".
Nothing joins task → agent → tool calls → files → timings → verdict.

**Learning.** "SONA RL" is `confidence += reward·0.1` on the top-3 similar patterns;
"LoRA-style distillation with EWC++" is `confidence += 0.001·reward` damped by a "Fisher"
that is `Σ embedding²` (admitted as a proxy); `neural_train` embeds and stores, `accuracy =
patternsStored > 0 ? 1.0 : 0`; `neural train` without data trains on canned template
sentences with InfoNCE positives chosen by batch position; MicroLoRA `apply()` was found
inert (Δ = 0). Six pattern stores with six schemas. Success increments exceed failure
decrements (+0.1/−0.05; +0.05/−0.02), every successful step is stored at confidence 0.8–1.0,
and the hook layer's learned confidence **is not part of the retrieval score**
(`0.6·Jaccard + 0.4·PageRank`). Five decay implementations either compound wrongly or never
run. The RETRIEVE→JUDGE→DISTILL→CONSOLIDATE ReasoningBank is a toy class (rule judge,
`"Apply a -> b -> c"` strings) the CLI does not use; AgentDB's `MemoryConsolidation`
controller is a declared no-op. **The good part**: the daemon distillation
(`memory-distillation.ts`) — incremental `distill_state(namespace, last_rowid)` cursor,
per-batch transactions, greedy clustering, a 4-field structural distill (summary ≤ 200,
detail ≤ 1,024, labels, paths), and **provenance tiers `oracle:test-exec | judge | proxy:
structural` where proxy never promotes (ADR-171)**; and `resolved_source ∈ {gold-oracle,
output-verifier, api-success, external}` on run transcripts.

**Knowledge graph.** `graph_edges` with synthetic node ids (`task:<id> → pattern:<id>`) that
resolve to no rows; real k-hop recursive CTE (depth ≤ 3) and personalised PageRank; three
of six "pathfinder" algorithms (`dynamic-mincut, spectral-sparsify,
connected-component-churn`) return k-hop neighbours with `1/(1+i)`; the library `MemoryGraph`
runs PageRank and label propagation on edges from `references` that no writer sets; the
`ruflo-knowledge-graph` plugin is prompt-only. A separate regex JS/TS import graph
(`graph-analyzer.ts`) with Stoer-Wagner and Louvain is real, ephemeral and unlinked to
memory — shallower than EOS's.

**Persistence and failure.** 20+ stores: two SQLite files (with **two engines on one file**,
sql.js whole-image and better-sqlite3 WAL — the source of corruption bugs #2431, #2584,
#2735, #2878, #3397, now gated by a WAL-sidecar refusal), JSON files rewritten whole without
locks (patterns, routing outcomes, tasks, agents — in two different files — hive-mind),
JSONL with 10 MB × 3 rotation for the opt-in recorders. The #2878 regression test proves
the sql.js path lost **11 of 12 concurrent writes** before an O_EXCL lock (10 s stale
takeover, 15 s timeout, reentrant via AsyncLocalStorage) was added. Genuinely good:
`fs-secure.writeFileAtomic` (tmp + fsync + rename + dir fsync), `recoverMemoryDatabase`
(`quick_check` → table copy → `integrity_check` and `rows_after ≥ rows_before` → atomic
rename → restore newest good backup on failure), idempotent hook side effects by
`sha256(project, family, tool_use_id)` O_EXCL markers, daemon worktree leases with
`kill(pid, 0)` liveness. Bad: any JSON over 10 MB or unparseable is treated as empty and
**overwritten with a bootstrap** — silent loss of the whole store; soft-deleted rows are
never garbage-collected; `guarded: true, cached: true, attested: true` are returned
unconditionally while the corresponding controllers are null.

**Provenance.** ADR-323 typed write-time provenance is real end to end (validated,
preserved on upsert, filterable, fails closed on ANN lookup errors) — and **no internal
writer sets it**, so nearly every row is `unknown`. `TieredMemoryStore` is the one store
with **supersede-not-delete** temporal validity (`valid_from, valid_until, superseded_by,
archived`; recall filters invalid rows unless `includeExpired`). Search results carry an
explain string `provenance: "semantic:0.812+lexical:0.400"`. A named bug class,
"confidence–similarity conflation", has its own regression test.

**Rust crates and `agentdb.rvf`.** None of the three crates (`ruflo-agntcy`,
`ruflo-federation-peer`, `ruflo-watermark`) implements memory or vectors; only the watermark
crate is wired (WASM). `agentdb.rvf` at the repo root is a **162-byte empty RVF container**
accidentally committed — the size of an agenticow COW branch — not a data store.

### 4.12 What Ruflo teaches EOS

**Transfer (each is a data model or a discipline, not a dependency):**
1. The **benchmark gate** for any router: frozen hashed corpus, blind labels, dev/test
   split, an AND-gate on accuracy and p95 latency, receipts committed whatever the result,
   and a coverage-complete label set with `none` (ADR-391).
2. `output-verifier.ts`'s **$0 structural verification ladder** as the shape of
   post-generation checks that escalate a tier.
3. **`routedBy`-style provenance** on every decision and hash-sampled shadow evaluation.
4. `global-ai-budget.ts`'s **file-locked, fail-closed, receipted spend fuse** — the one
   piece of cross-process discipline EOS lacks (its counters are unlocked read-modify-write).
5. The **hook catalogue shape**: prompt-submit = advise + context; post-edit = cheap
   append-only event; session-end = consolidate; subagent-stop = outcome feedback; dedup by
   `tool_use_id`; failure detection from `tool_response`; exit 2 (never 1) to block.
6. `capability-brain`'s **six-state truth model** (catalogued → registered → configured →
   reachable → healthy → authorized) as the schema for "which wrapper/procedure applies" and
   for `eos doctor`.
7. ADR-073's **`_stub`/`_real` honesty flags** and ADR-330's **bounded, dry-run-default,
   protected-role eligibility gate** as patterns.
8. Pheromone-style **reputation decay** as a model for procedure/note confidence — but
   derived at read time, as ADR-024 already requires.

**Do not adopt:** Thompson-sampled model choice; per-score-bucket learning; a reward equal
to "the API answered"; an uncertainty gate; four routers with divergent labels; per-keyword
hash "embeddings"; 472 eagerly-listed tools with boilerplate descriptions; multiple parallel
implementations of the same subsystem; tool descriptions that outrun the code; hooks that
spawn a CLI per event or nudge the user commercially; a "distributed" queue that is a Map;
SPARC-as-`mkdir`; Byzantine consensus for a single-process coding assistant.
## 5. EOS vs RAGFlow vs Ruflo — architecture matrix

No scores. Each row states what runs today, where the architectural strength and weakness
lie, where the three overlap unnecessarily, and what EOS should do. "Live" means on the
default code path; "dead" means real code nothing reachable calls.

### 5.1 Knowledge and retrieval

| Row | EOS 1.2.2 | RAGFlow | Ruflo 3.45 | Strength | Weakness | Overlap | EOS should |
|---|---|---|---|---|---|---|---|
| Knowledge representation | file-granular graph (`node`, `edge` × 7 kinds) + generic `fact(subject, predicate, object, origin, confidence, detector, source_ref, observed_at)` + `coverage`; notes of 5 kinds; 3 JSONL ledgers | chunk rows with ~30 typed fields and row-kind discriminators (`knowledge_graph_kwd`, `raptor_kwd`, `toc_kwd`, `compile_kwd`) in one doc-engine index; MySQL metadata | `memory_entries(namespace, key, content, type, embedding JSON, provenance_type, …)` + JSON stores per feature; types not behaviourally distinct | EOS: provenance rows and "looked vs found nothing"; RAGFlow: one index, one record shape, hidden derived rows | EOS: no symbol-level nodes, weights always 1; RAGFlow: no code structure; Ruflo: 20+ stores | RAGFlow's discriminator-in-one-index ≈ EOS's `source` column in `search` | Keep facts + coverage; add symbol-level rows (method → class → file) with a `kind` discriminator and `parent_id` (RAGFlow's `mom_id`) |
| Semantic model | 9 dataclasses; `FileSemantic` 15 fields; Java structural, Python top-level `ast`, JS regex | none for code; layout model for documents (ONNX) | none (regex JS/TS import graph, ephemeral) | EOS's Java lexer + scope scanner (339/345 dependent recall) | Python and JS/TS thin; no interface→impl dispatch | — | Keep; add interface→implementation resolution and Python calls/bases; leave JS regex unless measured |
| Knowledge graph | edges from declaration sites; recursive CTE impact; `trace` with the runtime-wired count | LLM-extracted entities/relations as rows; `nx.pagerank`; precomputed 2-hop paths; Leiden on the LCC; deprecated in UI; successor with chunk-level provenance | insert-only `graph_edges` with synthetic ids; real k-hop CTE and PPR; 3 of 6 pathfinders are placeholders | RAGFlow: graph beside the chunks, checkpoints, 2-hop precompute; EOS: the graph is extracted, not hallucinated | RAGFlow: uncitable CSV pseudo-chunk, LLM cost; Ruflo: edges to nothing | Graphify (host) already answers "what reaches this"; EOS impact overlaps it and is growing | Precompute 2-hop neighbourhoods with weights; `pagerank × lexical match` node ranking (30 lines of power iteration); deterministic community labels; **decide the EOS/Graphify boundary** (§9) |
| Vector search | none (C-10 decided) | HNSW in ES/Infinity; `0.1·title + 0.9·content`; drift check | brute-force cosine over newest 1,000 rows labelled HNSW; hash fallback; mock embeddings once mislabelled MiniLM | RAGFlow's drift check and title prior | Ruflo shows the silent-degradation failure mode | — | Keep C-10. If ever revisited: vectors only as a **re-scorer over ≤ 64 lexical candidates**, brute force, with a `backend: real|hash` flag on every result |
| Full-text search | FTS5 `unicode61`, OR'd phrases, equal column weights, no stemmer; IDF coverage over notes (title .5 / tags .3 / body .2) | field boosts (kwd 30 / questions 20 / title 10 / content 2), binary-TF short fields, Snowball stemmer, filler strip, synonyms, adjacent-bigram phrases, short-query min-should-match | library FTS5 `porter unicode61` (unwired); Lucene BM25 + Porter in one CLI path; substring "BM25" on the default path | RAGFlow's lexical machinery is the most complete of the three | EOS: recall@1 decays as the corpus grows (0.94 → 0.67); three unmeasured implementations | EOS coverage ≈ RAGFlow `token_similarity` (both query coverage) | `bm25(search, w_title, w_body, w_terms)`; `porter unicode61`; filler/question-word strip (fixes "ok, continue"); bigram term at 0.6; ⌈0.3·n⌉ min-should-match; a `synonyms` table; corpus IDF from `fts5vocab`; **one** retrieval implementation |
| Hybrid retrieval | none — six lexical paths ranked separately | lexical-gated kNN, `(1−w)·coverage + w·knn + prior`, backend-dependent; RRF k=60 in the newest code | `max(0.6·semantic + 0.4·lexical, semantic)`; library three-arm RRF + MMR (unwired) | RRF needs no calibration | RAGFlow: one parameter name, seven semantics; Ruflo: docstring says RRF, code blends | — | Fuse EOS's independent legs (notes, index FTS, symbols, runs, procedures) with **RRF k=60** and print each leg's rank in the explain string |
| Reranking | none | 25 providers; `0.7·coverage + 0.3·rerank`; embedding sim dropped when reranking; built-ins removed for speed | MMR; optional cross-encoder (one CLI path) | RAGFlow's honesty about built-in rerankers | cross-encoder undersold at 0.3 | — | No model reranker. Deterministic re-score of the top 64: coverage + bigrams + prior + recency; MMR by token-Jaccard for diversity |
| Context assembly | budgeted, priority-ordered task brief (1,500 tok, exempt lines); `build_context` greedy prefix; host injection on file touch | `kb_prompt` counts rendered blocks (97%); `message_fit_in` all-or-nothing + head truncation; knowledge may take 100% before history | ≤ 5 lines, no budget, no dedup against loaded files | EOS's delivered-by-hook, budgeted block | EOS: branch brief unbudgeted, three token estimators; RAGFlow: prefix-stop, tail cut; Ruflo: re-injects `MEMORY.md` | EOS prefix fit = RAGFlow prefix stop | One estimator; budget every block incl. the branch brief; reserved floor/ceiling per section; skip-and-continue instead of prefix stop; render kind + age + stable id; dedup against loaded files |
| RAG | not a RAG system: retrieval feeds an agent's context through hooks; no generation | full RAG: LLM-in-the-loop at ingestion (keywords, questions, tags, TOC, graph), retrieval and answer | none (memory lines into context) | RAGFlow's agentic RAG harness: budgets per phase, `ToolOutcome.status`, deterministic narrowing | LLM cost per chunk; dead planner code shipped | — | Stay a **retrieval and memory engine for agents**, not a RAG server; adopt `narrow_by_terms`, the status vocabulary and phase deadlines for EOS's own jobs |
| Code intelligence | symbols, edges, behaviour codes, coverage ladder, `trace`, `draft-test`, `why` | none | regex JS/TS import graph; TS-compiler codemods for 3 intents | EOS is alone here | interface dispatch, weights, Python/JS depth | Graphify | Keep and deepen (§10); it is EOS's non-fungible half |
| Business-process intelligence | journeys extension (step → bean → class, `resolution` + `candidates`), procedures catalogue; only one service populated | none | none | EOS's resolution provenance | depends entirely on host exports; core refuses a workflow entity (ADR-017) | — | Keep the extension model; add a generic `flow(step, owner, resolution)` table shape in core **only** when a second host populates it |
| Impact analysis | recursive CTE over 6 edge kinds, depth ≤ 5, 500 rows; direction-limited | n/a | `graph-analyzer` (regex, ephemeral) | EOS | no interface→impl; no ranking; hubs truncate | Graphify `affected` | Add reverse edge for `implements`; rank by depth × edge kind weight; stop at the runtime-wired boundary and say so (already) |

### 5.2 Memory

| Row | EOS 1.2.2 | RAGFlow | Ruflo 3.45 | Strength | Weakness | Overlap | EOS should |
|---|---|---|---|---|---|---|---|
| Agent memory (overall) | three stores by lifetime: notes (durable), work (intent), executions (episodes) + verifications; file-first, folded into SQLite | `memory/`: LLM-extracted, embedded, per-tenant rows (raw/semantic/episodic/procedural) with `valid_at/invalid_at/forget_at`, FIFO eviction, no reconciliation | four implementations, 20+ stores, platform-dependent primary; types not distinct | EOS's separation by lifetime and single writer per store | EOS: no supersession/expiry; RAGFlow: `invalid_at` never filtered; Ruflo: accretion | RAGFlow's four types ≈ EOS's note/lesson/procedure/run | Keep the three stores; add temporal validity (`valid_from/valid_until/superseded_by`) and typed write provenance; render kind and age |
| Semantic memory | notes (`finding`, `defect`, `decision`), scope hashes, staleness audit, guarded amend; 841 notes, 70% generated | `semantic` extracted items | `semantic` default type on every row | EOS's audit + amend invariants | EOS corpus is mostly generated inventory | — | Split **generated** from **learned** into different stores/kinds with different ranking and staleness rules; `supersedes` |
| Procedural memory | `kind: procedure` with parsed steps, counters by `run finish`, derived confidence; 15 procedures, 4 ever run | `procedural` extracted items (text) | none as a type; skills are prose; SPARC is `mkdir` | EOS is the only one with counters and confidence | steps are prose; no typed I/O, no per-step outcome | RAGFlow DSL steps (agents), Ruflo `workflow_execute` steps | Typed steps (`tool`, `inputs`, `outputs`, `on_failure`) linted for unknown refs/cycles; per-step outcome from wrapper events |
| Episodic memory | `executions.jsonl` (51 runs, 1,929 events, refs only, all in one project) | `episodic` extracted items; canvas `history`; `API4Conversation.dsl` full snapshots per turn | fragments (feedback, trajectories in-process, edits with null session, commands MCP-only); no unified ledger | EOS's event ledger is the only real one | capture is host-dependent; 34% CI polling; 1 s resolution; no rotation; no `work_item` link | RAGFlow `node_started/finished` event shape | Add `status` (ok/empty/miss/poor/redundant/error), `ms` from the wrapper's own clock, rotation, `outcome_source`; link runs to work items; capture editor edits via PostToolUse |
| Session memory | one id across stores; no session view; state dir never GC'd | `Conversation.message` whole list; canvas history window 26 | `current.json` metrics; fabricated restore; PreCompact ends the session | EOS's id threading | none of the three has a real "where was I" restore | — | `eos brief --session` = open runs + claimed work + failing procedures for this session/branch under a hard budget; session-end summary line |
| Execution history | `run list/show/tools/diff`; `route --stats` | `PipelineOperationLog`, `Task.progress`, Redis traces (TTL 600 s) | `TaskRecord` status board; opt-in JSONL recorders with `resolved_source` | Ruflo's `resolved_source`; EOS's durability | RAGFlow traces expire; Ruflo tasks have no tool calls | — | Adopt `outcome_source`; keep durable append-only |
| Tool-call history | wrapper EXIT-trap events (tool, target, ref, exit, ms) | `tool_use_callback` → Redis 600 s | `hooks_post-command` MCP-only; default hook records no Bash | EOS | only wrapper-routed actions | — | Add PostToolUse Bash/Edit capture for actions outside wrappers, flagged `source: hook` |
| Decision / ADR memory | `kind: decision` (Why/When/Component), 3 notes; 25 ADRs in the repo | none | 184 ADR files, many "Proposed" by a dream-cycle agent and never built | EOS's ADR discipline | `supersedes` missing | — | Add `supersedes`, `status`, link decisions to the runs and procedures they changed |
| Failure / learning memory | lessons required on failed finish, `## Seen again`, `## Known failures`; confidence derived | none | confidence nudges labelled RL; five decay implementations | EOS's "a failed run must leave a lesson" | nothing reads lessons into ranking or routing | — | Keep; add the discriminative outcome learner offline (§8, §12) |
| Learning | counters, lessons, `verified` rung; no policy change | chunk feedback prior (bounded, integer, off by default) | `learned-routing.ts` (real); distillation with provenance tiers (real, aimed at the wrong file); the rest is nudging | Ruflo's provenance tiers; RAGFlow's bounded prior | Ruflo: reward = API success; RAGFlow: raw priors bypass thresholds | — | Bounded `prior ∈ [0,100]` per note/procedure from run outcomes and evals, applied **scaled**; proxy evidence never promotes |

### 5.3 Agents, routing, execution

| Row | EOS 1.2.2 | RAGFlow | Ruflo 3.45 | Strength | Weakness | Overlap | EOS should |
|---|---|---|---|---|---|---|---|
| Planning / task decomposition | none by design | LangGraph slot table (depth 3, 8 slots), SCA judge — chat only; canvas planner dead | `QueenCoordinator` fixed templates (dead); live tools are passthroughs; SPARC is `mkdir` | RAGFlow's structured sufficiency verdict | both "planners" are LLM turns or templates | — | Do not build a planner. Plan state as data: procedure steps + work items with `parent`/`blocked_by`; the harness plans |
| Agent routing | none; `eos-researcher` profile | agents-as-tools with `{user_prompt, reasoning, context}` | four routers, four label sets, 36% top-1 at best | RAGFlow's handoff schema; Ruflo's benchmark gate | Ruflo's routers disagree | — | No agent router. A **capability table** (domain → wrappers, procedures, agents, risk, `verifyBeforeUse`) rendered into the brief; measured before any scorer |
| Model routing | deterministic 12 types × 7 factors → cheapest sufficient; advisory; 58/67 same answer; not applied | none (fixed per dialog) | Thompson bandit + deterministic tier router, disagreeing; cost-biased reward; #2250 gate | EOS's determinism and registry-as-data | EOS: no labelled corpus, not applied; Ruflo: everything else | EOS borrowed the shape from Ruflo | Labelled corpus + gate; turn the hook on for untyped subagents; widen to a task envelope (§8) |
| Effort selection | `EFFORTS` clamped per model; advisory | modes `low/medium/high/ultra` → rounds, fan-out, turns, tools, deadlines | none | RAGFlow's effort-as-orchestration knobs | EOS effort cannot be applied per call by the harness | — | Effort = envelope: brief budget, verification depth, subagent count, tools; advise the harness's model effort |
| Skills | generated `SKILL.md`, agent, hooks; index extensions; language plugins (static list) | none (templates are canvases) | 361 `SKILL.md`; no runtime; `capability-brain` data model | EOS's procedures-as-notes delivered by hook beat 30 KB skill files | plugin discovery static | — | Keep; adopt the capability-brain schema and the ≤ 5–8 KB skill + reference-file rule |
| Hooks | SessionStart, UserPromptSubmit, Stop, optional PreToolUse; exit 0 on failure; host adds PostToolBatch inject, Stop gate, wrapper guard | none (server product) | 11 events → one Node process each; `pre-bash` exit 1 (does not block); 5 handlers are `[OK]` stubs; sponsored nudge in context | EOS + host: exit 2 refusal with an alternative, note gate, dedup | EOS engine hooks never fire in a multi-repo workspace; `eos-close` missing in nexus | same event catalogue | Keep; add SubagentStop outcome feedback, PostToolUse Bash/Edit capture, `tool_use_id` dedup; never exit 1 to block |
| MCP | 12 tools, off by default, stdio, hand-rolled JSON-RPC, roster pinned by a test | 3 server tools (SSE/HTTP), dynamic descriptions with live inventories; client snapshots schemas | 472 tool definitions, eager, three implementations, hard-coded "27 enabled" | EOS's one-surface decision and pinned roster | RAGFlow's inventories in descriptions; Ruflo's roster | — | Keep off by default; if enabled, ≤ 12 tools, `list_*` instead of inventories, fixed candidate windows, deadlines |
| Multi-agent | none | agents as tools, sub-agents never cite | real Raft/PBFT/gossip **dead**; live = JSON vote counting; one `claude` process role-plays the queen; no result merge | RAGFlow's handoff brief | Ruflo's consensus for a single-process assistant | — | None in EOS. Provide the brief for subagents (`{task, why, known, do}`) and attribute their events (`agent` on runs) |
| Workflow execution | records runs; never executes | canvas scheduler by data readiness, pause = persist path; Redis task executor | `workflow_execute` sequential with resume; `parallel/loop` honestly skipped | RAGFlow's readiness-from-references and cursor persistence | RAGFlow: in-process, turn lost on disconnect; Ruflo: array order | — | Keep "records, never executes"; lint procedures by readiness rules; blocked work = persisted cursor |
| Verification | records adapter runs; person writes verdicts; `draft-test` grounding | SCA judge (LLM); evidence gate on quotes (deterministic) | `output-verifier.ts` $0 structural checks → escalate | RAGFlow's evidence gate; Ruflo's verifier ladder | EOS verifies nothing about an agent's output | — | Verification depth 1: cited ids exist, quotes found at `path:Lx-Ly`, changed files ⊂ scope, wrapper exits |

### 5.4 Cross-cutting

| Row | EOS 1.2.2 | RAGFlow | Ruflo 3.45 | Strength | Weakness | Overlap | EOS should |
|---|---|---|---|---|---|---|---|
| Provenance | `fact.origin/confidence/detector/source_ref/observed_at`; `coverage`; notes `session`, `scope_hashes`; runs `session`; trace task hash | chunk → doc → page positions; citations with off-by-one and hash collisions; verified quotes in the successor | ADR-323 typed provenance (sparsely populated); `resolved_source`; explain strings | EOS's static provenance; RAGFlow's evidence gate; Ruflo's outcome tiers | EOS: only `extracted` origin used; no write provenance on notes | — | `provenance_type` on notes (`user, agent, tool, generated`), `outcome_source` on runs, stable citation ids, evidence gate for lessons |
| Persistence | files → derived SQLite, atomic swap, digest-gated full rebuild; no locks | MySQL + doc engine + MinIO + Redis, eventual consistency, query-time pruning | two SQLite engines on one file, 20+ JSON stores, whole-image rewrites, corruption history | EOS's file-first + disposable index | EOS: full rebuild of 56 MB on any change, racy counters | — | Incremental fold with a `fold_state` cursor; O_EXCL lock for read-modify-write; atomic write for every non-append file; ledger rotation |
| Failure recovery | atomic swap, line-skipping ledgers, exit-0 hooks, collisions reported not resolved | retries with 20–300 s backoff, no-op timeouts, whole-loop re-execution of tools, cancel TTL shorter than task | `recoverMemoryDatabase`, idempotent hook markers, leases; "start fresh" on load error | Ruflo's recovery and idempotency; EOS's honesty | EOS: orphaned tmp, unlocked counters | — | Adopt idempotent event markers by `tool_use_id`, integrity check + backup before any rewrite, never overwrite an authored file after a read failure |
| Token efficiency | measured per call and per hook; brief ≤ 1,500; one surface; but 779k tokens of "changed on disk" notices outside any budget | budgets on rendered blocks; per-answer timing; analytics columns never written | claims unverified; `tokensSaved: null` when unmeasured (library) | EOS measures | nobody measures **tokens saved per session against a baseline** | — | A session-level baseline metric; `null` never `0` for unmeasured savings; stop churning always-loaded files |
| Observability | `eos cost`, `work stats`, `bench`, `doctor --memory`, golden sets (r@1/@3) | per-node events, per-run token sink (not persisted), Langfuse | `routedBy`, A/B by hash, trajectories (opt-in), self-audits | EOS's measurement culture; Ruflo's decision provenance | EOS: three telemetry defects; only one of three retrieval paths measured | — | Fix `rebuilt/ok/median`; add nDCG@10 + MRR@10 + worst-first report; measure `eos-query.sh notes`; per-run cost aggregation |
| Determinism / explainability | every number printable; no model anywhere | LLM in every stage | Thompson sampling; `Math.random()` metrics removed after audit | EOS | — | — | Keep absolute: no stochastic decision, no hidden state |
## 6. Memory architecture comparison, and the unified model for EOS 2.x

### 6.1 The nine memory kinds, as each system actually holds them

| Kind | EOS today | RAGFlow | Ruflo | Verdict |
|---|---|---|---|---|
| Semantic (what is true) | `finding`/`defect`/`decision` notes; scope hashes; audit; 70% generated inventory | LLM-extracted `semantic` items, `valid_at/invalid_at` stored, never filtered | every row `type='semantic'` by default; no behavioural difference | EOS has the only *audited* semantic store; its defect is corpus composition, not shape |
| Procedural (how) | `procedure` notes: steps, counters, derived confidence, Known failures | `procedural` items (text) | none (skills = prose; workflows = JSON steps without memory) | EOS alone; steps need types |
| Episodic (what happened) | `executions.jsonl` — refs only, closed vocabulary, folded | `episodic` items + whole-state DSL snapshots per turn | fragments; trajectories die with the process | EOS alone has a durable, joinable ledger; capture is its gap |
| Working / session | session id threaded; no view; state dir uncollected | canvas `history`, `path` cursor (pause/resume) | `current.json` metrics; fabricated restore | none has a real restore; RAGFlow's "pause = persist cursor" is the transferable idea |
| Project knowledge | code index (facts, edges, codes, coverage), git history, brain docs | none for code | none | EOS alone |
| Execution history | `run list/show/tools/diff`, `route --stats` | `Task`, `PipelineOperationLog`, Redis traces (TTL) | `TaskRecord` board; opt-in JSONL | EOS durable; Ruflo's `resolved_source` is the missing field |
| Decision / ADR | `decision` notes (3), 25 repo ADRs | none | 184 ADRs, many never built | EOS; needs `supersedes` and links to what the decision changed |
| Business workflow | journeys + procedures catalogue (extension) | none | none | EOS alone, one service populated |
| Failure / learning | lessons (required on failure), Seen again, Known failures; counters | bounded feedback prior (off) | confidence nudges; provenance-tiered distillation (real) | EOS's rule is right; nothing reads lessons back into ranking |

**The comparison's verdict:** EOS already has the *right decomposition* — three stores by
lifetime, one writer each, files first, index derived — and the other two systems supply
mostly negative evidence for merging them (Ruflo's accretion to 20+ stores; RAGFlow's
per-turn whole-state snapshots). What EOS lacks is not a store but **five properties on the
stores it has**: temporal validity, typed provenance, outcome source, a fold cursor, and a
lock. And one **composition fix**: generated inventories are not memory and must stop
competing with learned notes for the same slots.

### 6.2 What is stored, and what is not

| Store | Store this | Never store this | Why |
|---|---|---|---|
| Notes (semantic, decision, lesson, procedure) | claims a scan cannot re-derive; the *why*; steps; lessons with `execution` and `procedure`; `provenance_type`; `valid_from/valid_until/superseded_by`; `scope` + hashes | anything grep re-derives (API inventories, AGENTS.md copies, moved instruction sections — these become **generated artifacts** under `.eos/data/generated/`, indexed with `generated=1`, never injected as bodies, never audited as stale notes); rules (rules are loaded, not searched — measured 2026-09-18); prompts; secrets | the audited corpus is 70% inventory by count and its recall@1 fell as it grew; a rule that is findable but not delivered does not change behaviour |
| Work ledger | intent events with `parent`, `blocked_by`, `ticket`, `session`, `agent` | outcomes (those are runs) | ADR-020 |
| Execution ledger | `start/event/finish`; events with `kind, tool, target, ref, exit, ms, status`; `outcome`, `outcome_source`, `lesson`; a `decided` event; a `changed` event per file from PostToolUse | payloads, diffs, prompts, task text, tool arguments (ADR-019); polling repeats (collapse consecutive identical `bb checks` into one event with `count`) | 34% of today's events are CI polling; payloads would recreate the context problem |
| Verifications | `code, outcome, command, exit, log, output_sha256, verdict (person), session, execution` | output bodies | ADR-018 |
| Routing trace | task hash, type, level, factors, model, effort, `effort_in_use`, session, execution | task text | ADR-019 |
| Derived index | everything above folded; facts; coverage; git; symbol rows; 2-hop neighbourhoods; priors | nothing authoritative | rebuildable from files |

### 6.3 Storage format

- **Files stay the source of truth.** Notes: one Markdown file with front matter (add
  `provenance_type`, `valid_from`, `valid_until`, `superseded_by`, `supersedes`, `prior`
  is *not* stored — derived). Ledgers: JSONL, append-only, **with a `v` field on every line**
  (Ruflo's rule: new required fields bump it, additive optional fields do not) and
  **size-based rotation** (`executions.jsonl`, `.1`, `.2`, `.3`; the fold reads all).
- **One SQLite file per project**, rebuilt *incrementally* by a fold cursor
  (`fold_state(source, byte_offset, sha256)` per ledger and per note file), with the full
  rebuild kept as the recovery path. Every write to a derived file is tmp + fsync + rename;
  every read-modify-write of an authored file (procedure counters, `## Seen again`,
  telemetry trims) takes an O_EXCL `<file>.lock` with a 10 s stale takeover.
- **Generated artifacts** (inventories, AGENTS.md imports, moved sections) live under
  `.eos/data/generated/<source>/` as Markdown, are indexed into `search` with
  `source=generated:<kind>` and never into `note`.

### 6.4 Indexing strategy

| Layer | Index | Notes |
|---|---|---|
| Lexical | FTS5 `porter unicode61` over `search(source, ref, title, body, terms, kind, age_days)` with `bm25(title 10, terms 5, body 1)`; a `synonyms(term, alias)` table applied at query time; `fts5vocab` for corpus IDF | one implementation; `eos-query.sh notes` becomes a thin caller |
| Structural | `symbol(id, kind, name, file, parent_id, start, end, signature)`; `edge(src, dst, kind, weight)`; `hop2(src, dst, weight)` precomputed | RAGFlow `mom_id` and `n_hop_with_weight` shapes |
| Temporal | `valid_from/valid_until` columns on `note`; `finished_at` on `execution`; recency computed at read | never store a decayed number |
| Priors | `prior(subject_kind, subject, value ∈ [0,100], updated_at, source)` — moved only by run outcomes (`outcome_source ∈ {test-exec, ci, wrapper}`) and `note eval`; integer budget, largest-remainder split across the notes a run cited | RAGFlow's bounded feedback; Ruflo's "proxy never promotes" |
| Provenance | `fact`, `coverage` unchanged; `note.provenance_type`; `execution.outcome_source`; `search.explain` string per hit (`fts:0.81+cov:0.40+prior:0.05`) | Ruflo's explain string |

### 6.5 Retrieval strategy

1. **Query cleanup**: filler and question-word strip (`rmWWW`), casefold, Unicode word
   tokens, issue keys as one token, synonym expansion at reduced weight.
2. **Legs**: FTS5 BM25 with column weights (notes, procedures, runs, generated — each a
   `source`), symbol lookup (exact → prefix → substring), coverage re-score over the top 64
   with unigram 0.4 / bigram 0.6, procedure picker (title + tags only, as 1.1.x).
3. **Fusion**: RRF k=60 across legs; then `score = rrf × (1 + 0.2·0.5^(age/30d)) + prior/100·α`
   — recency and prior *scaled*, never raw.
4. **Diversity**: MMR by token-Jaccard (λ 0.7) so three copies of a journey note do not
   fill three slots.
5. **Floors**: keep the absolute threshold and the relative floor; **short-query
   min-should-match** ⌈0.3·n⌉ (all terms when n ≤ 2), relaxed to 0.1 on zero hits.
6. **Filters by default**: `valid_until IS NULL OR > now`, `superseded_by IS NULL`,
   `generated = 0` unless asked, `provenance_type` trust filter when the caller sets one.
7. **Explain**: every hit carries its legs and ranks; `eos note search --explain` prints them.

### 6.6 Lifecycle

| Event | Effect |
|---|---|
| `note add` | `valid_from = now`; `provenance_type` required (`user`, `agent`, `tool`, `generated`); dedup by digest, title tokens **and** word-trigram Jaccard ≥ 0.8 (paraphrase guard); scope hashed |
| `note amend --supersedes <id>` | new note; the fold stamps `superseded_by` on the old one; the old file is untouched (supersede-not-delete) |
| `note audit` | stale = scoped file changed; **plus** superseded-but-still-cited, expired, never-retrieved-in-90-days (a report, never an action) |
| `run finish` | counters (locked), lesson note (provenance `agent`), `outcome_source` recorded; priors move only for `test-exec/ci/wrapper` sources |
| Session end (Stop hook) | one `session` summary line in the execution ledger: runs, work touched, notes written, wrapper failures, duration — deterministic, ≤ 200 chars |
| Weekly / on demand (`eos consolidate`) | fold-only: recompute priors from the ledger, list near-duplicate note pairs, list procedures with no run in 90 days, list generated artifacts whose source changed — **a report a person acts on**, never a rewrite (ADR-018) |
| Retention | ledgers rotate by size, never by deletion; notes never expire automatically; `valid_until` is set by a person or a supersede |

### 6.7 Compression and summarisation

- No LLM summarisation in the engine. Compression is **structural**: RAGFlow's
  `narrow_by_terms` for long bodies (whole-line spans, ±600 chars, 1,200 per body, 16,000
  total, tables never narrowed); Ruflo's 4-field structured distill for lessons and session
  lines (summary ≤ 200, detail ≤ 1,024, labels = action vocabulary + top CamelCase/CONSTANT
  tokens by word boundary, paths with `:line`); title + kind + age instead of bodies until a
  file is touched.
- Polling events collapse at write time (`count`), not at read time.

### 6.8 Deduplication, confidence, provenance, time, relationships

- **Dedup**: digest, title tokens, trigram Jaccard (notes); `tool_use_id` markers (hook
  events); `UNIQUE(execution, ord)` (events); one consecutive-identical collapse (polling).
  Journey fan-out copies become **one** note with `services: [...]` and per-service scope
  entries instead of twelve files.
- **Confidence**: never stored. Derived words for procedures stay (`failing/unverified/
  fresh/aging/stale`); notes gain a derived `trust` from `provenance_type × age × prior`;
  facts keep `CERTAIN/LIKELY`. "Confidence–similarity conflation" (Ruflo's named bug) is
  prevented by never writing a retrieval score into a record.
- **Provenance**: `provenance_type` on notes, `outcome_source` on runs, `detector` on facts,
  `explain` on hits, `source` on generated artifacts, `decided` events for routing; a
  lesson's quoted evidence must be found at the cited `path:Lx-Ly` after whitespace
  normalisation (RAGFlow's evidence gate) or the lesson is written with `evidence:
  unverified`.
- **Temporal**: `valid_from/valid_until/superseded_by` on notes; `started_at/finished_at`
  on runs; `observed_at` on facts; recency computed at read; **filtered by default**.
- **Relationships**: `lesson.execution`, `lesson.procedure`, `execution.work_item`,
  `execution.procedure`, `decision.supersedes`, `note.scope → file`, `symbol.parent_id`,
  `edge`, `hop2` — all as columns or rows, all joinable, none inferred.

### 6.9 Token-budget handling

One estimator (chars ÷ 3.5, measured against the harness's own counts and recalibrated in
`eos cost`), one budget loop, every block: SessionStart brief ≤ 800 tokens; task brief ≤
1,500 with reserved shares (procedure 40%, runs 25%, lessons/decisions 15%, notes 10%,
route/record 10%; unused share flows down); file-touch injection ≤ 1,200 per touch and ≤
4,000 p95 per session (the W-02 target, enforced by the injector counting what it has
already delivered this session); bodies only for exact-scope, hand-written notes, narrowed
by the task's terms; everything else as `kind · age · title · id`. Dedup against the
always-loaded files (CLAUDE.md, MEMORY.md, skills) so nothing loaded is re-injected. A
session-level "delivered vs read" measurement (`context-budget.sh --reads`) is the
acceptance test, not the block sizes.
## 7. Context engineering comparison

### 7.1 How each system builds the context a model sees

| Concern | EOS 1.2.2 | RAGFlow | Ruflo 3.45 |
|---|---|---|---|
| Who assembles | EOS renders text; the harness owns the prompt. EOS's output is *one block inside* the agent's context | RAGFlow owns the whole prompt: system + knowledge + history + tool results | Claude Code owns the prompt; Ruflo contributes CLAUDE.md, a hook block and the MCP tool listing |
| Trigger | hooks: SessionStart (branch brief), UserPromptSubmit (task brief), PostToolBatch (host `note_inject`) — no decision by the agent (ADR-021) | per request | hooks: UserPromptSubmit (`route`: ≤ 5 memory lines + router box), SessionStart (`session-restore`) |
| Budget unit | task brief 1,500 tok × 3.0 chars; `build_context` budget × 4 chars; host injection 3,000 tok × 2.7 chars — **three estimators** | `tiktoken cl100k_base` for every model; `message_fit_in(msg, 0.95·max_tokens)`; `kb_prompt` stops at 97% of `max_tokens`; agentic RAG uses a **400,000-character** budget and a fixed **8,000-token** evidence budget | no token budget on the hook block; Claude Code's `skillListingBudgetFraction: 0.06`; MCP listing is whatever the registry holds (472 tool definitions, eager) |
| Section priority | explicit: PROCEDURE → LAST RUNS → KNOWN FAILURES → RELATED NOTES → record hint → ROUTE → branch; exempt lines (`RULE`, `ROUTE`) pass the budget | none: knowledge may take 100% of the window before history is considered; overflow → keep `[system, last user]` and **head-truncate**, so the knowledge block's tail is cut silently | `0.6·pageRank + 0.4·confidence` ranking at session start; prompt-time `0.6·Jaccard(trigrams) + 0.4·pageRank`, threshold 0.05, top-5, ≤ 80 chars each |
| Deduplication | brain heading dedup (first wins); per-session digest of delivered blocks (`~/.local/state/eos/prompted/`); host `note_inject` per-session dedup | none between rounds; tool results appended verbatim; agentic RAG suppresses near-duplicate tool calls at Jaccard 0.8 and collapses `REDUNDANT` hits to one line | dedupe of memory entries by id and content fingerprint at `intelligence.init()`; nothing against what is already in context |
| History | none (EOS holds no conversation); the harness does | chat: **entire** history every turn, dropped all at once under pressure; agent: last `2 × 13` messages; `full_question` rewrite from the last 3 user turns | none |
| Retrieved items rendered as | procedure steps, run lines, note **titles** (bodies only on file touch) | `kb_prompt` blocks: `ID, Title, URL, document_metadata, Content`; `memory_prompt`: raw content **without type or dates** | ≤ 80-char memory lines |
| Compression | clipping (steps 240, lessons 100, failures 140, reason 120); titles instead of bodies | `narrow_by_terms` (grep-like, ±600 chars, 1,200/chunk, 16,000 total, tables never narrowed); `tool_call_summary` exists and is **dead**; `message-compressor.ts` (Ruflo) exists and is unwired | `message-compressor.ts`: extractive TF-IDF under `budgetTokens`, preserves code fences/paths/URLs — "advisory tool + library, no auto-wire" |
| Citations | ids in text (`x-…` run ids, note titles, `path:line` in `why`) | `[ID:n]` with a **1-based/0-based mismatch** between renderer and resolver; agents cite by `sha1(chunk_id) % 500` (≈ 32% collision at 20 chunks); deterministic `insert_citations` fallback + `repair_bad_citation_formats` | none |
| Measured cost | brief median 287 tok/call; task brief 2,033–2,870 chars; host injection p95 7,621 tok/session (target 4,000); always-loaded set 13,418 tok | per-stage timing and token breakdown appended to every chat answer; per-run `token_usage_sink` — **not persisted** (`API4Conversation.tokens`, `TenantLLM.used_tokens` never written) | claimed −32% / −15% / 352× — the repo's own cost-tracker doc marks them "Claimed upstream, not yet verified"; `tokens_saved` fixed to `null` when unmeasured (#3289) |

### 7.2 Findings

1. **EOS is the only one of the three with a priority-ordered, budgeted, deduplicated block delivered without an agent decision.** RAGFlow's assembly is the classic "stuff the window, then cut the head"; Ruflo's is five lines and a box. EOS's design is right; its *measurement* shows two holes: the SessionStart brief is unbudgeted (3,055 chars) and the host's file-touch injection blows its p95 (7,621 vs 4,000 tokens) — while the largest cost in the workspace (779k tokens of "file changed on disk" notices) is outside anyone's budget.
2. **Budget the rendered block, not the payload, and test it** (RAGFlow's `kb_prompt` counts the decoration; `test_kb_prompt_metadata.py` pins that an empty entry does not shift numbering and an overflow entry is excluded whole). EOS's brief clips lines but the branch brief and the `IN FLIGHT` bodies are outside the loop. One estimator, one test shape, every block.
3. **Reserved shares beat greedy prefixes.** RAGFlow lets knowledge take 100% before history is considered; EOS's `_fit_sections` keeps a prefix and drops everything after the first section that does not fit — a large target file can evict the orientation. Give each section a floor and a ceiling.
4. **Render type and time with every memory item.** RAGFlow stores `valid_at/invalid_at/forget_at` and a type per memory, then throws them away in `memory_prompt`. EOS's brief prints run dates and outcomes but note titles carry no kind or age. A `lesson (2 days ago, from run x-…)` prefix costs six tokens and changes how the agent weighs it.
5. **Deterministic narrowing is transferable as-is.** `narrow_by_terms` is stdlib `re`, takes the task's terms, returns whole-line spans with bounded context and a measured table carve-out. It is what `note_inject` should do with an 8,000-character note instead of "≤ 3 full bodies then a digest", and what `get_file` should do instead of 12,000 raw characters.
6. **Citations must be stable ids checked afterwards.** RAGFlow's two numbering schemes each have a defect; the fix is one id authority (`note:<slug>`, `run:<id>`, `file:<path>:<line>`) and a post-hoc "which cited ids exist" check — the shape EOS `verify` could run over an agent's report.
7. **Per-session dedup exists in EOS and nowhere else; keep it, widen it.** The prompt hook already skips a block delivered earlier in the session. The same digest set should cover `note_inject` bodies and the SessionStart brief, and a "changed since delivered" rule should re-deliver only the diff.
8. **The unmeasured "savings" pattern is the anti-pattern.** Ruflo prints token-reduction percentages its own docs call unverified; RAGFlow has the columns and never writes them. EOS measures per call (`eos cost`) and per hook (`context-budget.sh --reads`), which is why its numbers can be trusted — but nothing yet measures the tokens a *session* saved against a baseline. That is the number the roadmap must add.

### 7.3 The EOS 2.x context pipeline (revised from the brief's template)

```text
Task text (never stored)                     ── hooks, no agent decision
  ↓ classify (type, level)                    ── deterministic; feeds the envelope
  ↓ envelope: budget, verification depth,     ── level → envelope table
             subagents, tools
  ↓ candidate pool
      procedure (title/tags match)            ── 1 procedure, whole Rules
      runs of it (target first, then recency) ── 3 + last lesson
      lessons / decisions naming it           ── titles + age + kind
      notes scoped to the files the task or   ── on file touch, narrowed by terms
        the procedure names
      code facts for those files              ── impact depth 2, codes, coverage grade
      routing advice                          ── 2 lines
  ↓ rank within section; reserved share per section (floor, ceiling)
  ↓ render with stable ids, kind and age; clip; count the rendered block
  ↓ per-session dedup (digest of delivered blocks; re-deliver only diffs)
  ↓ deliver (SessionStart / UserPromptSubmit / PostToolBatch)
  … execution by the harness; wrappers append events …
  ↓ verification depth from the envelope: ids cited exist; files changed ⊂ scope; wrapper exits
  ↓ `run finish` → counters, lesson, (offline) routing analysis
```

Changed from the prompt's template: **classification precedes retrieval** (it sizes the budget and picks the verification depth), **procedures and previous executions are one section** (a run is only meaningful under its procedure), **decisions are retrieved as notes** (they are a note kind), and **memory update is split** into the ledger write the wrappers already do and the periodic consolidation a person reviews (§14).
## 8. Model / effort routing comparison

Three systems, three postures. RAGFlow routes *effort* but not *model*; Ruflo routes
*model* but not *effort*; EOS routes both, deterministically, and applies neither.

### 8.1 What each one actually does

| | EOS 1.2.2 (`core/routing/`) | Ruflo 3.45 (`cli/src/ruvector/`) | RAGFlow (`rag/advanced_rag/harness/config.py`) |
|---|---|---|---|
| Unit routed | one task (prompt text, optional `--file`s), once per open run | one task string per `hooks_model-route` / `hooks_pre-task` / `agent_spawn` call | one chat request, by a user-chosen **mode** |
| Classification | 12 task types from 146 weighted keywords + 4 ordered rules; word-anchored, inflection-tolerant; per-project keyword extension; confidence `best/(best+second+1)` | `ModelRouter.analyzeComplexity`: `0.20·lexical + 0.35·semanticDepth + 0.25·taskScope + 0.20·uncertainty`, all substring `includes()` over three word lists; `EnhancedModelRouter`: ≥ 2 of 18 "tier-3" regexes → opus | none — `ModeSpec` is chosen by the caller (`low/medium/high/ultra/NAIVE`) |
| Complexity → level | 7 named factors, weights printed, cuts `.15/.35/.55` → LOW/MEDIUM/HIGH/CRITICAL; floors (architecture ≥ HIGH) and caps (trivial ≤ LOW) | buckets `<0.4 low / <0.7 med / high` in `ModelRouter`; `<0.3 / <0.6` in `EnhancedModelRouter` — **two routers, two threshold sets, different answers for the same text** | mode → `agentic, enable_sca, sca_max_rounds (3/5), use_fanout, action_max_turns (4/6), tools` |
| Model choice | cheapest registry entry meeting `(min_reasoning, min_coding)`; registry is data (`[model_routing.models]`), no vendor names in the policy | `haiku/sonnet/opus` tiers; `ModelRouter` = Thompson sampling over `score_m × Beta(α,β)` (non-deterministic for identical input); `EnhancedModelRouter` = deterministic thresholds | fixed per dialog (`Dialog.llm_id`) |
| Effort / reasoning budget | `EFFORTS = low…max`, level → preferred band, **clamped to what the model accepts**; single `clamp()` is the only code path that returns an effort | **none** — no `effort`, `budget_tokens` or `thinking` anywhere in `v3/@claude-flow/**/*.ts` | effort = mode: rounds, turns, fan-out, tools; wall-clock budgets as constants (180 s total, 50 s headroom, 120 s pass, 90 s prefetch …), `recursion_limit=60` |
| Uncertainty handling | one factor at weight 0.05, **no gate** (ADR-025, explicitly the lesson of Ruflo #2250) | gate `uncertainty > 0.15` escalates one tier unless a learned posterior suppresses it; at cold start the gate still fires — measured: "fix typo in README" → opus 19%, "add a unit test" → opus 58% | n/a |
| Application | advisory: `ROUTE` line in the task brief, `eos route`, MCP `get_context route:true`; optional PreToolUse hook rewrites `model` of an untyped subagent (off in nexus) | advisory: `[TASK_MODEL_RECOMMENDATION] Use model="haiku"` text + CLAUDE.md persuasion; **no PreToolUse `updatedInput` anywhere**; only `agent_execute` (a separate direct-API runtime) applies it mechanically | applied: the mode drives the LangGraph state machine directly |
| Learning from outcomes | none; `adjust_for_history` is identity; `route --stats` joins decisions to run outcomes and stops | Thompson posteriors per **score bucket**: reward haiku success 1.0 / sonnet 0.7 / opus 0.4 / failure 0; "success" from `agent_execute` = **the API returned without error**; state is a whole-file JSON rewrite after every call, no locking | none (per-request) |
| Verification after generation | `eos verify` records a run a person/adapter made; no output check | `output-verifier.ts`: $0 structural checks (empty, < 20 chars, refusal regex, unbalanced fences, repeated lines, TS/JSON syntax) → `{confident, suggestedModel, escalate}` up the ladder | SCA ("sufficient context") LLM judge returns `sub_queries[{satisfied, missing_fact, search_hint}]`; `ToolOutcome.status ∈ {OK, EMPTY, MISS, POOR, REDUNDANT, ERROR}` |
| Provenance of the decision | `routing.jsonl`: task **hash**, type, level, score, 7 factors, model, effort, reason, confidence, `override_source`, `effort_in_use`; `decided` event on the run | `routedBy: heuristic|bandit-fallback|hybrid`, optional `neuralBackend`, A/B sampling by FNV hash, DRACO-shaped trajectories (opt-in) | `stats.py` counts calls/tokens/wall-clock per phase — logged only |
| Measured accuracy | none — no labelled corpus; nexus: 58/67 decisions `sonnet/medium`, 0 applied | agent-routing bench (ADR-391): best candidate **36.3% top-1** on 113 blind-labelled prompts; **no** benchmark measures model-tier choice against labelled tasks or cost/quality | none |

### 8.2 What the evidence says

1. **Ruflo's routers do not agree with each other, and the one that "learns" learns the wrong thing.** Two routers, two threshold tables (`0.4/0.7` vs `0.3/0.6`); `EnhancedModelRouter` calls the base router and discards its pick, using only `complexity`. The bandit's reward is cost-weighted and quality-blind (any HTTP-200 from haiku is the best possible outcome), and its posteriors are keyed by score bucket, so ten typo successes make the router pick haiku for unrelated "add a unit test" tasks 93% of the time. ADR-025's decision to keep EOS deterministic and to treat outcome learning as a reviewed, offline step is confirmed by the Ruflo code, not contradicted by it.
2. **The uncertainty-gate lesson holds, and EOS already applied it.** The Ruflo code comment says it: "`uncertainty` here is structurally ~0.6-0.7 for low-complexity tasks … the gate fires on ~every trivial route … the learned suppression is computed and then discarded one line later." The fix engages only after learning; cold start still escalates. EOS's 0.05-weight factor with no gate is the right shape.
3. **Nobody applies the decision except by persuasion — including EOS in its real deployment.** Ruflo has no `PreToolUse` hook on `Task`/`Agent` and emits no `updatedInput`; its CLAUDE.md asks the LLM to read a marker. EOS built the hook (`eos-route.py`, `hookSpecificOutput.updatedInput.model`) but nexus runs with `hook = false`, the main model is pinned by the human (opus), and `CLAUDE_CODE_SUBAGENT_MODEL=sonnet` pins subagents. Sixty-seven recorded decisions changed nothing. **The mechanism EOS has is the one Ruflo lacks; the habit of turning it on is what is missing.**
4. **Effort is the dimension only EOS models, and the harness cannot take it per call.** Claude Code accepts a model per subagent call but effort per session or per agent definition. EOS therefore renders `/effort <value>` as advice. RAGFlow shows the other way to spend effort: not "how hard should the model think" but **how many rounds, how much fan-out, which tools, and how long** — knobs an orchestrator can actually set. That is the effort model EOS 2.x should adopt for its own work (verification depth, number of subagents, tools, context budget), while continuing to *advise* the harness's model effort.
5. **EOS's classifier is brittle in the same way as Ruflo's, and for the same reason.** Both are English keyword arithmetic. Ruflo's benchmark found that its pattern table could not even return `researcher`, `reviewer` or `none` — a label-coverage problem that caps accuracy at ~70% before any scoring question. EOS has no labelled corpus at all, and its real data shows domain words ("rate plan", "PR", "test env") likely colliding with the taxonomy; the hypothesis cannot be checked because only a hash of the task is stored (ADR-019). **The measurement is the missing piece, not a smarter scorer.**
6. **Cheap post-generation verification is real and transferable.** Ruflo's `output-verifier.ts` costs nothing, is tested, and produces an escalation signal grounded in an observed artifact rather than in the prompt. EOS's `verify` records what an adapter ran; it has no equivalent structural check of an agent's *output*. A deterministic ladder — "did the answer cite ids that exist", "did the diff touch the files the procedure names", "did the wrapper exit 0" — is the EOS-shaped version.

### 8.3 What EOS 2.x should do

- **Keep** deterministic classify → score → pick → clamp; keep "cheapest sufficient"; keep the registry as data; keep the task hash in the trace; keep uncertainty ungated.
- **Add a labelled routing corpus and a gate before touching weights** (Ruflo ADR-391's discipline: frozen, hashed, blind-labelled prompts; dev/test split; an AND-gate on accuracy *and* p95 latency; receipts committed whatever the outcome; an explicit `none`/abstain label). Until it exists, every threshold change is a guess, and `record_prompts = true` collects hashes that cannot be labelled — so the corpus must be authored, not mined.
- **Widen the decision** from `(model, effort)` to a *task envelope*: model, effort (advisory), context budget for the brief and injection, number of subagents allowed, verification depth (0 = record only; 1 = structural checks; 2 = run the procedure's success check), tool set (which wrappers the procedure names). Level → envelope is one table beside the level → requirements table; the harness applies what it can, EOS applies the rest (budget, verification).
- **Turn the hook on where it is safe** (untyped/`general-purpose` subagents only, as 1.2.1 already limits it) and make `routing.jsonl` join `routing-usage.jsonl` per session, so "advised vs used" is one query. Today 60 of 67 advised sonnet while sessions ran opus; nothing reports that gap.
- **Learn offline, per task class, never per call.** `adjust_for_history` stays identity until a reviewed analysis of `routing.jsonl ⋈ executions.jsonl ⋈ routing-usage.jsonl` proposes a threshold or capability change, which lands in `config.toml`, not in a posterior. Reward is the run's declared outcome (`ok/failed/abandoned`), never "the model answered".
- **Do not adopt** Thompson sampling, per-bucket priors, a cost-biased reward, an uncertainty gate, a second router with different thresholds, or persuasion-by-marker as the application mechanism.
## 9. Major architectural insights

1. **EOS's decomposition is right and its composition is wrong.** Three stores by lifetime,
   one writer each, files first, index derived, no model in any decision path — none of the
   other systems does this better, and both show what happens without it (Ruflo: 20+ stores,
   a platform-dependent primary, backups aimed at the wrong file; RAGFlow: whole-state
   snapshots per turn, analytics columns nobody writes). But the *content* of EOS's semantic
   store is 70% generated inventory and moved instruction sections, recall@1 fell from 0.94
   to 0.67 in one day as those arrived, and 257 notes are stale mostly because host scripts
   churn under documentation notes. The fix is a boundary, not a store: **generated
   artifacts are not memory.**

2. **The engine is proven in one project; the architecture must make the other twenty fill
   themselves.** 51 runs and 1,929 events exist in nexus and 0 in any service; services sit
   at 7/21 on the memory matrix. The engine's per-service hooks never fire because sessions
   start in the workspace root. Ruflo's "install a hook per event, spawn a process per
   event" and RAGFlow's queue workers are both wrong for this; the right shape is what the
   host already built — workspace-level hooks and wrapper capture — promoted into the
   engine: **a workspace instance that routes events to the project a path or a branch
   names**, so a run started from nexus about `svc-cpq-ordercapture` lands in that project's
   ledger.

3. **Push works, pull does not, and the push channel is over budget.** 40% of transcripts
   ask EOS anything beyond the brief (the plan's own "biggest risk"), while the file-touch
   injection runs at p95 7,621 tokens against a 4,000 target and re-delivers digests of
   "… 188 more" titles. The task brief's design — priority-ordered, budgeted, exempt rules,
   per-session dedup, delivered by hook — is the best context mechanism in the three
   systems; the SessionStart brief and the injector must be brought under the same loop, and
   "delivered vs read" must become the acceptance metric. RAGFlow's lesson is to budget the
   *rendered* block; Ruflo's is that injecting what the harness already loads is waste.

4. **Lexical retrieval is the correct bet, and EOS is using a fraction of what lexical
   retrieval can do.** RAGFlow — a system with every vector database on the market — runs
   its default path as lexical-gated ranking with a query-coverage re-score that is EOS's
   own formula, adds field boosts, binary-TF short fields, stemming, filler stripping,
   bigram proximity, synonyms and short-query min-should-match, and its newest code uses RRF
   "because RRF needs no threshold calibration." Ruflo's silent mock-embedding incident
   (synonyms at −0.988) is the argument for C-10 written in someone else's incident log.
   EOS has FTS5 with equal weights, no stemmer, and three retrieval implementations of which
   one is measured. **The upgrade is a dozen deterministic techniques, one implementation
   and a benchmark, not a vector store.**

5. **Provenance is EOS's strongest idea and it stops at the code.** `fact.origin`,
   `coverage`, "looked and found nothing", the runtime-wired count, verdicts only from a
   person — nothing in RAGFlow or Ruflo is as honest about what it knows. Yet notes have no
   write provenance (who wrote it: a person, an agent, a generator), runs have no outcome
   source (a test, CI, a wrapper exit, an agent's claim), and lessons cite nothing
   checkable. Ruflo built exactly those two fields (ADR-323 `provenance_type`,
   `resolved_source` with "proxy never promotes") and then forgot to populate one of them;
   RAGFlow's successor verifies every quoted claim as a substring at recorded offsets.
   **Extend the ladder from facts to memory.**

6. **Routing advice that nothing applies is data collection, and the data cannot be
   labelled.** 67 decisions, 58 identical, 0 applied, while the human ran opus and pinned
   subagents to sonnet by environment variable. ADR-019 keeps only a hash of the task, so the
   keyword-collision hypothesis cannot be checked. Ruflo's benchmark gate (frozen labelled
   corpus, dev/test split, AND-gate on accuracy and latency, receipts committed either way,
   an explicit `none` label) is the discipline EOS needs before any threshold moves — and
   Ruflo's bandit is the cautionary tale: a reward equal to "the API answered", posteriors
   keyed by score bucket, an uncertainty gate that fires on every typo at cold start. Keep
   the determinism; add the corpus; turn the hook on where it is safe; widen the decision to
   an envelope the engine itself can apply (budget, verification depth, subagents, tools).

7. **Nobody has a planner worth copying, and EOS should not build one.** RAGFlow's canvas
   planner is dead code; its live decomposition is a LangGraph slot table driven by LLM
   calls; Ruflo's `QueenCoordinator` is a fixed template keyed by task type, unreachable, and
   its live orchestration tools are self-admitted passthroughs (`executor: 'none'`). EOS's
   refusal to improvise steps is correct. What transfers is **plan state as data**:
   procedure steps with typed I/O and readiness rules (RAGFlow's `_schedulable`), work
   items with `parent`/`blocked_by`, and "pause = persist the cursor."

8. **Effort is an orchestration knob, not a model parameter.** The harness cannot take
   effort per call; RAGFlow's modes show effort as rounds, fan-out, tools and deadlines. EOS
   2.x should route a *task envelope* — brief budget, verification depth, allowed
   subagents, tool set — and apply the parts it owns.

9. **Concurrency by convention has already failed elsewhere.** Ruflo lost 11 of 12
   concurrent writes before adding an O_EXCL lock and corrupted its store by running two
   engines on one file; EOS's procedure counters, `## Seen again` appends and telemetry
   trims are unlocked read-modify-writes of git-tracked files, and an orphaned `eos.db.*.tmp`
   sits in nexus. Cheap to fix now; expensive after the first lost lesson.

10. **Full rebuilds do not scale to the workspace EOS already serves.** A 56 MB service index
    is rebuilt when one note changes; `file_cache.json` and `graph.json` (33.8 MB,
    pretty-printed) are rewritten whole every scan. RAGFlow's digest-gated task reuse and
    Ruflo's `distill_state` cursor point the same way: **incremental fold with a cursor, full
    rebuild as recovery.**

11. **The Graphify overlap is now a decision, not an accident.** In 2026-09-18 EOS's impact
    saw import edges only and Graphify answered "what reaches this"; since ADR-015 EOS
    follows `implements/extends/field/new/calls` with 339/345 recall and both are rebuilt by
    the same watcher, at 6 GB for Graphify versus 1 GB for EOS. Two code graphs per service
    is the RAGFlow-seven-backends problem in miniature. EOS 2.x should either consume
    Graphify's graph as a T3 provider or make its own graph good enough to retire the
    duplicate — measured on the same `affected` ground truth.

12. **Honesty is a feature with a maintenance cost, and EOS is drifting.** README lists an
    MCP tool that does not exist; telemetry columns are never set and "median" is a mean;
    the hook does not export what it claims; `.eos/data/` is not immutable. Ruflo's ADR-073
    shows the alternative culture (`_stub`/`_real` flags after an audit) and how far a project
    drifts without one. `test_documents_match_reality.py` should cover MCP names, hook
    claims and telemetry fields, and every EOS report should carry `measured | derived |
    unmeasured` on its numbers.
## 10. KEEP / CHANGE / REMOVE

### KEEP (architecture that must remain)

| Keep | Evidence it is right |
|---|---|
| **Stdlib-only core, no model, no execution** (ADR-001/010/018/025) | Ruflo's inert MicroLoRA, mock embeddings and daemon sweeps that "leaked for days" (#2661); RAGFlow's LLM call per chunk. Determinism is what makes EOS's numbers trustworthy |
| **Files first, SQLite derived, atomic swap** (ADR-005/013/014) | Ruflo's two engines on one file and 20+ stores; RAGFlow's cross-store safety nets. EOS's is the only persistence model with a single fold |
| **Three stores by lifetime, one writer each** (ADR-020/022/023) | the alternative — merged memory — produced RAGFlow's per-turn snapshots and Ruflo's six pattern stores |
| **Provenance rows + coverage; verdicts only from a person** (ADR-014/016/017/018) | no equivalent anywhere; Ruflo's `guarded: true` returned unconditionally is the counter-example |
| **Delivery by hook, one surface, no agent decision** (ADR-021) | measured 4/25 → hooks; Ruflo reaches the model by CLAUDE.md persuasion; RAGFlow is a server |
| **Budgeted, priority-ordered task brief with exempt rules and per-session dedup** | best context block of the three; RAGFlow's `message_fit_in` and Ruflo's 5 lines are worse |
| **Deterministic routing: classify → score → cheapest sufficient → clamp; registry as data; uncertainty ungated** (ADR-025) | Ruflo's #2250 gate and quality-blind bandit confirm each choice |
| **Procedures as notes; counters moved only by `run finish`; confidence derived, never stored** (ADR-023/024) | Ruflo's five decay implementations that compound or never run |
| **A failed run must leave a lesson** | no other system forces the write; Ruflo defaults success to true |
| **Index extensions for host-shaped knowledge** (ADR-013) | kept company identifiers out of core; the journeys extension is the only business-flow model in the three systems |
| **Structural Java parsing by position** (ADR-015) | 339/345 dependent recall; nothing comparable elsewhere |
| **Measurement culture**: golden sets, `bench` against ground truth, `doctor --memory`, `test_documents_match_reality`, ADRs with consequences | RAGFlow deletes zero-hit queries from its benchmark; Ruflo prints unverified percentages |
| **The write guards** (credentials, placeholders, scope, duplicates, amend invariants) | learned from real misuse; keep and extend to paraphrase dedup |

### CHANGE (redesign)

| Change | From | To | Why |
|---|---|---|---|
| **Corpus composition** | generated inventories, AGENTS.md imports and moved sections are `finding` notes (70% by count) | a `generated/` artifact store, indexed with `source=generated:*`, never injected as bodies, never audited as stale notes; notes are only what a session or a person learned | recall@1 decay, 257 stale, ranking exceptions (`--api`, BULK sources, injection demotion) all trace to this |
| **Retrieval** | six lexical paths, equal FTS weights, no stemmer, three implementations, one measured | one implementation: BM25 column weights, `porter unicode61`, filler strip, bigram coverage, min-should-match, synonyms, RRF across legs, scaled recency and prior, MMR, explain strings; `eos-query.sh notes` calls it | RAGFlow §5–6 techniques; measured on the existing golden sets plus nDCG/MRR |
| **Index build** | full rebuild on any digest change | `fold_state` cursor per source, incremental append-folds, full rebuild as recovery; `file_cache.json`/`graph.json` compact JSON | 56 MB per note edit today |
| **Concurrency** | none | O_EXCL lock for read-modify-write of authored files; atomic write for every derived file; integrity check before any rewrite | Ruflo #2878, #2431; orphaned tmp in nexus |
| **Memory properties** | notes: no supersession, expiry or write provenance; runs: no outcome source | `provenance_type`, `valid_from/valid_until/superseded_by/supersedes` on notes; `outcome_source`, `status` per event on runs; filtered by default | RAGFlow's schema (and its unfiltered `invalid_at`), Ruflo's ADR-323 and `resolved_source` |
| **Procedures** | prose steps | typed steps `(tool, target, inputs, outputs, on_failure)` with `{step@field}` references; linted for unknown references, cycles, never-scheduled steps; per-step outcome from wrapper events | RAGFlow `_schedulable`, `exception_method`; today "which step failed" exists only in the catalogue |
| **Context delivery** | task brief budgeted; SessionStart and injection not; three token estimators; prefix fit | one estimator, one loop for all three blocks; reserved floor/ceiling per section; skip-and-continue; kind + age + stable id per item; `narrow_by_terms` for bodies; dedup against loaded files; p95 ≤ 4,000 per session enforced by the injector | measured p95 7,621; branch brief 3,055 chars |
| **Routing** | `(model, effort)` advisory, unapplied, unlabelled | task envelope `(model, effort, brief budget, verification depth, subagents, tools)`; labelled corpus + gate before any tuning; hook on for untyped subagents; `routing ⋈ usage ⋈ executions` report; offline `adjust_for_history` from run outcomes only | §8 |
| **Verification** | records an adapter's run | depth-1 deterministic checks on an agent's output: cited ids exist, quotes found at `path:Lx-Ly`, changed files ⊂ procedure scope, wrapper exits; recorded as `verified` events | RAGFlow evidence gate; Ruflo `output-verifier` |
| **Workspace shape** | one `.eos` per project, per-project hooks that never fire, host glue of 5.8k lines re-implementing brief/prompt/inject/gate | a **workspace instance** in core: hooks registered once at the root; a path/branch/ticket → project resolver; events routed to the owning project's ledger; the host's `eos-brief.py`, `eos-prompt.py`, `note_inject.py`, `note_gate.py`, `eos-capture.sh` become engine templates | services at 7/21 because nothing starts a run in them |
| **Knowledge graph** | file-granular, weightless, one direction | symbol rows (method → class → file, `parent_id`), `implements` followed in reverse for dispatch, edge weights by kind, precomputed 2-hop neighbourhoods, `pagerank × lexical` node ranking; a measured decision on Graphify (consume or retire) | §9 item 11 |
| **Telemetry** | `rebuilt` never set, `ok` ignores exit codes, mean labelled median, eval traffic mixed in | fix the three; tag eval sessions; per-run cost aggregation; session-level tokens-saved baseline; `measured | derived | unmeasured` on every printed number | ADR-019 promises it |
| **CLI structure** | `eos.py` 2,896 lines, `notes.py` 1,766 | one module per command group (`cli/notes.py`, `cli/run.py` …) and `notes.py` split into store / validate / guards / retrieval / render / procedures | maintainability; the rewrite is mechanical and can be gated by the existing 931 tests |
| **Documentation honesty** | README/ARCHITECTURE/ADR claims drift | `test_documents_match_reality` covers MCP tool names, hook behaviour, telemetry fields; amend ADR-001/009 as ADR-006 was | Part F of the core audit |

### REMOVE (unnecessary or duplicated)

| Remove | Why |
|---|---|
| **Two of the three retrieval implementations** (`eos-query.sh notes` awk IDF and `eos query --search` as separate scorers) | unmeasured divergence; one scorer with one golden set |
| **Generated inventories as notes** (206 api-inventory, 182 agents-md, 106 context-budget sections, 56 journey fan-out copies) from the notes store | move to `generated/`; the corpus becomes what was learned |
| **Journey fan-out duplicates** (67 files in 20 identical-body groups) | one note, many scope entries |
| **Per-service engine hooks** (`eos-brief/eos-prompt/eos-close.py` in 13 services) | never fire; replaced by the workspace instance |
| **`symbol_index`, the JS `_RE_METHOD`, Java `wildcards`, `compose` as a separate command, `_impact_from_graph`'s ignored depth, the `graph.json` fallback for impact** | dead or misleading; the index is always present after `scan` |
| **Four ignore lists, three token estimators, two credential guards, two amend classifications** (core vs host) | one of each in core, imported by the host |
| **Pretty-printed `graph.json`, `file_cache.json` and MCP results** | 33.8 MB artifacts; `indent=2` is the only reason |
| **`eos ui` from the core roadmap** (keep it optional, stop counting it as a phase) | reads none of the memory stores; nothing depends on it; Tauri/nested `.eos` were never built |
| **Unbounded CI polling events** | collapse at write time with `count` |
| **The `eos context` document as an agent channel** | delivered 1 note in 62 before, a pointer now; the brief and injection are the channels; keep `context` as a debugging dump |
| **README claims that do not hold** (`get_history`, `EOS_SESSION` export, Stop-hook usage fold, backups, immutability) | replace with what the code does |
## 11. Adopt from RAGFlow

Only what materially improves EOS, and only in deterministic, stdlib form. Each item names
the RAGFlow evidence and the EOS form.

| # | Idea | RAGFlow evidence | EOS form | Improves |
|---|---|---|---|---|
| R1 | **Field-weighted BM25 with short, binary-TF fields** | `query.py:32-40` (kwd 30 / questions 20 / title 10 / content 2); `mapping.json:8-15` `idf·min(tf,1)` | `bm25(search, title 10, terms 5, body 1)`; symbols and tags in short columns | recall@1 on titled notes; no title stuffing |
| R2 | **Query cleanup** — filler and question-word strip, stop words | `common/query_base.py:38-56` (`rmWWW`) | a `_clean_query` before every scorer | the "ok, continue" false positive; noise prompts at 0 chars |
| R3 | **Stemming** | v0.26.4 Snowball; ES analyzer | `tokenize='porter unicode61'` (built into FTS5; zero cost) | "fails" vs "failure" |
| R4 | **Adjacent-bigram phrase boost and proximity** | `query.py:79-90` `"t1 t2"^(2·max(w))`, `~2` | FTS5 phrase/`NEAR` leg; bigram term at 0.6 in coverage (`query.py:180-209`) | multi-word task names ("rate plan change") |
| R5 | **Short-query minimum-should-match with relaxation** | `search.py:300,340-399` (0.3 → 0.1 → dense-only) | require ⌈0.3·n⌉ terms, all when n ≤ 2; relax on zero hits | precision on short prompts, recall on none |
| R6 | **Synonym dictionary, hot-reloadable, no WordNet** | `synonym.py:79-101` | `synonyms(term, alias, weight)` table in `eos.db` from `.eos/synonyms.toml` (service aliases, TR/EN task words) | Turkish prompts vs English notes (H-06's tag workaround) |
| R7 | **RRF k=60 to fuse independent legs** | `navigation.py:1529-1549` — chosen because it needs no threshold calibration | fuse notes / index / symbols / runs / procedures | removes three floors that each mean something else |
| R8 | **Bounded, integer, relevance-split feedback prior** | `chunk_feedback_service.py:84-138` | `prior(subject, value ∈ [0,100])` moved by run outcomes with `outcome_source ∈ {test-exec, ci, wrapper}` and by `note eval`; applied **scaled** (`prior/100·α`), never raw | closes "nothing reads lessons back"; avoids RAGFlow's scale mixing |
| R9 | **Tag lift as a rank feature** | `search.py:990-1020` `P(tag|hits)/P(tag)` | pure SQL over `note_tag` | tags become signal, not decoration |
| R10 | **Content-addressed chunk ids + task digests with reuse** | `chunk_service.py:241`, `task_service.py:544-637` | per-file symbol rows keyed by `xxh(content ∥ parser_version)`; a fold cursor instead of full rebuild | 56 MB rebuilds |
| R11 | **Parent/child small-to-big and title-path breadcrumbs** | `rag/nlp/__init__.py:458-483,1120-1164`; `search.py:1085-1139` | symbol rows with `parent_id` (method → class → file); every rendered symbol prefixed `path › Class › method`; retrieval may swap a method hit for its class summary | precision of code hits; token cost |
| R12 | **Hidden outline row per document** | `task_handler.py:886-940` (TOC chunk, `available_int=0`) | one `outline` row per file from the AST/lexer, no LLM; the thing `get_file` returns first | 12,000-character file dumps |
| R13 | **Stable citation ids and an evidence gate with offsets** | `structure.py:931-1034`; the `generator.py:176` off-by-one as the anti-example | ids `note:<slug>`, `run:<id>`, `file:<path>:Lx-Ly`; a lesson's quoted evidence must be found at the cited lines after whitespace normalisation, else `evidence: unverified` | hallucinated provenance; §7 finding 6 |
| R14 | **Precomputed 2-hop neighbourhoods, pagerank prior, hop-decayed expansion** | `utils.py:803-835`, `search.py:171-221` | `hop2(src, dst, weight)` table; `pagerank` column on `symbol`; `sim/(2+i)` expansion in `impact --rank` | "likely impacted" ordering; hub truncation |
| R15 | **Alias resolution blocking** — digit-bigram guard, Levenshtein ≤ ⌊min(len)/2⌋ | `entity_resolution.py:265-289` | symbol and service alias merge (`OrderCapture` / `order-capture` / `svc-cpq-ordercapture`; ticket-key variants) | prompt → project resolution in the workspace instance |
| R16 | **Deterministic narrowing** | `harness/grep_sed_narrow.py:245-382` (whole-line spans, ±600, 1,200/chunk, 16,000 total, tables never narrowed) | `note_inject` bodies and `get_file` narrowed by the task's terms | injection p95 |
| R17 | **`ToolOutcome.status` vocabulary; disable a probe after N empty results; near-duplicate call suppression at Jaccard 0.8** | `action_session.py:168-173, 69-87, 48` | `status` on execution events; `eos brief` stops re-offering a probe that returned `empty` twice in a session | event semantics; noise |
| R18 | **Budgeted, phase-structured control flow with a deadline per phase and independent stop conditions** | `agentic_rag_graph.py:75-81, 1712-1747` | the shape of `eos consolidate` and of the verification depth-2 job | bounded engine jobs |
| R19 | **Readiness from data references; DSL steps with typed I/O; error policy as data** | `canvas.py:472-529`; `base.py:57-67` (`exception_method`, `exception_goto`) | procedure steps `(tool, inputs, outputs, on_failure: retry|skip|stop|goto)`; `eos procedure lint` | procedures become checkable |
| R20 | **Sub-agent handoff schema** | `agent_with_tools.py:46-64` `{user_prompt, reasoning, context}` | `eos brief --for-subagent` renders `{task, why, known, do}` under a small budget | subagent briefs |
| R21 | **Evaluation metrics and reporting** | `rag/benchmark.py:206-260` (nDCG@10, MAP@5, MRR@10, worst-first) — minus its deletion of zero-hit queries | extend `core/retrieval.py`; keep zero-hit queries in the denominator; add p50/p90 latency | the acceptance gate for R1–R9 |
| R22 | **Shadow-run comparator for an indexer rewrite** | `task_executor_refactor/comparator.py` | `eos index --shadow` diffs the incremental fold against a full rebuild | safe migration to the cursor |

**Not adopted from RAGFlow** (see §13): every vector, LLM and infrastructure component;
the canvas engine; whole-state session snapshots; positional citations; unscaled priors;
live inventories in MCP descriptions.

## 12. Adopt from Ruflo

| # | Idea | Ruflo evidence | EOS form | Improves |
|---|---|---|---|---|
| U1 | **Router benchmark gate** | ADR-391: frozen hashed corpus, blind labels, dev/test split, AND-gate on accuracy and p95, receipts committed whatever the result, `none` label; finding that label coverage capped accuracy at ~70% | `evals/routing/*.tsv` authored (hashes cannot be labelled), `eos route --bench` with the gate; any weight change needs a receipt | the only way to know whether 58/67 identical decisions are right |
| U2 | **$0 structural output verifier → escalation ladder** | `ruvector/output-verifier.ts:209-293` | verification depth 1 (§10 CHANGE) recorded as `verified` events; a failed check suggests the next tier in the ROUTE line | verification of agent output, which EOS lacks entirely |
| U3 | **Decision provenance on every routing result** | `routedBy: heuristic|bandit-fallback|hybrid`; A/B sampled by FNV hash | `decided_by: policy|override|reused|hook`; a shadow policy evaluated on a hash sample of prompts and recorded, never applied | safe experimentation |
| U4 | **Outcome provenance tiers where proxy never promotes** | `memory-distillation.ts:23-26,331-333` (ADR-171); `run-transcript-recorder.ts:25-35` `resolved_source` | `execution.outcome_source ∈ {test-exec, ci, wrapper, agent-claim, user}`; only the first three move counters, the `verified` rung and priors | the "success defaults to true" failure mode |
| U5 | **Typed write-time provenance** | ADR-323 `provenance_type ∈ {user_claim, agent_output, system_observation, tool_result, unknown}`, preserved on upsert, filterable, fails closed — and the lesson that no internal writer set it | `note.provenance_type ∈ {user, agent, tool, generated}` **required** by every writer; trust filter in retrieval | who wrote it |
| U6 | **Supersede-not-delete temporal validity** | `mem/src/tiered-memory.ts:296-407` (`valid_from, valid_until, superseded_by`, recall filters invalid unless `includeExpired`, unparseable timestamps ignored rather than hiding the entry) | note front matter + fold stamping + default filters | EOS's missing `supersedes` |
| U7 | **O_EXCL lock with stale takeover; atomic write with dir fsync** | `memory-initializer.ts:3912-3983`; `fs-secure.ts:41-60`; regression #2878 (11 of 12 writes lost) | `core/lib/lock.py`, `core/lib/atomic.py` used by counters, `## Seen again`, trims, derived files | the unlocked read-modify-writes |
| U8 | **Versioned JSONL with a join key and size rotation; a pairing function that reports `droppedNoMatch`** | `router-trajectory.ts:13-14, 43-214, 326-405` | `v` on every ledger line; `executions.jsonl` rotation; `route --stats` reports unmatched decisions | 592 KB forever-growing ledger |
| U9 | **Incremental, idempotent fold with a cursor and bounded per-tick work** | `memory-distillation.ts:189-207, 270-393` (`distill_state(namespace, last_rowid)`, per-batch transactions, 1,000 rows/tick) | `fold_state(source, offset, sha256)`; full rebuild as recovery | full rebuilds |
| U10 | **Deterministic structured distill** | `structured-distill.ts` (summary ≤ 200, detail ≤ 1,024, labels by action vocabulary + CamelCase/CONSTANT tokens, paths with `:line`) — with word boundaries, not Ruflo's substring bug | lesson and session-summary rendering; FTS `terms` column | compact episodic lines |
| U11 | **Discriminative outcome learner** | `services/learned-routing.ts:45-123`: `support × mean quality × ln(1+A/df) × discriminative share`, support ≥ 2, quality ≥ 0.65, share ≥ 0.6 | offline `eos route --learn` proposing keyword additions per task type from `executions.jsonl` titles and outcomes; a person accepts into `config.toml` | fills `adjust_for_history` without ML or hidden state |
| U12 | **Hook side-effect dedup by `tool_use_id`; failure detection from `tool_response`; exit 2 to block** | `hook-handler.cjs:289-333, 347-364`; `pre-bash` exit 1 as the anti-example | `eos-event` markers; PostToolUse Bash/Edit capture with `status` from the response; `wrapper_guard` already exits 2 | duplicate events when a hook double-fires |
| U13 | **Capability truth model** | `capability-brain.ts:774-781`: catalogued / registered / configured / reachable / healthy / authorized, health defaulting to `unknown`; domain → `taskSignals, commands, skills, agents, risk, verifyBeforeUse` | `eos doctor` rows and a `capability.toml` rendered into the brief as "what applies to this task" | the wrapper table in CLAUDE.md becomes data; 264 guard overrides in 5 days say agents do not know what applies |
| U14 | **Honest status fields; `null` for unmeasured** | ADR-073 `_stub`/`_real`; `tokensSaved: null` (#3289); `algorithm: brute-force|hnsw`, `backend: onnx|mock` | `measured | derived | unmeasured` on every printed number; `null` never `0` | drift like the telemetry columns |
| U15 | **"Never shrink a curated file"** | `auto-memory-bridge.ts:510-543` (#3224 destroyed a 75-line hand index) | any EOS generator that rewrites AGENTS.md blocks, MEMORY.md indexes or note indexes may grow or reorder but never lose lines; otherwise a `.generated.md` sidecar and a report | safety of generators |
| U16 | **Bounded, dry-run-default, protected-role eligibility gate with EMA** | ADR-330 `pheromone-adaptive.ts` | the *shape* for a future "this wrapper/procedure is failing, stop offering it" gate: dry-run default, floors, protected items, deterministic re-enable — computed at read, never stored | safe degradation |
| U17 | **Session-end summary line** | `hooks-tools.ts:637-649` | one deterministic `session` line per Stop in the execution ledger | the "where was I" query |
| U18 | **Global spend fuse** | `global-ai-budget.ts` (O_EXCL ledger, fail-closed, hourly/daily caps, receipts) | the model for a workspace-wide cap on engine jobs and subagent spawns advised by the envelope | bounded cost |

**Not adopted from Ruflo** (see §13): everything stochastic, every "neural" label, the
swarm/consensus stack, 472 eager tools, per-event process spawns, JSON stores rewritten
whole, four routers, agent-config schemas without a loader.

## 13. Do not adopt

Attractive on the surface, wrong for EOS on the evidence.

| Feature | Where it looks good | Why not |
|---|---|---|
| **Embeddings / vector search / ANN (HNSW, RaBitQ)** | RAGFlow's default; Ruflo's headline | C-10 stands: RAGFlow's default path is lexical-gated anyway and its lexical machinery is what carries it; Ruflo's HNSW is brute force below N≈5k, and its mock embeddings were mislabelled MiniLM for months (synonyms at −0.988). Lexical retrieval cannot silently lose its semantics; stdlib cannot host a vector store honestly |
| **LLM-extracted memory** (RAGFlow `memory/`) | typed items with validity windows | an LLM call per turn, no reconciliation, contradictions accumulate; EOS's writer is the session (`note add`, `run finish --lesson`) under a Stop gate — measured to produce notes where instruction did not. Take the schema (§11), not the extractor |
| **Thompson-sampled / bandit model routing** | Ruflo "learns" | non-deterministic for identical input; reward = API success; per-bucket priors generalise a typo to all low-score tasks; cold-start over-escalation (#2250). ADR-018 forbids the engine concluding from runs on its own |
| **An uncertainty gate on routing** | intuitive | measured to fire on every trivial task; EOS already weights uncertainty at 0.05 with no gate |
| **A planner / task decomposer** | RAGFlow slot table; Ruflo `QueenCoordinator` | both are LLM turns or fixed templates; one is dead and the other unreachable. Plan state as data (steps, work items) is enough; the harness plans |
| **Multi-agent swarm, consensus, queen/worker** | Ruflo's marketing centre | Raft/PBFT/gossip are real and dead; live tools count votes in JSON; no result merge exists; one `claude` process role-plays the queen. A single-process advisor needs none of it |
| **Agent routing by embedding similarity** | Ruflo `hooks_route` | 36% top-1 at best; character-hash "semantics"; four label sets. A capability table rendered into the brief is the deterministic version |
| **MCP as the distribution channel; a large tool roster** | RAGFlow 3 tools; Ruflo 472 | measured twice in this workspace (2,194 tokens per session, 4/25 reach); Ruflo's eager 472 definitions with a 258-char repeated suffix are the extreme case. Hooks deliver; MCP stays optional and pinned |
| **Live inventories in tool descriptions** | RAGFlow `list_tools` | paid on every session start; use `list_*` tools |
| **Whole-state session snapshots per turn** | RAGFlow `API4Conversation.dsl` | rewritten whole, mixes definition with state; EOS's append-only ledgers already give resumability by fold |
| **Per-event process spawn hooks; a daemon** | Ruflo's hook handler; worker daemon | Node cold start per event; sweeps that leaked for days; EOS's Python hooks are already the cost floor, and the no-daemon stance is validated by Ruflo turning its workers off |
| **Stored, mutated confidence with decay passes** | Ruflo patterns; RAGFlow none | five decay implementations that compound or never run; EOS's derived-at-read words are correct |
| **Storing every step as a pattern at confidence 1.0; success defaulting to true** | Ruflo trajectories | self-reinforcing; the opposite of "count only explicitly finished runs" |
| **A code-graph store separate from the index** (Neo4j-style, or a second graph beside Graphify) | RAGFlow graph rows; Ruflo `graph_edges` | RAGFlow shows the graph belongs *in* the index; EOS already has two code graphs per service (EOS + Graphify) and must reduce to one, not add a third |
| **GraphRAG entity extraction over code** | RAGFlow | deprecated in RAGFlow's own UI; code entities are known exactly from the AST; the LLM half is the expensive half |
| **RAPTOR / community summaries** | RAGFlow tree | LLM per cluster; for code the hierarchy is the package tree and the outline row is free |
| **DeepDoc / OCR / layout models, Redis queues, MinIO, MySQL, ES** | RAGFlow's stack | infrastructure for PDFs and tenants; EOS indexes source and notes on one laptop |
| **Rust crates / WASM acceleration** | Ruflo's `crates/` | none implements memory or vectors; only a watermark crate is wired |
| **Skills as 30 KB `SKILL.md` files; SPARC** | Ruflo's 361 skills | Claude Code loads them, nobody routes them; SPARC is `mkdir`. EOS's procedures-as-notes delivered by hook are the better disclosure model |
| **`eos ui` as a central component; Tauri; nested `.eos`** | phases.md | never built, nothing depends on the UI, it reads no memory store; keep optional or drop from the roadmap |
| **Generator "auto-writing" notes; git-diff mining** | tempting for coverage (4.2% of Java files) | the cortex lesson (duplicates, contradictions, a reconciliation pass); 27/206 commits state a why; an auto-writer fills the near-duplicate band |
| **Rules as retrievable notes** | would shrink loaded files further | measured 2026-09-18: found if searched, never arrives when it matters; a scope cannot cross repositories. Rules stay loaded |
## 14. Proposed EOS 2.x architecture

### 14.1 The answer to the core question

If EOS were redesigned today from the strongest ideas in the three systems, it would be
**the same engine with five properties added to its stores, one retrieval implementation,
one workspace instance, and an envelope instead of a model pick** — not a RAG server and
not an agent runtime. RAGFlow contributes lexical retrieval techniques, the memory row
shape, evidence gates and procedure-as-data linting; Ruflo contributes benchmark
discipline, provenance tiers, supersession, locking, the fold cursor and the hook
catalogue; both contribute mostly negative evidence about planners, swarms, embeddings and
unbudgeted context. Overlaps that would be unnecessary: a second code graph (Graphify
already exists), a second memory store per feature, a second retrieval scorer, an LLM in
the engine.

### 14.2 Components and data flow

```text
                 Code / Docs / DB snapshots / APIs / Business workflows / Git
                                        │
                        ┌───────────────▼────────────────┐
                        │ 1  INGESTION                    │  scanner + language plugins (Java by position,
                        │    per-file digest → symbol rows│  Python ast, JS regex), index extensions
                        │    facts, coverage, exclusions  │  (journeys, procedures catalogue), git history
                        └───────────────┬────────────────┘
                                        │ append / upsert by content hash
                        ┌───────────────▼────────────────┐
                        │ 2  SEMANTIC + KNOWLEDGE MODEL   │  FileSemantic → symbol(parent_id) → edge(kind, weight)
                        │    outline row per file         │  → hop2 → pagerank; behaviour codes; bean names
                        └───────────────┬────────────────┘
                                        │
                        ┌───────────────▼────────────────┐
                        │ 3  PROJECT KNOWLEDGE GRAPH      │  in the same SQLite file, never a second store;
                        │    (or Graphify as a T3 provider│  the EOS/Graphify decision is measured (§17 P1)
                        └───────────────┬────────────────┘
                                        │
   authored files ─────────────────────►│◄──────────── ledgers (append-only, versioned, rotated)
   notes: finding/defect/decision/      │              work.jsonl · executions.jsonl · verifications.jsonl
   lesson/procedure (+provenance,       │              routing.jsonl · routing-usage.jsonl
   valid_from/until, supersedes)        │
   generated/ artifacts (not memory)    │
                        ┌───────────────▼────────────────┐
                        │ 4  UNIFIED MEMORY SYSTEM        │  one fold, fold_state cursor, O_EXCL locks, atomic writes;
                        │    note · work · execution ·    │  derived: trust, confidence words, priors, hop2, explain;
                        │    verification · prior · fold  │  filters by default: valid, not superseded, not generated
                        └───────────────┬────────────────┘
                                        │
                        ┌───────────────▼────────────────┐
                        │ 5  RETRIEVAL                    │  ONE scorer: clean → FTS5 BM25 (weights, porter) +
                        │    legs → RRF → recency·prior → │  symbols + coverage(uni .4/bi .6) + procedure picker
                        │    MMR → floors → explain       │  → RRF k=60 → scaled recency/prior → MMR → min-should-match
                        └───────────────┬────────────────┘
                                        │
                        ┌───────────────▼────────────────┐
                        │ 6  CONTEXT ENGINEERING          │  one estimator, one budget loop, reserved shares,
                        │    brief · task brief ·         │  stable ids, kind + age, narrow_by_terms, per-session
                        │    file-touch injection ·       │  dedup incl. loaded files; SessionStart ≤ 800,
                        │    subagent handoff             │  task ≤ 1,500, injection ≤ 4,000 p95 per session
                        └───────────────┬────────────────┘
                                        │
                        ┌───────────────▼────────────────┐
                        │ 7  TASK / MODEL / EFFORT ROUTER │  classify → score → level → ENVELOPE
                        │    deterministic, benchmarked,  │  (model, effort, brief budget, verification depth,
                        │    envelope not a model pick    │  subagents, tools) → advise harness, apply own parts
                        └───────────────┬────────────────┘
                                        │ hooks (SessionStart, UserPromptSubmit, PostToolUse, SubagentStop, Stop, PreToolUse Agent)
                        ┌───────────────▼────────────────┐
                        │ 8  HARNESS / AGENT / SKILLS     │  Claude Code, Devin: plan and execute; EOS calls no model
                        │    (outside EOS)                │  capability table rendered into the brief; procedures as data
                        └───────────────┬────────────────┘
                                        │ wrappers (EXIT-trap capture) · PostToolUse Bash/Edit capture
                        ┌───────────────▼────────────────┐
                        │ 9  TOOLS / EXECUTION            │  automation wrappers → eos-event(kind, tool, target, ref,
                        │    (outside EOS; captured)      │  exit, ms, status); polling collapsed; subagent attributed
                        └───────────────┬────────────────┘
                                        │
                        ┌───────────────▼────────────────┐
                        │ 10 VERIFICATION / REVIEW        │  depth 0 record · 1 structural (ids exist, quotes at
                        │    deterministic; verdicts by a │  path:Lx-Ly, changed ⊂ scope, exits) · 2 procedure success
                        │    person                       │  check via wrapper; outcome_source on every finish
                        └───────────────┬────────────────┘
                                        │
                        ┌───────────────▼────────────────┐
                        │ 11 EPISODIC RECORDING           │  run start/event/finish; decided; verified; changed; session
                        │                                 │  summary line at Stop; work item linked; agent named
                        └───────────────┬────────────────┘
                                        │
                        ┌───────────────▼────────────────┐
                        │ 12 MEMORY LEARNING (offline)    │  counters, lessons, priors from test-exec/ci/wrapper outcomes
                        │    fold-only; a person accepts  │  only; discriminative keyword proposals; consolidate report;
                        │                                 │  routing recalibration receipts → config.toml
                        └────────────────────────────────┘
```

Changes from the prompt's template: **the router sits before the harness and after context
engineering** (it sizes the context and picks the verification depth, and its output is an
envelope); **the knowledge graph is a projection inside the index**, not a layer that holds
its own store; **learning is offline and a person's decision**, never a step the engine
performs on its own inside the task loop; **a workspace instance** spans the projects,
because sessions start above them.

### 14.3 The workspace instance

```text
<workspace>/.eos/               one instance, hooks registered here once
   projects.toml                path prefix / branch pattern / ticket pattern / alias → project root
   knowledge/<project>/         shared knowledge tree (as nexus already does)
   generated/<project>/         inventories, imports, moved sections (indexed, never memory)
   eos.db                       workspace index: cross-project search, work --across, routing ⋈ usage
<project>/.eos/                 per project, as today: cache, brain, evidence, eos.db, config
```

Resolution: a prompt, a path or a branch names a project through `projects.toml` and the
alias blocker (R15); `run start` from the workspace root records the run in the **named
project's** ledger; events follow the run pointer; the brief for the workspace prints the
workspace's own items plus the named project's. This replaces nexus's 5.8k lines of hook
glue with engine templates, and makes the service ledgers fill themselves.

### 14.4 Boundaries that stay fixed

- EOS never calls a model, never executes a task, never plans, never concludes a verdict.
- Files are the truth; every table is a projection; every derived number is printable.
- One store per lifetime; one writer per store; one scorer; one estimator; one lock.
- The harness's rules stay in the harness's loaded files; EOS delivers facts, procedures,
  history and advice.
- MCP remains optional and pinned at 12 tools; hooks are the surface.
## 15. Target data model

Files first; every table below is derived. New or changed items are marked **new**.

### 15.1 Authored files

**Note front matter** (`<knowledge>/<project>/<date>-<slug>.md`)

```yaml
kind: finding | defect | decision | lesson | procedure
title: …
created: 2026-09-26T…Z
updated: …                    # amend
source: …                     # generator name, or absent
session: …                    # harness session id
agent: claude | devin | …     # new
provenance_type: user | agent | tool | generated   # new, required
tags: [...]
scope: [path, …]              # may span services: "svc:svc-rim:src/…"   # new prefix
scope_hashes: [sha256, …]
valid_from: …                 # new (defaults to created)
valid_until: …                # new, set by a person or a supersede
supersedes: <note-file>       # new (decision, finding, procedure)
# procedure only (engine-owned): procedure, runs_ok, runs_failed, last_verified, last_execution
# lesson only: execution, procedure, evidence: verified | unverified   # new
```

**Procedure body** — `## Steps` stays a list; a step may carry a typed suffix that the
linter parses and the brief renders unchanged:

```markdown
1. Build the service (tool: mvn) (in: service) (out: build_log) (on_failure: stop)
2. Open the PR (tool: bb) (in: branch, title) (out: pr_id) (on_failure: retry 1)
3. Watch CI (tool: bb) (in: {2@pr_id}) (out: checks) (on_failure: goto 5)
```

**Ledgers** (JSONL, `v: 2` on every line, rotation at 8 MB × 4):

```jsonc
// executions.jsonl
{"v":2,"type":"start","id":"x-…","project":"svc-rim","title":"…","procedure":"…","work_item":"w-…",
 "session":"…","agent":"claude","target":"env1","branch":"…","commit_start":"…","started_at":"…"}
{"v":2,"type":"event","execution":"x-…","at":"…","kind":"called","tool":"bb","target":"env1",
 "ref":"checks 412","exit_code":0,"ms":1840,"status":"ok","count":1,"source":"wrapper","step":3,"session":"…"}
{"v":2,"type":"finish","id":"x-…","at":"…","outcome":"ok","outcome_source":"ci","lesson":null,"commit_end":"…"}
{"v":2,"type":"session","session":"…","at":"…","runs":2,"work":1,"notes":1,"failures":0,"ms":5400000}
```

New fields: `project`, `status ∈ {ok, empty, miss, poor, redundant, error}`, `count`
(collapsed polling), `source ∈ {wrapper, hook, cli}`, `step` (procedure step index),
`outcome_source ∈ {test-exec, ci, wrapper, agent-claim, user}`, the `session` line type.

### 15.2 SQLite (per project; the workspace index has the same shape plus a `project` column)

**Kept as they are:** `meta`, `build_issue`, `note_tag`, `note_scope`, `work_item`,
`work_holder`, `work_event`, `verification`, `brain_doc`, `git_commit`, `git_commit_file`,
`git_commit_ticket`, `fact`, `coverage`, `scan_exclusion`, extension tables.

**Changed / new:**

```sql
-- notes gain the memory properties
ALTER TABLE note ADD COLUMN provenance_type TEXT;      -- user|agent|tool|generated
ALTER TABLE note ADD COLUMN agent TEXT;
ALTER TABLE note ADD COLUMN valid_from TEXT, valid_until TEXT, supersedes TEXT, superseded_by TEXT;
ALTER TABLE note ADD COLUMN procedure TEXT, execution TEXT;   -- joinable lesson/procedure links

-- generated artifacts are not notes
CREATE TABLE generated(id TEXT PRIMARY KEY, kind TEXT, source TEXT, title TEXT, path TEXT, sha256 TEXT, produced_at TEXT);

-- runs gain routing and provenance
ALTER TABLE execution ADD COLUMN project TEXT, outcome_source TEXT, decided_model TEXT, decided_effort TEXT;
ALTER TABLE execution_event ADD COLUMN status TEXT, count INTEGER, source TEXT, step INTEGER;
CREATE TABLE session_summary(session TEXT PRIMARY KEY, at TEXT, runs INTEGER, work INTEGER, notes INTEGER, failures INTEGER, ms INTEGER);

-- symbol-level code model (RAGFlow mom_id shape)
CREATE TABLE symbol(id TEXT PRIMARY KEY,           -- xxh(path ∥ kind ∥ qualified name)
  kind TEXT, name TEXT, qualified TEXT, file TEXT, parent_id TEXT, start_line INTEGER, end_line INTEGER,
  signature TEXT, modifiers TEXT, annotations TEXT, breadcrumb TEXT, content_hash TEXT, pagerank REAL);
CREATE TABLE outline(file TEXT PRIMARY KEY, body TEXT);          -- one row per file, no LLM
ALTER TABLE edge ADD COLUMN weight REAL DEFAULT 1.0;            -- by kind: calls 1.0, field 0.8, new 0.8, extends 0.9, implements 0.9, import 0.5
CREATE TABLE hop2(src TEXT, dst TEXT, weight REAL, PRIMARY KEY(src, dst));

-- priors and learning observations (moved only by run outcomes and evals)
CREATE TABLE prior(subject_kind TEXT, subject TEXT, value INTEGER CHECK(value BETWEEN 0 AND 100),
  updated_at TEXT, source TEXT, PRIMARY KEY(subject_kind, subject));
CREATE TABLE prior_event(at TEXT, subject_kind TEXT, subject TEXT, delta INTEGER, execution TEXT, source TEXT);

-- routing joins
CREATE TABLE routing_decision(at TEXT, session TEXT, execution TEXT, task_hash TEXT, type TEXT, level TEXT,
  score REAL, model TEXT, effort TEXT, budget INTEGER, verify_depth INTEGER, subagents INTEGER,
  decided_by TEXT, effort_in_use TEXT, factors TEXT);
CREATE TABLE routing_usage(session TEXT, model TEXT, messages INTEGER, input INTEGER, output INTEGER,
  cache_read INTEGER, cache_create INTEGER, subagent INTEGER, PRIMARY KEY(session, model, subagent));

-- incremental fold
CREATE TABLE fold_state(source TEXT PRIMARY KEY, byte_offset INTEGER, sha256 TEXT, folded_at TEXT);

-- retrieval
CREATE VIRTUAL TABLE search USING fts5(source UNINDEXED, ref UNINDEXED, kind UNINDEXED,
  title, terms, body, tokenize='porter unicode61');
CREATE TABLE synonyms(term TEXT, alias TEXT, weight REAL DEFAULT 0.25, PRIMARY KEY(term, alias));
```

Row-kind discriminators (`source`, `kind`, `generated`) keep everything in one `search`
table, as RAGFlow keeps every derived row in one index. `prior`, `trust` and confidence
words are **never** written into `note` or `execution`; they are joined or derived.

### 15.3 Derived at read time (never stored)

| Value | Formula |
|---|---|
| Procedure confidence word | `failing / unverified / fresh (≤30 d) / aging (≤90 d) / stale` from the ledger |
| Note trust | `provenance_type` weight (user 1.0, tool 0.9, agent 0.7, generated 0.3) × validity (0 if expired or superseded) × `(1 + prior/100·α)` |
| Recency | `1 + 0.2 · 0.5^(age_days/30)` |
| Retrieval score | `RRF(legs, k=60) × recency + prior/100·α`, then MMR λ 0.7 |
| Staleness | scope hash mismatch; superseded-but-cited; expired; never retrieved in 90 days (report only) |
| Routing confidence | as today, plus `decided_by` and the benchmark's accuracy for that type |

### 15.4 Workspace resolver (`<workspace>/.eos/projects.toml`)

```toml
[[project]]
root = "../microservices/svc-cpq-ordercapture"
aliases = ["ordercapture", "order-capture", "OrderCapture", "cpq-oc"]
branch_pattern = "^(feature|bugfix|hotfix|chore)/PROJ2-"
tickets = ["PROJ2", "FM"]
knowledge = ".devin/knowledge/svc-cpq-ordercapture"
```
## 16. Target execution lifecycle

```text
SessionStart hook ─► eos brief (workspace + named project)            ≤ 800 tok, budgeted, deduped against loaded files
        │             IN FLIGHT · RUNS OPEN · FAILING PROCEDURES · KNOWN HERE · ledger sync
        ▼
UserPromptSubmit ─► eos brief --task "<prompt>" --task-only            ≤ 1,500 tok, reserved shares
        │             1 classify → level → ENVELOPE (model, effort, budget, verify_depth, subagents, tools)
        │             2 resolve project (alias / path / ticket)
        │             3 legs: procedure (title+tags) · runs (target, recency) · lessons/decisions · notes · symbols
        │             4 RRF → recency·prior → MMR → floors → render (kind · age · id) → per-session dedup
        │             5 ROUTE line + "Record this run: eos run start …"
        ▼
Agent opens the run ─► eos run start <project> --title … --procedure … --work <item>
        │             routes the title once (decided event), writes the pointer for this session
        │             PreToolUse(Agent) hook: untyped subagent gets envelope.model; named agents untouched
        ▼
Work ──┬── wrapper call ─► EXIT trap ─► eos-event kind/tool/target/ref/exit/ms/status[/step]   (collapses polling)
       ├── editor Edit/Write ─► PostToolUse hook ─► changed event (path only) · note_inject on touch
       │                          (exact-scope hand-written notes, narrowed by terms; ≤ 4,000 tok p95 / session)
       ├── raw Bash outside wrappers ─► PostToolUse hook ─► event with source: hook, status from tool_response
       ├── subagent ─► eos brief --for-subagent {task, why, known, do}; its events carry agent + parent run
       └── a fact learned ─► eos note add --kind … --provenance agent (dedup: digest, title, trigram)
        ▼
Verification ─► depth from the envelope
        │        0: record only
        │        1: cited ids exist · quotes found at path:Lx-Ly · changed files ⊂ procedure scope · wrapper exits 0
        │        2: the procedure's `## Success` command through its wrapper → verified event, outcome_source
        ▼
eos run finish --outcome ok|failed|abandoned [--lesson …] [--source test-exec|ci|wrapper|agent-claim|user]
        │        failed without a lesson is refused; the lesson note is written first (evidence gate on quotes)
        │        counters move under a lock; priors move only for test-exec/ci/wrapper outcomes
        ▼
Stop hook ─► eos-close: open runs and held work asked about once (exit 2, every answer accepted)
        │     session summary line appended; routing usage folded from the transcript
        ▼
Next session ─► the same brief, one run richer; SessionStart shows the failing procedure and its lesson
        ▼
Offline (a person runs it) ─► eos consolidate: priors recomputed, near-duplicates listed, unrun procedures listed,
                                generated artifacts whose source changed listed, routing accuracy vs corpus,
                                advised-vs-used report, keyword proposals — a report, never a rewrite
```

**Failure paths.** A hook that cannot reach EOS exits 0 and records `ok:false` where a log
exists. A wrapper whose capture fails still returns its own exit code. A run left open is
asked about once and otherwise reads as open. An event for an unknown run creates the run.
A lock that cannot be acquired within 15 s fails the *engine* command, never the wrapped
one. A derived-file read failure never triggers a write over an authored file. An unknown
project in the resolver records the run in the workspace ledger and says so in the brief.

**What the lifecycle never does.** It never picks a task, never decomposes one, never
retries a mutation, never marks a procedure trusted, never deletes a note or a ledger line,
never stores the prompt, never spends a model call.
## 17. Implementation roadmap

Each phase is one or more EOS releases developed upstream, pulled into nexus by subtree,
with tests first and an ADR where a decision is made — the discipline that shipped 1.0.0
and 1.2.0. Every phase ends with a measurable gate that a fresh session can run. Version
numbers are targets; the order is the dependency order. Estimates assume the pace of the
last two plans (a milestone per day of focused work) and are deliberately not calendar
dates.

### Phase 1 — Foundation (2.0.0): honesty, safety, one scorer

| Item | Components | Files likely affected |
|---|---|---|
| F1 Fix the measured defects | telemetry `rebuilt`/`ok`/median; `ai update` from runtime (copy `.md` templates); branch-brief budget; prompt-hook chat false positive (filler strip); `eos-event` `session_env`; agent template MCP fence; README MCP list | `core/telemetry.py`, `core/lib/updater.py`, `core/brief.py`, `core/notes.py`, `bin/eos-event`, `core/ai/templates/*`, `README.md`, `tests/test_documents_match_reality.py` (extend to MCP names, hook claims, telemetry fields) |
| F2 Locks and atomic writes | `core/lib/lock.py` (O_EXCL, pid, 10 s stale, 15 s timeout), `core/lib/atomic.py` (tmp + fsync + rename + dir fsync); used by counters, `## Seen again`, trims, all derived files; integrity check before `os.replace` | `core/notes.py`, `core/executions.py`, `core/telemetry.py`, `core/routing/trace.py`, `core/index.py`, generators |
| F3 One retrieval scorer | `core/retrieval.py` becomes the scorer: clean → FTS5 BM25 column weights + `porter unicode61` → coverage (uni .4 / bi .6) over top 64 → RRF across legs → scaled recency → MMR → floors → explain; `search_notes`, `eos query --search`, brief legs and `eos-query.sh notes` call it; `synonyms` table from `.eos/synonyms.toml` | `core/retrieval.py`, `core/notes.py` (retrieval code moves out), `core/index.py` (`search` schema, `fts5vocab`), `core/brief.py`, `core/mcp_server.py`, nexus `automation/eos-query.sh` (thin caller) |
| F4 Split the CLI | `core/eos.py` → `core/cli/{init,scan,index,notes,work,run,procedure,route,brief,intel,ai}.py`; `notes.py` → `notes/{store,validate,guards,render,procedures}.py` | mechanical; gated by the 931 tests |
| F5 Ledger discipline | `v` on every line; rotation `executions.jsonl` 8 MB × 4; fold reads all; `count` collapsing of consecutive identical events at write | `core/executions.py`, `bin/eos-event`, `core/index.py` |
| F6 Evaluation | `eos note eval` adds MRR@10, nDCG@10, worst-first report, p50/p90 latency; zero-hit queries stay in the denominator; golden sets re-run before/after F3 | `core/retrieval.py`, `docs/eos-evals/golden/*` (host) |

**Dependencies:** none. **Migration:** `SCHEMA_VERSION` 4 → 5 (rebuild, no migration);
`eos-query.sh notes` output format kept identical for callers. **Risks:** F3 changes
ranking — mitigated by the golden-set gate; F4 is large — mitigated by doing it last in the
phase with zero behaviour change. **Acceptance:** all 931 tests + new ones green; `eos cost`
shows non-zero `rebuilt` and real failures; branch brief ≤ 800 tokens on nexus; "ok,
continue" prints nothing; nexus.tsv recall@1 ≥ 0.80 and every set's recall@3 not lower than
today; `eos-query.sh notes` and `eos note search` return the same top-3 on the golden sets;
no unlocked read-modify-write remains (`grep` gate in `tools/check-clean.sh`).

### Phase 2 — Memory (2.1.0): the five properties and the corpus boundary

| Item | Components | Files |
|---|---|---|
| M1 Memory properties on notes | `provenance_type` (required by every writer; `eos note add --provenance`), `agent`, `valid_from/valid_until/supersedes/superseded_by` (fold stamps `superseded_by`); default filters in retrieval and brief; `note audit` reports superseded-but-cited and expired | `core/notes/*`, `core/index.py`, `core/retrieval.py`, `core/brief.py`, ADR-026 |
| M2 Outcome source and event status | `run finish --source`, `outcome_source`; event `status`, `source`, `step`; only `test-exec/ci/wrapper` move counters and the `verified` rung | `core/executions.py`, `core/notes/procedures.py`, `core/inspector.py`, `bin/eos-event`, ADR-027 |
| M3 Generated is not memory | `.eos/data/generated/` store; `generated` table; `search.source = generated:*`; injection never uses generated bodies; `note audit` skips them; a migration command moves `source ∈ {api-inventory, agents-md, context-budget, journey-map, repo-topology, H-05}` out of the notes tree with a mapping file | `core/notes/store.py`, `core/index.py`, `core/eos.py`, host `automation/*` generators, ADR-028 |
| M4 Journey fan-out → one note | multi-service `scope` prefixes (`svc:<name>:<path>`); the generator writes one file | `extensions/journeys.py`, host generator, `core/notes/store.py` |
| M5 Paraphrase dedup | word-trigram Jaccard ≥ 0.8 guard on `note add`, reported not refused for `amend` | `core/notes/guards.py` |
| M6 Priors | `prior`, `prior_event` tables; moved by `run finish` (integer budget 1, largest-remainder split across notes the run's events reference) and `note eval`; applied scaled in F3's scorer; `eos consolidate` recomputes from the ledger | `core/executions.py`, `core/retrieval.py`, `core/index.py`, ADR-029 |
| M7 Session line | Stop hook appends a `session` summary line; `eos brief --session` renders open runs, held work, failing procedures | `core/ai/templates/session_stop.py`, `core/executions.py`, `core/brief.py` |

**Dependencies:** F2 (locks), F3 (scorer). **Migration:** M3 moves ~590 files in nexus with
a mapping TSV (the A-06 verbatim check applies); `SCHEMA_VERSION` 6. **Risks:** M3 changes
what agents find — mitigated by keeping generated artifacts searchable through `--generated`
and measuring the golden sets before and after; M6 could bias ranking — mitigated by the
integer budget and `α ≤ 0.2`. **Acceptance:** `eos doctor --memory` gains rows for
provenance coverage (≥ 95% of notes typed), supersession, generated separation; notes store
in nexus ≤ 300 files, all `provenance_type` set; nexus.tsv recall@1 ≥ 0.85; injection p95
≤ 4,000 tokens per session over 20 fresh sessions (`context-budget.sh --reads`); a failed run
with `--source agent-claim` moves no counter.

### Phase 3 — Context engineering (2.2.0): one loop, every block

| Item | Components | Files |
|---|---|---|
| C1 One estimator and one budget loop | `core/context/budget.py`: chars ÷ 3.5 calibrated against `routing-usage.jsonl`; reserved shares (floor, ceiling) per section; skip-and-continue; applied to SessionStart brief, task brief, injection, `build_context` | `core/brief.py`, `core/inspector.py`, `core/notes/render.py`, host `note_inject.py` → engine template |
| C2 Render contract | every item `kind · age · title · id`; stable ids `note:<slug>`, `run:<id>`, `file:<path>:Lx-Ly`; procedure Rules whole; lessons with `evidence:` flag | `core/notes/render.py`, `core/brief.py` |
| C3 Narrowing and outlines | `narrow_by_terms` for bodies and `get_file`; `outline` row per file returned first | `core/context/narrow.py`, `core/inspector.py`, `core/index.py`, `core/knowledge/builder.py` |
| C4 Session dedup incl. loaded files | digest set covers brief, injection and the always-loaded files (`CLAUDE.md`, `MEMORY.md`, skills) read from `.eos/config.toml [ai] loaded`; re-deliver only diffs | `core/ai/templates/prompt_submit.py`, `core/context/dedup.py` |
| C5 Workspace instance | `<workspace>/.eos/projects.toml`, alias blocker (R15), hooks registered once at the root, run/event routing to the named project's ledger, `work --across` and brief across projects; nexus's `eos-brief.py`, `eos-prompt.py`, `note_inject.py`, `note_gate.py`, `eos-capture.sh` become engine templates | `core/workspace.py`, `core/ai/writer.py`, `core/ai/templates/*`, `core/executions.py`, ADR-030 |
| C6 Subagent handoff | `eos brief --for-subagent` → `{task, why, known, do}` ≤ 400 tokens; subagent events carry `agent` and the parent run | `core/brief.py`, `core/executions.py`, `core/ai/templates/pretooluse_task.py` |
| C7 Session-level measurement | `eos cost --sessions` joins telemetry, `routing-usage.jsonl` and hook deliveries: tokens delivered, tokens read (transcript fold), tokens saved vs the baseline sessions recorded in `docs/eos-evals/context-reads-baseline.md` | `core/telemetry.py`, `core/routing/usage.py` |

**Dependencies:** Phase 2 (kinds, ids, provenance). **Migration:** nexus replaces its hook
scripts with `eos ai update --workspace`; the 13 per-service hook trios are removed.
**Risks:** C5 is the largest structural change — mitigated by shipping the resolver
read-only first (brief across projects) and moving run routing second; C1 changes brief
bytes — mitigated by the byte-identical test for projects without the new config.
**Acceptance:** where-did-we-leave-off eval answered before the first acting command from
a **service** directory (not only nexus); SessionStart ≤ 800 and task brief ≤ 1,500 tokens
on every project; injection p95 ≤ 4,000; "changed on disk" notice volume for always-loaded
files down ≥ 50% over 20 sessions (the files stop being rewritten by generators); a run
started from nexus about a service lands in that service's ledger; `eos doctor --memory`
≥ 15/21 on two services within a month of C5.

### Phase 4 — Agent / model routing (2.3.0): measured, applied, widened

| Item | Components | Files |
|---|---|---|
| A1 Routing corpus and gate | `evals/routing/*.tsv` (≥ 200 authored prompts, blind-labelled, TR and EN, a `none` label, dev/test split); `eos route --bench` with an AND-gate (accuracy per type, p95 < 10 ms) and a receipt written to `docs/eos-evals/routing/`; no weight or threshold change without a receipt | `core/routing/bench.py`, `evals/routing/`, ADR-031 |
| A2 Envelope | `Decision` gains `budget`, `verify_depth`, `subagents`, `tools` from a level → envelope table; the brief and MCP render it; the engine applies budget and verification depth itself | `core/routing/policy.py`, `types.py`, `adapters.py`, `core/brief.py` |
| A3 Apply what is safe | `eos-route.py` on for untyped/`general-purpose` subagents in nexus; `routing_decision ⋈ routing_usage ⋈ execution` table and `eos route --stats` "advised vs used vs outcome" | `core/routing/trace.py`, `core/index.py`, nexus config |
| A4 Capability table | `capability.toml` (domain → wrappers, procedures, agents, risk, `verifyBeforeUse`) with the six-state truth model checked by `eos doctor`; rendered into the task brief as "applies here" | `core/capability.py`, `core/brief.py`, `core/eos.py` (doctor), host `capability.toml` |
| A5 Offline learner | `eos route --learn`: discriminative keyword proposals per task type from `executions.jsonl` (title terms × outcome); output is a diff for `[model_routing.keywords]` a person accepts; `adjust_for_history` reads only accepted config | `core/routing/learn.py`, ADR-032 |

**Dependencies:** Phase 3 (envelope needs the budget loop; capability table needs the
workspace). **Risks:** the corpus is human work (~2 days) — no shortcut exists; the hook
changes subagent models — mitigated by the 1.2.1 restriction to untyped agents and a
`[model_routing] hook_dry_run` that records what it would have set. **Acceptance:** bench
receipt committed with top-1 accuracy ≥ 0.75 on the test split and ≥ 0.9 on `none`; advised
model applied to ≥ 90% of untyped subagent calls in 20 sessions; the "advised vs used"
report exists and shows the gap; 264 guard overrides in 5 days falls below 50 after A4
(agents know what applies).

### Phase 5 — Execution and episodic memory (2.4.0): capture everywhere, verify cheaply

| Item | Components | Files |
|---|---|---|
| E1 PostToolUse capture | Bash outside wrappers → event `source: hook`, `status` from `tool_response`; Edit/Write → `changed` event (path only); dedup by `tool_use_id` markers | `core/ai/templates/posttooluse.py`, `bin/eos-event`, `core/executions.py` |
| E2 Typed procedure steps and lint | `(tool:) (in:) (out:) (on_failure:)` parsing; `eos procedure lint` (unknown references, cycles, never-scheduled steps, tools not in the capability table); `step` on events by tool + order; `procedure show` prints per-step last outcome | `core/notes/procedures.py`, `core/executions.py`, `core/brief.py`, ADR-033 |
| E3 Verification depth 1 and 2 | `eos verify --output <file>`: cited ids exist, quotes found at `path:Lx-Ly`, changed files ⊂ procedure scope, wrapper exits; depth 2 runs the procedure's `## Success` command through its wrapper and records `outcome_source`; results as `verified` events | `core/verification.py`, `core/context/evidence.py`, `core/eos.py` |
| E4 Work ↔ run linking | `run start --work` default from the branch's ticket; `work show` lists runs; `eos-close` asks about both | `core/executions.py`, `core/work.py`, `core/ai/templates/session_stop.py` |
| E5 Incremental fold | `fold_state` cursor per source; append-folds for ledgers and changed notes; full rebuild as recovery; `eos index --shadow` diffs the two | `core/index.py`, ADR-034 |
| E6 Compact artifacts | `graph.json`, `file_cache.json`, MCP results without `indent`; `graph.json` split per 5,000 nodes or replaced by the index for MCP `get_graph` (paginated) | `core/generators/*`, `core/lib/cache_store.py`, `core/mcp_server.py` |

**Dependencies:** Phase 2 (status, source), Phase 4 (verification depth from the envelope).
**Risks:** E1 doubles event volume — mitigated by collapsing and by `source` filtering in
`run tools`; E5 is the riskiest engine change — mitigated by the shadow comparator and by
keeping full rebuild one flag away. **Acceptance:** ≥ 95% of wrapper and editor actions in
20 sessions appear as events; `run show` shows which step failed for every failed run of a
typed procedure; verification depth 1 catches a planted hallucinated citation in the eval;
index update after one note change < 1 s on a 56 MB service index; `eos index --shadow`
reports zero row differences on three services.

### Phase 6 — Learning and optimisation (2.5.0): offline, reviewed, measured

| Item | Components | Files |
|---|---|---|
| L1 `eos consolidate` | fold-only report: priors recomputed, near-duplicate pairs, unrun procedures, superseded-but-cited, generated artifacts whose source changed, keyword proposals, routing accuracy vs corpus, advised-vs-used; bounded per run; never rewrites | `core/consolidate.py`, ADR-035 |
| L2 Graph decision | measure EOS `impact` vs Graphify `affected` on the same ground truth (Java test/subject pairs + a hand-labelled set); decide: consume Graphify as a T3 provider through the index, or add `implements` reverse edges, weights, `hop2` and `pagerank` and retire the duplicate | `core/knowledge/builder.py`, `core/index.py`, `core/bench.py`, ADR-036 |
| L3 Symbol rows and outlines | `symbol(parent_id, breadcrumb)`, `outline`, method → class → file small-to-big in retrieval and `get_file` | `core/knowledge/*`, `core/index.py`, `core/inspector.py` |
| L4 Honest numbers | `measured | derived | unmeasured` tag on every printed number; `null` never `0`; `test_documents_match_reality` covers every report | all `cmd_*` renderers |
| L5 Recalibration receipts | thresholds and capability numbers changed only with a bench receipt and an ADR addendum; `policy.adjust_for_history` reads accepted config only | `core/routing/*`, `docs/decisions/` |

**Dependencies:** everything above. **Risks:** L2 may conclude that Graphify wins — that is
a valid outcome and saves 1 GB per service of duplicate work; L1 must not become a daemon
(Ruflo's lesson). **Acceptance:** `eos consolidate` on nexus runs under 10 s and its report
is acted on at least once per month (a tracked count of accepted proposals); one code graph
per service; recall@1 on all golden sets ≥ 0.85 and never below the Phase 1 gate; the
capability matrix re-filled by a reader who did not do the work on **three** projects, not
one.

### Cross-phase host work (nexus)

- Move generated notes out (M3), collapse journey fan-out (M4), write `projects.toml` and
  `capability.toml` (C5, A4), author the routing corpus (A1), replace hook scripts with engine
  templates (C5), record the where-did-we-leave-off eval from a service directory (C gate).
- Stop rewriting always-loaded files from generators (the 779k-token notice cost); measure
  with `context-budget.sh --reads` after 20 sessions.
- Keep `docs/eos-plans/` as the status ledger, one row per item above, flipped in the same
  commit as the work — the protocol that closed the last two plans.
## 18. Risks and trade-offs

| Risk / trade-off | Consequence if ignored | Mitigation in this plan |
|---|---|---|
| **Lexical ceiling.** Paraphrase and cross-language recall stay bounded without embeddings | some "how do I" prompts never reach the right note | R1–R9 raise the ceiling substantially before it is reached; synonyms carry TR/EN; the golden sets say when the ceiling is hit; C-10 is re-examined only against a measured gap, and then as a ≤ 64-candidate re-scorer with a `backend` flag |
| **Corpus migration (M3) changes what agents find** | a session that relied on an inventory note stops finding it | generated artifacts stay searchable behind `--generated`; the A-06 verbatim check; golden sets before/after; the mapping TSV |
| **The workspace instance (C5) is a structural change to a working system** | broken briefs across 21 projects for a day | read-only resolver first, run routing second; byte-identical output where no `projects.toml` exists; the eval re-run from a service directory as the gate |
| **Routing corpus is human work** | without it, A2–A5 are unmeasured — the situation today | ~200 prompts is two days; the gate refuses tuning without it; `record_prompts` hashes cannot substitute |
| **Locks add failure modes** | a stale lock blocks a counter update | 10 s stale takeover, 15 s timeout, engine command fails not the wrapper, `eos doctor` reports lock files |
| **Incremental fold diverges from full rebuild** | silent index drift | `eos index --shadow` comparator; full rebuild one flag away; digest still recorded |
| **More capture, more noise** | events double, `run show` unreadable | write-time collapsing, `source` and `status` filters, `run tools --source wrapper` |
| **Priors bias retrieval towards the past** | a once-useful note keeps winning | integer budget, `α ≤ 0.2`, scaled not raw, recomputed from the ledger by `consolidate`, visible in `--explain` |
| **Verification depth 2 runs wrappers** | cost and side effects | only the procedure's `## Success` command, only through its wrapper, only when the envelope says so; reads only |
| **Envelope advice is still advice for model and effort** | the human keeps running opus | A3 measures advised-vs-used so the gap is a number, not a suspicion; the hook applies what the harness allows |
| **Graph decision may retire EOS's impact or Graphify** | political cost either way | measured on shared ground truth (ADR-011's rule); either outcome removes 1 GB of duplicate work per service |
| **Splitting `eos.py`/`notes.py` is churn** | merge conflicts with in-flight upstream work | done at the end of Phase 1 as pure moves, gated by the existing tests |
| **Honesty tags on every number cost lines** | brief grows | one character (`~` for derived, `?` for unmeasured) in the brief; full words in reports |
| **The plan is large for one maintainer** | half-done phases | each phase is releasable alone and independently useful; the status ledger shows the resume point; no phase depends on a later one |
| **Rate of change vs soak time** | 1.0.0 and 1.2.0 each landed in a day; defects surfaced in the audit | each phase gate requires 20 real sessions of data before the next phase starts |

**Trade-offs accepted:** no vector search (explainability and stdlib over paraphrase
recall); no planner (the harness plans; EOS refuses to improvise); no daemon (reports a
person runs); no MCP by default (hooks deliver); no automatic verdicts or promotions (a
person decides); generated inventories out of the memory corpus (findability of inventories
behind a flag over corpus quality); a workspace instance (one more `.eos` over 21 unfed
ledgers).
## 19. Final architectural principles

1. **Files are the truth; every table is a projection; every number is printable.** A store
   that cannot be rebuilt from files is not allowed; a value that cannot be explained is not
   printed.
2. **No model, no execution, no plan, no verdict inside the engine.** EOS advises the
   harness and records what happened. Learning is a report a person accepts.
3. **One of each.** One store per lifetime with one writer; one retrieval scorer; one token
   estimator; one budget loop; one lock; one code graph per service; one numbering
   authority for citations.
4. **Generated is not memory.** What a scan or a generator can re-derive lives beside the
   memory, indexed and flagged, never ranked against what a session learned.
5. **Provenance on every layer.** Facts carry detector and origin; notes carry who wrote
   them and until when they hold; runs carry where the outcome came from; retrieval hits
   carry their legs; routing decisions carry who decided. Proxy evidence never promotes.
6. **Temporal validity is a column, not a deletion.** Supersede, never delete; filter
   invalid by default; derive confidence and recency at read time from immutable
   timestamps; never store a decayed number.
7. **Delivery is a hook, never a decision; every block is budgeted; every budget is
   measured.** Priority-ordered, deduplicated against what is already loaded, rendered with
   kind, age and a stable id, narrowed by the task's terms.
8. **Lexical, deterministic, benchmarked.** Field weights, stemming, filler stripping,
   bigrams, synonyms, min-should-match, RRF, scaled priors — measured on golden sets with
   MRR and nDCG, zero-hit queries in the denominator, receipts committed whatever the result.
9. **Routing is an envelope, decided deterministically, applied where the harness allows,
   recalibrated only with a receipt.** Uncertainty never escalates on its own; the cheapest
   sufficient model wins; a reward is a declared run outcome, never "the model answered."
10. **Capture where the action happens; never fail the action.** Wrappers and PostToolUse
    hooks append references, never payloads; polling collapses; subagents are attributed;
    a lost line never hides a run.
11. **Concurrency is a lock and an atomic write, not a convention.** Append-only for ledgers,
    tmp + fsync + rename for everything derived, O_EXCL for every read-modify-write of an
    authored file, an integrity check before any replace.
12. **Silence is not absence, and a claim is not a measurement.** Coverage rows say what was
    looked for; `measured | derived | unmeasured` says what a number is; `null` is never `0`;
    a document that names a capability the code lacks fails a test.
13. **The workspace is the unit of deployment.** Sessions start above the projects, so hooks
    register once, a resolver names the project, and every project's ledger fills itself.
14. **Host-shaped knowledge stays in extensions; rules stay in loaded files.** A business
    flow model enters core only when a second host can fill it; a rule that is findable but
    not delivered does not change behaviour, so rules are loaded, facts are searched.
15. **Ship the writer with the schema, then measure whether it is written to.** A table
    nobody writes is an audit finding (cortex, Ruflo). A version is cut when the matrix holds
    in the projects the work is meant to serve, not in the one it was seeded in.
