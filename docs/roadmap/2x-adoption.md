# EOS 2.x branch — what landed overnight and how to adopt it

Branch `eos-2x` (from `main` 1.5.1), released on the branch as 1.6.0 → 1.23.3.
Every step: tests first, the full suite green before the commit
(1,169 passed, 1 skipped at 1.13.4), `tools/check-clean.sh` clean. `main` untouched.
The row-by-row ledger with commits is `docs/roadmap/2x-progress.md`.

## What changed, by the question it answers

| Release | Item | What it does |
|---|---|---|
| 1.35.0 | `verify_depth`'s Stop-gate consumer (A2) | When the Stop verify gate has already fired for another reason, a run following a procedure routed at depth 2 gets one more line naming that procedure's own read-only `## Success` checks -- `eos verify --procedure <slug>`. Depth 0/1 and a session with no routed decision are byte-identical to before: the hint is additive to an already-fired gate, never a trigger of its own |
| 1.35.0 | `eos route --learn` (A5) | Proposals only, never written to config: a (type, level, model, effort) whose finished routed runs fail or are abandoned at a high rate, and a level whose advised model is consistently overridden by the model actually used. Below a handful of runs it says "too few runs" rather than guess a rate |
| 1.35.0 | `[ai] loaded` dedup against the harness's own context (C4) | Project-relative globs naming files the harness already puts in context every turn (`CLAUDE.md`, `.claude/rules/*.md`, ...). A note whose whole body already sits, word for word, inside one of them is left out of the PostToolBatch injection and the Accumulated Knowledge section, and each says how many it left out. Unset, nothing changes |
| 1.35.0 | `eos cost --context`'s saved-vs-baseline column (C7) | A source this repo's own baseline measured before (`hook: PostToolBatch`, `hook: UserPromptSubmit`) carries a `vs baseline` line: today's median tokens/session against the one-time reading, `~N% saved`. Below 5 sessions with that source, no percentage is printed |
| 1.34.0 | `[model_routing] default_task_type` (A1) | The type a task gets when no keyword table matches any of its words, `normal_implementation` unless set; a project picks it on its own corpus's dev split (ADR-025 addendum). Nothing else in the decision changes |
| 1.33.1 | A metadata-only `note amend` changes only its fields (M1) | `--provenance`/`--agent`/`--valid-until` alone rewrite just those front-matter lines: `updated` keeps the day the content last changed and `session` the session that last wrote the note, so marking a whole store's provenance neither resets every note's age nor claims them for the migrating session. An amend with `--body`, `--reaffirm` or `--scope` stamps `updated` as before |
| 1.33.0 | `eos verify --procedure`: EOS runs a procedure's read-only Success checks (E3 depth 2) | A `## Success` bullet marked `(read-only)` -- and only that one, never guessed from the command text -- can be run by EOS itself, not just recorded after someone else ran it. `eos verify --procedure <slug>` runs every marked check via `subprocess.run` on its parsed argv (never a shell), records a `verified` execution-ledger event on completion, and lists every unmarked check without running it. A digest of the output is always kept, the last 500 characters only when a check did not pass |
| 1.33.0 | `verify_depth` on the routing envelope (A2) | `eos route`'s `Decision` gains `verify_depth` (0/1/2), derived from `level` alone: LOW 0 (record only), MEDIUM 1 (structural checks), HIGH/CRITICAL 2 (run the procedure's Success check). Printed in `eos route`'s text and JSON; a decision reused inside an open run recomputes it from the reused level. Nothing else consumes it yet |
| 1.33.0 | The capability six-state truth model, `verify_before_use` (A4) | `eos capabilities --status <name>` reports where a declared capability stands: catalogued/registered/configured/reachable/healthy/authorized -- read-only throughout, nothing executed to find out. `healthy` always reads `unknown` (EOS records no live health signal); a capability's own `verify_before_use` marker (`capabilities.toml`) holds it at `reachable` instead of `authorized` until one exists |
| 1.33.0 | C4/C7 measured against the report, no behaviour change | What ADR-027's context diet covers of C4's "dedup against the harness's own always-loaded files" (self-repeat suppression of EOS's own output only; no such dedup exists) and what a saved-vs-baseline `eos cost` column would need (a channel-to-source mapping this pass measured but did not build) -- against the host's own baseline records, read-only. `docs/roadmap/2x-progress.md`'s "C4/C7 measurement" has the numbers |
| 1.32.0 | Working-tree capture beyond HEAD moves (E1, D1) | A new PreToolUse:Bash hook snapshots the working tree before a mutating git call (`git status --porcelain=v1 -z`); PostToolUse diffs it and records a `changed` event for `stash pop/apply`, `checkout -- <path>`/`restore <path>`, `reset --hard` to the same HEAD, and `clean` -- none of which move HEAD, so the existing HEAD-diff capture could not see them. Fails closed throughout (no git, no repo, a timeout -- no event, no exception). Also fixes a race where a subagent's first git call could inherit the main session's HEAD baseline and misattribute a commit |
| 1.32.0 | `consolidate` batches its git subprocesses (D2) | `change_capture` no longer spawns one `git diff` plus one `git log` per finished run; both are batched into a small, constant number of `git` calls (`git diff-tree --stdin`, one `git log` building a commit graph walked in Python) with identical results. Measured on a synthetic 300-run ledger: 10.469s -> 0.107s |
| 1.32.0 | `consolidate` reports a stale generated index (L1, D3) | `eos index .`'s own declared freshness key (`inputs_sha256` vs. a fresh sources digest) is checked without rebuilding; `eos consolidate` names `.eos/data/eos.db` when it no longer matches. The brain/graph files a scan writes declare no equivalent freshness key of their own, so their staleness is not reported (not defined in the codebase) |
| 1.32.0 | `eos brief`/`procedure show` name where a procedure's steps run (C5c, D4) | For a project that declares `[workspace] root` and is listed back by that workspace's `projects.toml`, both now state the workspace root's absolute path next to a procedure's steps -- a session in a service directory previously had no way to tell a step's relative paths resolve from the workspace root rather than its own cwd |
| 1.32.0 | Review of D1-D2 (RVf) | `git diff-tree --stdin` (D2's batched path) cannot tell an unresolvable ref (a rewritten or pruned commit) from a genuinely empty diff -- both print nothing, exit 0 -- unlike plain `git diff A..B`, which fails loudly for the former. Fixed with one `git cat-file --batch-check` call validating every ref before batching; a pair naming a bad ref is now `None` ("unmeasurable"), not silently `0 files` |
| 1.32.0 | Review of D3-D4 (RVg) | `index.is_stale()` (D3) took `eos consolidate` down with a raw traceback on an invalid `[index] ticket_pattern`, despite promising a pure read that never raises; fixed inside `is_stale()` itself. The new workspace-root line (D4) had no length cap, unlike a procedure's rules, so an unusually deep workspace path could blow past the whole task budget; capped at 400 characters |
| 1.31.3 | Review of N12 (RVe) | `consolidate._has_hook_event` counted any `source="hook"` event as proof the capture-relevant hook had a chance to fire, but one of `core/hooks.py`'s ten `_record` call sites -- `_route_subagent`'s `decided` event (a subagent routing decision) -- opens the run and records unconditionally, unlike every other call site which all gate on `cfg["capture"]`. A run whose only hook-sourced event is a routing decision (capture off, routing on) was therefore misclassified as having real capture-hook activity. Fixed: `_has_hook_event` now excludes `decided` events. Checked and confirmed correct, not fixed: a source-less `changed` event can never be a hook event -- `core/hooks.py` (the only writer of `source="hook"`, always hardcoded) did not exist before the `source` field itself (both landed together in the commit that introduced the field, release 1.3.0), so nothing wrote a hook event before the field existed, and nothing since writes one without it; N12's "known limitation" about the host's 2 source-less `changed` events is not a limitation, the exclusion is provably right. Re-measured on the host: unchanged, 16/40 (40%) over 7 runs with commits, 61 unmeasurable (38 without hook events) -- no run in the host's history hit the routing-only edge case, but the fix closes a real future misclassification | `change_capture`'s exclusion of another run's finish commit from `touched` counted a run that finished idle (no commit of its own, `commit_start == commit_end`) as if its `commit_end` were real foreign work; when that HEAD was really an earlier, still-open run's own intermediate commit, the earlier run's own uncaptured file silently vanished from `touched`, overstating capture. Fixed by dropping an idle run's `commit_end` from the exclusion set. Re-measured on the host: N11's own 18/201 (8.96%) undercounted by 4 files; now 18/205 (8.78%) |
| 1.31.1 | Review of N8-N10 (RVc) | A metadata-only `note amend` (`--provenance`/`--agent`/`--valid-until` alone) fell into the scope re-hash path and refused whenever a scoped file had changed for unrelated reasons, though nothing about scope was asked to change (N8); `note add --evidence` skipped the credential and placeholder guards `--body`/`--source`/`--title` get, so a pasted credential or an unfilled `<...>` placeholder was written verbatim (N10); `consolidate.touched_files`'s `git diff --name-only` collapsed a pure rename to its new path alone whenever the caller's `diff.renames` config was on, undercounting `touched` non-deterministically (N9). Fixed with a test each |
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
