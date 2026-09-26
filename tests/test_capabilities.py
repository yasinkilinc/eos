"""capabilities.toml: what a project offers instead of a raw command (ADR-026)."""
import subprocess
import sys
from pathlib import Path

import pytest

from core import brief, capabilities

REPO = Path(__file__).resolve().parents[1]
EOS = [sys.executable, str(REPO / "core" / "eos.py")]

REGISTRY = """
[[capability]]
name = "tracker"
run = "scripts/tracker.sh"
does = "issue|search <KEY>"
words = ["tracker", "ticket"]
block = ['tracker-cli\\s']
hint = ['curl\\s[^|;&]*tracker\\.example']

[[capability]]
name = "db"
run = "scripts/db.sh"
words = ["database", "sql"]
hint = ['(?<![\\w/.-])psql\\s']
"""


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    assert subprocess.run(EOS + ["init", str(root), "--no-ai"], capture_output=True).returncode == 0
    (root / ".eos" / "knowledge").mkdir(parents=True, exist_ok=True)
    (root / ".eos" / "knowledge" / "capabilities.toml").write_text(REGISTRY, encoding="utf-8")
    return root


def test_the_registry_loads_in_file_order(project):
    found = capabilities.load(project)
    assert [c.name for c in found] == ["tracker", "db"]
    assert found[0].program == "tracker.sh"


@pytest.mark.parametrize("text, message", [
    ('[[capability]]\nrun = "x.sh"\n', "needs a name"),
    ('[[capability]]\nname = "a"\n', "needs `run`"),
    ('[[capability]]\nname = "a"\nrun = "x"\ncolour = "red"\n', "unknown key"),
    ('[[capability]]\nname = "a"\nrun = "x"\nhint = ["(unclosed"]\n', "not a regex"),
    ('[[capability]]\nname = "a"\nrun = "x"\n[[capability]]\nname = "a"\nrun = "y"\n', "used twice"),
])
def test_a_malformed_entry_is_refused_with_its_position(project, text, message):
    (project / ".eos" / "knowledge" / "capabilities.toml").write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        capabilities.load(project)


def test_no_file_is_no_capabilities(tmp_path):
    assert capabilities.load(tmp_path) == []


def test_block_is_found_before_hint_and_a_wrapper_call_is_never_a_raw_form(project):
    declared = capabilities.load(project)
    assert capabilities.match("tracker-cli search x", declared).tier == "block"
    found = capabilities.match("cd x && curl -s https://tracker.example/1 | jq .", declared)
    assert (found.capability.name, found.tier) == ("tracker", "hint")
    assert capabilities.match("scripts/tracker.sh search x | grep curl https://tracker.example", declared) is None
    assert capabilities.match("echo hello", declared) is None


def test_a_task_names_capabilities_by_their_declared_words(project):
    declared = capabilities.load(project)
    assert [c.name for c in capabilities.for_task("look at the ticket and the sql", declared)] == ["tracker", "db"]
    assert capabilities.for_task("refactor the parser", declared) == []


@pytest.mark.parametrize("command, expected", [
    ("ls -la", [("ls", None)]),
    ("cd sub && git -C x commit -m 'a; b' && ls", [("cd", None), ("git", "commit"), ("ls", None)]),
    ("FOO=1 BAR=2 python3 run.py | tee log", [("python3", None), ("tee", None)]),
    ("python3 - <<'EOF'\nimport os; print(1) | 2\nEOF\necho done", [("python3", None), ("echo", None)]),
    ("sudo -n env A=1 true", [("true", None)]),
    ("bash scripts/x.sh arg", [("x.sh", None)]),
])
def test_a_command_line_is_reduced_to_program_names(command, expected):
    assert capabilities.programs(command) == expected


def test_only_actions_are_worth_recording():
    assert capabilities.worth_recording("python3", None)
    assert capabilities.worth_recording("git", "push")
    assert not capabilities.worth_recording("git", "status")
    assert not capabilities.worth_recording("grep", None)


def test_the_task_brief_names_the_wrappers_a_task_calls_for(project):
    text = brief.build(project, task="find the ticket PROJ-12 in the tracker", task_only=True)
    assert "WRAPPERS FOR THIS TASK" in text and "tracker → scripts/tracker.sh" in text
    assert brief.build(project, task="rename a local variable", task_only=True) == ""


def test_the_cli_answers_for_a_task_and_for_a_command(project):
    listed = subprocess.run(EOS + ["capabilities", str(project)], capture_output=True, text=True)
    assert "tracker → scripts/tracker.sh" in listed.stdout and "db → scripts/db.sh" in listed.stdout
    covered = subprocess.run(EOS + ["capabilities", str(project), "--command", "psql -c 'select 1'"],
                             capture_output=True, text=True)
    assert covered.stdout.startswith("hint: `db` has a wrapper: scripts/db.sh")
    assert "no capability" in subprocess.run(EOS + ["capabilities", str(project), "--command", "ls"],
                                             capture_output=True, text=True).stdout


def test_a_capability_answered_by_a_harness_tool_is_never_mistaken_for_a_command(tmp_path):
    edit = capabilities.Capability(name="edit", run="Edit tool", hint=(r"(?<![\w-])sed\s+-i",))
    assert edit.program == ""
    assert capabilities.wrapper_in('git commit -m "Edit the file"', [edit]) is None
    assert capabilities.match("sed -i s/a/b/ README.md", [edit]).capability is edit
    assert capabilities.Capability(name="gh", run="gh pr view").program == "gh"
