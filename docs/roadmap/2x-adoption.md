# EOS 2.x branch — what landed overnight and how to adopt it

Branch `eos-2x` (from `main` 1.5.1), released on the branch as 1.6.0 → 1.10.2.
Every step: tests first, the full suite green before the commit
(1,123 passed, 1 skipped at 1.10.0), `tools/check-clean.sh` clean. `main` untouched.
The row-by-row ledger with commits is `docs/roadmap/2x-progress.md`.

## What changed, by the question it answers

| Release | Item | What it does |
|---|---|---|
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

Live-verified in headless sessions: the verify Stop gate (re-run on 1.9.1: gate
once, the check re-run so it counts, `verify_after_gate: 1`), the subagent handoff,
the citation check (with `--plugin-dir`).

## Adopting it

1. Review `eos-2x` against `main` (a PR on GitHub from `eos-2x`), then merge.
2. In nexus: `git subtree pull --prefix tools/eos eos main --squash`, then
   `bash automation/install-eos-cli.sh` (the global CLI moves to 1.10.2).
3. The nexus side of this work is on the local branch `eos-2x` in the worktree
   `../nexus-2x` (not pushed: the VPN was off). Its commits: the `verify.toml`
   scopes and their test, the golden-alternates proposal, six read-only scripts
   declared for procedure lint. Cherry-pick them onto `mac` or merge the branch.
4. Settings worth turning on in nexus (all off or absent today):
   - `[hooks] handoff_tokens = 400` -- the subagent handoff;
   - `verify.toml` is in the worktree commit; with it the Stop gate is live
     (`[hooks] verify`, on by default);
   - `cite_check` is on by default once the plugin is 1.8+.

## Decisions waiting for you

- **F3 labels:** `nexus-2x docs/eos-evals/golden/nexus-alternates-proposal.md` --
  which of the 11 near-duplicate answers are equivalent (7 judged so → recall@1
  0.879). The retrieval scorer work waits for this.
- **M2:** should procedure counters move only on `verified` runs?
- **C1:** one token estimator at the measured 2.22 chars/token would shorten briefs
  by about a quarter (they are sized at 3.0 today).
- **Procedure texts:** `admin-toolbox`, `review`, `report` are named as tools by three
  procedures and exist nowhere (`eos procedure lint .`).
- **L2 graph (ADR-032, proposed):** EOS's graph found 99.4% of the tests affected by a
  change against Graphify's 83.6% (353 pairs, three services), at 0.16 ms against ~1 s.
  Stop building Graphify's per-service graph.json once nothing else reads it?
- **Phase 2 migration (M1, M3, M4):** provenance on every note and moving ~590
  generated notes out of the notes store change the host's corpus.
