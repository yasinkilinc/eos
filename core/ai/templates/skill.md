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
| `impact_analysis` | What a file reaches and what reaches it, to `depth` hops; `include` adds provenance, detector coverage and the file's commits | medium | **Grep** for the import string, if the project is small enough that this is quick by hand |
| `get_context` | Project context; pass `task` to rank notes against it and `target` to anchor on a file | medium | **Read** the files it summarizes, when you only need one or two of them |
| `compose` | Alias of `get_context` with `task`/`target`, kept for one release | medium–high | `get_context` with `task` |
| `get_graph` | The entire generated project graph | high, and grows with project size | almost never the right first call — see below |
| `search_index` | Full-text search over notes, brain documents and index extensions | low | `search_notes`, when you only want authored findings |
| `get_parent_implementation` | Real source from a linked parent project, by symbol | low–medium | doing it by hand when the parent's names differ from this project's, which is the case it exists for |
| `search_notes` | What an earlier session already learned about this | low | nothing else holds this — it is not derivable by rescanning |
| `add_note` | Records a durable finding so the next scan doesn't lose it | low | nothing else does this either |

One habit worth forming: when `impact_analysis` comes back with less than you
expected, ask for `include: ["coverage"]` before concluding there is nothing
there. It reports what each detector was asked about, so "this file has no
dependents" and "nothing here looks for that kind of dependency" stop reading
the same.

`get_graph` deserves a specific warning: on a project of any real size, the
full graph can be larger than what fits usefully in a conversation. Reach for
`impact_analysis` on the one file in question, or `find_symbol`, before asking
for the whole graph — and if you do need the graph, treat it as something to
filter or export, not to read end to end.

## Commands the MCP tools do not cover

These are CLI-only, and each answers something none of the tools above can.
Run them with `eos <command> <project>`; every one takes `--format json`.

| Command | What it answers | When it earns its cost |
|---|---|---|
| `eos rules [--untested]` | Which refusals this code can raise, and what the tests do about each | Before writing a test, and before claiming a behaviour is covered |
| `eos trace <file>` | What an entry point serves and reaches — **and how much of the system no call graph can reach** | When asked "what does this endpoint do" on a system whose components are looked up by name |
| `eos why <file>` | Where a fact came from: detector, origin, confidence, `path:line` — and which detectors found nothing | When an answer looks wrong or suspiciously empty |
| `eos draft-test <code>` | The skeleton of the missing test -- the right method name, assertion library and code to assert -- with the arrangement left to you | When `eos rules` says a refusal is `reachable`, to start from the file's own conventions rather than a blank line |
| `eos verify <code>` | Records what an adapter ran and what happened | After running a test, so the next session does not re-run it to find out |
| `eos findings` | Every recorded run and what it was judged to be | Before re-testing something |
| `eos ask [question]` | Questions this project's index extensions provide | First, on any project with extensions — it is where project-shaped answers live |
| `eos cost` | What EOS has cost this project per command | When deciding whether a habit is worth keeping |

Two of these are worth a habit.

**`eos rules --untested` before writing a test.** It separates four states, and
the useful one is `reachable`: a test already reaches the class that raises the
refusal and nothing asserts it. The fixture exists, so the missing work is one
assertion — and `eos draft-test` drafts the skeleton to start it from, with
the arrangement left to you.

**`eos why` when an answer is empty.** An empty impact result and a detector
that never ran look identical. `why` is the only thing that separates them, and
guessing wrong there wastes a whole investigation.

## What this system will not tell you

It records what was run; it does not conclude what a failure meant. A failing
test can mean the rule is not enforced, the test is wrong, or the environment
was — nothing in an exit code separates those, so `eos verify` stores a
`verdict` only when a person passes one. Treat any claim of a defect that did
not come from a person looking at a log as something this system did not make.

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
