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
and what the failed ones taught. Where `.claude/hooks/eos-brief.py` and
`.claude/hooks/eos-prompt.py` are installed, both have already run.

Around a task someone will ask about later: `eos run start . --title "..."`
before the first action, `eos run finish . --outcome ok|failed|abandoned`
after (a failed run needs `--lesson`). Claim work before you start it
(`eos work add . --title "..." --claim`), and record a note afterwards if you
learned something a future `eos scan` could not re-derive (`eos note add .`).

<!-- eos:mcp-only:begin -->
EOS's read-only tools are also registered as an MCP server in `.mcp.json`.
<!-- eos:mcp-only:end -->
For what each command costs and when a plain file read wins instead, see
`.claude/skills/eos/SKILL.md`.
