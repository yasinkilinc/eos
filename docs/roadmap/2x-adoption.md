# EOS 2.x branch — what landed overnight and how to adopt it

Branch `eos-2x` (from `main` 1.5.1), released on the branch as 1.6.0 → 1.23.3.
Every step: tests first, the full suite green before the commit
(1,169 passed, 1 skipped at 1.13.4), `tools/check-clean.sh` clean. `main` untouched.
The row-by-row ledger with commits is `docs/roadmap/2x-progress.md`.

## What changed, by the question it answers

| Release | Item | What it does |
|---|---|---|
| 1.31.0 | Lessons carry evidence (N10) | `note add --evidence "<command, run id, or file:line>"` for `kind lesson` only (another kind is refused by name); `amend` carries it through a rewrite unchanged. The task brief marks a lesson naming neither an execution nor evidence with `[no evidence]`; `eos consolidate` counts and lists them (`LESSONS WITHOUT EVIDENCE`). Existing lessons are not migrated |
| 1.31.0 | Change capture measured (N9, E1) | `eos consolidate` compares, per finished run with a commit range, the files its `changed` events named against the files git says the run's commits touched (`—`, never `0%`, when the range cannot be measured); prints `CHANGE CAPTURE <c>/<t> files (<share>) over N runs with commits; M runs unmeasurable` and the same in JSON. Measured on the host: 18/454 files (4%) over 45 runs with commits, 23 unmeasurable -- far under the report's >= 95% target |
| 1.31.0 | Expired and replaced procedures are not matched; amend sets provenance and validity (N8) | `notes.procedures()` takes `include_expired` (default False) so `best_procedure` and `run start --procedure` stop offering an expired procedure, the same way a replaced one is already refused; `note amend` gains `--provenance`/`--agent`/`--valid-until`, a metadata-only amend now valid on its own |
| 1.6.0 | Verified completion (ADR-028) | `verify.toml` maps files to their check; a turn that ends with work changed after its last passing check is stopped once; an ok run is labelled `verified` or `claimed` |
| 1.7.0 | Telemetry (F1) | `rebuilt` recorded, failures by exit code, a real token median |
| 1.7.0 | Hooks (F1) | no task brief for pure conversation (`[brief] filler`); session brief budgeted at 800 tokens |
| 1.7.0 | Locks and atomic writes (F2, ADR-029) | no lost counter or trimmed line under concurrent sessions (35/100 → 100/100 measured) |
| 1.7.0 | Ledger (F5, ADR-029) | `v` on every line, rotation at 8 MB x 4, no finish for a run never started |
| 1.7.0 | Evaluation (F6) | nDCG, zero-hit, latency, worst-first misses, `a.md\|b.md` alternates |
| 1.7.0 | CLI split (F4) | `core/eos.py` 3,090 → 716 lines, `core/cli/*`; 99 help screens byte-identical |
| 1.8.0 | Subagent handoff (C6, ADR-030) | `eos brief --for-subagent`; `[hooks] handoff_tokens` appends it to subagent prompts |
| 1.8.0 | Citation check (E3a, ADR-030) | a subagent citing a missing absolute path or a line past the end is asked once to fix it |
| 1.8.0 | Fix | the routing hook sent only `{model}` as the whole tool input -- live it would have dropped prompts |
| 1.8.0 | `eos brief --resume` (M7), paraphrase guard (M5) | what a session left; a reworded duplicate note refused |
| 1.9.0 | `eos consolidate` (L1), `eos procedure lint` (E2), `eos cost --context` (C7), work-run links (E4), note `[kind, age]` (C2), compact artifacts (E6) | maintenance report, procedure checks, context measurement, traceability, smaller files |
| 1.9.1 | Branch review | a fresh reviewer found eleven defects (verify false clears, citation false blocks, a lock takeover race, placeholder mapping, cross-ledger finish, hook robustness, lint, brief rebuilds); each fixed with a test; locks are now the kernel's `flock` |
| 1.9.2 | Second review | seven findings in those fixes: the verify gate parses the command as a shell would (quotes, comments, heredocs, `&& echo PASS`); placeholder bounds; lock fallback on a read-only state dir; finish prefers the caller's ledger; container paths |
| 1.10.0 | Typed steps (E2, ADR-031), `eos cite` (E3 depth 1) | `run show` names the step a failed run failed at; an answer's quotes are checked against the lines they cite |
| 1.10.1-2 | Fixes | a retried step is not the failed step; a third review: `$(...)` checks, `bash -c`, finish's root walk |
| 1.11.0 | Main-session citation check, brief step line | the answer the user reads is checked at Stop like a subagent's; the brief says where the last failure stopped |
| 1.12.0 | Verify gate measured on the host's history | 36 of 37 past sessions with scoped changes would be stopped; half had run the check uncountably, so that now gets a hint at once; `consolidate` names silent steps |
| 1.12.1 | Fifth review | `set -e` read as bash reads it: not in conditions or function bodies, `set +o errexit`, subshells |
| 1.12.2 | Sixth review | a check inside an if/loop/case/function block never counts; deep nesting refused, not a crash |
| 1.12.3 | Seventh review | block words read at every command start, not only a fragment's first word |
| 1.30.1 | Review of N4-N6 (RVb) | 3 findings, each fixed with a test: `route --stats` compared a raw transcript model string against the router's canonical id with no alias resolution, so `sonnet` vs its own alias `claude-sonnet-5` counted as a mismatch (N5); a run with several `decided` events (an explicit `--model`/`--fresh` re-route) was joined to its first decision instead of the one later calls in the run actually reused (N5); `run finish`'s out-of-scope warning read the procedure note and changed refs from the CLI's own invocation root instead of the run's project, so it silently never fired for a run a workspace routed to a named project's ledger (N6, C5b) |
| 1.30.0 | Finish names out-of-scope changes (N6) | at `run finish`, a changed file the run's procedure scope does not cover prints as a warning (up to 5, then a count); never refuses the finish or changes the outcome |
| 1.30.0 | Advised vs used vs outcome (N5) | `route --stats` joins a run's routing decision to the model its session's usage fold shows actually ran (the subagent tally preferred) and the run's outcome: advised == used / != used / unknown counts, each group's ok rate |
| 1.30.0 | Run show folds repeats (N4) | consecutive ledger events identical but for their ordinal and timestamp collapse into one line with an `xN` marker and the first..last time; the ledger and `--format json` stay whole |
| 1.29.0 | One token estimator (N3) | `core/context/budget.py`, 2.22 chars/token measured on the host's transcripts, replaces five separate guesses (brief 3.0, injection 2.7, bench/telemetry chars/4); task brief, branch brief and injection budgets tighten to match; `eos cost`'s printed ratio comes from the same constant |
| 1.29.0 | Notes provenance and valid-until (N2) | `note add --provenance/--agent/--valid-until`; search, the context section and `consolidate` leave an expired note out and list it; `eos note amend` now carries the fields through a rewrite instead of dropping them (RVa) |
| 1.29.0 | Synonym groups in note search (N1) | `[notes] synonyms` in a project's config: each group is one concept for ranking; the subagent handoff's own word match and a narrowed body now read a query through the same groups instead of only the literal words (RVa) |
| 1.28.0 | Project to workspace (C5c) | `[workspace] root` in a project's config, confirmed by the workspace's projects.toml: a session started in the project gets the workspace's task brief (its procedures and runs) beside its own |
| 1.27.0 | Workspace hooks (C5c) | the root's briefs cover its live and named projects; notes injected on a touch (`post-batch`) and the opt-in note gate (`[hooks] notes`) moved from host scripts into the engine; `worktrees` in projects.toml; `[hooks] start_command`/`prompt_command` for a host's own lines |
| 1.26.0 | Workspace run routing (C5b) | from a root with `.eos/projects.toml`, a run whose title names one project is recorded in that project's ledger; `--project`, `--here`; events, finish and the Stop close follow it |
| 1.23.3 | Java nested scopes | a nested Java class named like its outer one no longer swaps end lines with it; same-named containers (`Req.Builder`, `Resp.Builder`) each keep their methods |
| 1.23.2 | Review of L3 and L4 | a Java constructor is never a method's parent, an ambiguous parent is left unlinked, parent resolution is linear; unmeasured totals are None |
| 1.23.1 | Integrity before replace (F2b) | an atomic rewrite whose temp file does not hold what was written replaces nothing; a new index that fails `PRAGMA quick_check` keeps the previous one |
| 1.23.0 | Symbols with their place (L3) | `find_symbol` returns `breadcrumb` (file > class > method); `eos.db` has a `symbol` table with `parent_id` (schema 8, rebuilt on the next `eos index`) |
| 1.22.0 | Honest numbers (L4) | the cost reports mark estimated numbers `~` and unmeasured ones `—`, never 0; their JSON says which field is which (`provenance`) |
| 1.21.1 | Review of C3b and the rotation fix | narrowing ranks spans by their rarest term, claims only the terms it kept and counts what it left out; the rotation fix also holds without fcntl |
| 1.21.0 | `eos cost --sessions` (C7b) | per session, what EOS delivered against what entered the model's context and what it re-read; unmeasured halves print `—` |
| 1.20.1 | Ledger read during a rotation | a reader that caught a rotation between its rename and the next append read the ledger as empty; it now reads it again under the lock |
| 1.20.0 | Narrowed note bodies (C3b) | with a task, a finding longer than 1,200 characters enters `get_context` as the spans its terms hit, and says so; other kinds stay whole |
| 1.19.1 | Review of C3a | narrowing stops short of a neighbouring table or fenced block, counts lines as editors do, marks a cut line, and never makes the target section larger than the head cut it replaces |
| 1.19.0 | Narrowing (C3a), `core/notes/` package (F4b) | `get_file` with `terms` returns the file's outline and only the line spans the terms hit (whole tables and fenced blocks, ~600 characters each side); `get_context` narrows a target longer than 12,000 characters to the task's terms instead of cutting its head; `core/notes.py` split with no behaviour change |
| 1.18.0 | Note graph export (G2), replaced-note citations (M1b) | `eos note graph --output` for Graphify's community and visual views; `consolidate` names notes citing a replaced note |
| 1.17.0 | `supersedes` (M1a) | a note replaces another without migrating anything; search and briefs stop offering the old one |
| 1.16.0 | Note graph (ADR-033) | notes linked by citation, lesson, shared scope and ticket; `eos note related`; the brief's `LINKED TO` |
| 1.15.0 | Success-check labels (E3d) | an operational run is verified when its procedure's Success checks (tool and subcommand) ran after its last change; on the host 9 verified, 8 claimed (1.15.1) |
| 1.14.0 | Workspace, read-only (C5a) | the session brief on a ticket branch names that ticket's open work and runs in sibling projects |
| 1.13.1 | Whole-branch review | rotation-safe ledger reads, the verify hint kept for the main session, paraphrase guard ignores quoted evidence, builtins are not tools, clean errors on a broken config |
| 1.13.0 | Fuzzer kept, fewer refusals | `tools/verify_fuzz.py` in the suite (400 commands); `check; exit $?` and read-only `&&` tails count: refusals 27% -> 22%, still 0 false clears |
| 1.12.4 | Differential fuzz | 20,000 generated commands in real bash, check passing and failing: one false-clear shape (after `exit 0`) fixed, then 0 false clears, 0 crashes |

Live-verified in headless sessions: the verify Stop gate (1.9.1: gate once, the
check re-run so it counts; 1.10.2: `&& echo VERIFIED` counts and no gate fires),
the subagent handoff, the subagent citation check, and on 1.11.0 the main
session's citation check (stopped once, the model read the file and corrected it),
and on 1.12.0 the uncounted-check hint (a piped check was named at once, the model
re-ran it countably, no Stop gate), and on 1.13.1+ all of it end to end in one
session. Seven fresh reviews, a differential fuzz and a final whole-branch review ran; every
finding is fixed with a test (ledger rows RV-RV14).

## Adopting it

1. Review `eos-2x` against `main` (a PR on GitHub from `eos-2x`), then merge.
2. In nexus: `git subtree pull --prefix tools/eos eos main --squash`, then
   `bash automation/install-eos-cli.sh` (the global CLI moves to 1.23.3).
3. The nexus side of this work is on the local branch `eos-2x` in the worktree
   `../nexus-2x` (not pushed: the VPN was off). Its own commits: the `verify.toml`
   scopes and their test (4ebb01c), the verified-completion design as built
   (a1b5c7b), the golden-alternates proposal (df70c4e), six read-only scripts
   declared for procedure lint (e7c86c7), the three config scripts recording run
   events (ed42e16), the L2 graph report (145837e); the rest are subtree pulls.
   Cherry-pick them onto `mac` or merge the branch. Its host shell tests pass except
   `test-release-run-recovery` (2 of 8), which touches no file the branch changed.
4. Settings worth turning on in nexus (all off or absent today):
   - `[hooks] handoff_tokens = 400` -- the subagent handoff;
   - `verify.toml` is in the worktree commit; with it the Stop gate is live
     (`[hooks] verify`, on by default);
   - `cite_check` is on by default once the plugin is 1.8+.

## Decided (2026-09-27)

- **F3 labels:** the proposal as written -- the 7 rows judged equivalent carry the
  alternate, and row 11 also accepts the symlink memory note (first place unchanged).
  Host golden set: recall@1 0.879, recall@3 1.0, MRR@10 0.939. The scorer work is
  unblocked.
- **M2:** counters keep moving on every `ok` run. `procedure show` prints how many of
  those were `verified` / `claimed` beside the counter (1.24.0), not enforced. Revisit
  after about two weeks of runs finished on 1.24: every earlier host run finished on
  1.5.1 and carries no label, so a verified-only rule today would freeze every counter
  for want of labels, not of checks.

## Decisions waiting for you

- **C1:** one token estimator at the measured 2.22 chars/token would shorten briefs
  by about a quarter (they are sized at 3.0 today).
- **Procedure texts:** `admin-toolbox`, `review`, `report` are named as tools by three
  procedures and exist nowhere (`eos procedure lint .`).
- **The `automation` verify scope:** replaying 61 past sessions, 21 of the 23 changes
  never checked were wrapper edits (bb.sh, jenkins.sh, generate-env.sh ...) with no
  automation test run. Keep asking, or also accept a live call of the edited wrapper /
  `bash -n` as its check (`nexus-2x .devin/knowledge/nexus/verify.toml`)?
- **L2 graph (ADR-032, proposed):** EOS's graph found 99.4% of the tests affected by a
  change against Graphify's 83.6% (353 pairs, three services), at 0.16 ms against ~1 s.
  Stop building Graphify's per-service graph.json once nothing else reads it?
- **Phase 2 migration (M1, M3, M4):** provenance on every note and moving ~590
  generated notes out of the notes store change the host's corpus.
