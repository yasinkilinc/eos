# EOS

EOS is a code-intelligence engine you drop into a project so an AI coding
agent — or you — can actually find things in it. It scans a codebase into a
symbol cache, a dependency graph and a Markdown knowledge vault, keeps your
own hand-written notes alongside them, and serves all of it over MCP so an
agent gets grep-fast symbol lookup, real import-graph impact analysis, and a
budgeted context bundle instead of guessing from file names. If your project
is a thin layer on top of another codebase — a fork, a vendored dependency, a
platform repo you don't own — EOS can link that parent and search both
through one index.

I built it because pointing an agent at a large, real codebase and asking
"where is X defined" or "what breaks if I change Y" kept costing more tokens
and more wrong guesses than it should have. The runtime that does the actual
scanning is pure Python standard library, on purpose: it gets copied into
every project EOS manages, and a tool that needs `pip install` before it can
even look at your code is a tool people won't bother running. The optional
dashboard and file watcher are the only parts that need real dependencies,
and they're isolated behind their own install step.

## 60-second install

```bash
git clone https://github.com/<you>/eos.git
cd eos
./bin/install.sh              # installs the `eos` launcher to ~/.local/bin

eos --version
eos init /path/to/your/project
eos scan /path/to/your/project --full
eos context /path/to/your/project --budget 12000
```

Requires Python 3.11 or newer and nothing else. The installer copies the
canonical `core/` runtime to `~/.local/share/eos/<version>/` and installs an
executable launcher under `~/.local/bin/eos`. Override the locations with
`EOS_INSTALL_PREFIX`, `EOS_DATA_DIR`, `EOS_BIN_DIR`, or `EOS_PYTHON`.

## What `eos init` gets an agent

`eos init` does two things: it bootstraps `.eos/` (the scan cache, the
knowledge vault, the runtime copy), and it writes four integration surfaces
so a coding agent actually finds EOS without you wiring anything by hand:

1. **A decision-support skill** at `.claude/skills/eos/SKILL.md` — when to
   reach for `eos context`/`compose`/`impact` instead of grepping blind.
2. **A researcher agent profile** at `.claude/agents/eos-researcher.md` — a
   subagent whose whole job is answering "where/what/why" questions via EOS.
3. **Two hooks**, registered in `.claude/settings.json`: `eos-brief.py` runs
   `eos brief` at session start and puts what is in flight in front of the
   session, and `eos-close.py` asks a session, once, what happened to the work
   it claimed. They are the surfaces that cost the agent no decision — see
   [ADR-021](docs/decisions/021-session-ledger.md).
4. **A section in `AGENTS.md`**, wrapped in `<!-- eos:begin -->` /
   `<!-- eos:end -->` markers so anything you wrote around it survives.

All of them are re-runnable — `eos ai update` refreshes just the generated
block after an EOS upgrade, leaving everything else in each file alone. Pass
`--no-ai` to `eos init` to skip them for a project that doesn't want them. See
[ADR-009](docs/decisions/009-ai-integration-layer.md).

**One surface per project.** `eos init --surface cli` (the default) writes no
MCP registration, because an MCP tool roster is charged to every request
whether or not a tool is called — measured at 2,194 tokens per session across
an 18-service workspace, before anything was asked. `--surface mcp` or
`--surface both` adds `mcpServers.eos` to `.mcp.json`, merged key-wise so
other servers survive, and the generated skill then describes the tools too.
The choice is recorded in `.eos/config.toml` under `[ai] surface`, so
`eos ai update` after an upgrade does not silently put back a registration a
project removed on purpose.

## CLI

| Command | Purpose |
|---|---|
| `eos init <path> [--link-parent <path>] [--link-label <name>] [--surface cli\|mcp\|both] [--no-ai]` | Bootstrap `.eos/`, write the AI integration surfaces |
| `eos brief <path> [--session <id>] [--agent <name>]` | What a session needs before it starts: work in flight, and the notes matching this branch |
| `eos route <path> <task> [--file F]… [--model M] [--effort E] [--json] [--fresh] [--no-record] [--stats]` | Which model and how much effort a task deserves, and why; advice for the harness, EOS calls no model |
| `eos scan <path> [--full] [--with-parents]` | Incremental or full scan; optionally index linked parents too |
| `eos update <path> [--dry-run]` | Update the runtime from canonical `core/` |
| `eos doctor <path>` | Validate `.eos/` integrity |
| `eos info <path>` | Show instance summary |
| `eos status <path>` | Project status and latest scan metadata |
| `eos clean <path>` | Remove generated artifacts (cache/brain/graph); id and config are kept |
| `eos context <path> [--budget N]` | Generate an AI-oriented project context, budgeted in approximate tokens |
| `eos compose <path> <task> [target] [--budget N]` | Compose focused context for one task, optionally anchored on a file |
| `eos impact <path> <file> [--depth N] [--include facts\|coverage\|history]` | What a file reaches and what reaches it, to N hops |
| `eos why <path> [file] [--predicate P] [--format json]` | Provenance of a file's facts, and which detectors found nothing |
| `eos rules <path> [--untested] [--format json]` | Behaviour codes this project throws, and which no test names |
| `eos trace <path> <file> [--depth N]` | What an entry point serves and reaches, and what is wired at run time |
| `eos ask <path> [question] [arg]` | Run a question this project's index extensions provide; omit the name to list them |
| `eos draft-test <path> <code> [--write]` | Draft a test for a behaviour code the suite does not assert |
| `eos verify <path> <code> --outcome … --command …` | Record what an adapter ran, and what happened |
| `eos findings <path> [--failed-only]` | Recorded runs and what they were judged to be |
| `eos cost <path>` | What EOS has cost this project, per command (off by default) |
| `eos graph <path> [--type import\|all] [--output -]` | Export the generated project graph as JSON |
| `eos index <path>` | Rebuild `.eos/data/eos.db` from notes, the work ledger, brain, graph, and git history |
| `eos query <path> [sql \| --search "..."] [--limit N]` | Read-only SQL, or full-text search, against `eos.db` |
| `eos parents <path>` | List configured parent-project links |
| `eos parent <path> <symbol> [--limit N]` | Real source for a symbol from a linked parent, inlined |
| `eos note add\|list\|show\|search\|skip\|amend\|audit <path> ...` | Manage authored knowledge notes (see below) |
| `eos work add\|claim\|log\|block\|unblock\|done\|drop\|list\|show\|stats <path> ...` | What sessions are working on here, and what came of it (see below) |
| `eos ai update <path> [--no-agents-md] [--surface cli\|mcp\|both]` | Refresh the AI integration surfaces for the current EOS version |
| `eos mcp <path>` | Start the read-only stdio MCP server |
| `eos bench <path> [--samples N]` | Measure EOS's own tools against baselines, on this project |
| `eos ui [port] [--yes] [--no-install]` | Start the multi-project dashboard |

## What the index does and does not do

Read this before trusting an artifact.

| Language | Symbols | Import edges | Entry points |
|---|---|---|---|
| Python | real `ast` | resolved, including relative imports | `main`/`app`/`cli` filename heuristics |
| Java | masking lexer + scope scanner: classes, methods with signatures, fields with types, annotations with values | imports, `extends`, `implements`, field types, `new`, and calls resolved through field types | Spring annotations read off declarations |
| JavaScript / TypeScript | regex | path specifiers, `import type`, re-exports | `index.*` filename heuristics |

Anything else — C, SQL, Rust, Go, HTML — is not parsed. Every scan prints how
many files it skipped and why, so silence is never mistaken for absence:

```text
Scanned 803 files across ['python']
Skipped 731 path(s) by ignore rules: build (730), bin (1)
  Un-ignore a default with [scan] unignore in .eos/config.toml
314 file(s) in languages EOS does not index: .sql (314)
```

`build`, `bin`, `dist`, `target`, `node_modules` and the usual caches are
ignored by default. For a project whose real source lives in one of them, add
`unignore` to `.eos/config.toml`:

```toml
[scan]
unignore = ["build"]
```

`eos index` also reads two configurable patterns for mining git history, both
under `[index]` in `.eos/config.toml`:

```toml
[index]
ticket_pattern = "\\b[A-Z][A-Z0-9]+-[0-9]+\\b"   # default; JIRA-style keys
merge_branch_pattern = "^Merge in \\S+ from (\\S+) to \\S+"
```

`merge_branch_pattern` is unset by default — GitHub's merge-commit subject
line is already handled without configuration. The example above is
Bitbucket's merge-commit body format (`Merge in <project> from <source> to
<target>`); a project on a host with its own merge-commit convention sets its
own pattern here to get source-branch extraction for free. It is matched
line-by-line against the commit body (`re.MULTILINE`), not the whole body at
once.

### Where a fact came from

Every derived fact records how it came to be known — `origin` (extracted,
documented, inferred, verified), `confidence`, the detector that produced it,
and the `path:line` it was read from. `eos why` prints them:

```bash
eos why . src/main/java/com/example/OrderService.java
```

The last lines of that answer are the ones worth reading. `coverage` records
what each detector was *asked* about, so a detector that examined 2,652 files
and produced nothing is distinguishable from one that never ran — otherwise
"this project has no message producers" and "nobody looked for message
producers" print the same silence.

Provenance is not part of `graph.json`: that artifact reaches 25 MB on a real
service and is returned whole over MCP. Facts travel in
`.eos/data/brain/evidence.jsonl` and are queried from `eos.db`. See ADR-014.

### The context an agent actually gets

```bash
eos context .                 # orientation
eos compose . "<task>" <file> # focused on a task and a file
```

Two things matter about the output. It says the same thing once — the brain is
five documents and four of them list the entry points, so concatenating them
spent 45% of the budget repeating itself (9,350 of 20,804 characters on one
real service; 11,345 after). And when a target file is given, the section about
it answers rather than dumps: what reaches it, what it reaches, and what it can
refuse with — each refusal graded by what the tests do about it.

```text
## What Is Known About The Target

- Reached by 1 file(s), 1 of them tests
  - src/test/java/.../FmTopUpValidationCommandTest.java
- Reaches 44 file(s) within 2 hops
- Can refuse with 1 behaviour code(s):
  - `INVALID_TOPUP_CHAR_VALUE` — reachable (FmTopUpValidationCommand.parseBigDecimal)
```

That last line is a task: the test already reaches the class, so the missing
assertion is an afternoon's work.

### What this code refuses to do, and what no test names

Most systems identify a refusal with a constant — `AGE_LIMIT`, not "too
young". That constant is what the API returns, what a test asserts, and what a
ticket quotes, so it can be read out of the source rather than inferred:

```bash
eos rules . --untested
```

Each code is listed with the class and method that throws it, the exception
type, and the test files that name it. Untested codes lead, because the gap is
what people came for.

Coverage is graded, because a tested/untested answer buries the interesting
states:

| state | meaning |
|---|---|
| `verified` | a recorded run passed — the only rung that observed anything |
| `asserted` | a test reaches the throwing class **and** names the code |
| `reachable` | the class is exercised, this refusal is not asserted |
| `named` | the code appears in a test, nothing touches the class |
| `none` | no test reaches the class or names the code |

On one real service: 51 / 134 / 9 / 209. `reachable` is a third of the corpus —
the code runs under test and nobody checks that branch — and it is the cheapest
gap to close, because the fixture already exists.

Still a floor: reaching a class is not exercising the branch that raises the
code. `eos rules` says so on every run. See ADR-016.

### What an endpoint actually does

```bash
eos trace . src/main/java/com/example/OrderController.java
```

The routes it serves, the files it reaches with the hop count, the behaviour
codes it can refuse with — and then the part that makes the rest trustworthy:
how many named components in the project have no caller at all.

That last number matters more than it sounds. Measured on one real service,
235 files throw a behaviour code and only 6 are reachable from any of its 39
entry points, because a coordinator resolves the rest by name from
configuration at run time. On that shape of system the call graph is a partial
view by construction, and a trace that printed only what it could follow would
look complete and be wrong. See ADR-017.

### Drafting the test that is missing

The gap worth acting on is narrow: a test already reaches the class that
raises a refusal, and nothing checks that branch — 134 of 403 codes on one
real service. The fixture exists, the mocks exist, the file exists. What is
missing is one method.

```bash
eos draft-test . INVALID_TOPUP_CHAR_VALUE
```

What you get is a skeleton, not a finished test: the right method name, the
assertion library the neighbouring tests use, the code to assert and the call
site — with the arrangement that drives the code down that branch left to you,
marked in the body. It cannot know that part, and a reviewer told otherwise
would trust it.

Every symbol it names is checked against the source first; if the class or
method cannot be grounded it refuses instead of guessing, because a plausible
guess in generated code survives review by looking right.

It goes to stdout, or with `--write` to `.eos/data/candidates/`. Never into
the source tree, and nothing here edits anything a build compiles. It says
`DRAFT` inside the method, because the one thing it cannot know is the
arrangement that drives the code down that branch.

### Indexing something only your project has

Some knowledge is not in the code. A system that implements its business flows
as a configured chain of named steps, looked up at run time, cannot be
understood from the source alone — the configuration *is* the program. EOS
indexes that kind of artifact through **extensions**: small Python modules the
project supplies, which add tables to the same index everything else lives in.

```toml
[index]
extensions = ["tools/eos-ext/flows.py"]
```

A module may define `SCHEMA` (its tables), `COUNTS` (numbers `eos index`
prints), `QUESTIONS` (named answers, runnable as `eos ask`),
`sources(root, notes_dir)` (what it reads, hashed for staleness) and
`load(build)` (the rows). The contract is documented in `core/extensions.py`.

`QUESTIONS` is what turns a table into an answer. The useful questions about a
project-shaped artifact are usually joins between the extension's own rows and
the core facts — which class a configured step resolves to, which behaviour
codes it can raise, whether a test names any of them — and nobody types a
four-way join twice:

```bash
eos ask .                      # list the questions this project provides
eos ask . flow-rules TOP_UP
```

`extensions/journeys.py` in this repository is the reference implementation
and is usable as-is. It indexes journey documents and exported step-chain
snapshots, and resolves each configured step to the service that runs it —
recording *how* it was resolved and what else it could have been, so an
attribution can be checked rather than believed:

```bash
eos query <project> "SELECT flow, sort_id, bean_name, owner, resolution
                     FROM journey_step WHERE owned = 1 ORDER BY sort_id"
```

A configured extension that is missing or unimportable stops the build and
keeps the previous index; one that fails while running costs only its own
tables and is reported in `build_issue`. See ADR-013.

## MCP

```bash
eos mcp /path/to/project
```

EOS exposes a read-only MCP server over stdio for local code intelligence.
It's self-contained and doesn't need the UI dependencies. It is **off by
default** since 0.34: `eos init` registers it only under `--surface mcp` or
`--surface both`, because its tool roster is billed to every request of every
session whether or not a tool is called, and on a workspace of many projects
that is the largest fixed cost EOS imposes. A single-project setup where the
roster is one server may well want it; run `eos ai update <path> --surface
mcp` to turn it on. Registered tools:

`get_project`, `get_structure`, `get_context`, `get_file`, `find_symbol`,
`compose`, `impact_analysis`, `get_graph`, `get_history`,
`get_parent_implementation`, `search_notes`, `add_note`.

All are read-only except `add_note`, which writes a note into the configured
knowledge directory and nowhere else — it also refuses bodies containing
credential-shaped text and scope entries naming sensitive files or paths
outside the project.

## Notes

Notes record what a scan cannot re-derive — why something is the way it is:

```bash
eos note add /path/to/project --kind finding --title "..." --body "..." --tags a,b
eos note add /path/to/project --kind defect --title "..." \
  --cause "..." --solution "..." --metric "..."
eos note list /path/to/project [--tag a]
eos note show /path/to/project "<file name or part of a title>"
eos note search /path/to/project "query"
eos note skip /path/to/project --reason "..." --session <id>
eos note amend <note-file> /path/to/project [--body "..." | --reaffirm "..."] [--scope a,b]
eos note audit /path/to/project
```

`skip` records that a session had nothing durable to write down, so a
Stop-hook gate stops asking; it never writes a note.

`amend` is the only way a note that has gone stale comes back into agreement
with the code: it rewrites the note and re-hashes its `--scope`. Use `--body`
when the claim itself changed, `--reaffirm` when the file moved on but the
claim still holds, and `--scope` when the scoped file was renamed or deleted
(`--body` and `--reaffirm` re-hash the recorded path, so for a file that is
gone they can only refuse). When one note scopes both a removed file and a
changed one, `--scope` and `--body` go in the same command: a scope-only
amend may neither re-hash nor drop an entry that still exists and changed. It
refuses an unchanged body, a placeholder value, and any amend that would
leave the note with no scope entry resolving to a file.

`audit` reports every note whose scoped files have moved, split into
hand-written (revise with `amend`) and generated (re-run the generator), and
prints the command for each. It's a report and always exits 0.

Notes live in `.eos/knowledge/` by default, or wherever `[knowledge] dir` in
`.eos/config.toml` points — useful when the project's own repository isn't a
good home for them (a workspace of repos that share one knowledge directory,
for instance). Notes are never touched by `scan`, `clean` or `update`.

`--scope` records which files a note is about and hashes them at write time,
so `eos doctor` can later flag notes whose code has moved on since. Notes
matching the current task are injected into `get_context` output, capped at
15% of the budget.

## Work in flight

Notes answer "what was learned here". They cannot answer "what is happening
here right now", and a second agent starting on work a first agent is already
holding costs both of them a session. That is a different shape of record — it
has a lifecycle, it expires, and two sessions write it at once — so it is a
separate ledger:

```bash
eos work add /path/to/project --title "..." --claim --session <id> --agent claude
eos work list /path/to/project [--status live|open|active|blocked|done|dropped|all] [--across]
eos work claim /path/to/project <id> --session <id>
eos work log /path/to/project <id> --body "where this got to"
eos work block /path/to/project <id> --reason "what it is waiting on"
eos work done /path/to/project <id> --note "..."
eos work show /path/to/project <id>
```

It is an append-only event log — `work.jsonl`, beside the notes in the
knowledge directory — and an item's state is the fold of its events. That is
what makes it safe for a cloud session and a laptop to write at the same time:
neither loses the other's line, which a read-modify-write of a status field
could not promise.

Three things it does that a to-do list does not:

**A second claim is reported, not resolved.** Claim an item another session
holds and both holders are named, every time it is listed. Picking a winner
would be EOS deciding which of two agents is wasting its time.

**A claim nobody has touched for a day is marked stale**, not closed. A
session that went away and a session still thinking look identical from here;
naming it is the whole intervention.

**It says how far behind it might be.** This reaches another machine the way
notes do — through the knowledge directory, through git — so every listing
ends with whether the ledger is pushed, unpushed, uncommitted, untracked or
gitignored. A ledger that reads as authoritative while being local to one
laptop is the failure mode worth spending a line on.

`--across` reads every sibling ledger under the same knowledge root, which is
the arrangement `[knowledge] dir` exists for: one workspace, many services,
and the work next door is the work most likely to collide with yours. Live
items are injected into `eos context` output as well, capped at 8% of the
budget.

`eos work show <id>` prints the item's whole history and, when it carries a
`--ticket`, the commits in the index that name that ticket — next to the
claim, never folded into it. A ticket with no commits may be work that is not
committed yet or a claim that outran it, and nothing here can tell those
apart.

`eos index` folds the ledger into `eos.db` as `work_item`, `work_holder` and
`work_event`, so work can be asked about *with* something else — the commits
naming its ticket, an extension's own rows, the notes written while it ran:

```bash
eos query /path/to/project "SELECT w.id, w.status, w.ticket, COUNT(t.sha) AS commits
                            FROM work_item w
                            LEFT JOIN git_commit_ticket t ON t.key = w.ticket
                            GROUP BY w.id"
eos query /path/to/project "SELECT session, agent, item FROM work_holder"
```

Items are searchable too — `eos query <path> --search "top-up"` returns the
item someone claimed this morning next to the note written about it last
month. There is deliberately no `stale` column: staleness is a question about
now, and a value computed when the index was built would answer it with the
wrong clock. `updated_at` is there; the query compares it to its own.

## The session brief

```bash
eos brief /path/to/project
```

One command, no arguments to choose, meant for the start of a session: what is
in flight here, and which recorded notes match the current branch. It derives
its own query from the branch name and the ticket keys in it, because at
session start nobody has typed a task yet.

It exists because of a measurement. Offered as twelve MCP tools on a real
workspace, EOS was reached for in 4 of 25 sessions — a tool an agent has to
*decide* to call competes, on every question, with a `Read` it can do without
asking, and usually loses. What does not compete is what nothing else
produces. So `eos init` also writes `.claude/hooks/eos-brief.py` and registers
it as a SessionStart hook: the brief arrives in the session's context with no
call to remember and no tool to choose. The hook exits 0 on every failure path
and prints nothing when there is nothing to say — a session-start block that
is noisy is one that gets deleted.

The other end of the loop is `.claude/hooks/eos-close.py`, registered as a
Stop hook. It asks a session, once, what happened to the items **it** claimed
— not open work nobody took, and not another session's claims. Every answer is
accepted (`eos work done`, `eos work block`, `eos work drop`); a gate with no
way through gets deleted, and a deleted gate records nothing. It never asks
twice (`stop_hook_active`), never stops a session it could not ask EOS about,
and never stops one that passed no session id. To turn it off, remove its
entry from `hooks.Stop` in `.claude/settings.json`.

`eos cost` now counts sessions as well as calls: how many identified
themselves, how many of those called EOS for something beyond the opening
brief, how many were still holding work when the Stop hook asked, and how many
could not produce a brief at all. That last one is what keeps the first honest
— a hook that cannot reach EOS would otherwise remove its whole session from
the denominator, and a project where nothing worked would report the best
adoption figure it has ever had. The hook records its own failure, into a
telemetry log that already exists and never into one it would have to create.
That is the number "4 of 25" was, and it was previously countable only by hand.

**Sessions identify themselves with nothing configured.** Claude Code exports
`CLAUDE_CODE_SESSION_ID` into the environment of every command it runs, so
`eos cost` counts sessions on a plain install, with no hook and no flags. A
harness that exports no id of its own sets `EOS_SESSION`; one that exports a
differently-named id declares it:

```toml
[telemetry]
enabled = true
session_env = ["MY_HARNESS_RUN_ID"]
```

Only variables that *are* a session id belong there. `DEVIN_PERMISSION_MODE`
was observed set inside a Claude Code session on a machine with Devin's editor
extension installed: an inherited variable is evidence of what is installed,
never of what is running, and reading one as a marker attributes one agent's
work to another. Every record says which variable it came from, so "read from
the harness" and "nobody passed one" never print as the same number.

## What changed, as opposed to what was called

```bash
eos work stats /path/to/project [--since 2026-09-01]
```

`eos cost` says whether the engine was reached for. This says whether reaching
for it changed anything — and after a hook is installed, the first number is
close to tautological, since the hook runs in every session by construction.

```text
14 item(s) over 63 event(s), 12 of them claimed by a session.

Collisions           2  item(s) claimed by two sessions at once
Went quiet           3  item(s) held with no event for over 24h
Closed               9  (7 done, 2 dropped; 4 block(s) recorded)
Open now             5  (1 stale, 0 contested)
Claim to close      4h median, 6d longest
```

Each line is a cost somebody already paid, replayed from the events rather
than read off the current state: a collision settled an hour later leaves no
trace in the final status and is exactly the event worth counting. Every
number measures what sessions *recorded* — work done without closing an item,
and an item closed without the work, are indistinguishable from here, and the
report says so on every run. Sessions identify themselves through `--session` or `$EOS_SESSION`,
which the hook exports so that every later call in the session is attributed
too. Telemetry records the id and nothing else new — it is an opaque marker a
harness generated, never anything a person typed.

## Operational memory: how a task is done here, and what happened last time

Notes say what is true and the work ledger what a session intends. Two more
stores answer the two questions a fresh session is otherwise left to
rediscover or improvise:

```bash
eos brief . --task "deploy the service to staging"     # what to do, and how it went before
eos run start . --title "Deploy wallet" --procedure deploy-to-staging --target staging
eos-event --kind ran --tool jenkins --target staging --ref build#118 --exit 0   # from a wrapper
eos run finish . --outcome failed --lesson "Built from main; check the branch first"
eos procedure show . deploy-to-staging                  # steps, counts, confidence, recent runs
eos run show . <id> | eos run tools . | eos run diff . <id>
```

- **Executions** (ADR-022) — `executions.jsonl`, append-only: each run's
  start, the events of what it did (kind, tool, target, a reference — never a
  payload), and its declared outcome. Wrappers append events with no id and no
  interpreter; capture never fails the command around it.
- **Procedures** (ADR-023) — notes of `kind: procedure` with `## Steps`. Their
  run counters and `last_verified` move only when a run naming them finishes;
  `eos procedure audit` recomputes them from the ledger.
- **Lessons and decisions** (ADR-024) — note kinds with required sections. A
  failed run must leave a lesson; the same lesson again reads as recurring.
  Confidence (`failing`, `unverified`, `fresh`, `aging`, `stale`) is derived
  when asked and never stored.
- **The task brief** — `eos brief --task` hands a session the procedure, its
  last three runs and the last failure's lesson under 1,500 tokens. A
  UserPromptSubmit hook runs it on every prompt, prints nothing when nothing is
  recorded, and nothing when the same block was already delivered; the prompt
  is never written anywhere.

`eos doctor --memory` prints the 21-row capability matrix for a project, and
`docs/plans/operational-memory.md` is the plan these came from, with its
acceptance protocol.

## Which model, how much effort

Before a task starts, EOS can say which model it deserves and at what
reasoning effort — and why. It only advises: nothing in EOS calls a model.
The harness (or the agent reading the brief) applies the decision.

```bash
eos route . "refactor the auth flow and update tests"
eos route . "fix the typo in the readme" --json
eos route . "rename the handler" --file src/handler.py --file src/routes.py
eos route . "design the billing boundaries" --model opus --effort max
eos route . --stats          # decisions made inside runs, against each run's outcome
```

```text
Task: refactor the auth flow and update tests

Type:        refactoring
Complexity:  HIGH  (score 0.42)
Model:       sonnet
Effort:      high
Confidence:  0.55
Source:      auto

Reason:
Refactoring, HIGH (architectural impact 0.60, reasoning required 0.50); cheapest model meeting HIGH; effort high.
```

How it decides, all of it deterministic and printed with the answer
(ADR-025):

- **Type** — one of twelve (`trivial_edit` … `repository_wide_change`), from
  weighted keyword tables and four ordered rules. Short tokens are
  word-anchored, so `ci` never matches inside `decision`.
- **Complexity** — seven named factors between 0 and 1 (scope, file count,
  dependency count from the index when `--file` is given, architectural
  impact, reasoning required, failure risk, uncertainty), weighted into a
  score and cut into `LOW`, `MEDIUM`, `HIGH`, `CRITICAL`. Uncertainty never
  raises a level on its own.
- **Model** — the cheapest registered model that meets the level's minimum
  reasoning and coding capability. The strongest is used only when the level
  needs it, or when nothing else qualifies, and the reason then says so.
- **Effort** — the level's preferred effort, clamped to what that model
  accepts: the requested value, else the highest accepted value below it,
  else the lowest the model has. An unsupported value is never returned.

An explicit choice wins for its half of the decision, in this order: the
`--model` / `--effort` flag, then `EOS_ROUTE_MODEL` / `EOS_ROUTE_EFFORT`, then
the config defaults, then the policy. A fixed model with `effort` left on
auto gets the best effort that model accepts; a fixed effort with the model
on auto may move to a model that accepts it. An unknown or unavailable model
is refused (exit 2) rather than swapped for another.

Inside an open run (`eos run start`), the first decision is the run's: it is
appended as a `decided` event and returned again on later calls instead of
being re-derived; `--fresh` decides again. Each recorded decision is one line
in `.eos/data/routing.jsonl` — type, level, model, effort, reason, and a hash
of the task, never the task itself.

Everything is optional and off in the brief until a project asks for it:

```toml
[model_routing]
enabled = true              # false: `eos route` still answers; the brief and MCP add nothing
default_model = "auto"      # or a registry id / alias
default_effort = "auto"     # or low | medium | high | xhigh | max
brief = "with-brief"        # "with-brief" | "always" | "never"
hook = false                # true: `eos ai update` installs the subagent-model hook below

[model_routing.models.local-coder]     # add a model, or correct a default by id
provider = "openai-compatible"
reasoning = 3                          # 1..5
coding = 4                             # 1..5
cost = 0.5                             # relative to the others
efforts = ["low", "medium", "high"]    # what this model accepts

[model_routing.keywords]               # extend the classifier, e.g. for another language
debugging = ["hata", "çöküyor"]
```

With the table present, `eos brief --task` ends with two lines — under
`"with-brief"` only when the brief has something else to say, under
`"always"` on every task prompt:

```text
ROUTE  refactoring HIGH → sonnet/high (conf 0.55) — Refactoring, HIGH (…); cheapest model meeting HIGH; effort high.
  Apply: Task({model: "sonnet"}) for subagents; /effort high for this session; eos route . "<task>" for the factors
```

With `hook = true`, `eos ai update` also installs `.claude/hooks/eos-route.py`
as a `PreToolUse` hook on the subagent tool: when the agent spawns a subagent
without naming a model, the subagent's prompt is routed and the call proceeds
with the chosen model. An explicit model is never changed, effort is left to
the session (the harness takes it per session, not per call), and any failure
lets the call through untouched. Turning the flag off removes the hook and
only its own settings entry.

The default registry holds three generic tiers (`haiku`, `sonnet`, `opus`)
with relative capability and cost numbers; they are defaults to correct in
config, not facts about vendors. Over MCP, `get_context` with `task` and
`route: true` returns the same decision.

## Linked parent projects

A project that is a thin overlay on another codebase can link it, so both are
searchable through one index:

```bash
eos init /path/to/project --link-parent /path/to/parent
eos scan /path/to/project --with-parents
eos parents /path/to/project
```

Parent files are indexed under `@parent:<label>/` keys in the same cache;
`get_parent_implementation` returns their real source. Parent trees are only
ever read.

## eos-ui (optional)

`eos ui` is a small local dashboard: a project list, a drill-down import
graph per project, and (if you point `EOS_GRAPHIFY_ROOT` at one) a viewer for
Graphify artifacts. It's entirely optional — nothing else in EOS needs it.

`eos ui` installs its own Python dependencies (FastAPI, uvicorn, watchdog)
into a venv EOS owns on first run, and starts the server — but it does not
build the React frontend, since that's a one-time step, only needed once per
pull of frontend changes:

```bash
cd ui/frontend
npm ci
npm run build      # -> ui/static/, served by the FastAPI app at /
cd ../..
eos ui              # or: bin/start.sh
```

Until the frontend is built, `ui/static/` doesn't exist and `/` returns a
JSON hint (`{"status": "frontend-not-built", ...}`) instead of the page; the
API itself (`/api/...`) works either way.

### Frontend dev loop

For actual frontend work, run the two servers side by side instead of
rebuilding on every change:

```bash
eos ui 8000              # terminal 1: FastAPI + API on :8000
cd ui/frontend && npm run dev   # terminal 2: Vite on :5173
```

Vite serves the app on `:5173` and proxies `/api` requests to the FastAPI
server on `:8000` (see `ui/frontend/vite.config.ts`), so the browser only
ever talks to `:5173` and hot-reloads on save. `npm run build` is only needed
again once you're done and want a static `ui/static/` for `eos ui` to serve
directly.

### Workspace view — one thing it deliberately doesn't do

The dashboard's cross-project view links two projects when one's graph
mentions the other's name, matched two ways: a separator-delimited form
(token-boundary match) and a compact form (substring match, dropped below 6
characters to avoid matching unrelated short words). An earlier version also
derived an extra alias for any dash-separated name with three or more
segments by dropping its first segment — a generic "strip the org/team
prefix" heuristic. That generic multi-segment alias derivation has been
removed: it produced more false cross-project links than it was worth. If you
expected two projects with a shared multi-part name prefix to auto-link and
they don't, this is why — link them explicitly instead of relying on name
matching.

## Environment

| Variable | Overrides | Default | Read by |
|---|---|---|---|
| `EOS_PYTHON` | interpreter used to run EOS | `python3` | `bin/eos`, `bin/install.sh` |
| `EOS_INSTALL_PREFIX` | install root | `$HOME/.local` | `bin/install.sh` |
| `EOS_DATA_DIR` | runtime copies | `$PREFIX/share/eos` | `bin/install.sh` |
| `EOS_BIN_DIR` | launcher location | `$PREFIX/bin` | `bin/install.sh` |
| `EOS_UI_DB` | central instance registry | `~/.eos-ui/eos.db` | eos-ui |
| `EOS_UI_ALLOWED_HOSTS` | Host header allowlist | `127.0.0.1,localhost` | eos-ui server |
| `EOS_CORE_PATH` | core source for the UI | discovered | eos-ui |
| `EOS_GRAPHIFY_ROOT` | Graphify artifact root(s) to scan, `os.pathsep`-separated | unset (feature off) | eos-ui |
| `EOS_GRAPHIFY_REFRESH_CMD` | graph refresh command template (`{project}`, `{key}`) | unset | watcher queue |
| `EOS_GRAPHIFY_COORDINATOR_ROOT` | candidate coordinator script path(s), `os.pathsep`-separated | unset (feature off) | watcher queue |
| `EOS_GRAPHIFY_QUEUE_STATE` | queue state file | `~/.eos-ui/graphify-queue.json` | watcher queue |
| `EOS_PARENT_SEGMENT` | path segment name that marks a "parent" repository | unset (nothing is a parent) | eos-ui |

## Dependency tiers

EOS treats what it depends on as three tiers, not one requirements file,
because they fail differently (`core/preflight.py`, [ADR-010](docs/decisions/010-dependency-tiers.md)):

- **T1 — Python itself.** EOS can't install its own interpreter, so instead
  of a traceback it prints the exact command for your platform
  (`brew install python@3.12`, `apt install python3.12`, `pyenv install`).
- **T2 — the UI's dependencies** (FastAPI, uvicorn, pydantic, watchdog).
  Installed by EOS, after asking, into a venv EOS owns — never your system
  interpreter, never the project's own environment. `eos ui --yes` skips the
  prompt; `eos ui --no-install` reports what's missing and stops instead.
- **T3 — optional integrations**, like Graphify. Unset configuration means
  the feature is off, not broken.

The CLI core itself (everything except `eos ui`) has zero third-party
dependencies, on any tier.

## `eos bench`

```bash
eos bench /path/to/project --samples 50
```

Measures EOS's own tools against plain alternatives, **on the project you run
it against** — never a bundled fixture, so the number is always about your
code, not a demo. It measures two different kinds of claims differently
([ADR-011](docs/decisions/011-bench-ground-truth.md)):

- `find_symbol` has real ground truth: the language plugins already know
  which symbol is defined in which file, so `eos bench` samples symbols from
  that parsed set and checks whether `find_symbol` finds the right one.
- Impact analysis has no independently-known correct answer, so `eos bench`
  compares against `grep` as a labeled baseline, not a correct answer — and
  the report says so explicitly.

The report is scoped to the project it ran against and says so: a result on
a small Python codebase is not a claim about EOS on a large Java one.

## Design principles

1. **Zero external dependencies in the core runtime** — every project gets a
   self-contained Python stdlib runtime.
2. **Separation of runtime and data** — updates replace `runtime/` but never
   touch `data/`.
3. **Knowledge model as single source of truth** — plugins produce a semantic
   model; generators derive Markdown/Graph/JSON from it.
4. **Permanent ID** — each instance is identified by a UUID stored in
   `.eos/id.txt`, surviving path moves and renames.

## Architecture and decisions

`ARCHITECTURE.md` covers the data model and the pipeline in more depth.
Every non-trivial architectural choice — including the extraction into an
independent repository, the AI integration layer, dependency tiers, `eos
bench`'s ground-truth boundary, and the MIT license — is recorded as an ADR
in `docs/decisions/`, in context/decision/consequences form.

## Contributing

Issues and pull requests are welcome. A few things that keep contributions
easy to review:

- `core/` stays pure Python standard library — no third-party `import` under
  it, ever (see [ADR-001](docs/decisions/001-runtime-vs-core.md) and
  [ADR-010](docs/decisions/010-dependency-tiers.md)). New optional
  functionality goes in `ui/` or behind a T3 environment variable instead.
- Run `python3 -m pytest -q` before opening a PR; `bash tools/check-clean.sh .`
  also runs in CI and fails on any leftover internal identifier.
- New language support is a new plugin under `core/plugins/`, following the
  existing detector/parser/analyzer split — see ADR-004.
- A change to an architectural decision gets its own ADR, not a silent
  rewrite of an old one.

## License

MIT — see [LICENSE](LICENSE).
