# Plan: operational memory — target 1.0.0

**Status of this plan: ACTIVE.** Engine at the time of writing: 0.38.0.
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
| OM-00 | M0 | Acceptance harness `tests/test_operational_memory.py` + `eos doctor --memory` | 0.39.0 | — | TODO |
| OM-01 | M0 | Fresh-session eval scenario `evals/scenarios/where-did-we-leave-off.md` | 0.39.0 | C-21 | TODO |
| OM-10 | M1 | ADR-022: the execution ledger | 0.40.0 | — | TODO |
| OM-11 | M1 | `core/executions.py`: record + event model, append, fold | 0.40.0 | C-04 C-05 | TODO |
| OM-12 | M1 | `eos run start\|event\|finish\|list\|show` | 0.40.0 | C-04 C-05 C-06 | TODO |
| OM-13 | M1 | `execution`, `execution_event` tables; `_load_executions` | 0.40.0 | C-04 C-09 | TODO |
| OM-14 | M1 | Session threading: one id across executions, work, notes, verifications | 0.40.0 | C-19 | TODO |
| OM-15 | M1 | Host capture contract: `eos run event` from a wrapper in one line | 0.40.0 | C-06 C-12 | TODO |
| OM-20 | M2 | ADR-023: procedures are notes | 0.41.0 | — | TODO |
| OM-21 | M2 | `kind: procedure` — front matter, `## Steps` parsing, validation | 0.41.0 | C-03 | TODO |
| OM-22 | M2 | `eos procedure list\|show\|new\|audit` | 0.41.0 | C-03 | TODO |
| OM-23 | M2 | Counters + `last_verified` moved only by `eos run finish` | 0.41.0 | C-17 C-18 | TODO |
| OM-24 | M2 | Index extension for host step catalogues (steps + last state) | 0.41.0 | C-03 | TODO |
| OM-30 | M3 | `eos brief --task` — procedure + executions + lessons, budgeted | 0.42.0 | C-07 C-08 C-11 C-20 | TODO |
| OM-31 | M3 | Execution ranking: procedure, target, outcome, recency | 0.42.0 | C-08 | TODO |
| OM-32 | M3 | UserPromptSubmit hook template; SessionStart template extended | 0.42.0 | C-07 C-21 | TODO |
| OM-33 | M3 | Score floor for OR retrieval (the audit's side finding) | 0.42.0 | C-09 | TODO |
| OM-40 | M4 | `kind: lesson`, `kind: decision` — sections, validation | 0.43.0 | C-14 C-15 | TODO |
| OM-41 | M4 | `eos run finish --outcome failed` asks for a lesson; Stop hook enforces | 0.43.0 | C-17 | TODO |
| OM-42 | M4 | Derived confidence at read time; never stored | 0.43.0 | C-18 | TODO |
| OM-43 | M4 | Verification records carry `session` and `execution` | 0.43.0 | C-16 | TODO |
| OM-50 | M5 | Event `tool` / `target` vocabulary; `eos run tools` | 0.44.0 | C-12 | TODO |
| OM-51 | M5 | `changed` events + finish-time git delta; `eos run diff` | 0.44.0 | C-13 | TODO |
| OM-60 | M6 | Skill/agent templates teach the loop; `docs/phases.md`; README | 1.0.0 | C-21 | TODO |
| OM-61 | M6 | Full harness green; audit protocol re-run and recorded | 1.0.0 | all | TODO |

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

- **OM-00** `tests/test_operational_memory.py`: the 21 checks above, each a
  test with the check id in its name, each red. `eos doctor --memory`: the
  same checks run against a real project, printed as the matrix. Skipping a
  check is a failure; the harness may not silently shrink.
- **OM-01** `evals/scenarios/where-did-we-leave-off.md`: the protocol in
  `evals/README.md`, applied to this question — a fresh session is given a
  task the project has executed before, and reports whether it found the
  procedure and the prior runs, in what order, at what cost. Recorded now as
  the baseline (expected: not found), re-run at M6.

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
- **OM-21** Front matter: `procedure: <slug>`, `tools: [...]`, `targets:
  [...]`, `prerequisites: [...]` (free text each), `success: [...]`,
  `runs_ok`, `runs_failed`, `last_verified`, `last_execution`. Body sections
  `## Steps` (an ordered list, parsed; each step may name a tool), `## When
  not to use this`, `## Known failures` (auto-maintained: lesson titles).
  `_compose_body` (`core/notes.py:303`) validates as it does for `defect`.
  The counters are the only front-matter fields the engine rewrites, and only
  from `eos run finish` (OM-23); everything else is authored.
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

- **OM-30** `eos brief --task "<text>"`. Sources, in order, under one budget
  (default 1,500 tokens, `--budget`): the best-matching procedure (steps
  only, no prose), its last three executions (one line each: date, outcome,
  target, where it broke), the lessons linked to any failed one (titles), then
  what the branch-derived brief shows today. The task text is a query and is
  discarded — never written to telemetry, the ledger or a note (ADR-019).
  Reuses `search_notes` / `word_weights` (`core/notes.py:1127`, `:1206`) for
  procedures and lessons; `build_context(task=…)` (`core/inspector.py:447`)
  is retired into this, so the task-aware path exists once and on the CLI.
- **OM-31** Execution ranking: same procedure first, then same `target`,
  then recency; a `failed` execution with a lesson outranks an `ok` one of
  the same age, because it is the one a session needs to read.
- **OM-32** Hook templates in `core/ai/templates/`: `session_start.py` calls
  `eos brief` as today; a new `prompt_submit.py` calls `eos brief --task`
  with the submitted text and returns the block as context. Both silent on
  failure, both leaving the `ok: false` telemetry mark. `eos init` and
  `eos ai update` register both. A host whose sessions start from a parent
  directory registers them there — the ADR says so, because the audit found
  the brief installed in thirteen projects and running in none.
- **OM-33** A score floor for OR retrieval in `core/index.py:542` and
  `core/notes.py:1206`: results below a fraction of the top score are cut,
  so a five-word question returns the answer and its neighbours, not 176
  rows. Measured with `eos note eval` before and after; the golden sets
  decide the fraction.

### M4 — lessons, decisions, learning (0.43.0)

- **OM-40** `KINDS` (`core/notes.py:89`) gains `lesson` and `decision`.
  `lesson`: `## What went wrong`, `## What was learned`, `## Next time`, and
  front matter `execution: <id>`, `procedure: <slug>` — all validated.
  `decision`: `## Why`, `## When`, `## Component`, front matter `supersedes:`
  optional. Retrieval treats both as findings (never bulk).
- **OM-41** `eos run finish --outcome failed` without `--lesson` and without
  an existing lesson linked to the execution is refused with the command
  that would satisfy it; the Stop hook (`session_stop.py`) asks once about
  executions this session left unfinished, as it asks about work today.
- **OM-42** Confidence is **derived at read time** from `runs_ok`,
  `runs_failed` and the age of `last_verified`, printed by `eos procedure
  show` as a word (`fresh`, `aging`, `stale`, `failing`) with the numbers
  beside it, and never stored — the same reasoning ADR-020 gives for not
  storing `stale`. The `fact.confidence` column is left as it is; the audit's
  finding that three of four `origin` values are never written is recorded
  in the ADR as a known limit, not fixed here.
- **OM-43** `core/verification.py` `Record` gains `session` and `execution`;
  `eos verify` inside a running execution fills both from the environment;
  `eos run show` lists the execution's verifications.

### M5 — tool and change memory (0.44.0)

- **OM-50** `tool` and `target` on every `ran` / `called` event are
  normalised (`tool` is the wrapper or program name, `target` is the
  environment or system); `eos run tools [--procedure P] [--target T]`
  answers which tools ran, how often, and the last outcome each was part of.
- **OM-51** `changed` events carry a path and, at `finish`, the engine
  records `commit_start` / `commit_end` from git; `eos run diff <id>` prints
  the paths and the commit range, and resolves the range through the existing
  `git_commit` tables. Content is never stored — the reference stays
  resolvable as long as the repository is.

### M6 — closing the loop (1.0.0)

- **OM-60** The generated skill and agent templates (`core/ai/templates/`)
  describe the loop in the order a session lives it: brief → run start →
  events (automatic where the host captures) → verify → finish → lesson. The
  AGENTS section is regenerated; `docs/phases.md` gains Phase 5 → done;
  README gains one section.
- **OM-61** `eos doctor --memory` green on the reference project; the audit
  protocol (§8) re-run by a fresh session and the report committed beside the
  baseline from OM-01. Version 1.0.0 is cut only when both are true.

## 6. Host integration contract

The engine cannot see a tool call it did not make. The host workspace owns
the capture points, and this plan asks three things of it, all optional in
the sense that the engine works without them and honest in the sense that
without them C-06, C-12 and C-21 cannot pass:

1. **Capture** — every wrapper the host routes external actions through
   appends one `eos run event` line (OM-15). Wrappers are the right place
   because they are already the only door to the outside world in a
   disciplined workspace, and because a hook sees a command string while a
   wrapper sees a result.
2. **Hooks at the directory sessions start from** — SessionStart and
   UserPromptSubmit registered where the session's working directory
   actually is (OM-32).
3. **Procedures written down once** — the host's existing runbooks and step
   catalogues either become `kind: procedure` notes (OM-21) or are indexed by
   the extension (OM-24). A procedure that lives only inside a large
   instruction document is invisible to the brief and costs the whole
   document to reach.

A host records its own mapping of these three in its own documentation; the
engine's plan does not name hosts.

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
