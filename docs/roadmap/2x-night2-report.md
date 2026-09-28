# EOS 2.x night 2 -- morning report (branch `eos-2x-0928`)

2026-09-27 21:15 -> 2026-09-28 ~05:35 local. N1-N11 and four reviews (RVa-RVd) are
all DONE; releases went out from 1.29.0 to 1.31.2. The one measured gate that
matters most, E1's change-capture share, is still far under target: 18/205 files
(8.78%) against >= 95%, and the fix that raised it from 4% is forward-only -- it
cannot repair the host's already-finished runs. A four-and-a-half-hour permission
stall (23:33-03:55) ate a large share of the night. Nothing destructive happened;
`main`, `mac`, the old `eos-2x` branch and the global `eos` CLI were not touched.

## What landed

| Commit | Item | What it does |
|---|---|---|
| a413d6d | N1 synonyms | `[notes] synonyms` in a project's config; a group is one concept in search (weight from notes using any member, not the rarest member's). Off without the table; `notes.rank(..., synonyms=)` for hosts. No host list written. |
| 5932f5b | N2 provenance | `note add --provenance human\|agent\|generated`, `--agent`, `--valid-until`; MCP `add_note` records agent `agent` with no name. Expired notes leave search/context; `consolidate` lists them. Existing notes untouched (forward-only). |
| 151d9a4 | N3 one estimator | `core/context/budget.py`, 2.22 chars/token everywhere chars/3 or chars/4 was guessed before. Task brief 1,500 tok: 4,500 -> 3,330 chars; branch brief 800 tok: 2,400 -> 1,776; injection 3,000/4,000 tok: 8,100/10,800 -> 6,660/8,880. |
| 51c2951 | RVa (review of N1-N3) | 3 findings, each with a test: `note amend` dropped provenance/agent/valid_until on rewrite; the subagent handoff's literal word filter silently dropped a synonym-matched note; body narrowing missed the same synonym-matched hit. Released **1.29.0**. |
| 5a684b2 | N4 fold on read | `run show` folds identical consecutive events into one `xN` line; ledger stays append-only (fold key excludes `ord`/`at`). `--format json` untouched. |
| 11eb5d2 | N5 advised-vs-used | `route --stats` joins the routed decision, the model the subagent actually ran, and the run's outcome (verified/claimed); unmatchable sessions print `unknown`, never a guessed match. |
| 471b2e6 | N6 scope at finish | `run finish` warns (never refuses) when changed files land outside the procedure's declared `scope` (exact match, directory prefix, or glob). |
| c9aa5f2 | release | **1.30.0** -- N4-N6. |
| f5725e2 | RVb (review of N4-N6) | 3 findings, each with a test: N5 compared the transcript's raw model string against the router's canonical id without the alias table, so an exact match counted as different; N5 joined a run to its *first* `decided` event instead of its latest, mis-attributing re-routed runs; N6 read the procedure/changed-refs from the CLI's own path instead of the run's own project, so a workspace-root `run finish` on a routed run printed no warning at all. Released **1.30.1**. |
| 1f2a525 | N8 (RVa follow-up) | `notes.procedures()`/`best_procedure` now also skip expired procedures (not just replaced ones); `run start --procedure` refuses an expired one by exact slug; `note amend` gains `--provenance`/`--agent`/`--valid-until`. |
| 2ccaa25 | N9 change-capture measurement | `consolidate.touched_files`: `git diff --no-renames --name-only start..end` per finished run vs the `changed`-event refs it recorded; `None` ("unmeasurable") on a missing commit or a no-commit run, never a false 0. `consolidate` prints `CHANGE CAPTURE c/t files (share) over N runs with commits; M unmeasurable`. **Host: 18/454 (3.96%) over 45 runs with commits, 23 unmeasurable.** |
| 7c50e0b | N10 lesson evidence | `note add --evidence` (lessons only); `amend_note` carries it through unchanged; brief tags an evidence-less lesson `[no evidence]`; `consolidate` lists them. Host: 1 lesson lacks both `evidence` and `execution`. |
| d3907ba | release | **1.31.0** -- N9-N10. |
| c2259ce | RVc (review of N7-N10) | 3 findings, each with a test: a metadata-only `note amend` (only the new N8 flags) still fell into the scope re-hash path and could refuse spuriously; `note add --evidence` skipped the credential/placeholder guards `--body` already has; `touched_files` undercounted pure renames non-deterministically depending on the machine's `diff.renames` setting -- fixed with `--no-renames`. Released **1.31.1**. Noted, not fixed: `consolidate` on a synthetic 300-run ledger takes 3.46s (one `git diff` subprocess per finished run). |
| 6ffd966 (nexus) | N7 routing candidates | `docs/eos-evals/golden/routing-candidates.tsv` (nexus): 260 real prompts with a proposed label and the router's prediction. 242 disagree, but 206 of those are the router falling back to its default class (`normal_implementation`/MEDIUM) on short Turkish prompts with no lexical signal -- English prompts route correctly in the same run. Genuine disagreements (~36): `code_review` for plainly trivial/debug prompts (~15), `test_generation` inflated LOW->MEDIUM (~13), general LOW->MEDIUM inflation. 48 prompts dropped for privacy (names, a credential, test probes, email/phone/JWT shapes). Known gap recorded separately: nexus `docs/eos-evals/routing-tr-proposal.md`. Used by nothing yet, as planned. |
| 6e4a7bd, 9f75566, c3ae2d3 | N11 change-capture gap, classified and partly fixed | Classified all 436 missed file-instances behind N9's 18/454: (a) 183 -- a git-mutating **Bash** call (subtree pull, merge, checkout, script write) moved HEAD with zero `changed` events, because only Edit/Write/MultiEdit/NotebookEdit ever recorded one; (b) 253 -- measurement noise, another run's own finish commit or a subtree-squash commit's files landing inside this run's git range; (c) 0 unattributable. Fixed (a) in recording: `core/hooks.py` now diffs the session's last-seen git HEAD against HEAD after every git-mutating Bash call and records one `changed` event per file (tool `bash`), capped at 200/call -- **forward-only, cannot add events to the host's already-finished sessions**. Fixed (b) in measurement: `consolidate._foreign_files` drops a file from `touched` only when *every* commit naming it is foreign to the run (another run's own `commit_end`, or a subtree-squash commit reconstructed from its own `Squashed '<prefix>' ...` message) -- `captured` can only shrink `touched`, never inflate the share. Re-measured (measurement fix only): **18/201 (8.96%)**, same 45/23. No version bump (N7/N8/N11 did not release, matching N1-N10's pattern; only reviews do). |
| dda1290 | RVd (review of N11) | 1 finding, with a test: `change_capture`'s foreign-commit set held every finished run's `commit_end` with no check that the run actually made a commit; an idle run's `commit_end` (== `commit_start`, i.e. "no commit made") could really be an *earlier, still-open* run's own intermediate commit, so once that earlier run finished, this run's own genuine uncaptured file was wrongly excluded as "foreign" -- overstating its capture share. Fixed by excluding a run's `commit_end` from the foreign set only when `commit_start != commit_end`. Re-measured: **18/205 (8.78%)**, same 45 runs with commits, 23 unmeasurable -- this is the corrected, current number (201 was 4 files short). Checked and found already safe: the squash branch's missing `!= commit_end` guard looked like the same bug class but is unreachable (a real `git subtree pull --squash` always lands as a merge parent, never a run's own commit tip). Released **1.31.2**. |
| (N12, see progress ledger) | N12 no-hook-events runs excluded from the share | A finished run with real commits but zero hook-recorded events (`x-context-budget-b-f689` was one) is measurement noise, not a real 0-captured run. `source == "hook"` (set only by `core/hooks.py`'s one `_record` call site) reliably tells hook-recorded events from CLI/wrapper ones; `change_capture` now buckets such a run into a new `runs_unmeasurable_no_hook_events` count instead of folding it into `captured`/`touched`. Render: `CHANGE CAPTURE c/t files (share) over N runs with commits; M runs unmeasurable (K without hook events)`. Re-measured: **16/40 (40%) over 7 runs with commits; 61 unmeasurable, 38 of them for zero hook events** -- 38 of RVd's 45 "measurable" runs, not the one this row's premise named, never had the harness's hook fire at all. No version bump. |
| bc1c0d2 | RVe (review of N12) | One finding, fixed with a test: of `core/hooks.py`'s ten `_record` call sites, nine open the run through `cfg["capture"]` -- only `_route_subagent`'s `decided` event (a subagent routing decision) records unconditionally, by design, so N5's advised-vs-used report keeps working when a project turns `capture` off. `_has_hook_event` counted that "decided" event the same as real capture activity, so a run whose only hook evidence was a routing decision would have been misclassified as measurable rather than no-hook-events. Fixed: `_has_hook_event` excludes `decided`. Checked and found already correct, no fix needed: whether a source-less `changed` event is safe to treat as non-hook (N12's own open question) is provable, not just likely -- `core/hooks.py` and the `source` field were introduced together in one commit (released 1.3.0), so no hook event anywhere in the ledger's history can lack a source, in either direction of time; N12's "known limitation" about the host's 2 source-less `changed` events is not a limitation. Totals were checked to reconcile (`runs_with_commits + runs_unmeasurable` always equals every finished run with an outcome) and `captured`/`touched` cannot be inflated (per-run set arithmetic, summed as integers, no cross-run dedup claimed). Subagent hook events and C5b workspace-vs-service ledger routing were re-checked against `_has_hook_event` and found safe, the same conclusion RVd already reached for `touched_files`. Released **1.31.3**. Host re-measured, read-only: unchanged, 16/40 (40%) over 7 runs with commits, 61 unmeasurable (38 without hook events) -- no run in the host's own history carries a `decided`-only hook event, so the fix changes nothing measured tonight but closes a real future misclassification. |

Releases this session: **1.29.0, 1.30.0, 1.30.1, 1.31.0, 1.31.1, 1.31.2, 1.31.3** (current `core/VERSION`). N7, N8, N11 and N12 did not bump the version, matching the pattern from earlier in the branch where only review (RV) steps release.

## Measured numbers (source in parentheses)

- Change capture (E1 gate, target >= 95%), host, read-only, all over the same 45
  runs-with-commits / 23-unmeasurable set:
  - Baseline (N9): **18/454 (3.96%, "~4%")**.
  - After N11's recording+measurement fix (measurement side only -- the recording
    fix is forward-only and cannot touch these already-finished runs): **18/201 (8.96%)**.
  - After RVd's correction (an idle-run exclusion bug in the measurement fix
    itself): **18/205 (8.78%)**, over 45 runs with commits, 23 unmeasurable.
  - After N12 (excluding runs with real commits but zero hook-recorded events
    from the share, counted separately instead): **16/40 (40%)**, over 7 runs
    with commits; 61 unmeasurable, 38 of them specifically for no hook events.
    n=7 is too small to trust as a rate, but it is no longer diluted by 38
    sessions the hook never had a chance to instrument -- this is the honest
    current number.
  - After RVe (a `decided`-only routing event no longer counts as capture-hook
    evidence): unchanged, **16/40 (40%)**, over 7 runs with commits; 61
    unmeasurable, 38 without hook events -- no run in the host's history hit
    the routing-only edge case RVe fixed, so the number does not move, but the
    fix closes a real misclassification a future capture-off/routing-on
    project would hit.
  - All five read directly off `python3 core/eos.py consolidate <host> --format json` (N9/N11/RVd/N12/RVe).
- N7 routing corpus (nexus 6ffd966): 260 candidates, 242 disagreements, 206 of
  them the router's Turkish-prompt blind spot (no signal, not a real defect),
  ~36 genuine misclassifications, 48 dropped for privacy.
- RVc: `consolidate` on a synthetic 300-run ledger: 3.46s (one `git diff` subprocess
  per finished run) -- noted as a cost, not treated as a defect.

## Decisions taken without asking

Full list with reasons: `docs/roadmap/2x-progress.md`, "Decided without asking"
(N1-N11 section). Condensed:

- N1: a synonym group's weight counts notes using *any* member, not the rarest
  member's; groups are never merged across a shared word.
- N2: no `valid_from` (`created` already says it); a past `--valid-until` is
  refused on add, never silently hides a note; MCP's `add_note` records
  provenance `agent` with no name.
- N3: `brief.py`/`inject.py` keep `CHARS_PER_TOKEN` as a re-export of
  `budget.CHARS_PER_TOKEN` rather than rewriting every importer.
- N5: "used" model prefers the session's subagent-model tally over the main
  session's model, since a routed decision applies to the subagent call.
- N6: a scope entry matches a changed ref by exact path, directory prefix, or
  glob -- not the parent-link resolution `_scope_anchor` uses for a note's own
  scope files.
- N8: all three existing callers of `notes.procedures()` keep `include_expired=True`
  so no existing command's output changes; the new filtered default has no
  caller yet, left there for `best_procedure`-shaped matching to opt into.
- N9: a run's commit *range* is diffed, not its individual commits -- on a
  shared branch this can include another session's commits, inflating
  `touched`; a run that made no commit is "unmeasurable", never a confident 0.
- N10: `--evidence` is refused by kind name (lessons only), matching how
  `defect`'s required fields work; `amend_note` carries `evidence` through
  every rewrite unchanged, since dropping it on the next `--body` edit would
  be a worse regression than not having the field.
- N11: `BASH_CHANGED_LIMIT=200` per call; `_foreign_files` only ever shrinks
  `touched`, never inflates `captured`'s share; the subtree-squash prefix is
  read from the squash commit's own message, not a config value, so it
  generalizes to any subtree mount including this host's own `tools/eos`.
- RVd: the idle-run fix filters at `change_capture` (where each run's
  `commit_start` is in hand), not inside `_foreign_files`; a timestamp-window
  alternative was tried and reverted -- a run's own window necessarily spans
  nearly every commit in its own git range, so it kept exactly the commits
  N11 meant to exclude (regressed the existing concurrent-session test).

## Stalls and usage

- **23:33 -> 03:55**: the N9/N10 background subagent sat on a permission
  prompt for shell loop constructs (`while`/`until`/`sleep`/`tail`/`wc`).
  Allow rules were added to nexus's `.claude/settings.local.json` at 03:58.
  A wakeup attempt at 00:19 did not unstick it -- roughly 4.4 hours lost.
- The status line never ran in the VSCode session used for most of the night,
  so weekly usage was tracked by a cost-weighted token proxy until a real
  reading was available. Readings (`~/.claude/usage/rate-limits.json`
  `seven_day.used_percentage`, all from the status line once it started
  running):
  - 21:39 = 36% (proxy: 2.11M units)
  - 23:15 = 37% (proxy: 11.47M units)
  - 03:55 = 39% (proxy: 18.45M units)
  - 04:33 = 39%
  - 05:04 = 40%
  - 05:35 = 40%
  - **06:53 (fresh read, `at` 1790567615) = 40%** -- unchanged since 05:04/05:35,
    not stale (read at the same time as this reading's own `at` timestamp),
    `resets_at` 2026-10-01 15:00 local.
- At 04:35 the work moved from the VSCode session to a terminal session,
  where the status line does run -- explaining why real percentage readings
  start appearing from 04:33 on, replacing the proxy.
- **MR stall (this step)**: the agent finishing this row waited on a
  background pytest run from 05:41 to 06:53 with no output and was stopped
  and restarted; this rewrite and the remaining MR steps run in the foreground
  from 06:53 on.
- No stop was ever forced by the 48%/50% usage rules; the branch finished on
  its own at N11/RVd, well under the ceiling.

## Open items / limitations

- **E1's own gate (>= 95% change capture) is not met**: 8.78% is the honest
  number and the recording fix that raised it from 4% cannot repair history --
  only sessions run after 1.31.1/1.31.2 benefit.
- One of the host's 45 measurable runs (`x-context-budget-b-f689`, 32 touched
  files) recorded zero events all session despite real commits -- the harness
  never invoked the EOS hook there at all; not something an engine code path
  can fix. Concrete proposal left in N11's row: audit hook registration across
  nested worktrees/workspace project directories, and/or have a CLI-only
  backfilled run mark itself unmeasurable.
- The Bash-HEAD-diff mechanism (N11) structurally cannot see `git stash pop`,
  `git checkout -- <path>`, or `git restore <path>` -- none of these move
  HEAD. A real fix needs a working-tree diff around every Bash call, which is
  materially bigger than this pass's scope.
- The per-session git-HEAD baseline is shared between a main session and every
  subagent it spawns, with no locking -- two truly concurrent git-mutating
  Bash calls (main + subagent, or two subagents) can race and split/misattribute
  a diff. Failure direction is a miss, not an overclaim (same safe direction as
  N9's own session-vs-commit-authorship limitation); left as a known limitation.
- N7: router still defaults with no signal on short Turkish prompts (206 of
  242 "disagreements"); tracked separately, nexus `docs/eos-evals/routing-tr-proposal.md`.
  Not used by anything yet, per the user's 21:10 default ("A1 candidates
  proposed into a file, used by nothing until approved").
- F3 `synonyms` (N1) stays off by default and engine-only, per the user's
  21:10 default -- no host synonym list was written.
- M1/M3/M4 (provenance migration of ~590 existing host notes, generated
  store, journey fan-out) remain BLOCKED/not started -- forward-only per the
  user's 21:10 default; a person decides on migrating old notes.
- L2 (EOS `impact` vs Graphify `affected`) is still a NEEDS DECISION -- ADR-032
  proposed, status quo kept.
- EOS run `x-eos-2-x-ce9f` (nexus worktree) was already closed before this
  session started work: `outcome ok`, finished 2026-09-27T19:02:17Z. Nothing
  to finish; confirmed by `run show` in the nexus worktree, not re-opened.

## Why 38 host runs had no hook events (read-only, 08:50)

Confirmed from the host ledger and `.claude/settings.json` history: the EOS plugin --
the only thing that writes `source="hook"` events -- was enabled in the host on
2026-09-26 14:38 UTC (first hook event in the ledger at 14:38:49, in the plugin's own
verification run) and replaced the host's own hooks on 09-27. By start date:
09-24 0/12 runs with hook events, 09-25 0/30, 09-26 2/18, 09-27 8/8. Runs started
later on 09-26 in sessions opened before the change stayed hookless: a plugin
enabled mid-session is picked up only by a new session. Not Devin, not a `capture`
setting. Nothing to backfill; `consolidate` already labels these runs unmeasurable.
Still open: a session opened with a service repo as its project root gets no
plugin (those repos carry no `.claude/settings.json`); the host's rule is that
sessions start in the workspace root, so no change was made.

## How to merge

**EOS** (this repo): `main` has not moved since this branch forked.

```
git switch main
git merge --ff-only eos-2x-0928
git push origin main
```

**nexus** (main checkout, not the `nexus-2x-0928` worktree): the worktree's
`eos-2x-0928` already carries tonight's subtree pull, committed and pushed.

```
git fetch origin
git switch mac
git merge --ff-only origin/eos-2x-0928
```

The worktree `../nexus-2x-0928` can stay or be removed once `mac` has the
merge -- nothing in it is unpushed. The global `eos` CLI is untouched by any
of this (still whatever version it was before tonight); re-run
`automation/install-eos-cli.sh` in nexus only if `mac` should pick up 1.31.2.
