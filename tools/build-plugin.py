#!/usr/bin/env python3
"""Render the Claude Code plugin's skill and agent from the engine's templates.

The plugin (`plugin/`) is how Claude Code gets EOS's hooks, its skill and its
researcher agent without anything written into a project (ADR-026). The skill
and agent text lives once, in `core/ai/templates/`, for both delivery modes;
this writes the plugin's copies, and `tests/test_plugin.py` fails when they
drift. Run it after editing a template or bumping `core/VERSION`:

    python3 tools/build-plugin.py          # write
    python3 tools/build-plugin.py --check  # exit 1 if anything would change
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from core.ai import writer  # noqa: E402

# The researcher's envelope in the plugin: read-only tools, the cheaper model
# the plan measured as sufficient for evidence gathering, low effort, a turn
# cap. Plugin agents may not declare hooks, MCP servers or a permission mode
# (they are ignored), so none is attempted.
AGENT_FRONT = """---
name: eos-researcher
description: Answers a question about this codebase with evidence — starts from what earlier sessions learned (EOS notes and runs), proves it from the real source with path:line, and records what a future scan could not re-derive.
model: sonnet
effort: low
maxTurns: 12
tools: Read, Grep, Glob, Bash
disallowedTools: Write, Edit, NotebookEdit
---
"""

BRIEF_SKILL = """---
name: brief
description: The EOS brief for a task, on request — its recorded procedure, last runs, lesson and the wrappers it calls for.
disable-model-invocation: true
argument-hint: "<task>"
---

!`eos brief "${CLAUDE_PROJECT_DIR:-.}" --task "$ARGUMENTS" --task-only --agent claude 2>/dev/null || echo "EOS is not installed here (no eos on PATH)."`

The block above is what EOS has recorded for this task (empty when nothing is).
Follow a recorded procedure's steps rather than improvising plausible ones, read
the last failure's lesson before repeating it, and use the wrappers it names.
"""


def _body(text: str) -> str:
    end = text.index("\n---", 3)
    return text[end + len("\n---"):].lstrip("\n")


def outputs() -> dict[Path, str]:
    version = (REPO / "core" / "VERSION").read_text(encoding="utf-8").strip()
    skill = writer._render("skill.md", version, "cli")
    agent = AGENT_FRONT + "\n" + _body(writer._render("agent.md", version, "cli"))
    manifest_path = REPO / "plugin" / ".claude-plugin" / "plugin.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["version"] = version
    return {
        REPO / "plugin" / "skills" / "eos" / "SKILL.md": skill,
        REPO / "plugin" / "skills" / "brief" / "SKILL.md": BRIEF_SKILL,
        REPO / "plugin" / "agents" / "eos-researcher.md": agent,
        manifest_path: json.dumps(manifest, indent=2) + "\n",
    }


def main(argv: list[str]) -> int:
    check = "--check" in argv
    stale = []
    for path, text in outputs().items():
        current = path.read_text(encoding="utf-8") if path.is_file() else None
        if current == text:
            continue
        stale.append(path.relative_to(REPO))
        if not check:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
    for path in stale:
        print(("stale: " if check else "wrote: ") + str(path))
    return 1 if check and stale else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
