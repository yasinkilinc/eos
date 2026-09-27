# ADR-030: Subagent handoff and citation check

## Status

Accepted, 2026-09-27 (2.x roadmap C6, E3a). Branch `eos-2x`, released in 1.8.0.

## Context

The owner of the host workspace named agent work quality as the next measure of
EOS, and two failures as the most tiring: work reported done that was not (ADR-028)
and claims without evidence. Subagents are where both concentrate: a subagent
starts with none of the brief its parent was given, so it runs the raw command a
wrapper covers and improvises a recorded procedure; and its answer comes back full
of `path:line` references nobody checks.

## Decision

- **Handoff.** `brief.for_subagent` (`eos brief --for-subagent --task`) gives the
  parent run, the matching procedure's rules, the wrappers the task names and the
  notes whose titles meet it, in at most 400 tokens. With `[hooks] handoff_tokens`
  set, the pre-agent hook appends it to every subagent prompt. Off by default.
- **Whole input.** The harness takes a PreToolUse `updatedInput` as the tool's whole
  input. The routing hook sent `{model}` alone — live, it would have dropped the
  prompt of every routed call. Both paths now send every field the call had, with
  only the model or the prompt changed.
- **Citation check.** `core/citations.py` reads `path:line`, `path:a-b` and
  `[x](path#L12)` and reports only what cannot be right: an absolute path that does
  not exist, a line past the end of an existing file. A relative path found nowhere
  may be another repository's; a path abbreviated with `...` is shorthand; neither is
  reported. SubagentStop asks the subagent once to correct them or call them
  unverified (`[hooks] cite_check`, on). The hook is synchronous: an async hook
  cannot block.

## Evidence

Live, headless: an Explore subagent's first prompt carried the handoff (a wrapper and
a note); a subagent told `CLAUDE.md:9999 -- the file has 152 lines` read the file and
corrected its answer. Over 277 historical subagent answers (2,058 references) 47 flag
today, mostly files that shrank after the answer was written — live, the check reads
the file the subagent just read.

## Consequences

- A subagent may take one more turn when its references are wrong.
- The handoff costs up to 400 tokens per subagent call when enabled.
- A worktree session loads the plugin's hook registration from the main checkout;
  live tests of registration changes need `--plugin-dir`.

## Addendum: depth 1, quotes (1.10.0)

`eos cite [--output file|-]` checks an answer the way the SubagentStop hook does,
and also its quotes: a quote placed right after a reference (`` path:12 `code` ``,
or a fenced block after a line ending in the reference) must be found within two
lines of the range it cites. A reference inside a code span, another reference and a
bare path are not quotes. Measured on the host's transcripts: the first version read
the prose after `` `path:12` `` as a quote (131 flags in 6,019 answers); fixed, 45
remain over all history, where files changed after the answer, and 1 of 69 quotes in
the last three days. The hook keeps checking references only: a misread quote would
stop a subagent that did nothing wrong. Not done from the roadmap's E3: changed files
within the procedure's scope, wrapper exits, depth 2.

## Addendum: the main session (1.11.0)

The Stop hook checks the main session's final answer (`last_assistant_message`,
which the harness hands Stop as it does SubagentStop) the same way: a missing
absolute path or a line past the end stops the turn once per set of problems,
under the same `[hooks] cite_check`. Quotes stay out of the hook, as for subagents.
