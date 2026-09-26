---
name: brief
description: The EOS brief for a task, on request — its recorded procedure, last runs, lesson and the wrappers it calls for.
disable-model-invocation: true
argument-hint: "<task>"
---

!`eos brief "${CLAUDE_PROJECT_DIR:-.}" --task "$ARGUMENTS" --task-only --agent claude 2>/dev/null || echo "EOS is not installed here (no eos on PATH)."`

The block above is what EOS has recorded for this task (empty when nothing is).
Follow a recorded procedure's steps rather than improvising plausible ones, read
the last failure's lesson before repeating it, and use the wrappers it names.
