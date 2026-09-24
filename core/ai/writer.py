"""Write the AI-integration surfaces into a project.

Nothing here overwrites a user's file. Markdown surfaces carry an EOS block
between markers and everything outside it is preserved verbatim; .mcp.json and
.claude/settings.json are edited key-wise so other servers and other hooks
survive. Re-running refreshes only the EOS block, which is what makes
`eos ai update` safe.

**A project gets one surface, not two.** An MCP registration costs its tool
roster in every request whether or not a tool is called -- measured on an
18-service workspace at 2,194 tokens per session, 39% of that workspace's
entire MCP surface, paid before anything was asked. The CLI costs nothing
until it is run and takes the project as an argument, so one binary covers
every service. Shipping both does not give an agent options; it charges for
one path, and adds a decision about which to take to a tool whose adoption
problem was already that reaching for it was a decision. `surface="cli"` is
therefore the default, and the skill an agent loads describes only the surface
that project actually has.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

BEGIN = "<!-- eos:begin -->"
END = "<!-- eos:end -->"

SURFACES = ("cli", "mcp", "both")

# The skill is one file for both surfaces, with the MCP half fenced off. Two
# templates would be two documents to keep true, which is the failure the
# document tests exist to catch.
_MCP_ONLY = re.compile(
    r"<!-- eos:mcp-only:begin -->.*?<!-- eos:mcp-only:end -->\n?", re.DOTALL)

_TEMPLATES = Path(__file__).resolve().parent / "templates"


def _render(name: str, version: str, surface: str = "cli") -> str:
    text = (_TEMPLATES / name).read_text(encoding="utf-8")
    if surface == "cli":
        text = _MCP_ONLY.sub("", text)
    else:
        text = text.replace("<!-- eos:mcp-only:begin -->\n", "")
        text = text.replace("<!-- eos:mcp-only:end -->\n", "")
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


HOOK_FILE = ".claude/hooks/eos-brief.py"
PROMPT_HOOK_FILE = ".claude/hooks/eos-prompt.py"
STOP_HOOK_FILE = ".claude/hooks/eos-close.py"

# The loop, in the order a session lives it: what is in flight when it opens,
# what is recorded about the task it is given, what it left behind when it
# ends. The prompt hook is the one that knows the task (ADR-022/023, M3).
_HOOKS = (
    ("SessionStart", HOOK_FILE, "session_start.py"),
    ("UserPromptSubmit", PROMPT_HOOK_FILE, "prompt_submit.py"),
    ("Stop", STOP_HOOK_FILE, "session_stop.py"),
)


def _write_hooks(root: Path, version: str) -> list[Path]:
    """The hooks: the surfaces where reaching for EOS is not a choice."""
    written = []
    for _event, relative, template in _HOOKS:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_render(template, version), encoding="utf-8")
        path.chmod(0o755)
        written.append(path)
    return written


def _register_hooks(root: Path) -> Path:
    """Add the hook entries to .claude/settings.json, keeping the rest.

    Matched on the script path rather than the whole command, so a project
    that edited the invocation (a different interpreter, an env prefix) gets
    its version refreshed rather than a second copy appended on every update.
    """
    path = root / ".claude" / "settings.json"
    data = {}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            raise SystemExit(f"eos: {path} is not valid JSON; fix it or move it aside.")
        if not isinstance(data, dict):
            raise SystemExit(f"eos: {path} is not a JSON object; fix it or move it aside.")

    hooks = data.setdefault("hooks", {})
    for event, relative, _template in _HOOKS:
        registered = hooks.setdefault(event, [])
        if not isinstance(registered, list):
            raise SystemExit(f"eos: hooks.{event} in {path} is not a list; fix it.")
        command = 'python3 "$CLAUDE_PROJECT_DIR/' + relative + '"'
        for existing in registered:
            commands = existing.get("hooks", []) if isinstance(existing, dict) else []
            if any(relative in str(item.get("command", "")) for item in commands
                   if isinstance(item, dict)):
                break
        else:
            registered.append({"hooks": [{"type": "command", "command": command}]})

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


def mcp_registered(root: Path) -> bool:
    """Whether this project still pays for an MCP tool roster."""
    path = Path(root) / ".mcp.json"
    if not path.is_file():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    return isinstance(data, dict) and "eos" in (data.get("mcpServers") or {})


def write_all(root: Path, version: str, agents_md: bool = True,
              surface: str = "cli") -> list[Path]:
    """Write every integration surface for `version`.

    `agents_md=False` leaves AGENTS.md alone. It is the one surface EOS does
    not own: in a repository whose AGENTS.md is tracked and governed by
    someone else, refreshing the skill for a new engine version should not
    also modify a committed file. Without the option the choice is between a
    stale skill and a dirty working tree, and the stale skill wins by default
    -- measured on one service, whose generated skill sat two engine versions
    behind while the engine itself was current.

    `surface` decides whether this project registers an MCP server at all.
    The hook and the skill are written either way: they are what makes the
    engine reached without a decision, and that is the part that was missing.
    """
    if surface not in SURFACES:
        raise ValueError(f"surface must be one of {', '.join(SURFACES)}; got {surface!r}")
    root = Path(root)
    written: list[Path] = []

    skill = root / ".claude" / "skills" / "eos" / "SKILL.md"
    skill.parent.mkdir(parents=True, exist_ok=True)
    skill.write_text(_render("skill.md", version, surface), encoding="utf-8")
    written.append(skill)

    agent = root / ".claude" / "agents" / "eos-researcher.md"
    agent.parent.mkdir(parents=True, exist_ok=True)
    agent.write_text(_render("agent.md", version, surface), encoding="utf-8")
    written.append(agent)

    written.extend(_write_hooks(root, version))
    written.append(_register_hooks(root))

    if surface in ("mcp", "both"):
        written.append(_write_mcp(root))

    if agents_md:
        path = root / "AGENTS.md"
        upsert_block(path, _render("agents_section.md", version, surface))
        written.append(path)

    return written
