# Plan: operational memory — target 1.0.0

**Status of this plan: CLOSED at 1.0.0, 2026-09-24.** Engine at the time of writing: 0.38.0; M0 landed in 0.39.0, M1 in 0.40.0, M2 in 0.41.0, M3 in 0.42.0, M4 in 0.43.0, M5 in 0.44.0, OM-60 in 0.45.0, the host's H rows against 0.46.0; 1.0.0 was cut when §8 held, with the record in §10.
**Target: 1.0.0**, defined as "the acceptance harness in M0 is green in full."

This file is the one place a session resumes from. It is a ledger, not an
essay: the status table below is updated in the same commit as the work it
describes, the acceptance checks are executable, and "where were we" is
answered by running them, never by asking.

---

## 0. Why this plan exists

An audit on 2026-09-24 (the protocol is reproduced in §8 so it can be run
again) asked one question of the engine: **does a new session know how a task
should be done, and what happened the last time it was done, at a small token
cost?** The answer was no, on both halves, and the reasons were specific:

| The engine has | The engine lacks |
|---|---|
| Semantic memory — notes, ranked retrieval (`core/notes.py`, `core/retrieval.py`), a code index with provenance (`core/index.py`, `fact` table) | **Procedural memory.** No entity holds an ordered, reusable procedure with prerequisites, success criteria or a verification history. The words `workflow`, `procedure`, `recipe` occur in two prose templates and nowhere else. |
| An intent ledger — `core/work.py`, seven lifecycle verbs, folded on read (ADR-020) | **Episodic memory.** Nothing records what a session did: no tool calls, commands, files, git operations, external-system calls, no timeline. ADR-020 says it in one line: *an item is what a session said, not what happened.* |
| Run records — `core/verification.py`, ten fields, never a verdict (ADR-018) | **A learning loop.** One conditional promotion (`core/inspector.py:819-842`: a passing run + a test that reaches and names the code → grade `verified`), last-write-wins, never demoted, never counted, read by nothing a session starts with. |
| A session-start brief — `core/brief.py`, ~200 tokens, wired by hook (ADR-021) | **Task awareness.** The only component that takes the task text and ranks knowledge against it is `build_context(task=…)` (`core/inspector.py:447`), reachable only through the MCP surface that ADR-021 stopped registering. Every live channel is path- or branch-aware. |

The ledger and the run records were built for exactly this and had never been
written to: zero `work.jsonl` files, three verification records, in a
workspace of 464 notes. That is the second finding, and it shapes every
milestone below: **a store nobody writes to is not memory.** Each milestone
therefore ships its writer with its schema, and the writer is a hook or a
wrapper, never a habit (ADR-021's argument, applied to the write side).

## 1. Target architecture

```text
New session
  ↓  SessionStart hook          → eos brief                (branch-derived)
  ↓  UserPromptSubmit hook      → eos brief --task "<…>"   (task-derived)
        ├── procedural memory   → the one procedure that fits, as steps
        ├── episodic memory     → the last N executions of it: outcome, where it broke
        └── lessons             → what a failed execution taught
  ↓  compact block, ≤ 1,500 tokens
  ↓  session executes; every external action is an event on ONE execution record
  ↓  eos run finish --outcome … [--lesson …]
        ├── procedure counters and last_verified move
        └── a failure writes a lesson note, linked to the execution
  ↓  next session: the same brief, one execution richer
```

Three stores, three lifetimes, deliberately not merged (extends ADR-020):

| Store | Answers | Lifetime | File |
|---|---|---|---|
| Notes (exists) | what is true here | durable, audited | `<knowledge>/*.md` |
| Work ledger (exists) | what a session intends | hours to days | `<knowledge>/work.jsonl` |
| **Execution ledger (new)** | what a session did | permanent, append-only | `<knowledge>/executions.jsonl` |

A **procedure** is a note (`kind: procedure`), because a procedure has a
note's lifetime and inherits everything notes already have: retrieval,
injection on touch, scope hashes, the staleness audit, the Stop gate. A
**lesson** and a **decision** are notes too. An **execution** is not a note:
it is an event stream with a beginning and an end, and it never becomes true
or false — it happened.

## 2. Constraints this plan honours

Existing decisions that a milestone may not break. Each is named where it
bites.

- **ADR-001 / 010** — `core/` stays stdlib-only; no embeddings, no vector
  store. Retrieval is IDF coverage and FTS5, as today.
- **ADR-005** — the knowledge model is the single source of truth; the SQLite
  index is derived and rebuilt from the files. Every new store is a file
  first, a table second.
- **ADR-013** — anything project-shaped is an index extension, not core.
- **ADR-018** — the engine records runs and never concludes from them. A
  procedure's counters and `last_verified` are *observations*. The engine
  never writes "recommended", "trusted" or a verdict; a person does.
- **ADR-019** — telemetry never records what was asked. The task text is used
  at retrieval time and **never persisted**. An execution's `title` is what
  the session declares (as a work item's is), not the prompt.
- **ADR-020** — work is intent; notes and work do not merge. Executions are a
  third store and reference a work item by id when one exists; they do not
  replace it.
- **ADR-021** — one surface; what must be reached is reached by a hook. Every
  read side here is hook-wired; the write side is wrapper- or hook-driven.

## 3. Status table — the resume point

Update the `Status` column in the commit that changes it. `TODO` → `DOING`
(one item at a time, name the session in the commit) → `DONE` (its checks
are green). A session resumes at the first non-`DONE` row of the lowest
milestone.

| ID | Milestone | Deliverable | Version | Checks | Status |
|---|---|---|---|---|---|
| OM-00 | M0 | Acceptance harness `tests/test_operational_memory.py` + `eos doctor --memory` | 0.39.0 | — | DONE |
| OM-01 | M0 | Fresh-session eval scenario `evals/scenarios/where-did-we-leave-off.md` | 0.39.0 | C-21 | DONE |
| OM-10 | M1 | ADR-022: the execution ledger | 0.40.0 | — | DONE |
| OM-11 | M1 | `core/executions.py`: record + event model, append, fold | 0.40.0 | C-04 C-05 | DONE |
| OM-12 | M1 | `eos run start\|event\|finish\|list\|show` | 0.40.0 | C-04 C-05 C-06 | DONE |
| OM-13 | M1 | `execution`, `execution_event` tables; `_load_executions` | 0.40.0 | C-04 C-09 | DONE |
| OM-14 | M1 | Session threading: one id across executions, work, notes (verifications: OM-43) | 0.40.0 | C-19 | DONE |
| OM-15 | M1 | Host capture contract: `eos run event` from a wrapper in one line | 0.40.0 | C-06 C-12 | DONE |
| OM-20 | M2 | ADR-023: procedures are notes | 0.41.0 | — | DONE |
| OM-21 | M2 | `kind: procedure` — front matter, `## Steps` parsing, validation | 0.41.0 | C-03 | DONE |
| OM-22 | M2 | `eos procedure list\|show\|new\|audit` | 0.41.0 | C-03 | DONE |
| OM-23 | M2 | Counters + `last_verified` moved only by `eos run finish` | 0.41.0 | C-17 C-18 | DONE |
| OM-24 | M2 | Index extension for host step catalogues (steps + last state) | 0.41.0 | C-03 | DONE |
| OM-30 | M3 | `eos brief --task` — procedure + executions + lessons, budgeted | 0.42.0 | C-07 C-08 C-11 C-20 | DONE |
| OM-31 | M3 | Execution ranking: procedure, target, outcome, recency | 0.42.0 | C-08 | DONE |
| OM-32 | M3 | UserPromptSubmit hook template; SessionStart template extended | 0.42.0 | C-07 C-21 | DONE |
| OM-33 | M3 | Score floor for OR retrieval (the audit's side finding) | 0.42.0 | C-09 | DONE |
| OM-40 | M4 | `kind: lesson`, `kind: decision` — sections, validation | 0.43.0 | C-14 C-15 | DONE |
| OM-41 | M4 | `eos run finish --outcome failed` asks for a lesson; Stop hook enforces | 0.43.0 | C-17 | DONE |
| OM-42 | M4 | Derived confidence at read time; never stored | 0.43.0 | C-18 | DONE |
| OM-43 | M4 | Verification records carry `session` and `execution` | 0.43.0 | C-16 | DONE |
| OM-50 | M5 | Event `tool` / `target` vocabulary; `eos run tools` | 0.44.0 | C-12 | DONE |
| OM-51 | M5 | `changed` events + finish-time git delta; `eos run diff` | 0.44.0 | C-13 | DONE |
| OM-60 | M6 | Skill/agent templates teach the loop; `docs/phases.md`; README | 0.45.0 | C-21 | DONE |
| OM-61 | M6 | Full harness green; audit protocol re-run and recorded | 1.0.0 | all | DONE |
| H-00 | H | Baseline: the fresh-session eval run in the host, report kept | — | C-21 | DONE |
| H-01 | H | Capture: every external-action wrapper appends one `eos run event` | after M1 | C-06 C-12 | DONE |
| H-02 | H | Hooks registered where sessions actually start: SessionStart + UserPromptSubmit | after M3 | C-21 | DONE |
| H-03 | H | Procedures written once: each recurring task becomes a `kind: procedure` note | after M2 | C-03 C-07 | DONE |
| H-04 | H | Step catalogues indexed through the extension (`[index] extensions`) | after M2 | C-03 | DONE |
| H-05 | H | The always-loaded instruction set split: index ≤ 4k tokens, pages on demand | — | C-20 | DONE |
| H-06 | H | Golden sets: a second project, and procedure questions | after M3 | C-09 | DONE |
| H-07 | H | Re-audit in the host: `eos doctor --memory` green on real projects; eval re-run | after M6 | all | DONE |

**Order:** every M row before any H row except H-00 and H-05, which depend
on nothing in the engine and can be done at any time. The H rows are the
host's half of the same plan (§6); a host records what each one maps to in
its own documentation, and this table is where their status lives.

## 4. Acceptance checks

One check per row of the audit's capability matrix. Each becomes a test in
`tests/test_operational_memory.py` (M0) that **fails today** and passes when
its milestone lands. `eos doctor --memory` runs the same checks against a
real project and prints the matrix with a status per row — that is the
command a session runs to learn where the plan stands.

| Check | Capability | Passes when |
|---|---|---|
| C-01 | SQLite persistence | (already true) index rebuilt from files; new tables present |
| C-02 | Project knowledge | (already true) notes indexed and searchable |
| C-03 | Procedural memory | a `kind: procedure` note with ≥1 step parses; `eos procedure show` prints steps in order with prerequisites and success criteria |
| C-04 | Episodic memory | `eos run start` … `finish` leaves one record with ≥1 event, readable back by id |
| C-05 | Execution timeline | `eos run show <id>` prints events in `at` order with `session`, `tool`, `target` |
| C-06 | Execution events | an event appended from a shell one-liner (no Python) lands in the ledger |
| C-07 | Task matching | `eos brief --task "…"` selects the procedure whose title/steps best match, on a fixture where two procedures compete |
| C-08 | Historical execution retrieval | `eos brief --task` lists the last 3 executions of that procedure, most recent first, failed ones flagged with their lesson |
| C-09 | SQL/FTS retrieval | executions and procedures reachable through `eos query --search`; OR results bounded by a score floor |
| C-10 | Semantic retrieval | **out of scope by ADR-001/010**; the check asserts the decision is recorded, not that a vector store exists |
| C-11 | Context packing | `eos brief --task` output ≤ 1,500 tokens (chars/3.5) on a fixture with 50 executions and 20 procedures |
| C-12 | Tool/application memory | `eos run tools --procedure <id>` answers "which tools ran, how often, last outcome" |
| C-13 | File/change tracking | `eos run diff <id>` lists paths from `changed` events and the commit range recorded at finish |
| C-14 | Decision memory | `kind: decision` validates `why` / `when` / `component` sections |
| C-15 | Lesson memory | `kind: lesson` validates `what went wrong` / `learned` / `next time` and links to an execution id |
| C-16 | Evidence | a verification record carries `session` and `execution`; `eos run show` lists its verifications |
| C-17 | Success/failure learning | after `finish --outcome ok` the procedure's `runs_ok` and `last_verified` moved; after `failed`, `runs_failed` moved and a lesson exists or the finish was refused |
| C-18 | Confidence | `eos procedure show` prints a derived confidence from counters + age; nothing in the files stores it |
| C-19 | Cross-session persistence | a second process, given the session id of the first, lists that session's executions, work items and notes together |
| C-20 | Minimal-token retrieval | a project with 1,000 executions returns the same ≤ 1,500-token brief as one with 10 |
| C-21 | AI integration | the fresh-session eval (OM-01) reports the session found procedure and history from the brief alone, without opening a skill |

## 5. Milestones

Each milestone is one version, one ADR where a decision is made, tests first,
and its status rows flipped in the same commits.

### M0 — measure first (0.39.0)

Nothing is built before the thing that says whether it worked exists.

- **OM-00** `core/memory_audit.py`: the 21 checks above as read-only
  functions, one per row, each answering "capability present, and used
  here?" with `IMPLEMENTED / PARTIAL / MISSING / DECIDED` and one evidence
  line. `eos doctor --memory [--format json]` runs them on a real project,
  prints the matrix with the next open row, exits 1 while any row is open.
  `tests/test_operational_memory.py`: one test per row that exercises the
  milestone's interface on a fixture and then asks the same check. The
  eighteen red ones are `xfail(strict=True)` naming the OM row they wait
  for, so the suite stays green and the moment a milestone makes one pass
  the marker must go in the same commit or the suite fails loudly. Nothing
  may `skip`; the matrix must always render all 21 rows.
- **OM-01** `evals/scenarios/where-did-we-leave-off.md`: the protocol in
  `evals/README.md`, applied to this question — a fresh session is given a
  task the project has executed before, and reports whether it found the
  procedure and the prior runs, in what order, at what cost. Recorded in the
  host as the baseline (H-00, expected: not found), re-run at H-07.

**Interfaces the harness pins.** The tests call these names; a milestone
implements to them, and a name that turns out wrong is changed in the test,
the harness and this list in one commit.

| Where | Names |
|---|---|
| `core/executions.py` (M1) | `start(root, title, *, procedure, work_item, session, agent, target) → Record`; `event(root, id, *, kind, tool, target, ref, exit_code, ms, body) → Event`; `finish(root, id, *, outcome, lesson) → Record` — refuses `failed` without a lesson (M4); `load(root)`; `by_session(root, session)`; `ranked(root, *, procedure, target, limit)` (M3); `tools(root, *, procedure, target) → [ToolUse(tool, count, last_outcome)]` (M5); `diff(root, id) → {paths, commit_start, commit_end}` (M5). `Record`: `id, title, procedure, work_item, session, agent, started_at, finished_at, outcome, target, branch, commit_start, commit_end, lesson, events`. `Event`: `execution, ord, at, kind, tool, target, ref, exit_code, ms, body, session`. |
| `core/eos.py` (M1) | `cmd_run_start`, `cmd_run_event`, `cmd_run_finish`, `cmd_run_list`, `cmd_run_show`; `eos run event <path> <id> --kind … --tool … --target … --ref … --exit N` |
| `core/notes.py` (M2, M4) | `KINDS` gains `procedure`, `lesson`, `decision`; `Note` gains `procedure` (slug), `runs_ok`, `runs_failed`, `last_verified`, `execution`; `procedure_steps(note) → [str]` from `## Steps`; `procedure_confidence(note, now=None) → failing\|unverified\|fresh\|aging\|stale` (`unverified` added in M4: a procedure never run ok is not "stale"); `add_note(kind=…)` validates `## Steps` / `## Why, ## When, ## Component` / `## What went wrong, ## What was learned, ## Next time`; `SCORE_FLOOR` (M3) |
| `core/index.py` (M1, M3) | `execution`, `execution_event` tables; `search` rows with `source` in `execution`, `procedure`; `SCORE_FLOOR` |
| `core/brief.py` (M3) | `build(root, *, session, agent, task=None, budget=None)` |
| `core/verification.py` (M4) | `Record.session`, `Record.execution`; `record(…, session=, execution=)` |
| `core/ai/templates/` (M3) | `prompt_submit.py`; `writer._HOOKS` gains `("UserPromptSubmit", ".claude/hooks/eos-prompt.py", "prompt_submit.py")` |

### M1 — the execution ledger (0.40.0)

- **OM-10** ADR-022 records the decision: a third append-only ledger,
  `executions.jsonl`, beside `work.jsonl`, folded on read, same properties as
  ADR-020 (append-only, collisions reported, staleness computed at read).
  What it stores is **references, never payloads**: a command's name and
  argument shape, a path, an exit code, a log path, a commit — not output,
  not diffs, not the prompt (ADR-019).
- **OM-11** `core/executions.py`. Record: `id, title, procedure, work_item,
  session, agent, started_at, finished_at, outcome, target, branch,
  commit_start, commit_end, lesson`. Event: `execution, ord, at, kind, tool,
  target, ref, exit_code, ms, body`. Event kinds are a closed vocabulary:
  `ran` (a command or wrapper), `read`, `changed`, `called` (an external
  system), `verified` (points at a verification record), `noted` (a note
  written), `decided`. `outcome` is one of `ok, failed, abandoned`, declared
  by the session — the engine records it and draws no conclusion (ADR-018).
- **OM-12** CLI. `eos run start --title … [--procedure P] [--work W]` prints
  the id; `eos run event <id> --kind ran --tool jenkins --target env1 --ref
  "<job#build>" --exit 0`; `eos run finish <id> --outcome ok|failed|abandoned
  [--lesson "<text>"]`; `eos run list [--session S] [--procedure P] [--target
  T] [--outcome O] [--since D]`; `eos run show <id>`. Every mutation exits 0
  when the state already holds (idempotent, as `eos work` is).
- **OM-13** Index: `execution`, `execution_event` tables loaded by the same
  fold the CLI uses (ADR-020's rule); joins to `work_item`, `note.session`,
  `git_commit`. Rebuilt from the file; no `UPDATE` path.
- **OM-14** One session id, read the way `core/telemetry.py` reads it
  (harness variable first, `--session` override), written into executions,
  work events, notes and verifications alike. `eos brief --session S` and
  `eos run list --session S` return the same session's records from all
  stores in one listing — this is C-19.
- **OM-15** The capture contract for hosts, documented in the ADR and in the
  generated skill: a wrapper appends one event with one command, no Python,
  under 50 ms, and **must not fail the wrapped command if the ledger cannot
  be written** (the same silence rule as ADR-021's hook, with the same
  `ok: false` telemetry mark). The current execution id comes from an
  environment variable the session sets at `run start`, so a wrapper never
  has to know which execution it is part of.

### M2 — procedural memory (0.41.0)

- **OM-20** ADR-023: a procedure is a note of `kind: procedure`. Why a note
  and not a fourth store: it has a note's lifetime, and notes already carry
  retrieval, touch injection, scope hashes, the staleness audit and the Stop
  gate. Why not a work item: work is what one session intends; a procedure is
  what every session should do.
- **OM-21** Front matter: `procedure: <slug>` (derived from the title unless
  given) and the four engine-owned observations `runs_ok`, `runs_failed`,
  `last_verified`, `last_execution`, written as `0`/absent at creation. Body
  sections, parsed case-insensitively: `## Steps` (required, an ordered or
  bulleted list; a step names its tool as `(tool: <name>)`), `## Prerequisites`,
  `## Success`, `## When not to use this`, and `## Known failures`
  (engine-appended on a failed run). Prerequisites and success live in the
  body rather than the front matter because they are prose a person edits,
  and the front matter is where the engine writes. `_compose_body` refuses a
  procedure without steps. `note amend` carries the procedure fields through
  a rewrite, so revising the steps never resets the history.
- **OM-22** `eos procedure list [--target T] [--tool X]`, `show <slug>`
  (steps numbered, counters, derived confidence, last three executions),
  `new --title … --steps -` (from stdin), `audit` (procedures with a failed
  latest run, or none in N days, or a scoped file changed).
- **OM-23** `eos run finish` with `--procedure` set moves `runs_ok` /
  `runs_failed`, `last_verified` (on `ok` only) and `last_execution`, and
  appends the lesson title to `## Known failures` on `failed`. This is the
  only write path into a procedure's counters; a hand edit of a counter is
  reported by `eos procedure audit` as a mismatch against the ledger.
- **OM-24** An index extension, in the pattern of `extensions/journeys.py`,
  for hosts that already keep machine-readable step catalogues (a scenario
  engine, a runbook directory): it indexes step definitions and the last
  recorded state into `procedure_step` / `procedure_state` so they are
  queryable without being rewritten as notes. Configured per project under
  `[index] extensions`; nothing in core knows the host's format.

### M3 — task-aware retrieval (0.42.0)

- **OM-30** `eos brief --task "<text>" [--budget N] [--task-only]`. Sections in
  priority order under one budget (default 1,500 tokens at a conservative
  3.0 chars/token): the best-matching procedure (steps, prerequisites,
  success, counters), its last three runs (date, outcome, target, tools,
  lesson when failed) plus the most recent failed-with-lesson run when it
  falls outside those three, the procedure's known failures, the nearest
  non-procedure notes, a one-line "record this run" command, then the
  branch brief. With no procedure it says "none recorded — do not present
  improvised steps as this project's" and finds runs by the task's words.
  `--task-only` returns only the task sections and an empty string when none
  found anything. The task text is a query and is never written anywhere
  (ADR-019; a test greps every file after a run). `build_context(task=…)`
  stays as the MCP surface's path; the CLI path is this one.
- **OM-31** Execution ranking: candidates by procedure, else by the task's
  words, else all; target-matching runs lead; then most recent first, with
  ledger order breaking the ties runs finished within one second share. A
  failed run with a lesson is **not** promoted above newer runs — the order
  stays honest — and the brief shows it on its own line when it falls
  outside the top three, which is what "the one a session needs to read"
  required.
- **OM-32** `core/ai/templates/prompt_submit.py`, registered for
  `UserPromptSubmit` by `eos init` / `eos ai update` beside SessionStart and
  Stop. It runs `eos brief --task <prompt> --task-only`; prints nothing when
  nothing matched, and nothing when the same block was already delivered in
  this session (digests of printed blocks kept per session in
  `$EOS_STATE_DIR/prompted/`; never the prompt). Always exit 0, never stderr.
  The SessionStart brief gains `RUNS OPEN`, the executions left unfinished.
  A host whose sessions start from a parent directory registers them there
  (H-02).
- **OM-33** Score floors, relative to the best result: `notes.SCORE_FLOOR =
  0.4`, `index.SCORE_FLOOR = 0.5` (BM25 is on its own scale; the LIKE
  fallback uses its hit count). Swept on the golden set: recall@1/@5
  unchanged at every floor up to 0.5; mean results 4.3 → 2.8 on the note
  path, 20 → 6.8 on FTS; "deploy" and "deploy env1" went from 14 and 19
  notes to 3 each.

### M4 — lessons, decisions, learning (0.43.0)

- **OM-40** `KINDS` (`core/notes.py:89`) gains `lesson` and `decision`.
  `lesson`: `## What went wrong`, `## What was learned`, `## Next time`, and
  front matter `execution: <id>`, `procedure: <slug>` — all validated.
  `decision`: `## Why`, `## When`, `## Component`, front matter `supersedes:`
  optional. Retrieval treats both as findings (never bulk).
- **OM-41** `eos run finish --outcome failed` is refused unless it carries
  `--lesson` or a lesson note already names the run (`eos note add --kind
  lesson --execution <id>`); the refusal prints both. With `--lesson` the
  finish writes the lesson note first (what went wrong: the run and its
  non-zero exits; what was learned: the lesson; next time: `--next-time` or a
  pointer back to the procedure), so a failure to write it leaves the run open
  rather than finished with the lesson lost. The same lesson again is appended
  under `## Seen again` instead of duplicated. The Stop hook asks once about
  runs this session left open, next to the work it holds.
- **OM-42** `notes.procedure_confidence(note, now=None)` → `failing` (latest
  finished run failed), `unverified` (none finished ok), `fresh` (≤ 30 days),
  `aging` (≤ 90), `stale`. Computed from the ledger beside the note and never
  stored; shown by `procedure show` and in the task brief's procedure line.
  The `fact.confidence` column is left as it is — static-fact provenance, a
  known limit recorded in ADR-024, not conflated with this.
- **OM-43** `verification.Record` gains `session` and `execution`; `eos verify`
  inside an open run fills both and appends a `verified` event to it, and `eos
  run show` lists the run's verifications.

### M5 — tool and change memory (0.44.0)

- **OM-50** Tool names are normalised at write and at read — the program or
  wrapper name, lower-cased, without a path or a script suffix, so
  `automation/jenkins.sh` and `jenkins` count once; targets are lower-cased.
  `eos run tools [--procedure P] [--target T]` → per tool: calls, runs, non-zero
  exits, the targets it acted on, and the outcome of the latest run it was part
  of. `executions.tools()` returns `ToolUse(tool, count, failures, runs,
  last_outcome, last_at, targets)`.
- **OM-51** `eos run diff <id>` → the paths the run's `changed` events name,
  its `commit_start..commit_end`, and — resolved through git while the
  repository holds them — the commits and files inside that range. A run that
  committed nothing says so. References only; no content.

### M6 — closing the loop (1.0.0)

- **OM-60** The generated skill and agent templates (`core/ai/templates/`)
  describe the loop in the order a session lives it: brief → run start →
  events (automatic where the host captures) → verify → finish → lesson. The
  AGENTS section is regenerated; `docs/phases.md` gains Phase 5 → done;
  README gains one section.
- **OM-61** `eos doctor --memory` green on the reference project; the audit
  protocol (§8) re-run by a fresh session and the report committed beside the
  baseline from OM-01. Version 1.0.0 is cut only when both are true.

## 6. Host milestones (H) — the half the engine cannot do

The engine cannot see a tool call it did not make, cannot register a hook in
a directory it does not own, and cannot shrink a document it did not write.
The audit found the engine's 1.0.0 would be **unfed** without these: the
brief was installed in thirteen projects and had run in none, because
sessions start one directory up; the ledger had a schema and no writer. So
the host's work is in the same status table, numbered H, and 1.0.0 in the
host's sense (§8, step 1 on *real* projects) needs every H row `DONE`.

The rows are written for any host. A host keeps its own file mapping each
H-xx to its concrete scripts, hooks and documents; that file is host
documentation, this plan does not name hosts.

- **H-00 Baseline.** The fresh-session eval (OM-01) run once in the host
  before anything changes, report kept beside the engine's. It is the number
  H-07 is compared against. Depends on nothing.
- **H-01 Capture.** Every wrapper through which the host routes an external
  action — build, deploy, ticket, review, database, queue, observability,
  test run, push — appends one `eos run event` (OM-15): `tool` = the wrapper,
  `target` = the environment or system, `ref` = the log or cache path the
  wrapper already writes, `exit` = its own exit. One shared helper, under 50
  ms, never failing the wrapped command. Wrappers are the right place because
  a hook sees a command string while a wrapper sees a result, and because in
  a disciplined workspace they are already the only door out.
- **H-02 Hooks where sessions start.** SessionStart → `eos brief` and
  UserPromptSubmit → `eos brief --task` registered in the directory sessions
  are actually opened from, not only in each project (OM-32). Where the host
  has a non-hook agent, its start-of-task step runs the same one command.
- **H-03 Procedures written once.** Each recurring task a session is given —
  deploy to an environment, deliver a configuration change, take a change to
  merge, run a regression, run a scenario, upgrade a product version —
  becomes one `kind: procedure` note (OM-21) naming its tools and the wrapper
  output that proves success. A procedure that lives only inside a large
  instruction document is invisible to the brief and costs the whole document
  to reach.
- **H-04 Catalogues indexed.** Where the host already keeps machine-readable
  step definitions with recorded state (a scenario engine, a runbook
  directory), the extension (OM-24) is configured for them, so "which step
  failed, when" is a query and not a table someone maintains by hand.
- **H-05 The always-loaded set split.** Whatever the host loads into every
  session unconditionally is measured, and anything over the brief's own
  budget by an order of magnitude is split into an index plus pages opened by
  name. Not engine work, and in the audited host larger than every engine
  gain combined: 72% of what a session paid before its first tool call was
  one document. Depends on nothing; can be done first.
- **H-06 Golden sets.** A second project's question/answer set, and a set of
  *procedure* questions ("how do I …"), so the score floor (OM-33) and the
  task-brief ranking (OM-30) are measured in the host, not guessed.
- **H-07 Re-audit in the host.** `eos doctor --memory` green on the host's
  real projects, the eval re-run and compared with H-00, and the §8 protocol
  applied by a reader who did not do the work.

## 7. What is deliberately not in this plan

- Vector or embedding retrieval (ADR-001, ADR-010).
- Automatic verdicts, "trusted" procedures, or any promotion the engine makes
  on its own (ADR-018). Counters move; conclusions do not.
- Storing prompts, outputs, diffs or logs. References only (ADR-019, and the
  size argument: a ledger that carries payloads is the context problem it
  was built to solve).
- Recording work the wrappers did not see. A raw command run around the
  wrappers leaves no event, and the plan says so rather than pretending a
  hook can reconstruct it.

## 8. How the result is checked

The same audit that produced §0, so that "done" means the same thing at the
end as it did at the start:

1. `eos doctor --memory` on the reference project: every row `IMPLEMENTED`.
2. `tests/test_operational_memory.py`: 21 of 21 green, none skipped.
3. The fresh-session eval (OM-01) re-run by a session with no context, per
   `evals/README.md`; its report says the procedure and the prior executions
   were found from the brief, before the first tool call, and states the
   tokens the brief cost.
4. The capability matrix from the audit prompt re-filled by a reader who did
   not do the work, with file:line evidence per row. A row that is
   `IMPLEMENTED` only because a similarly named thing exists is a failure of
   this plan, not of the reader.

If any of the four is not true, the version is not 1.0.0 and the status table
says which row is open.

## 9. Resume protocol

For the session that picks this up:

1. Read §3. The first row that is not `DONE`, in the lowest milestone, is the
   work.
2. Run `eos doctor --memory` (after M0) or `pytest tests/test_operational_memory.py`
   to see the same thing from the code's side. If they disagree with §3, the
   table is wrong; fix the table first, in its own commit.
3. Flip the row to `DOING`, do the work with its tests first, flip it to
   `DONE` in the commit that makes its checks green, bump `core/VERSION` when
   a milestone closes.
4. Do not start a later milestone while an earlier one has an open row. The
   order is the dependency order.
5. Record anything learned that this plan did not predict as a note — a
   `lesson` once M4 exists, a `finding` before — not as a comment in the
   plan.

## 10. What 1.0.0 means, and what it does not

Cut on 2026-09-24 when the four conditions of §8 held, each recorded where a
later reader can check it:

1. `eos doctor --memory` on the reference project (the host workspace's own
   root): 21 of 21 rows, every one traced by an independent reader to code and
   to a command it ran.
2. `tests/test_operational_memory.py`: 23 of 23 green, no `xfail` left.
3. The fresh-session eval re-run (`evals/scenarios/where-did-we-leave-off.md`),
   by a session that did none of the work, handed what the two hooks print:
   both halves answered before its first acting command — the procedure with
   its steps and its honest `unverified`, and "no execution is recorded" as a
   fact about the store confirmed three ways — at 3,402 characters of hook
   context, against a baseline of ~20,000 characters that found nothing. Its
   findings (a clipped step line, a `.` that pointed at the wrong store, a
   section `procedure show` omitted) were fixed before the cut.
4. The capability matrix re-filled by a reader who did not do the work. Its
   caveats are the honest description of this version and are kept in the
   host's records verbatim; four of them were fixed before the cut (the
   catalogue's history folded into the brief, verification records indexed and
   searchable, C-19 requiring a real join, two more wrappers capturing), one
   was left as designed (C-20's volume proof is the 1,000-execution fixture in
   the gate; the doctor's threshold is a floor for real projects).

What it does not mean, in the reader's words: *"21/21 in the one project the
work was seeded in, 6/21 in the projects the work is meant to serve."* The
engine can remember; a project remembers only what runs are started in it.
The reference project's ten executions were recorded by one session in one
sitting, on real scenarios against a real environment, because the loop had
to be exercised once to be judged — the counters and the lessons that came
out of it are real, and so is the fact that they are one day old. Every
other project in the host has the hooks, the wrappers and the procedures
(which live in the host's root by decision) and an empty ledger. The next
version of this matrix worth reading is the one taken a month of work later,
and the plan's own rule for that reading stands: a row that is `IMPLEMENTED`
because a similarly named thing exists is a failure of the plan, not of the
reader.
