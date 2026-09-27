"""`eos procedure lint` (2.x roadmap E2): what makes a procedure hard to follow or to verify."""
import subprocess
import sys
from pathlib import Path

from core import procedure_lint

EOS = [sys.executable, str(Path(__file__).resolve().parents[1] / "core" / "eos.py")]


def _project(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    assert subprocess.run(EOS + ["init", str(root), "--no-ai"], capture_output=True).returncode == 0
    knowledge = root / ".eos" / "knowledge"
    knowledge.mkdir(parents=True, exist_ok=True)
    (knowledge / "capabilities.toml").write_text('[[capability]]\nname = "tracker"\nrun = "scripts/tracker.sh"\n'
                                                 'does = "issue <KEY>"\n', encoding="utf-8")
    (root / "scripts").mkdir()
    (root / "scripts" / "deploy.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    return root


def _procedure(root, title, steps, success=None):
    args = EOS + ["procedure", "new", str(root), "--title", title, "--steps", "-"]
    if success:
        args += ["--success", success]
    done = subprocess.run(args, input="\n".join(steps) + "\n", capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    return done.stdout.split("\t")[0]


def test_lint_names_unknown_tools_undeclared_scripts_and_missing_success(tmp_path):
    root = _project(tmp_path)
    slug = _procedure(root, "Release the service", [
        "Read the ticket (tool: tracker)", "Deploy it (tool: deploy)", "Tag it (tool: git)",
        "Ask the release board (tool: board-bot)", "Announce it"])
    [report] = procedure_lint.lint(root)
    assert report["procedure"] == slug
    problems = "\n".join(report["problems"])
    assert "board-bot" in problems and "unknown tool" in problems
    assert "deploy" in problems and "not declared" in problems
    assert "tracker" not in problems and "(tool: git)" not in problems
    assert "no Success section" in problems


def test_a_clean_procedure_has_no_problems_and_the_cli_says_so(tmp_path):
    root = _project(tmp_path)
    _procedure(root, "Check a ticket", ["Read it (tool: tracker)"], success="the ticket is read")
    assert procedure_lint.lint(root)[0]["problems"] == []
    done = subprocess.run(EOS + ["procedure", "lint", str(root)], capture_output=True, text=True)
    assert done.returncode == 0 and "0 problem" in done.stdout


def test_a_wrapper_path_is_not_taken_for_the_program_it_is_named_after(tmp_path):
    """Branch review finding 8: (tool: scripts/git.sh) normalised to `git`, found
    git on PATH and hid an undeclared wrapper."""
    root = _project(tmp_path)
    (root / "scripts" / "git.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    _procedure(root, "Tag a release", ["Tag it (tool: scripts/git.sh)"], success="the tag exists")
    [report] = procedure_lint.lint(root)
    assert any("scripts/git.sh" in p and "not declared" in p for p in report["problems"]), report


def test_a_shell_builtin_name_is_not_a_tool(tmp_path):
    """Whole-branch review: `(tool: test)` passed because /bin/test exists."""
    root = _project(tmp_path)
    _procedure(root, "Run the suite", ["Run it (tool: test)"], success="green")
    [report] = procedure_lint.lint(root)
    assert any("(tool: test)" in p and "unknown tool" in p for p in report["problems"])
