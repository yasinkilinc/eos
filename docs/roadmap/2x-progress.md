# EOS 2.x — overnight progress ledger (branch `eos-2x`)

Source of the steps: the host's architecture report §17 (nexus
`docs/eos-plans/eos-2x-architecture-research.md`) and the approved verified-completion
design and plan (nexus `docs/eos-plans/2026-09-27-eos-verified-completion-{design,plan}.md`).
A 15-minute schedule reads this file: if a step is IN PROGRESS with a live background
worker it waits; otherwise it continues the IN PROGRESS step or starts the first TODO.
Every task is committed with explicit paths and pushed (`origin eos-2x`); host parts go
to the nexus worktree `../nexus-2x` on branch `eos-2x` and are committed locally only
(user, 01:20: the VPN is off -- nexus is never pushed). Stop at 09:00 local.

Rules: tests first; full suite green before every commit; no force push; no branch
deletion; the nexus `mac` checkout and the global `eos` CLI are not touched.

| Step | What | Status | Commits / notes |
|---|---|---|---|
| S1 | Verified completion (plan tasks 1-7): `core/verify.py`, hook records, Stop gate, run `verified`/`claimed`, 1.6.0, nexus `verify.toml`, live check | DONE | task 1 de1b8e9, task 2 840b9fd, task 3 d414973, task 4 795ce46, task 5 1f7f36a+fd2d6cb (gate miss fixed), task 6 nexus-2x 4ebb01c (local), task 7 live: gate blocked once, model answered 'not verified' |
| F1 | Measured defects: telemetry `rebuilt`/`ok`/median; branch-brief budget; prompt-hook chat false positive; `eos-event` session env; agent template MCP fence; README MCP list; `test_documents_match_reality` widened | DONE | F1a telemetry 0643618; F1b chat filler 0c1e345; F1c branch brief 66e588d (nexus 3,142 -> 1,936 chars, 645 tok); F1d MCP names already right, now pinned by a test; F1e eos-event session_env 3ee9228; F1f stale temps bc7c280; README roster pinned by a test. (ai update .md templates: fixed in 1.3.0) |
| F2 | `core/lib/lock.py` (O_EXCL, pid, stale 10 s, timeout 15 s) and `core/lib/atomic.py` (tmp+fsync+rename+dir fsync) on every read-modify-write; `check-clean` grep gate | DONE | core/lib/{lock,atomic}.py; counters, note sections, three log trims, run pointer; 16 concurrent finishes lose none (35/100 unlocked); an empty just-created lock was judged dead -- fixed; gate = tests/test_write_discipline.py |
| F5 | Ledger discipline: `v` on every line, rotation 8 MB x 4, fold reads all, collapse identical consecutive events | DONE | `v` on every line (Python and eos-event); rotation 8 MB x 4 under the ledger lock; the fold reads rotated files oldest first; finish refuses an id never started. Collapse of identical consecutive events deferred: 20% of nexus events repeat, all real wrapper calls at distinct times -- collapsing needs count-aware consumers (with E5 incremental fold) |
| F6 | `eos note eval`: MRR@10, nDCG@10, worst-first, p50/p90 latency; zero-hit in the denominator | DONE | MRR@depth, nDCG@depth (one relevant note: 1/log2(rank+1)), zero_hit, latency p50/p90, misses worst first |
| F3 | One retrieval scorer (`core/retrieval.py`) behind the golden-set gate (recall@3 not lower, nexus recall@1 ≥ 0.80) | BLOCKED (labels) | baseline nexus r@1 0.667 r@3 0.970; misses are near-duplicate notes (sections vs procedures written from them). Rejected: title bigram bonus (nexus r@3 0.97->0.94), light stemming (0.97->0.91). Added: golden `a.md|b.md` alternates. Proposal for the user: nexus-2x docs/eos-evals/golden/nexus-alternates-proposal.md (7 of 11 judged equivalent -> r@1 0.879). Structural unification of the search paths waits for the labels |
| F4 | Split `core/eos.py` into `core/cli/*` with zero behaviour change | DONE | eos.py 3,090 -> 716 lines (bootstrap, hook fast path, main); core/cli/{common,project,notes,work,runs,procedures,route,intel}.py moved verbatim by an AST splitter (helpers go with the one group that reaches them, shared ones to common); all 99 help screens byte-identical; runtime copy works; memory_audit C-06 looks in core.cli.runs |

| M5 | Paraphrase guard on `note add`: word-trigram Jaccard ≥ 0.8 against existing notes | DONE | refused on add, reported on amend; bodies under 20 trigrams not compared; measured first: no pair in the host's 20 stores reaches 0.6 |
| M7 | Session line: `eos brief --session` renders the session's open runs, held work, failing procedures | DONE | `eos brief --resume [--session id]`: open runs with their last event, held work, failed runs with their lesson; the per-session line itself is sessions.jsonl (SessionEnd, since 1.3) |
| C6 | Subagent handoff: `eos brief --for-subagent` <= 400 tokens {task, why, known, do}; optional pre-agent injection (`[hooks] handoff`) | DONE | brief.for_subagent + `eos brief --for-subagent`; `[hooks] handoff_tokens` appends it to subagent prompts. Live (headless, worktree + shim): the Explore subagent's first prompt carried the handoff (wrapper + note). Fixed on the way: updatedInput is the whole tool input -- the routing hook sent only {model} |
| E3a | Citation check at SubagentStop: an absolute path that does not exist or a line past the end of the file stops the subagent once | DONE | core/citations.py; `[hooks] cite_check` (on); SubagentStop now synchronous (async could not block). Over 277 real subagent answers (2,058 references) 47 flag today, mostly files that shrank after the answer (live, the check sees the file the subagent just read); abbreviated `.../` paths are skipped. Live (headless, `--plugin-dir` 2.x plugin): the subagent was told `CLAUDE.md:9999 -- the file has 152 lines`, read the file and corrected it |
| E2a | `eos procedure lint`: step tools not declared / unknown, procedures with no Success | DONE | on the host: 15 procedures, 11 problems -- 8 scripts not declared, 3 unknown tools (admin-toolbox, review, report); typed step fields (in/out/on_failure) and cycles not done |
| C2a | Kind and age on every note a brief names (`[finding, 3d]`) | DONE | ids (`note:<file>`) left out: 60-100 characters each against an 800-token brief |
| E6a | Compact machine artifacts: graph.json, file_cache.json, MCP results without indentation | DONE | measured on one service: file_cache 64.7 -> 42.2 MB, graph 23.3 -> 18.9 MB; `eos graph --output` exports stay indented (a person reads them); graph.json split/pagination not done |
| E4 | Work and runs linked: `run start` joins the one open item whose ticket the branch names; `work show` lists its runs with verified/claimed | DONE | the Stop-hook "ask about both" part is the host gate's |
| L1a | `eos consolidate`: fold-only report -- failing/unrun procedures, lint, notes that read alike (>= 0.6), stale notes, runs open for days, stale work, verified rate | DONE | host: 1 failing, 11 never run, 5 lint, 38 stale notes, 8 stale work, < 1 s; priors, keyword proposals and advised-vs-used not included |
| C7a | `eos cost --context [--since]`: resident context by source from the harness transcripts, checked against cache reads | DONE | host since 09-17: 88 sessions, 14,342 calls, explains 99% of 4.2B cache reads; Bash split into wrapper/raw by the project's own registry. Joining telemetry deliveries per session not done |
| RV | Review of the whole branch by a fresh agent (background); fix what it finds | DONE | 11 findings (03:48): verify false clears, citation false blocks, lock takeover race, placeholder mapping, finish across ledgers, hook robustness, mtime attribution, lint which(), brief rebuilds, alternates spacing, context_cost robustness. All eleven fixed with a test each; the lock is now the kernel's (flock) |
| RV2 | Second review, of the fix commits only (9f11cfb..5793841) | DONE | caf6a71, release 1.9.2. 7 findings (04:14): placeholder bound, lock dir fallback, heredoc cut, too strict segments/quotes/pipefail, finish ledger preference, container paths, mixed-placeholder scopes |
| E2b | Typed steps: `(in:) (out:) (on_failure:)`, lint (unknown input, bad jump, cycles), events placed on steps by tool, `run show` failed step, `procedure show` per-step status (ADR-031) | DONE | on the host's deliver-config-sql-script the last run shows steps 6-7 ok and 1, 3, 4 not seen: generate-config-ids, check-changeset-refs, db-grant-check record no events. Fix after pulling into nexus-2x: an ok run printed `failed at step 6` for a call that failed and then passed -- now only a step whose last event failed, only on failed runs. Host failed runs: 1 of 3 names its step; the other two failed in tools no step names (roadmap acceptance "every failed run" needs the steps' tools to record events) |
| E3b | Verification depth 1: `eos cite` -- references and quotes against the lines they cite (ADR-030 addendum) | DONE | host transcripts: 131 -> 45 flags over 6,019 answers after skipping code-span prose, references and paths as quotes; 1 of 69 quotes in the last 3 days; not wired into the hook |
| C7b | `eos cost --context` charged an image its base64 length | DONE | one screenshot (592k chars) made a session explain 182%; images now cost 1,600 tokens: host since 09-24 explains 97% (was 116%), Read 13.1% (was 27.1%) |
| E5 | Incremental fold | NOT NEEDED (measured) | a full build of a copy of the largest service index (54 MB: 17.8k symbols, 31.7k edges, 48.4k facts) takes 0.86 s, a no-change refresh 0.08 s -- the roadmap's acceptance (< 1 s after one note change) holds without the riskiest engine change. Measured without git history (a copy); re-measure on a live service before revisiting |
| A5 | Offline keyword learner | ALREADY THERE | `eos route --eval` prints keyword suggestions for the dev split (1.2); nothing to add |
| E6b | `graph.json` split / paginated MCP `get_graph` | ALREADY THERE | `get_graph` was removed from the MCP roster (ADR-026); `eos graph --output` exports |
| RV3 | Third review, of caf6a71..7e7acb4 (second-review fixes, typed steps, eos cite) | DONE | 3 findings (05:06), each fixed with a test, release 1.10.2: a check inside `$(...)`/backticks counted (`echo $(false; pytest)`); `bash -c "<check>"` never counted; `finish` crashed on an unrelated ancestor's broken config. Nothing found in the lock fallback, placeholder bounds, misquoted or steps |
| L2 | Graph decision: EOS `impact` vs Graphify `affected` on test/subject pairs, three services | NEEDS DECISION | ADR-032 (proposed): 353 pairs, recall@2 EOS 99.4% vs Graphify 83.6% (49 subjects missing from its snapshot), 0.16 ms vs ~1 s per query. Keep EOS's graph; whether the host stops building graph.json is the user's call. Full report: nexus-2x docs/eos-evals/l2-graph-comparison.md |
| E2c | The task brief says where a failed run failed (`failed at step N` on its LAST RUNS line) | DONE | host: the three config scripts now record events (nexus-2x ed42e16), so deliver-config-sql-script's steps 1, 3, 4 can be placed |
| E3c | The main session's answer checked at Stop like a subagent's: a missing absolute path or a line past the end stops the turn once (`cite_check`) | DONE | the Stop input carries `last_assistant_message` (checked in the 2.1.283 bundle's schema) |
| L1b | `eos consolidate`: steps whose tool no run of the procedure ever recorded | DONE | host: 11 steps in 4 procedures -- commit-and-push names raw `git` (never recorded), deliver-config-sql-script 1/3/4 (fixed for future runs by nexus-2x ed42e16) and 5 (admin-toolbox is a screen), open-a-pr `review`/`pre-pr-check`, run-a-scenario `obs` |
| RV4 | Fourth review, of 5a5774b and aa34917 (brief step line, main-session citation check) | DONE | no defect reproduced: once-rule, verify/cite interaction (both reasons in one block, each asked once), procedure-scoped run lines; 1,738 main-session answers of the last 2 days flagged 0 (their references are relative, found or left alone) |
| S1b | The verify gate measured on the host's history, and eased where it was wrong | DONE | replay of 61 sessions: 36 of 37 with scoped changes would be stopped (47 instances: 23 never checked, 24 checked in an uncountable form). Now: `/bin/bash`-style prefixes pass through; under `set -e` a check ending its `&&` chain counts; a check run uncountably gets a hint at once (once per session) |
| RV5 | Fifth review, of 7a49d29 (errexit rule, attempted, hint) | DONE | 4 findings, each fixed with a test, release 1.12.1: `set -e` wrongly reached `if`/`while` conditions and unexecuted function bodies; `set +o errexit` did not turn it off; a quoted check counted as an attempt. Found alongside: a subshell's `set -e` leaked to the outer shell -- subshells are now tracked by depth; `set +o pipefail` honoured |
| RV6 | Sixth review, of 22b4b41 (subshell depth, compound guard) | DONE | 3 findings, release 1.12.2: under `set -e` a check in a block that may never run (if false / empty loop / uncalled function / case) counted -- blocks are now tracked and never counted; 600 nested parens raised RecursionError -- capped at 20; a check in a block that does run is refused (kept as a known limitation, the safe direction) |
| M1/M3/M4/M6 | Provenance on every note, generated store, journey fan-out, priors | BLOCKED | M1/M3/M4 migrate the host's notes (~590 files) -- a person decides; M6 needs F3 |
| M2 | Counters moved only by verified outcomes | NEEDS DECISION | 1.6 labels ok runs verified/claimed from evidence; whether procedure counters move only on verified is a policy call |

## Found along the way

- `eos run finish <unknown id>` succeeded and wrote a finish line for a run never started
  (seen while testing F1a) -- fixed in F5.
- A session in the nexus worktree loads the plugin's hooks.json from the main nexus checkout's
  `tools/eos` (the marketplace is registered once); the `eos` command on PATH decides the code.
  Live tests of hook-registration changes need `--plugin-dir` (seen testing E3a).
- Live on 1.9.1 the Stop gate fired but the model's re-check ('... | tail -5; python3 ...') did not count:
  a piped check with ';' is not the command's exit status. The gate's reason now says how to run it;
  re-run live: gate once, check run countably, sessions.jsonl verify_gates 1 / verify_after_gate 1.
- Live on 1.10.2 (headless, nexus-2x, `--plugin-dir`): an edit left unchecked was stopped once and the
  model answered "not verified" (verify_gates 1); the same edit followed by
  `bash automation/tests/test-routing-corpus.sh && echo VERIFIED` -- refused by 1.9.1's rule -- counted,
  and the turn ended with no gate (verify_gates 0). $0.27 for both.
- Install checked for the morning's adoption without touching the global CLI: `bin/install.sh` from the
  nexus-2x subtree with EOS_DATA_DIR/EOS_BIN_DIR in a scratch prefix installs 1.10.2; `eos cite` and
  `eos procedure lint` answer from it. The global `eos` stays 1.5.1.
- Live on 1.12.0: an edit, then the check piped into `tail` -> the PostToolUse hint named the check and
  the form that counts; the model re-ran it on its own; the turn ended with no Stop gate
  (verify_gates 0, hinted verify-form). $0.16.
- Host shell tests in nexus-2x on 1.12.0 (26 scripts, two live/install ones skipped): all pass except
  test-release-run-recovery (2 of 8: "retry left it failed", "re-plan lost history"). It reaches no EOS
  code and no file this branch changed; not compared against `mac`, which stays untouched.
