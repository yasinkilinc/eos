# ADR-027: The context diet — admit what is worth its re-reads, let the rest go

## Status

Accepted, 2026-09-26. Extends ADR-026 (the plugin's hooks); measured against
Claude Code 2.1.283.

## Context

One workspace, 87 main sessions (2026-09-17 to 09-26), priced from the
transcripts' own usage: the main session is 78.7% of spend, and 79.4% of that
is cache reads — the context re-read by every call. Mean context per call is
289k tokens (p90 624k); 12 compactions in 92 sessions. Charging every item that
entered a session with tokens × the calls it stayed for reproduces the recorded
cache reads (3.28B items + 0.71B first-call base ≈ 4.00B):

| Source | Share of cache reads |
|---|---:|
| tool inputs the model wrote (commands, Write/Edit bodies, inline scripts) | 20.5% |
| system prompt, tool schemas, first context | 18% |
| Read results (three quarters whole files) | 13% |
| raw shell output (`sed -n`/`cat`/`head` reads, grep, scripts) | 13% |
| wrapper output | 10.5% |
| instructions, skill listing, changed-on-disk notices | 10.9% |
| EOS brief and notes | 1.0% |

So the cost is size × duration, and what EOS injects is not where it goes.
Model routing touches 2.1% of spend (untyped subagents); five context tools
read at source (RAGFlow, two context optimizers, two rolling-context tools, a
context manager) either bound a short-lived loop, act on a closed transcript,
rewrite live traffic through a proxy, or report unmeasured savings. The
harness offers what a hook can use: a `PreToolUse` deny with a reason the model
reads, `PreCompact` stdout appended as compact instructions, `PostCompact` with
the summary, `SessionStart` with `source: compact`, and the transcript path on
every event.

## Decision

Two directions, each through a hook, each deterministic.

**Admit what is worth its re-reads.**
- `pre-read`: a main-session `Read` with no range of a file longer than
  `[hooks] outline_lines` is denied once with the file's outline
  (`core/outline.py`, headings and declarations with line numbers) and its
  length; the same Read again passes. Off (0) by default. A warning that let the
  read through would add the outline to the context and save nothing.
- A reading task's ROUTE block gains a `Context:` line: explore in a subagent,
  whose reads end with it (investigation, code review, repository-wide change;
  not LOW).
- `post-tool`: files the session read are watched by mtime; a shell command
  that rewrites one gets one hint per session naming it (the harness sends a
  changed file back). The Edit tool moves the baseline.

**Let the rest go at the right moment.**
- `pre-compact`: the compact instructions name what must survive — the open
  run and its procedure, work held, files changed — and say that raw tool output
  and file contents may go.
- `post-compact`: records which of those the summary kept (a quality measure,
  not an output: the harness shows PostCompact stdout to the user only).
- `session-start` after a compaction: the same list, back in the context
  (`[hooks] compact`, on by default, independent of `brief`).
- `post-tool` on `eos run finish`: when the session's last call carried more
  than `[hooks] clear_hint_tokens` (150k), one hint that `/clear` is safe — the
  run's state is in EOS.

The window itself (`autoCompactWindow`) is the host's setting, not EOS's.

## Consequences

- A long file read without a range costs one extra turn the first time.
- Every Read runs one short hook process (mtime noted, no content read).
- Nothing here edits the context: every change enters the conversation before
  or at the harness's own compaction, so the prompt cache is never broken by EOS.
- Measure with the host's resident-context table before and after; keep only
  what moves it.
