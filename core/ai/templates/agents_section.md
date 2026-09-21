## EOS

This project is indexed by EOS {{VERSION}} — parsed symbols, imports, a
project graph, git history, notes recorded by earlier sessions, and a ledger
of what those sessions took on, all under `.eos/`. `eos scan` refreshes the
index; a stale index is the usual reason an answer looks wrong.

Start with one command:

```bash
eos brief .
```

It prints what is in flight here — claimed, blocked, stale, or held by two
sessions at once — and the notes that match this branch. Nothing else can tell
you that another session is already on the thing you are about to start. Where
`.claude/hooks/eos-brief.py` is installed it has already run at session start.

Two habits are worth keeping regardless of the tool: claim work before you
start it (`eos work add . --title "..." --claim`), and record a note afterwards
if you learned something a future `eos scan` could not re-derive
(`eos note add .`).

<!-- eos:mcp-only:begin -->
EOS's read-only tools are also registered as an MCP server in `.mcp.json`.
<!-- eos:mcp-only:end -->
For what each command costs and when a plain file read wins instead, see
`.claude/skills/eos/SKILL.md`.
