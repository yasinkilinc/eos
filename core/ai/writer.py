"""Write the AI-integration surfaces into a project.

Nothing here overwrites a user's file. Markdown surfaces carry an EOS block
between markers and everything outside it is preserved verbatim; .mcp.json is
edited key-wise so other servers survive. Re-running refreshes only the EOS
block, which is what makes `eos ai update` safe.
"""
from __future__ import annotations

import json
from pathlib import Path

BEGIN = "<!-- eos:begin -->"
END = "<!-- eos:end -->"

_TEMPLATES = Path(__file__).resolve().parent / "templates"


def _render(name: str, version: str) -> str:
    text = (_TEMPLATES / name).read_text(encoding="utf-8")
    return text.replace("{{VERSION}}", version)


def upsert_block(path: Path, body: str) -> bool:
    """Put `body` between the EOS markers in `path`, preserving the rest."""
    block = f"{BEGIN}\n{body.rstrip()}\n{END}\n"
    path.parent.mkdir(parents=True, exist_ok=True)

    if not path.exists():
        path.write_text(block, encoding="utf-8")
        return True

    old = path.read_text(encoding="utf-8")
    if BEGIN in old and END in old:
        head, _, rest = old.partition(BEGIN)
        _, _, tail = rest.partition(END)
        new = f"{head}{block.rstrip()}{tail}"
    else:
        separator = "" if old.endswith("\n\n") else ("\n" if old.endswith("\n") else "\n\n")
        new = f"{old}{separator}{block}"

    if new == old:
        return False
    path.write_text(new, encoding="utf-8")
    return True


def _write_mcp(root: Path) -> Path:
    path = root / ".mcp.json"
    data = {}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            # A malformed file is the user's, not ours to discard.
            raise SystemExit(f"eos: {path} is not valid JSON; fix it or move it aside.")
    servers = data.setdefault("mcpServers", {})
    servers["eos"] = {"command": "eos", "args": ["mcp", "."]}
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


def write_all(root: Path, version: str) -> list[Path]:
    root = Path(root)
    written: list[Path] = []

    skill = root / ".claude" / "skills" / "eos" / "SKILL.md"
    skill.parent.mkdir(parents=True, exist_ok=True)
    skill.write_text(_render("skill.md", version), encoding="utf-8")
    written.append(skill)

    agent = root / ".claude" / "agents" / "eos-researcher.md"
    agent.parent.mkdir(parents=True, exist_ok=True)
    agent.write_text(_render("agent.md", version), encoding="utf-8")
    written.append(agent)

    written.append(_write_mcp(root))

    agents_md = root / "AGENTS.md"
    upsert_block(agents_md, _render("agents_section.md", version))
    written.append(agents_md)

    return written
