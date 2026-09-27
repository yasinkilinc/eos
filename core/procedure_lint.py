"""What makes a recorded procedure hard to follow or to verify (2.x roadmap E2).

A procedure is handed to an agent as "this project's way"; a step naming a tool
nobody can find, or a procedure with no stated success, is followed badly or
reported done without a check. For each step's `(tool: X)`:

  declared        a capability in capabilities.toml (by name or its run)  -- fine
  a program       found on PATH (git, curl)                               -- fine
  not declared    a script of that name exists in the project: declare it so
                  the brief names it and a raw bypass gets a hint
  unknown tool    none of the above

and a procedure without a `## Success` section cannot be verified. The typed
marks' own checks (an input nothing provides, on_failure loops) are `core.steps`.
"""
from __future__ import annotations

import shutil
from pathlib import Path

SCRIPT_DIRS = ("scripts", "automation", "bin", "tools")
# Names that resolve on PATH as a shell builtin's twin, not as a tool a step means.
BUILTINS = {"test", "true", "false", "echo", "printf", "read", "time", "cd", "type", "command", "wait",
            "kill", "[", "which", "yes", "sleep", "env"}
SCRIPT_SUFFIXES = ("", ".sh", ".py")


def _script(root: Path, tool: str) -> Path | None:
    candidate = root / tool
    if "/" in tool and candidate.is_file():
        return candidate
    name = Path(tool).name
    for directory in SCRIPT_DIRS:
        for suffix in SCRIPT_SUFFIXES:
            path = root / directory / f"{name}{suffix}"
            if path.is_file():
                return path
    return None


def lint(project_root: str | Path) -> list[dict]:
    from core import capabilities, executions, notes, steps

    root = Path(project_root).expanduser().resolve()
    try:
        declared_caps = capabilities.load(root)
    except Exception:  # noqa: BLE001 - a broken registry declares nothing
        declared_caps = []
    declared = {executions.normalize_tool(c.name) for c in declared_caps}
    declared |= {executions.normalize_tool(c.run) for c in declared_caps if c.run}
    reports = []
    for note in notes.load_notes(root):
        if note.kind != "procedure":
            continue
        problems = []
        for tool in notes.procedure_tools(note):
            key = executions.normalize_tool(tool)
            # A tool written as a path is a script, never the program its name
            # resembles (scripts/git.sh is not git).
            program = "/" not in tool and tool not in BUILTINS and bool(shutil.which(tool))
            if key in declared or program:
                continue
            script = _script(root, tool)
            if script is not None:
                problems.append(f"(tool: {tool}) -- {script.relative_to(root)} exists but is not declared "
                                "in capabilities.toml")
            else:
                problems.append(f"(tool: {tool}) -- unknown tool: no capability, program or script by that name")
        problems += steps.lint(steps.parse(note.body), note.body)
        if not notes.procedure_success(note):
            problems.append("no Success section: finishing it cannot be checked")
        reports.append({"procedure": note.procedure, "title": note.title, "path": str(note.path),
                        "problems": problems})
    return reports


def render(reports: list[dict]) -> str:
    total = sum(len(r["problems"]) for r in reports)
    lines = [f"{len(reports)} procedure(s), {total} problem(s)"]
    for report in reports:
        if report["problems"]:
            lines.append(f"  {report['procedure']}  {report['title']}")
            lines += [f"    - {problem}" for problem in report["problems"]]
    return "\n".join(lines)
