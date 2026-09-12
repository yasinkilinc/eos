---
name: eos
description: Load when a question needs project-wide facts EOS already indexed — where a symbol lives, what a file affects, what earlier sessions learned — and you want to weigh an EOS tool against a plain Read or Grep before reaching for either.
---

# EOS (engine {{VERSION}})

EOS indexes this project into `.eos/data/eos.db`: parsed symbols, imports, a
project graph, git history, and notes an agent chose to record. It is
read-only except for `add_note`. `eos scan` refreshes the index; if an answer
looks wrong, a stale index is the first thing to suspect.

## What follows is decision support, not a rulebook

Every tool below is described with what it answers and roughly what it costs.
None of it is a permission list. `Read`, `Grep` and `Glob` are frequently the
cheaper way to the same fact, and this file says so per tool rather than
pretending otherwise. Decide per question; a tool being available is not a
reason to call it.

## The 12 tools

| Tool | What it answers | Rough cost | Cheaper alternative |
|---|---|---|---|
| `get_project` | Project name, detected stack, entry points | low | reading `.eos/config.toml` yourself |
| `get_structure` | Bounded list of project files | low–medium | **Glob** — same information, no round trip |
| `get_file` | Contents of one project-relative file | same as the file | **Read** — identical bytes, nothing gained by going through EOS |
| `find_symbol` | Where a name is defined, across the whole index | low–medium | **Grep** — usually just as fast, and exact when you know the string |
| `impact_analysis` | Direct importers and imports of a file | medium | **Grep** for the import string, if the project is small enough that this is quick by hand |
| `get_context` | Generated project-wide summary | medium | **Read** the files it summarizes, when you only need one or two of them |
| `compose` | Context focused on a task and an optional target file | medium–high | Read the files you already know you need |
| `get_graph` | The entire generated project graph | high, and grows with project size | almost never the right first call — see below |
| `get_history` | Commits touching an area, from the index | low | `git log` directly, if you are already in a shell |
| `get_parent_implementation` | Real source from a linked parent project, by symbol | low–medium | doing it by hand when the parent's names differ from this project's, which is the case it exists for |
| `search_notes` | What an earlier session already learned about this | low | nothing else holds this — it is not derivable by rescanning |
| `add_note` | Records a durable finding so the next scan doesn't lose it | low | nothing else does this either |

`get_graph` deserves a specific warning: on a project of any real size, the
full graph can be larger than what fits usefully in a conversation. Reach for
`impact_analysis` on the one file in question, or `find_symbol`, before asking
for the whole graph — and if you do need the graph, treat it as something to
filter or export, not to read end to end.

## Costs are shaped by the project, not fixed

The "rough cost" column above is a shape, not a measurement of this codebase.
On one Java overlay codebase, `get_context` returned roughly as many tokens as
reading four or five mid-sized source files directly — informative context for
that project's size, not a number that transfers to a project ten times
larger or one file per directory instead of twenty.

If `.eos/data/bench.md` exists in this project, it holds numbers measured
here, not elsewhere. Prefer it over the table above whenever the two disagree
— it is describing the codebase actually in front of you.

## The notes loop

This is the one thing nothing else substitutes for:

1. **Before** starting on a question — `search_notes` for the subject. An
   earlier session may have already paid for the discovery, and re-deriving
   it from source is real cost paid twice.
2. **After** learning something that would not be re-derived by the next
   `eos scan` — a non-obvious cause, a measured cost, a constraint that isn't
   visible in the code itself — `add_note`. A finding that lives only in this
   conversation is a finding the next one pays for again.

---

These are inputs to your judgement, not rules. If a cheaper path answers the
question, take it.
