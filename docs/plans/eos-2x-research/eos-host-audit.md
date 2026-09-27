# EOS host audit — how nexus actually wires and feeds EOS 1.2.2

Audited 2026-09-26 (15:04–15:21 +03), read-only, against
`<host>` (branch `mac`, HEAD `1e7b08a`),
the 20 services under `../microservices/`, the installed runtime
`~/.local/share/eos-nexus/share/eos/1.2.2`, and the state dir `~/.local/state/eos`.
Paths below are relative to nexus unless absolute. `N tok` = tokens at the
source's own conversion (context-budget uses chars ÷ 3.5, note_inject ÷ 2.7,
EOS telemetry ÷ 4).

Raw outputs captured beside this file: `doctor-memory-*.txt`, `route-stats.txt`,
`brief-*.txt`, `audit-*.txt`, `note-eval.txt`, `note-searches.txt`, `cb-*.txt`,
`cost.txt`, `run-list.txt`, `work-list.txt`, `procedure-list.txt`,
`snapshot-before.txt`, `snapshot-after.txt`.

---

## 0. Headline numbers

| What | Number |
|---|---|
| Notes (all 22 stores, one shared tree `.devin/knowledge/`) | 842 files, 841 parseable, 2.05 MB (~586k tok @3.5) |
| …by kind | finding 809 (96%), procedure 15, defect 10, lesson 4, decision 3 |
| …by provenance | generated/moved 591 (70% of notes, 73.5% of bytes); organic session-stamped 99 (16.6% of bytes, 34 sessions) |
| Stale per `eos note audit` | 257 = 60 hand-written (nexus 36, services 24) + 197 generated (195 of 206 api-inventory = 95%) |
| Execution ledger (nexus only; no service has one) | 2,028 lines / 592 KB: 51 runs, 1,929 events, 45 ok / 3 failed / 3 open, 9 lessons, 0 runs linked to a work item |
| Work ledger | 32 events, 14 items: 13 in flight (8 stale >24 h), 1 done ever |
| Routing | 67 decisions (sonnet 60) vs real main-session usage overwhelmingly opus-5-5 (2,058 of 2,816 msgs) — advisory only, nothing applies it |
| `eos doctor --memory` | nexus 21/21; svc-cpq-ordercapture 7/21 |
| Index `.eos/data/eos.db` | 6.2 MB, 28 tables + FTS5 (334 rows); service index 55.9 MB |
| Service `.eos/` total | 1.04 GB over 20 services; `graphify-out/` 6.0 GB |
| Wrappers that capture events | 20 of 73 `automation/*.sh`; 19 of the 26 in CLAUDE.md's wrapper table |
| Always-loaded instructions (Claude, atlas) | 46,964 chars / 13,418 tok (target 16,000) |
| Hook injection per session (134 transcripts) | note_inject median 1,863 / p95 7,621 tok; prompt hook median 2,100 / p95 6,085; SessionStart median 657 |
| Sessions that asked EOS on purpose | 54 of 134 transcripts (40%); telemetry: 15 of 62 real sessions (24%) beyond brief/scan/route |
| Retrieval golden sets (119 queries, run today) | recall@1 0.67–1.0, recall@3 0.875–1.0; nexus.tsv r@1 fell 0.94 → 0.82 → 0.67 within 2026-09-24 as procedures and moved sections joined the store, and is still 0.667 today |

---

## 1. Hooks — what runs, what it injects, how it exits

Registered in `.claude/settings.json` (there is no `.claude/hooks/` dir in nexus;
all scripts live in `automation/hooks/`). `settings.local.json` adds no hooks
(keys: `permissions`, `agent`).

| Event (settings.json lines) | Script | Timeout | Exit rule |
|---|---|---|---|
| PostToolBatch `Read\|Edit\|Write` (4-15) | `automation/hooks/note-inject.sh` → `note_inject.py` | 1 s | always 0; prints `additionalContext` JSON or nothing (note-inject.sh:8-12, note_inject.py:685-713) |
| Stop #1 (16-25) | `note-gate.sh` → `note_gate.py` | 10 s | 0 = pass, 2 = block (stderr shown to model), 3 = "wrong interpreter, try another" (note_gate.py:29, 746-776; note-gate.sh:115-162) |
| Stop #2 (26-34) | `eos-usage.py` | 5 s | always 0; detached `eos route --usage-from` (eos-usage.py:20-40) |
| PreToolUse `Bash` (36-47) | `wrapper_guard.py` | 5 s | 2 = block with wrapper name; 0 otherwise; any internal error → 0 (wrapper_guard.py:122-159) |
| SessionStart (48-58) | `eos-brief.py` | 20 s | always 0, never stderr (eos-brief.py:11-13) |
| UserPromptSubmit (59-69) | `eos-prompt.py` | 10 s | always 0, never stderr (eos-prompt.py:13) |

Devin (`.devin/hooks.v1.json`) registers only `PreToolUse → wrapper_guard.py`;
Devin gets the brief by instruction (`.devin/rules/global_rules.md:17`,
`.devin/agents/atlas.md:41`: "Devin has no hooks, so the first command of a task
is `eos brief . --task`").

### 1.1 SessionStart — `eos-brief.py`
- Runs `eos brief <nexus> --agent claude --session <id>` (25-38), then each
  service whose shared store holds `executions.jsonl`/`work.jsonl` and whose brief
  has `RUNS OPEN (` or `IN FLIGHT (n>0)` — max 5 (22, 41-61, 118-124, 149-156).
  Adds up to three one-liners: MEMORY.md over budget (64-80), **"EOS NOTES NOT
  SAVED"** when `.devin/knowledge` has uncommitted files (83-97), watcher running
  older EOS than `tools/eos` (100-115).
- Today it would print the nexus brief — **3,064 chars / 23 lines**: IN FLIGHT 13
  (8 stale), "KNOWN HERE (0 of 236 notes match this branch)", RUNS OPEN 3,
  "ledger has commits that are not pushed" — plus the unsaved-notes line (2 files
  modified). No service qualifies: no service store has a ledger.
- Measured (`context-budget.sh --reads`, 134 transcripts): 23 sessions, 35 fires,
  median 657 tok, p95 871, total 15,323.
- Why it exists here: the engine installs its own `.claude/hooks/eos-brief.py`
  per service — present in 13 services — and it "had run in none, because
  sessions in this workspace start from `nexus/`" (eos-brief.py:4-7;
  `docs/eos-plans/operational-memory-host.md:14-16`: "had run 3 times in all of
  history, all in one session").

### 1.2 UserPromptSubmit — `eos-prompt.py`
- Ignores harness notices (`<task-notification>`, `<system-reminder>` …, 196-202).
- `eos brief <p> --task <prompt> --task-only --agent claude` for nexus plus up to
  3 services the prompt names by directory name or a ≥6-char tail such as
  "ordercapture" (34, 205-233). Rewrites `eos note show .` to the service path
  (263-270). Total cap **6,000 chars** (37, 274-276).
- Per-session dedup by sha256 of each delivered block in
  `~/.local/state/eos/prompted/<session>.workspace` (141-147, 171-190) — 21 files,
  8,775 bytes today.
- **NOTES ELSEWHERE** (≤3 lines): scans *every* store's front matter per prompt
  for an issue key or a word present in ≤1% of notes (40-138). This exists
  because the index is per-project and a prompt like "1588 ne durumda" names no
  service (15-21).
- **SERVICE GUIDE**: points at the service's AGENTS.md imported as EOS notes
  (150-168).
- "The prompt is a query that is never written anywhere" (11-13) — but with
  `[model_routing] record_prompts = true` (config.toml:22-25) the task brief
  records a routing decision per task prompt (`core/brief.py:383-406` →
  `core/routing/trace.py:84-86` appends to `.eos/data/routing.jsonl`).
- Measured: 18 sessions, 120 fires, **median 2,100 tok/session, p95 6,085**,
  total 41,551 (09-24 baseline: 6 sessions, median 923, p95 4,222).

### 1.3 PostToolBatch — `note_inject.py` (the only automatic "push" of knowledge)
- Resolves touched paths to (service, relative) incl. worktrees and nexus itself
  (103-111, 141-174). Exact `scope` match → full body (**≤3 bodies per path**,
  hand-written ranked before bulk; `api-inventory`/`repo-topology` never get a
  body) (86, 273, 358-383, 528); everything else in that store → digest of
  **≤8 titles** + "… N more" (400, 408-418, 550-583). Stale mark when the file's
  sha256 differs from the note's `scope_hashes` (276-326).
- Cap **3,000 tok × 2.7 chars = 8,100 chars** per batch (66, 85, 508); instruction
  files (CLAUDE.md, SKILL.md, MEMORY.md, agent/rules dirs) are skipped (387-405).
- Dedup per session in `$TMPDIR/eos-note-inject/<sha256(session)>.json` (639-664).
- **Live in this audit** (a subagent session): 11 injections, ~28k chars (~8k
  tok) in total. Three hit the cap or were truncated (after reading `mvn.sh` +
  `scenario.sh`, `eos-query.sh`, `ensure-eos-mcp.sh`). The digest drained the
  nexus store 8 titles at a time ("… 188 more" → 139 → 83 → 59 → 43 → 19 → 3).
  This contradicts `docs/superpowers/specs/2026-09-07-agent-memory-loop-design.md:194`
  ("additionalContext is dropped in an isolated subagent context"), at least on
  this harness build.
- Measured: 52 sessions, 232 fires, **median 1,863 tok/session, p95 7,621**, total
  159,492; 91 body blocks = 83,343 tok. 61 of those bodies (67%, 57,110 tok) went
  to files the session edited later, the relevance proxy the plan uses. 09-24
  baseline: 43 sessions, median 1,501, p95 6,689. The W-02 target of **p95 ≤ 4,000**
  (`docs/eos-evals/context-reads-baseline.md:67`) is **not met and has moved the
  wrong way**.

### 1.4 Stop — `note_gate.py` (the only mechanism that makes notes get written)
- Blocks when the transcript shows Edit/Write/NotebookEdit/MultiEdit on
  `.java .ts .tsx .js .py .sql .xml .yaml .yml` under `microservices/<svc>/`
  (worktrees mapped back) **and** no note in that service's store carries
  `session: <this id>` **and** no skip was recorded (30-31, 87-168, 471-509).
- It also lists hand-written notes whose scoped files changed, with ≤5 s of
  `git log` enrichment and a ready `eos note amend` command (24, 536-637).
- `stop_hook_active` → never loops (478-479); any failure → do not block.
- Rationale (4-7): "dev-atlas has told agents to write one since day one … and
  that produced 9 notes on 2 calendar days. A Stop hook exiting 2 does."
- note-gate.sh probes interpreters because the launchd PATH gave macOS's 3.9 and
  silently disabled the gate: "17 blocking on a normal PATH, 0 on
  PATH=/usr/bin:/bin" over 48 transcripts (note-gate.sh:8-17).
- `eos note skip` writes `.skips.jsonl` (gitignored, `core/notes.py:438-463`).
  **Zero `.skips.jsonl` files exist** in any store today.

### 1.5 PreToolUse(Bash) — `wrapper_guard.py`
- 9 regex rules plus git read verbs inside FM repos. It refuses: psql/mongo,
  kubectl, mvn/mvnw, javap/`jar t`, reading wrapper caches, cat/grep/sed on
  `automation/*.sh|py`, `grep -r`, `rg`, and curl to wrapped hosts (27-107).
- Escape hatch `# guard:allow <reason>` is logged to
  `logs/wrapper-guard-overrides.log` (110-119, 138-141): **264 overrides**
  09-22 → 09-26 (20/82/80/72/10 per day). The top keywords in the reasons are
  "read" 62, "cat" 17 and "rg" 12, so agents bypass the guard about 70×/day.
- Note: `eos-query.sh notes` itself uses `rg` (eos-query.sh:240-244) while agents
  are forbidden it.

### 1.6 Stop — `eos-usage.py`
Folds the transcript into one line per session of per-model message and token
totals (no content) in `.eos/data/routing-usage.jsonl` (docstring 1-11).

### 1.7 Not wired in nexus
- **Engine `eos-close.py`** (Stop: asks a session to close work it claimed; engine
  1.2.0 file in 13 services, `microservices/svc-cpq-ordercapture/.claude/hooks/eos-close.py:1-35`).
  Nexus has no equivalent, and 8 of its 13 in-flight items are stale.
- **Engine `eos-route.py`** (PreToolUse on the subagent tool, sets a model for
  untyped subagents) is installed only with `[model_routing] hook = true`
  (`tools/eos/README.md:690-698`, `core/ai/writer.py:151`). nexus config lacks it.
- An unexplained `PostToolUse:additional_context(PostToolUse:Edit)` appears in 8
  transcripts (last 09-23); its source was never measured
  (context-reads-baseline.md:56-57).

---

## 2. Capture — how wrappers feed the execution ledger

- **Shared helper:** `automation/lib/eos-capture.sh`. A wrapper sources it and
  calls `eos_capture_on_exit "$@"` right after `set -euo pipefail` (e.g.
  `automation/mvn.sh:40-44`). That sets an EXIT trap that calls
  `tools/eos/bin/eos-event` (or `eos-event` on PATH) (eos-capture.sh:34-84). A
  wrapper with its own EXIT trap calls `eos_capture_emit` from it
  (`scenario.sh:87`).
- **Fields written** (eos-event:67-69):

  | Field | Value |
  |---|---|
  | `type` | `"event"` |
  | `kind` | `ran` for mvn/apitest/gitx, else `called` (eos-capture.sh:27-32); `eos-event` accepts ran/read/changed/called/verified/noted/decided |
  | `tool` | script basename |
  | `target` | the first arg matching `env[0-9]+`, local, prod, production or staging (54-57) |
  | `ref` | the first two non-flag operands, ≤120 chars, never SQL/JQL/body (61-64) |
  | `exit_code`, `ms` | the wrapper's own exit code and elapsed time |
  | `session`, `execution` | as resolved below |
  | `ord`, `body` | null |

  `ms` comes from bash `$SECONDS`, so **resolution is 1 s**: all 1,887 recorded
  `ms` values are multiples of 1000, and 610 are 0.
- **Finding the current execution** (eos-event:12-15, 40-51): first the env
  `EOS_EXECUTION` + `EOS_EXECUTION_LEDGER` if exported. Otherwise the pointer
  file `${EOS_STATE_DIR:-~/.local/state/eos}/current/<session>`, whose content is
  `<execution>\t<ledger path>`, with session = `EOS_SESSION` else
  `CLAUDE_CODE_SESSION_ID`. `eos run start` writes the pointer.
  `EOS_STATE_DIR` is unset here. The dir holds 2 pointers:
  1. This session's: `x-architecture-research-eos-42f5` → nexus ledger.
  2. `s-1`: points into a **pytest tmp dir**, a test that leaked into the real
     state dir.
- **Subagent attribution:** a subagent's shell inherits the parent's
  `CLAUDE_CODE_SESSION_ID` (verified in this audit), so every wrapper call from
  any subagent lands on the parent's open run. Runs carry no agent identity:
  `agent` is null on 50 of 51 starts.
- **Silent when no run is open** (eos-event:43-51, eos-capture.sh:19-21). Wrapper
  calls outside a bracketed run leave nothing, and their number cannot be measured.
- **Coverage:**
  - All automation scripts: 20 of 73 capture (apitest, argo, bb, bssapi,
    cache-clear, confluence, db, eca, gitx, jenkins, jinspect, jira, kafka, mvn,
    ntf, obs, pre-pr-check, redis, s3, scenario).
  - The 26 wrappers in CLAUDE.md's table: 19 capture. Not capturing:
    `generate-config-ids`, `check-changeset-refs`, `db-grant-check`,
    `release-manifest`, `release-run`, `search`, `context-budget` (and
    `eos-query`).
- **What the ledger actually holds** (1,929 events):
  - By tool: bb 761, db 303, bssapi 295, confluence 95, eca 94, argo 81, gitx 69,
    jira 60, route 39, jinspect 38, obs 34, jenkins 26, mvn 12, scenario 11,
    cache-clear 8.
  - **CI polling dominates:** `bb checks` 317 + `bb pr` 187 + `bb refresh` 158 =
    662 events (34%).
  - By kind: called 1,806, ran 81, decided 39 (routing), changed 2, verified 1.
  - By exit code: 0 1,665, 1 174, 2 41. By target: env0 578, env1 127, env2 2,
    none 1,222.

---

## 3. Knowledge store

- **Config:** nexus `[knowledge] dir = ".devin/knowledge/nexus"` (`.eos/config.toml:2-3`).
  Every service config says `dir = "../../nexus/.devin/knowledge/<service>"`
  (20/20). Rationale:
  - The service `.eos/` is gitignored, and FM repos need a PR per note.
  - So notes live in nexus on a direct-push branch (decision K4,
    `docs/superpowers/specs/2026-08-20-eos-knowledge-loop-design.md:89-91`;
    `.devin/knowledge/README.md`).
- **Size:** 22 store dirs, 842 `.md`, 4.6 MB on disk (du), 2,053,383 bytes of
  notes: ≈513k tok @4, ≈586k @3.5, ≈760k @2.7 (note_inject's measured floor).
  - One file has no front matter and is invisible to EOS:
    `svc-crm-mash-up/20260915-v4-1-0-tag-upgrade-and-the-three-product-defects-it-surfaced.md`.
- **By store:**

  | Store | Notes |
  |---|---:|
  | nexus | 236 |
  | svc-domainconfigserver | 143 |
  | svc-cpq-ordercapture | 118 |
  | svc-pcm-product-catalog | 51 |
  | svc-crm-customerinformation | 50 |
  | svc-rim | 29 |
  | svc-crm-activity | 23 |
  | svc-crm-batch | 22 |
  | svc-crm-mash-up | 20 |
  | … | … |
  | svc-cpq-ntf-integrator | 4 |

- **Kinds vs `KINDS`** (`tools/eos/core/notes.py:89` = defect, finding,
  procedure, lesson, decision). All five exist, but finding is 809 (96.2%):
  procedure 15, defect 10, lesson 4, decision 3. Service stores hold only
  findings and 9 defects plus 1 decision. Procedures and lessons live only in
  nexus. "journey-map" is a `source`, not a kind (notes.py:240-260).
- **Provenance** (by `source` / `session`):

  | Class | Notes | Bytes | Share of bytes |
  |---|---:|---:|---:|
  | Generated or moved verbatim | 591 | 1,505,770 | 73.5% |
  | …api-inventory | 206 | 747,155 | |
  | …agents-md (service AGENTS.md sections) | 182 | 208,065 | |
  | …context-budget (sections moved out of CLAUDE.md/skills) | 106 | 307,817 | |
  | …journey-map | 56 | 108,766 | |
  | …H-05 (dev-nexus sections) | 31 | 115,246 | |
  | …context-budget-memory 7, repo-topology 3 | 10 | 18,721 | |
  | Migrated docs/memory (`source: docs/…`) | 57 | 51,515 | 2.5% |
  | Hand-written, no `session` | 94 | 152,415 | 7.4% |
  | **Organic, session-stamped** (the gate's product) | **99** | 339,762 | 16.6% |

  - The organic notes come from 34 distinct sessions: 3 on 08-20, then 2–11/day,
    27 on 09-25 and 8 on 09-26.
  - By store: nexus 49, svc-cpq-ordercapture 31, customerinformation 5, the rest ≤4.
- **Front matter actually used** (of 841):

  | Field | Notes |
  |---|---:|
  | title, kind, created | 841 |
  | tags | 806 (4,418 tags in total) |
  | source | 777 |
  | session | 428 |
  | scope | 364 |
  | scope_hashes | 362 |
  | updated | 48 |
  | procedure | 19 |
  | runs_ok, runs_failed | 15 |
  | last_execution, last_verified | 4 |
  | execution | 3 |

  No `status`, `supersedes` or `commit` field exists. The 09-07 spec proposed
  the last two (§3.2); only `updated` shipped.
- **Scope hashes:** 364 notes (43%) are scoped: 624 entries, 585 hashed, 34
  `null` (the file did not exist in that service, e.g. journey fan-out),
  **0 `@parent:`** entries (note_inject.py:54-57 records the same).
- **Duplicates by design:** 20 identical-body groups (≥200 chars) cover 67
  notes; the largest has 12 copies. This is journey fan-out: a journey crosses
  services and stores are per-project, so the generator writes one copy per
  service (open item E2, closed 09-23 as "not a defect").
- **Staleness (`eos note audit`, run on all 21 stores):**
  - nexus: **36 hand-written** notes (15 H-05 sections, 6 context-budget
    sections, 15 others; 10 of them scoped to `automation/bb.sh`).
  - Services: 24 hand-written + **197 generated** (195 api-inventory, 1
    journey-map, 1 repo-topology). 6 service stores are clean.
  - **Total 257 stale.** Most come from host scripts churning under the notes
    that describe them, and from the parent upgrade under the API inventory.
  - The context-budget report had already counted 26 stale nexus notes before
    its work began (`docs/eos-evals/context-budget-report.md:112`).
- **Java coverage:** 102 of 2,402 `src/main` Java files are scoped by some note
  (**4.2%**), against the 09-07 spec target of ≥15% (baseline 3.6%)
  (`specs/2026-09-07-agent-memory-loop-design.md:243`).
- **The store's own README is stale:**
  - `.devin/knowledge/README.md:41` lists "Two kinds".
  - Line 74 says `eos note search` "scores title and tags only". Now it is IDF
    coverage 0.5 title / 0.3 tags / 0.2 body.
  - Line 78 says `eos-query.sh notes` "requires every word". Now it ORs and
    ranks (eos-query.sh:203-229).
- **Churn:** 122 of 311 nexus commits since 09-19 touch `.devin/knowledge`, and
  38 touch `tools/eos` (32 subtree pulls; EOS went 0.11.0 → 1.2.2 in 7 days).

---

## 4. Ledgers and the operational-memory matrix

| File | Size | Content |
|---|---|---|
| `.devin/knowledge/nexus/executions.jsonl` | 2,028 lines, 592,328 B | 51 `start`, 48 `finish`, 1,929 `event` (details below) |
| `.devin/knowledge/nexus/work.jsonl` | 32 lines, 15,476 B | open 14, block 9, log 5, claim 2, unblock 1, done 1 → 13 in flight, 8 stale |
| `.eos/data/routing.jsonl` | 67 lines, 43,138 B | 09-25..26, 9 sessions; see §9 |
| `.eos/data/routing-usage.jsonl` | 15 → 16 lines | per-session model totals from the Stop hook |
| `.eos/data/telemetry.jsonl` | 5,049 → 5,073 lines, 1.1 MB | one line per eos CLI call |
| `verifications.jsonl` | nexus 1, svc-cpq-ordercapture 2, svc-crm-customerinformation 1 | — |

Execution ledger in detail:
- **Span:** 2026-09-24 06:46Z → 09-26 12:07Z (25 / 1,706 / 246 records per day),
  8 distinct sessions.
- **Outcomes:** ok 45, failed 3; 3 still open, including this audit's parent run
  "Architecture research: EOS vs RAGFlow vs Ruflo (EOS 2.x)".
- **Lessons:** 9 finishes carry one. All 3 failed runs have one (required), and 6
  ok runs do. Lengths run 165–1,315 chars.
- **Procedure on start:** none 30, run-a-scenario 9, deliver-config-sql-script 8,
  open-a-pr-on-an-fm-service 2, commit-and-push 1, plus `normal_implementation` 1
  (a routing task type used as a slug).
- **Linking:** `work_item` is set on **0 of 51** runs, so the work and execution
  ledgers never join. `target`: env1 10, env0 1, nexus 1, null 39.
- **Events per run:** median 20, max 327; 50 of 51 runs have ≥1 event.
- **Beyond nexus:** no `executions.jsonl` or `work.jsonl` exists in any service store.

- **`eos work list .`**: 13 items in flight, 8 stale.
  - 7 are "blocked on: digital sales channel" (PROJ2-1232/1234/1235/1238/1239/1268/1269).
  - 1 is `active` and claimed 24 h ago (PROJ2-1988), stale.
  - The ledger has unpushed commits.
- **`eos procedure list .`**: 15 procedures, of which 4 have ever run:
  - deliver-config-sql-script 7 ok / 1 failed
  - run-a-scenario 7 / 2, "failing"
  - open-a-pr 2 / 0
  - commit-and-push 1 / 0
  - The other 11 are "never / unverified".
- **`eos route . --stats`**: "39 of 67 recorded; model usage for 15 session(s)".
  - normal_implementation MEDIUM sonnet/medium ok 16
  - test_generation ok 5, failed 1, open 1
  - planning ok 6
  - simple_implementation haiku/low ok 3, open 1
  - code_review ok 3
  - architecture HIGH sonnet/high open 1
  - debugging ok 1
  - investigation ok 1
- **`eos cost .`**: 5,055 calls since 09-19, ~1.26M tok returned (brief 2,175
  calls, median 287 tok; scan 2,126; note 312; route 170; run 115). It reports
  "141 sessions identified; 20 (14%) called EOS beyond the opening brief".
  - **That denominator mixes in eval traffic.** Telemetry splits into 1,059 calls
    from 62 real (UUID) sessions, 1,847 calls from 79 `eval-arrival-*` style
    sessions, and 2,167 session-less calls (mostly watcher scans).
  - Real sessions going beyond brief/scan/route: **15 of 62 (24%)**.
  - Transcripts show 54 of 134 (40%) asked EOS on purpose. The gap exists
    because `eos-query.sh notes|parent` bypasses the eos CLI and therefore
    telemetry.

### `eos doctor --memory`
- **nexus: 21 of 21 satisfied.**
  - C-01: 34 tables. C-02: 236 notes. C-03: 15 procedures.
  - C-04: 51 executions, 50 with events. C-05: 1,929 events. C-11/C-20: brief
    `--task` ~1,391 tok (budget 1,500).
  - C-12: 16 tools. C-13: 1 execution with changed paths. C-14: 2 decisions.
  - C-15: 3 lessons linked. C-16: 1 verification. C-17: 4 procedures with
    counters. C-19: 7 shared session ids.
  - C-10 is DECIDED: "no vector retrieval by ADR-001/ADR-010".
- **svc-cpq-ordercapture: 7 of 21.**
  - C-01, C-02 (118 notes), C-07, C-09 (2 rows), C-10, C-18 and C-21 pass.
  - Every episodic, procedural, decision and lesson row is PARTIAL: "ledger
    present, 0 executions".
  - The 1.0.0 audit read 6/21. The seventh row is C-09, which now counts 2
    verification rows.

---

## 5. Index, extensions, and Graphify

### 5.1 `.eos/data/eos.db` (nexus)
- **Basics:** 6,164,480 B, `schema_version 4`, engine 1.2.2, rebuilt
  2026-09-26T12:07:56Z (the same second the parent run started). `with_parents 0`.
  Plus an orphan 172 KB `eos.db.*.tmp` from 09-15.
- **Tables** (28 + FTS5 `search` + 5 shadow tables):

  | Table | Rows |
  |---|---:|
  | brain_doc | 5 |
  | build_issue | 0 |
  | catalogue_procedure | 27 |
  | coverage | 11 |
  | edge | 548 |
  | execution | 51 |
  | execution_event | 1,929 |
  | fact | 675 |
  | flow_step | 0 |
  | git_commit | 1,109 |
  | git_commit_file | 5,212 |
  | git_commit_ticket | 95 |
  | journey_doc | 0 |
  | journey_snapshot | 1 |
  | journey_step | 0 |
  | meta | 22 |
  | node | 279 |
  | node_symbol | 4,338 |
  | note | 236 |
  | note_scope | 141 |
  | note_tag | 1,666 |
  | procedure_state | 33 |
  | procedure_step | 184 |
  | scan_exclusion | 51 |
  | **search (FTS5)** | **334**: note 236, execution 51, procedure 27, work 14, brain 5, verification 1 |
  | verification | 1 |
  | work_event | 32 |
  | work_holder | 1 |
  | work_item | 14 |

- **Extensions** (`[index] extensions`, config.toml:8):
  - `journeys.py` (743 lines) adds journey_doc, journey_step, flow_step and
    journey_snapshot. **All are empty in nexus**, because the journey documents
    were moved into notes (W-04). `docs/journeys/*.md` are now 1.6–2.4 KB index
    stubs.
  - `procedures.py` (167 lines) adds catalogue_procedure 27, procedure_step 184
    and procedure_state 33, from `../logs/scenarios/catalogue.json` (config.toml:16-17).
- **Service index (svc-cpq-ordercapture):**
  - 55.9 MB, 31 objects, `with_parents 1`, journeys extension only.
  - Rows: fact 48,357, edge 31,693, node 3,782, node_symbol 17,826,
    git_commit 1,605, git_commit_ticket 1,526, journey_step 465, flow_step 82,
    journey_doc 3, note 117, execution 0.
  - Top predicates: import-edge 17,357, calls-edge 8,492, annotation 7,616,
    new-edge 5,386, field-edge 4,707, names-code 1,734.
- **One store per index.** No cross-store index exists, which is why
  `eos-prompt.py` re-parses all ~842 note files on each prompt for
  NOTES ELSEWHERE.
- **Three independent retrieval implementations:**
  1. `automation/eos-query.sh notes`: rg/grep, then awk IDF with the title ×3,
     api-inventory suppressed (eos-query.sh:160-309). It is the most used, at 159
     calls in 134 transcripts.
  2. `notes.search_notes`: IDF-weighted coverage 0.5 title / 0.3 tags / 0.2 body.
     Used by `eos note search/eval`, brief and inject ranking.
  3. `eos query --search`: FTS5 BM25, OR-joined.

  Golden sets measure only #2. #1 was measured once (H-05: r@1 0.73 / @3 0.91 /
  @5 0.97, `operational-memory-host.md:25`).

### 5.2 Graphify
- **`graphify-out/` is 6.0 GB:**
  - projects 4.5 GB, cache 624 MB, parents 578 MB
  - four dated snapshots of 47–61 MB
  - `graph.json` 113 MB, `GRAPH_REPORT.md` 487 KB
  - 19 per-project graphs under `projects/by-name/`, **last written 2026-09-19**,
    a week stale.
  - Example: svc-cpq-ordercapture has 26,291 nodes / 95,489 edges (82 MB), against
    EOS's 3,782 nodes / 31,693 edges for the same service.
- **Channels:**
  - `.mcp.json` now holds only 2 servers, `graphify` and `graphify-project`.
  - `graphify` is disabled for Claude (`settings.json:97-99`); `graphify-project`
    is enabled.
  - The CLI `graphify affected` is the documented blast-radius tool (CLAUDE.md:97-114;
    project-intelligence decision table, SKILL.md:36-37).
- **Refresh:** the live watcher is PID 2171, `python -m ui.cli watch --graphify`,
  up 4.5 h, running EOS 1.2.2. It rescans EOS on file changes and also drives
  Graphify. Launched by `~/Library/LaunchAgents/com.example.nexus.eos-watcher.plist`.
- **Audit document** (`docs/eos-graphify-audit.md`, 09-18):
  - "They are complements. The cheap EOS answer to a structural question is the
    expensive mistake" (102-103).
  - Division of labour: EOS answers identity/content and "what did we learn";
    Graphify answers "what reaches this / what breaks" (93-100).
  - `eos impact` saw import edges only then. Since 0.14.0 it follows
    implements/extends/field/new/calls, with 339/345 recall (injected dev-nexus
    note). **The overlap is growing.**
  - Past failure: a scan feedback loop of 10,373 scans, 10,269 for nexus, because
    `graphify-out` sat inside a watched root (58-63).
- **Overlap in practice:**
  - Both build a per-service code graph, and both are regenerated by the same
    watcher.
  - Only EOS holds memory. Graphify's own `memory/` holds one query file from 09-10.
  - Graphify's footprint is ~6× EOS's service `.eos` total (1.04 GB).

---

## 6. Services

- **Config:** 20 of 20 `../microservices/*` have `.eos/config.toml`, every one
  pointing at `../../nexus/.devin/knowledge/<service>`.
  - Each has 1 extension (journeys), `[telemetry] enabled`, and no
    `[model_routing]`.
  - Parent links are `[links.parent]`/`[links.common]`. Example:
    svc-cpq-ordercapture → `base-cpq-ordercapture` @ `v4.1.16-fm` + `base-common`.
- **Version drift:** the vendored runtime `.eos/runtime/VERSION` is **1.2.0 in
  all 20** while the engine is 1.2.2.
- **Engine-generated Claude files:** 13 of 20 carry `.claude/settings.json`, the
  hook trio (eos-brief/eos-prompt/eos-close), `agents/eos-researcher.md`
  (2,512 chars) and `skills/eos/SKILL.md` (11,903 chars, "EOS (engine 1.2.0)").
  None of these fire, because sessions start in nexus.
- **Size:** `.eos` is 1.04 GB across 20 services. The largest is
  svc-cpq-ordercapture at 217 MB: data/cache 89 MB, brain 59 MB, eos.db 54 MB,
  graphs 12 MB, plus a stale 1.4 MB `.eos/eos.db` from 08-19.
- **`eos brief ../microservices/svc-cpq-ordercapture`:**
  - Without a task: **371 chars**, 7 lines. "IN FLIGHT (0)", and "KNOWN HERE
    (1 of 118 notes match this branch)" for `feature/PROJ2-1905`.
  - With `--task "PROJ2-1588 suspend order fails at submit in ordercapture"`:
    **1,069 chars**, 17 lines. "PROCEDURE — none recorded for this task", 3
    related notes, a "Record this run" hint, then the branch brief.
- **No service has any run**, so services never appear in SessionStart and sit
  at 7/21 in `doctor --memory`.

---

## 7. Instruction budget (`automation/context-budget.sh`, read-only modes)

- **Default / `--check`** (exit 0, nothing over target). Claude atlas,
  unconditional:

  | File | Chars | Tokens | Target |
  |---|---:|---:|---:|
  | ~/.claude/CLAUDE.md | 1,408 | 402 | 405 |
  | CLAUDE.md | 9,527 | 2,722 | 3,500 |
  | .claude/agents/atlas.md | 3,891 | 1,112 | 1,300 |
  | skill:project-intelligence | 7,732 | 2,209 | 2,300 |
  | skill:dev-atlas | 11,971 | 3,420 | 4,500 |
  | skill:dev-nexus | 9,788 | 2,797 | 3,000 |
  | MEMORY.md | 2,647 | 756 | 1,000 |
  | **Total** | **46,964** | **13,418** | **16,000** |

  - Devin atlas: unconditional 4,674 tok; 13,100 with the three skills.
  - A Devin session started inside a service loads its AGENTS.md: max 9,275 tok
    (svc-domainconfigserver), median 1,452.
  - Not counted: the hooks' injections and the harness itself. The plan puts the
    harness at ~26k tok, citing "a haiku atlas subagent that called no tool
    consumed 64,307 tokens" (`docs/eos-plans/context-budget.md:30-32`).
- **`--lint-memory`:** MEMORY.md 756/1,000 tok, 0 findings.
- **`--rules check`: exit 1.** 126 candidate sentences, 341 ledger rows, 159
  rules. **2 are uncovered:** `CLAUDE.md:135` (the ECA wrapper row) and
  `MEMORY.md:20` (script upload as the user). The rule ledger has drifted since
  G-03 reported 0.
- **`--reads`** (134 transcripts; 09-24 baseline in brackets):

  | Item | Today | 09-24 baseline |
  |---|---|---|
  | Instruction files: nexus CLAUDE.md | median 3,359 tok across transcripts (historical sizes; 2,722 on disk today) | — |
  | Instruction files: MEMORY.md | median 2,432 tok (756 on disk today) | — |
  | Instruction files: ~/.claude/CLAUDE.md | 402 tok | — |
  | Hook: PostToolBatch | 52 sessions, median 1,863 / p95 7,621 / total 159,492 | 43, 1,501 / 6,689 |
  | Hook: UserPromptSubmit | 18 sessions, 2,100 / 6,085 / 41,551 | 6, 923 / 4,222 |
  | Hook: SessionStart eos-brief | 23 sessions, 657 / 871 | 9, 63 / 125 |
  | note_inject bodies | 91 blocks, 83,343 tok; 67% on files edited later | — |
  | Most injected path | `pom.xml` 11×, 9,455 tok | — |
  | Markdown read on demand: skill pages | 77 reads, 150,267 tok (dev-nexus SKILL.md 42,645; dev-atlas 36,947) | — |
  | Markdown read on demand: EOS notes | 42 reads, 31,448 tok | — |
  | EOS asked on purpose | **54 of 134 sessions** | 45 of 111 (41%) |
  | …`eos-query.sh notes` | 159 calls, 73,624 tok | 110 calls |
  | …`eos-query.sh parent` | 54 calls, 13,165 tok | — |
  | **Harness "changed on disk" notices** | 45 sessions, median 10,719 tok, **779,053 total** — top: CLAUDE.md 27,397, MEMORY.md 26,436, bootstrap.sh 20,465, dev-atlas SKILL.md 17,230 | 716,507 total |
  | Skills invoked | dev-nexus 14, dev-atlas 7, superpowers:brainstorming 5, project-intelligence 4, … | — |

  The "changed on disk" notices are the **single largest context cost** in the
  workspace, larger than all hooks combined.
- **`--arrival` was not run.** It drives `eos brief <nexus> --task` for 54
  prompts. With `record_prompts = true` that appends routing decisions to nexus
  `.eos/data/routing.jsonl` (`core/brief.py:383-406` → `core/routing/trace.py:84-86`),
  which is a write. Documented result: **54/54**, with procedure 33/33, pointer
  5/5, loaded 5/5 and noise 11/11 (`docs/eos-evals/context-budget-report.md:50`).
  The baseline was 26/53 (`context-budget-baseline.md:29`).
- **Plan findings** (`docs/eos-plans/context-budget.md`,
  `docs/eos-evals/context-budget-report.md`):
  - Unconditional load went 38,114 → 13,274 tok (−65%). The live atlas subagent
    cost 86–91k → 66–68k tok per answer, about 22k saved per session
    (report:11-13, 26-28).
  - Earlier, dev-nexus SKILL.md was cut from 285,912 chars (81,689 tok) to
    10,193. The pre-work atlas session loaded **113,493 tok** unconditionally
    (`operational-memory-host.md:10-13, 25`).
  - Method: rules stay loaded; how-to, facts and reference move into EOS notes,
    delivered by hooks. A rule may leave the loaded set only if a hook refuses the
    violation or an arrival test proves the prompt hook delivers it
    (context-budget.md:79-103).
  - `docs/eos-plans/context-budget-map.tsv` maps 121 moved sections
    (source → rev/sha256 → note). The verbatim check A-06 passed 77/77.
  - The plan names its own biggest risk: only 41% of sessions ask EOS at all.
    "A section moved to class R that is not searched is lost unread"
    (context-budget.md:618-628). Today that figure is 40%.
  - Still open: W-06, the changed-file notices.

---

## 8. Evals

### 8.1 Golden sets (`docs/eos-evals/golden/`)
Eight files: 7 retrieval sets totalling **119 queries**, plus `arrival.tsv` with
54 prompts. Re-run today with `eos note eval`, which is read-only
(`core/retrieval.py:86-120`):

| Set (queries) | r@1 | r@3 | r@5 | MRR | Earlier |
|---|---:|---:|---:|---:|---|
| nexus (33) | **0.667** | 0.970 | 1.0 | 0.816 | all on 09-24: 0.94 / 1.00 at H-05 → 0.818 / 0.970 / 1.0 / 0.895 at B-03 → 0.67 at G-03 |
| nexus-procedures (16) | 0.6875 | 0.875 | **0.875** | 0.781 | B-03: 0.6875 / 0.875 / **0.9375** / 0.805 → r@5 −0.06 today |
| claude-md (14) | 0.786 | 0.929 | 0.929 | 0.857 | r@1 0.86 → 0.79 (report) |
| dev-atlas (19) | 0.789 | 1.0 | 1.0 | 0.895 | — |
| project-intelligence (8) | 0.75 | 1.0 | 1.0 | 0.854 | — |
| journeys (9) | 1.0 | 1.0 | 1.0 | 1.0 | r@1 1.0 (report) |
| svc-cpq-ordercapture (20) | 0.95 | 0.95 | 0.95 | 0.95 | **0.70 → 0.95** after IDF + title weight (note `20260923-note-retrieval-was-anded…`:29-30) |

- **What decays is recall@1 as the corpus grows:**
  - "why is my PR blocked…" now ranks a 09-26 plan note first.
  - "merge the pull request" has fallen to rank 6.
  - "get this fix onto env1" is not in the top 10.
  - "what happens to the earlier steps when one command fails" is not in the top
    10 for ordercapture, because of the vocabulary gap the retrieval note
    documents: no stemmer, "fails" versus "failure".
- r@3 holds, and it is the acceptance criterion (A-05).
- Retrieval is lexical only, by decision: ADR-001/ADR-010, C-10 "DECIDED".

### 8.2 where-did-we-leave-off
- **Baseline** (engine 0.39.0, `docs/eos-evals/where-did-we-leave-off-baseline.md`):
  - 23 commands; "the first useful one was the 13th".
  - ~20–21k chars read, none containing a procedure or a run.
  - Answered honestly "none recorded" for both halves (27-31).
- **Re-run** (0.46.0, `…-rerun.md:14-20`):
  - Answered both halves **before the first acting command**, from the hooks'
    context. It read 3,402 chars of hook output + 11,202 chars of skill document
    = 14,604 before any command, ~25k including follow-ups; the first useful
    command was the 3rd.
  - The procedure was `0 ok / 0 failed, unverified`: written from documents, not
    distilled from a real deploy.
  - Caveat: the session was *handed* the hook output as a captured file (line
    8-10), not given a live hook.

### 8.3 `operational-memory-audit-1.0.0.md` (independent re-audit)
- "21 of 21 rows `IMPLEMENTED`/`DECIDED` on `nexus`" (12), verified end to end
  on one real failure → lesson → procedure "Known failures" → next brief (16-19).
  Hook cost ~3,035 chars against a ~20,000-char baseline (20-21).
- **Caveats, verbatim in substance:**
  - "21/21 on `nexus` and **6/21** on `svc-cpq-ordercapture` and
    `svc-crm-customerinformation`; no `executions.jsonl` or `work.jsonl` exists
    outside nexus" (25-29).
  - "All ten executions were written by one session in a four-minute window"
    (30-32).
  - The scenario catalogue is not joined to the brief (33-37; since fixed).
  - "`work.jsonl` has never been written anywhere" (40-41).
  - pre-pr-check/jinspect didn't capture (42-43; fixed).
  - "C-20 passes at the floor (10 executions)" (44-45).
  - **"the honest matrix is not '21/21, done': it is 21/21 in the one project the
    work was seeded in, 6/21 in the projects the work is meant to serve"**
    (48-51).
- **Today:** nexus has 51 real runs over three days, so the seed caveat is weaker.
  The service caveat is unchanged (7/21, 0 runs).

### 8.4 `0.26.0.md` (first CLI eval, 4 fresh sessions on ordercapture)
- `eos rules` answered 403 behaviour codes at 17:27 and 70 at 17:31, with no
  code change. Cause: a plain `eos scan` silently dropped every parent fact
  (16-26; fixed 0.28.0).
- 3 of 4 sessions lost their first 2–4 commands to the `<project>`-is-a-path
  argument (41-48).
- The notes loop was unreachable from the CLI (MCP tool names in the skill)
  (50-57).
- `draft-test` emitted uncompilable code (78-85).
- All 6 open items were closed in 0.29–0.33 (177-216).

### 8.5 rules-hold
Baseline 9/9 and after 9/9. The scorer first reported 6/9 because of regex false
positives, which were hand-checked and narrowed (`context-budget-baseline.md:59, 73-80`).
Each answering subagent cost 86–91k tok before the budget work and 66–68k after.

---

## 9. Agents, skills, and task → agent/model routing

- **`.claude/agents/`** holds only `atlas.md`.
  - Front matter: `skills: [project-intelligence, dev-atlas, dev-nexus]`,
    **`effort: low`** (atlas.md:4-8).
  - INTAKE "starts with what EOS already knows … `eos brief . --task`", and runs
    are bracketed with `eos run start/finish` (44).
  - "EOS is not an MCP server, it is Bash" (19).
- **Default agent:** `settings.local.json` sets **`"agent": "atlas"`**, so every
  main session is atlas. CLAUDE.md:59-62 says "Work through the `atlas` agent".
- **Skills:** `~/.claude/skills/{atlas,dev-atlas,dev-nexus,graphify,project-intelligence}`
  are symlinks into `nexus/.devin/skills/*`, one source for two transports.
  EOS lines per file:

  | File | EOS lines | Key reference |
  |---|---:|---|
  | CLAUDE.md | 15 | the command block at 97-112 |
  | dev-atlas | 8 | "`eos brief . --task` is the first command" (14); step 8 "EOS refresh … the Stop hook asks" (28) |
  | dev-nexus | 9 | — |
  | project-intelligence | 7 | the EOS/Graphify decision table (36-37) |
  | global_rules (Devin) | 3 | — |
  | atlas skill | 1 | — |
  | graphify skill (62,255 chars) | 0 | — |

- **`eos-researcher` and the `eos` skill** exist only in the 13 service `.claude/`
  dirs (engine 1.2.0). Nexus has neither. They appear in sessions only through
  additional working directories.
- **Routing today is static configuration plus the human:**
  - The main model is chosen by the human. Folded usage for 16 sessions: main
    thread opus-5-5 2,058 msgs, opus-5 638, sonnet-5 81, fable-5-1 39.
  - The subagent model is pinned by env `CLAUDE_CODE_SUBAGENT_MODEL=sonnet`
    (settings.json:100-102). Subagents ran sonnet-5 for 2,496 msgs and opus-5-5
    for 597.
  - Agent choice is the atlas default. Nothing classifies a task to an agent.
- **EOS model routing** (`[model_routing] enabled, brief="with-brief",
  record_prompts=true`, config.toml:22-25):
  - Prints `ROUTE …` and `Apply: Task({model: "…"}) for subagents; /effort …`
    lines in the task brief (`core/routing/adapters.py:45-54`), explicitly
    **advisory**: "EOS never configures a provider" (adapters.py:3-9).
  - No `eos-route.py`, because `hook = true` is absent. The only PreToolUse hook
    is wrapper_guard.
  - Recorded 67 decisions: sonnet 60, haiku 4, opus 3; effort medium 58;
    confidence mostly 0.26–0.46; all `override_source: auto`, 0 reused.
  - 39 of those decisions are also ledger events (`kind: decided, tool: route`).
  - Net effect: the recommendations are recorded and not applied. The effort the
    harness reported (`effort_in_use`) was "high" in 27 of 67 decisions whose
    recommendation was mostly "medium".

---

## 10. What was tried before and abandoned (for "do not adopt")

| Tried | Outcome | Evidence |
|---|---|---|
| **Four parallel implementations** (July 2026): cortex (Py), eos (Py), yos (Rust), cos (Rust) | eos continued. cortex donated the write path; yos donated parent-linking; nothing was salvaged from cos | `docs/eos-salvage/README.md:3-10, 22-60`; `specs/2026-08-20…:37-50` |
| **Rich schemas without a CLI command** (cortex lessons/patterns/history/relationships) | Stayed empty across 5 snapshots; "Tooling decides what gets written; schemas do not" (D1) | salvage README:31; spec 08-20:54-58 |
| **Automatic git-diff mining** (cortex) | Produced duplicates and contradictions and needed `cortex_reconcile.py`; quality fell (D2) | spec 08-20:60-63, 290 |
| **Generator fills the "why" from git** | Refuted: 27/206 FM commits (13.1%) state a why; 8.7% excluding agent commits; ~1.2% in BASE parents; the commit message is a strict subset of the note | spec 09-07:19-40 |
| **Instruction-driven note writing** | "produced 9 notes on 2 calendar days" → replaced by the Stop gate (exit 2) | note_gate.py:4-7; spec 09-07:11 |
| **EOS as MCP** (18 servers, one per service) | Retired: 2,194 tok of roster per session (39% of the MCP surface). `get_graph` 306k–6.47M tok; `impact_analysis` 74% recall vs grep 100% at 1.66× cost; `compose` a flat ~1.2k surcharge; `get_context` false on 16/17 services. Re-registration rejected: "an MCP tool is still something the agent must decide to call — 4 in 25 sessions" | eos-query.sh:9-20; eos-graphify-audit.md:46-50; spec 09-07:231 |
| **`eos context` as the delivery channel** | Delivered 1 note out of 62 (ordered by file date) | eos-graphify-audit.md:40-44 |
| **Vector/embedding search** | Excluded: "Jaccard + tag is enough; embeddings bring network and dependencies and break the stdlib-only `install.sh`"; C-10 DECIDED (ADR-001/010) | spec 08-20:292; doctor C-10 |
| **Auto-writing notes** from the auditor | Rejected: 206 api notes are all re-derivable by grep; an auto-writer fills the 0.4–0.9 near-duplicate band | spec 09-07:220 |
| **Personal-memory rules moved into EOS notes** | Arrival test failed (A: no; B retrieval 3/3). "Rules are not searched, they are loaded." A scope cannot name a path outside its project, so sibling-repo rules cannot be EOS notes | eos-graphify-audit.md:162-190 |
| **Serena / Java LSP (JDT LS)** | Dropped 09-10: cannot follow FM overrides into BASE parents (jar `v4.0.6-fm` vs checkout `v1.0.0`); ≤3 GB heap per JVM; +9.6k tok at session start | note `20260910-serena-was-evaluated…` |
| **Per-service engine hooks** | Installed in 13 services, never fire (sessions start in nexus) → re-implemented as workspace hooks | eos-brief.py:4-7; operational-memory-host.md:14-16 |
| **Windsurf as a third platform** | Removed 09-18: 107 files, ~35k lines | eos-graphify-audit.md:137-141 |
| **Graphify MCP by default** | `graphify` disabled for Claude on context grounds; CLI `affected` preferred | settings.json:97-99; eos-graphify-audit.md:156-160 |
| **18 data MCP servers** (Jira, Bitbucket, Postgres/Mongo, fm-observability, bss-api, Kafka) | Replaced by digest-printing Bash wrappers with measured savings (e.g. `jira.sh search` ~605 tok vs raw ~40k/233k) | project-intelligence capability note; CLAUDE.md wrapper table |

Other plans in `docs/superpowers/`:
- **specs:** knowledge-loop 08-20, agent-memory-loop 09-07 ("Onay bekliyor",
  i.e. awaiting approval, but implemented), note-auditor-supersession 09-09,
  public-release 09-12.
- **plans:** 5 implementation plans of 1,210–1,577 lines each, for the same work.

**09-07 spec §8 success criteria**, 19 of the 30 days elapsed:

| Criterion | Target | Today |
|---|---|---|
| Real task notes | ≥1 per real work session | 99 organic notes from 34 sessions |
| `skip` rate | <50% | 0 skips recorded |
| Java coverage | ≥15% | **4.2%** |
| Injection on file touch | ≥20% of touches | proxy only: note_inject fired in 52 of 134 transcripts (per-touch rate not measured) |
| Injection cost per session | <3,000 tok | median 1,863 ✓, **p95 7,621 ✗** |
| Near-duplicates | 0 | 0 accidental (67 in fan-out groups) |

---

## 11. Pain points visible in the workspace

1. **Services have no episodic memory.**
   - 0 runs in any service; every run is recorded in nexus. The whole operational
     loop is proven in one project (the audit's own caveat 9).
   - The engine hooks that would start it in services never fire.
2. **Push beats pull, and pull is a minority habit.**
   - 40% of transcripts (24% of real telemetry sessions) ask EOS anything beyond
     the brief.
   - The plan's own "biggest risk" (context-budget.md:618-628) is still the
     biggest risk.
   - Knowledge arrives mostly through note_inject: 159k tok over 52 sessions,
     p95 7.6k per session, above its 4k target.
3. **The corpus is mostly not learned knowledge.**
   - 70% of notes are generated or moved sections.
   - Bulk api-inventory notes are 95% stale and suppressed from search by
     default. Keeping them costs audits and ranking work (`--api` flag,
     BULK_INDEX_SOURCES, injection demotion).
4. **Staleness is the dominant maintenance load.**
   - 257 notes are stale.
   - 15 of nexus's 36 stale hand-written notes are verbatim-moved dev-nexus
     sections, scoped to scripts that keep changing (bb.sh alone accounts for 10).
   - Every host-script edit falsifies documentation notes.
5. **Retrieval recall@1 decays with growth** (nexus 0.94 → 0.67 within
   09-24, as ~160 procedure and moved-section notes arrived; r@3 held at
   0.97–1.0).
   - New organic notes displace procedures, and there is no stemmer.
   - Three retrieval implementations differ in behaviour, and only one is
     measured.
6. **Ledgers do not join.**
   - 0 of 51 runs reference a work item; `agent` is null on 50 of 51.
   - Subagent events merge into the parent's run.
   - 34% of events are CI polling noise; durations have 1 s resolution.
7. **Routing is data collection without an effect.** 60/67 decisions recommend
   sonnet while the human runs opus.
8. **Telemetry is polluted.** 36% of calls come from eval sessions and 43% have
   no session, so `eos cost`'s 14% headline is not the real-session rate (24%).
9. **Version and copy drift.**
   - Service runtimes are 1.2.0 against engine 1.2.2.
   - The store README describes superseded ranking.
   - The rule ledger has 2 uncovered rules.
   - Graphify graphs are a week stale.
   - A pytest pointer leaked into `~/.local/state/eos/current/s-1`.
   - The ledger has unpushed commits.
10. **Rules versus facts is a hard boundary.** EOS cannot carry rules (arrival) or
    anything about a sibling repo (scope root). Loaded files and MEMORY.md
    therefore remain, and churn in them creates the largest context cost:
    779k tok of "changed on disk" notices.
11. **Workspace friction around EOS.**
    - 264 guard overrides in 5 days.
    - Hook budgets trade detail for cost; truncation notices appear on
      `pom.xml`/script touches.
    - The context-budget plan is recorded in Turkish while notes are in English;
      Turkish procedure names needed hand-added tags to be found (H-06).

`eos note search . "<q>"` for "eos limit", "retrieval", "brief", "token", "eos
stale" and "graphify" returns mostly the *moved-section* notes (`dev-atlas 0.x`,
`dev-nexus 4`, `nexus CLAUDE.md: …`). The notes that record limits are:
- "Note retrieval was ANDed and unweighted…"
- "A plain eos scan used to delete every fact the linked parent contributed"
- "Generated api-inventory notes are stale behind a parent upgrade…"
- "Known limits the supersession pass shipped with…"
- "project-intelligence: capability policy… measured cost of each call":
  "Everything else EOS offered lost to Read/Grep/Glob on measurement and was
  removed".

MEMORY.md carries 2 EOS rules:
- "EOS değişikliği sormadan nexus'a" (carry EOS changes into nexus without
  asking; memory file `eos-github-subtree-flow.md`).
- "Devin tarafına dokunma" (don't touch the Devin side; service AGENTS.md
  reaches Claude through EOS, `claude-agents-mirror-devin.md`).

`docs/open-items.md` is now an index. Its EOS-specific rows:
- **E1:** point the handoff doc at the newer EOS note.
- **E2:** closed; journey fan-out is by design.

---

## 12. What the real deployment says about EOS's memory model

- **Shape.** Memory is a git-tracked tree of Markdown notes with YAML front
  matter, plus two JSONL ledgers (runs/events, work items), rebuilt on demand
  into a per-project SQLite index. Search is lexical, IDF-weighted, with no
  embeddings. The host adds everything that makes it live:
  - the hooks (inject on touch, brief on prompt, block at stop)
  - wrapper capture
  - the rule/fact split for instructions
  - the evals
- **Host glue is large:** ~5.8k lines of Python in `automation/hooks` +
  `automation/lib`, beside EOS core 18.1k lines and 15.5k lines of tests.
- **What works (measured):**
  - Task briefs hand a procedure over before the first command (the eval re-run).
  - The Stop gate turned note-writing from ~0 into 99 organic notes.
  - Titles plus IDF give r@3 ≥ 0.875 on every set.
  - Moving how-to out of loaded files cut 65% of the start-up load.
- **What does not transfer:**
  - The loop exists in exactly one project.
  - Services are 7/21.
  - Knowledge is 70% bulk.
  - Staleness grows with every script edit.
  - Pull usage is ~24–40% of sessions.
  - Routing and work items are recorded but not acted on.
- **Do-not-adopt evidence already paid for here:**
  - MCP as the distribution channel (twice)
  - embeddings (install/dependency cost, rejected by ADR)
  - auto-mining or auto-writing notes
  - schemas without a write command
  - rules stored as retrievable notes
  - per-project hooks in a multi-repo workspace
  - JDT-LS-based LSP for parent/overlay code

---

## 13. Audit footprint (what this read-only audit changed)

- **Unchanged:** `routing.jsonl` (67 lines), `executions.jsonl` (2,028),
  `work.jsonl` (32), nexus `eos.db`, the service `eos.db`. No wrapper was run.
  `--arrival` and `eos brief <nexus> --task` were deliberately skipped: both write
  routing records.
- **Telemetry appended** (unavoidable: `[telemetry] enabled`, no env switch,
  `core/telemetry.py:28-30`):
  - nexus 5,049 → 5,073 lines. 27 rows carry this session id, some of them from
    the parent's own calls.
  - svc-cpq-ordercapture 564 → 569.
  - +1 line in each other service's `telemetry.jsonl` from `eos note audit`.
- **`routing-usage.jsonl` 15 → 16:** this is the parent session's own Stop hook
  (12:17:04Z, model `claude-fable-5-1`), not this audit.
- **Hook state written by the harness on my Reads:** `$TMPDIR/eos-note-inject/<sha>.json`.
- **One temp file** was written to `/tmp` and deleted immediately.
- **No commits.** No files in nexus were modified.
