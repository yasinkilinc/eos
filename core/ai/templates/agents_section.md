## EOS

This project is indexed by EOS {{VERSION}} — parsed symbols, imports, a
project graph, git history, notes recorded by earlier sessions, and a ledger
of what those sessions took on, all under `.eos/`. `eos scan` refreshes the
index; a stale index is the usual reason an answer looks wrong.

Start with one command, and give it the task once you have one:

```bash
eos brief .
eos brief . --task "<what you were asked to do>"
```

The first prints what is in flight here — claimed, blocked, stale, held by two
sessions at once, or left running — and the notes that match this branch. The
second prints the procedure this project follows for the task, its last runs
and what the failed ones taught. Where EOS's hooks are installed (the Claude
Code plugin, or `.claude/hooks/eos-*.py`), both have already run.

Where the project declares wrappers for its external systems, `eos
capabilities .` lists them and the task brief names the ones a task calls
for. Use the wrapper rather than the raw command it covers: it keeps the
output small, applies the project's safety checks and records the call.

Where the project turned routing on, the task brief ends with a `ROUTE` line:
the model and effort this project's policy picks for the task, and why. Use
that model when you spawn work for the task (a subagent's `model` argument),
set the effort it names for the session if you can, and keep any model or
effort the user asked for explicitly. `eos route . "<task>"` prints the whole
decision with its factors; EOS only advises and never calls a model itself.

Around a task someone will ask about later: `eos run start . --title "..."`
before the first action, `eos run finish . --outcome ok|failed|abandoned`
after (a failed run needs `--lesson`). Claim work before you start it
(`eos work add . --title "..." --claim`), and record a note afterwards if you
learned something a future `eos scan` could not re-derive (`eos note add .`).

<!-- eos:mcp-only:begin -->
EOS's read-only tools are also registered as an MCP server in `.mcp.json`.
<!-- eos:mcp-only:end -->
For what each command costs and when a plain file read wins instead, see the
`eos` skill (the Claude Code plugin's, or `.claude/skills/eos/SKILL.md`).
