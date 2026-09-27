# EOS 2.x night 2 -- morning report (branch `eos-2x-0928`)

Stopped at 22:01 on 2026-09-27, not at 08:30: the usage rule fired. The weekly limit
was 36% at 21:39 (the user's `/usage` reading); the status line never runs in the
VSCode session, so the stop was decided by a proxy -- this session's cost-weighted
token total. Without a second reading the budget was 1.0M units after 21:39; at 22:01
it was 2.08M, most of it one background subagent (Sonnet, weighted at Opus prices,
so the real share is likely lower). Nothing ran after the stop.

## What landed

| Commit | Item | What it does |
|---|---|---|
| a413d6d | N1 synonyms | `[notes] synonyms = [["wiki", "confluence"], ...]` in a project's config; each group is one concept in search (weight from notes using any member; naming two members counts once). Off without the table; `notes.rank(..., synonyms=)` for hosts. No host list written. |
| 5932f5b | N2 provenance | `note add --provenance human\|agent\|generated`, `--agent` (implies agent), `--valid-until YYYY-MM-DD`; MCP `add_note` records agent. An expired note leaves search and the context section; `eos consolidate` lists EXPIRED NOTES. Existing notes untouched. |
| 151d9a4 | N3 one estimator | `core/context/budget.py`, 2.22 chars/token (measured) everywhere: task brief 1,500 tok 4,500 -> 3,330 chars; branch brief 800 tok 2,400 -> 1,776; injection 8,100/10,800 -> 6,660/8,880. Briefs and injections get shorter -- the budgets were being overrun by ~35%. |

Suite green at every commit (1,314 tests at N3, 1 skip), `check-clean` clean. The
nexus branch `eos-2x-0928` carries the subtree pull of these commits.

Not started: the review of N1-N3 (RVa), N4 collapse on read, N5 advised-vs-used,
N6 procedure scope, RVb, N7 routing candidates. The review should come first: N1-N3
have had no fresh-agent review.

Decisions taken without asking: `2x-progress.md`, "Decided without asking".

## How to merge

EOS: `git switch main && git merge --ff-only eos-2x-0928 && git push` (main has not moved).
Bump `core/VERSION` on main if nexus should pick it up (`install-eos-cli.sh` compares it).

nexus: in the main checkout, `git merge --ff-only origin/eos-2x-0928` on `mac`, then
`bash automation/install-eos-cli.sh && bash .devin/scripts/ensure-eos-mcp.sh --all`.
The worktree `../nexus-2x-0928` can stay or go -- nothing in it is unpushed.

## For the next long run

- Run it from a terminal (`claude --agent atlas`): the status line works there and
  gives the real weekly percentage; in VSCode it never ran.
- Start a fresh session for it: this one carried ~200k tokens of context, so every
  tool call cost ~20k units of cache read.
- Hand each step to a subagent from the start and keep the main session idle.
