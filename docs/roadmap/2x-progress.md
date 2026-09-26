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
| S1 | Verified completion (plan tasks 1-7): `core/verify.py`, hook records, Stop gate, run `verified`/`claimed`, 1.6.0, nexus `verify.toml`, live check | IN PROGRESS | task 1 de1b8e9, task 2 840b9fd, task 3 d414973, task 4 (run labels) |
| F1 | Measured defects: telemetry `rebuilt`/`ok`/median; branch-brief budget; prompt-hook chat false positive; `eos-event` session env; agent template MCP fence; README MCP list; `test_documents_match_reality` widened | TODO | |
| F2 | `core/lib/lock.py` (O_EXCL, pid, stale 10 s, timeout 15 s) and `core/lib/atomic.py` (tmp+fsync+rename+dir fsync) on every read-modify-write; `check-clean` grep gate | TODO | |
| F5 | Ledger discipline: `v` on every line, rotation 8 MB x 4, fold reads all, collapse identical consecutive events | TODO | |
| F6 | `eos note eval`: MRR@10, nDCG@10, worst-first, p50/p90 latency; zero-hit in the denominator | TODO | |
| F3 | One retrieval scorer (`core/retrieval.py`) behind the golden-set gate (recall@3 not lower, nexus recall@1 ≥ 0.80) | TODO | |
| F4 | Split `core/eos.py` into `core/cli/*` with zero behaviour change | TODO | |
