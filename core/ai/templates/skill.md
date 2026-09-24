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

Every command below is described with what it answers and roughly what it
costs. None of it is a permission list. `Read`, `Grep` and `Glob` are
frequently the cheaper way to the same fact, and this file says so per entry
rather than pretending otherwise. Decide per question; a command being
available is not a reason to run it.

## Two calls that are not a judgement call

Everything else here competes with a `Read` you can do without asking, and
frequently loses. These two do not, because nothing else produces what they
hold:

- **`eos brief .`** at the start — what another session is doing right now
  (claimed, blocked, stale), and which recorded notes match this branch. A
  SessionStart hook runs it for you where one is installed; run it yourself
  where one is not. It is one screen, and it is the only way to find out that
  the thing you are about to start is already half-done next door.
- **`eos work claim .` / `eos note add .`** at the end — what you took, and
  what you learned. A finding that lives only in a conversation is a finding
  the next session pays for again.

<!-- eos:mcp-only:begin -->
## The 12 tools

| Tool | What it answers | Rough cost | Cheaper alternative |
|---|---|---|---|
| `get_project` | Project name, detected stack, entry points | low | reading `.eos/config.toml` yourself |
| `get_structure` | Bounded list of project files | low–medium | **Glob** — same information, no round trip |
| `get_file` | Contents of one project-relative file | same as the file | **Read** — identical bytes, nothing gained by going through EOS |
| `find_symbol` | Where a name is defined, across the whole index | low–medium | **Grep** — usually just as fast, and exact when you know the string |
| `impact_analysis` | What a file reaches and what reaches it, to `depth` hops; `include` adds provenance, detector coverage and the file's commits | medium | **Grep** for the import string, if the project is small enough that this is quick by hand |
| `get_context` | Project context; pass `task` to rank notes against it and `target` to anchor on a file. Over MCP it returns; the CLI `eos context` **writes** `.eos/data/brain/llm_context.md` unless you pass `--stdout` | medium | **Read** the files it summarizes, when you only need one or two of them |
| `compose` | As an MCP tool, an alias of `get_context` with `task`/`target`, kept for one release. The CLI `eos compose <project> "<task>" [file]` is not redundant: `eos context` takes no task, so this is the only command-line way to rank notes against one | medium–high | `get_context` with `task`, over MCP only |
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
<!-- eos:mcp-only:end -->

## The commands

`<project>` is a **path**, not a project name, and it comes before the
command's own arguments: `eos <command> <project> [arguments]`. Inside the
project, that path is `.` — `eos rules .`, `eos why . <file>`,
`eos ask . <question> [value]`. Passing the project's name instead appends it
to the working directory and fails with "No .eos directory found at
.../<name>/<name>", which reads like a broken tool rather than a wrong
argument. Every command takes `--format json`.

| Command | What it answers | When it earns its cost |
|---|---|---|
| `eos brief` | What is in flight here and which notes match this branch | At the start of a session, before reading anything |
| `eos brief --task "…"` | For the task you were just given: the procedure recorded for it (steps, success criteria, run counts), its last runs with outcomes and lessons, the nearest notes — under 1,500 tokens. A prompt hook runs it for you and prints nothing when nothing is recorded | Before the first action of a task; follow a recorded procedure rather than improvising one, and read the last failure's lesson before repeating it |
| `eos work list [--across]` | Every item in flight, who holds it, what is stale or contested; `--across` covers sibling projects sharing one knowledge root | When picking up work, and before starting something someone may already hold |
| `eos work add --title "…" --claim` | Records that you took this, so a parallel session sees it | The moment you start, not the moment you finish |
| `eos work show <id>` | One item's whole history, and the commits naming its ticket | When a claim looks stale, or done and unproven |
| `eos run start --title "…"` / `eos run finish --outcome ok\|failed\|abandoned` | Opens and closes an execution: the record of what this session *did*, which wrappers fill with events on their own (`eos run event`, or `eos-event` from a shell). A `failed` finish needs `--lesson "…"` and writes it as a `lesson` note linked to the run; `eos verify` inside an open run lands on its timeline | Start before the first action of a task you will be asked about later; finish when it is done or given up |
| `eos procedure list` / `eos procedure show <slug>` | How a recurring task is done here: ordered steps, the tools they use, what proves success, how many runs went ok or failed and when it was last verified | Before doing a task the project has done before — follow the recorded steps rather than improvising plausible ones |
| `eos procedure new --title "…" --step "…"` | Writes a procedure note (`kind: procedure`); counters then move only when a run naming it finishes (`eos run start --procedure <slug>`) | The first time a recurring task is done well enough to be worth repeating; `eos procedure audit` checks counters against the ledger |
| `eos run list` / `eos run show <id>` | Past executions and one run's timeline: which tools ran, against what, with what exit code, and how it ended | Before repeating a task: what happened the last time it was done |
| `eos query "<sql>"` | Work joined against everything else indexed: `work_item`, `work_holder`, `work_event` next to `git_commit_ticket`, `note` and any extension's tables | When the question is about work *and* something else — which flow a claim touches, whether its ticket has commits |
| `eos rules [--untested]` | Which refusals this code can raise, and what the tests do about each | Before writing a test, and before claiming a behaviour is covered |
| `eos trace <file>` | What an entry point serves and reaches — **and how much of the system no call graph can reach** | When asked "what does this endpoint do" on a system whose components are looked up by name |
| `eos why <file>` | Where a fact came from: detector, origin, confidence, `path:line` — and which detectors found nothing | When an answer looks wrong or suspiciously empty |
| `eos draft-test <code>` | The skeleton of the missing test -- the right method name, assertion library and code to assert -- with the arrangement left to you | When `eos rules` says a refusal is `reachable`, to start from the file's own conventions rather than a blank line |
| `eos verify <code>` | Records what an adapter ran and what happened | After running a test, so the next session does not re-run it to find out |
| `eos findings` | Every recorded run and what it was judged to be | Before re-testing something |
| `eos ask [question] [value]` | Questions this project's index extensions provide; with no question, the list of them | First, on any project with extensions — it is where project-shaped answers live |
| `eos parent <symbol>` | Real source from a linked parent project, by symbol | On an overlay codebase, whenever the class that decides the behaviour is not in this project |
| `eos cost` | What EOS has cost this project per command, and how many sessions reached for it at all | When deciding whether a habit is worth keeping |
| `eos work stats [--since]` | What came of the work: collisions, claims that went quiet, time from claim to close | When asking whether any of this is helping, rather than how often it ran |

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

## The session loop

This is the one part nothing else substitutes for. Four steps, two of them at
the start and two at the end.

1. **Open with `eos brief <project>`** — what is claimed, blocked or stale
   here, and which notes match this branch. Where a SessionStart hook is
   installed this has already run and its output is above; otherwise it is the
   cheapest call you will make all session.
2. **Before starting on a subject** — `eos note search <project> "<query>"`,
   then `eos note show <project> "<name>"` for one that looks relevant. An
   earlier session may have already paid for the discovery, and re-deriving it
   from source is real cost paid twice. `eos compose <project> "<task>"`
   returns the bodies of the notes ranking highest against a task, which is how
   to read several at once.
3. **Claim what you take** — `eos work add <project> --title "…" --claim
   --session <id>`, or `eos work claim <project> <id>` for an item that already
   exists. A parallel session reads the ledger you did not write, and two
   agents on one item costs both of them a session.
4. **Close what you learned** — `eos note add <project> --kind finding --title
   "..." --body "..."` for anything the next `eos scan` could not re-derive (a
   non-obvious cause, a measured cost, a constraint invisible in the code), and
   `eos work done <project> <id> --note "…"`, `eos work block <project> <id>
   --reason "…"` or `eos work drop <project> <id> --reason "…"` for where the
   work got to. A finding that lives only in this conversation is a finding the
   next one pays for again; a claim never closed reads to the next session as an
   agent that vanished mid-task. Where a Stop hook is installed it will ask you
   for exactly this once, before the session ends — all three answers are
   accepted, and "it is blocked" or "it will not be done" are as good as "done"
   as long as one of them is recorded.

---

These are inputs to your judgement, not rules. If a cheaper path answers the
question, take it.
