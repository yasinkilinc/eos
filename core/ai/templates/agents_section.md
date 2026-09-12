## EOS

This project is indexed by EOS {{VERSION}} — parsed symbols, imports, a
project graph, git history, and notes recorded by earlier sessions, all in
`.eos/data/eos.db`. It is read-only except for adding notes; `eos scan`
refreshes it, and a stale index is the usual reason an answer looks wrong.

Its tools are registered as an MCP server in `.mcp.json`. Two habits are worth
keeping regardless of the tool: search recorded notes before starting on a
subject, and record a note afterward if you learned something a future
`eos scan` could not re-derive on its own.

For what each tool costs and when a plain file read wins instead, see
`.claude/skills/eos/SKILL.md`.
