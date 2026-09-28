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
    ("sed -i '' 's/alpha/beta/' notes.txt", [("sed", None)]),
])
def test_a_command_line_is_reduced_to_program_names(command, expected):
    assert capabilities.programs(command) == expected


def test_only_actions_are_worth_recording():
    assert capabilities.worth_recording("python3", None)
    assert capabilities.worth_recording("git", "push")
    assert not capabilities.worth_recording("git", "status")
    assert not capabilities.worth_recording("grep", None)
    assert not capabilities.worth_recording("eos", None), "EOS records its own commands"


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


# --- A4: the six-state truth model (report line 810/1545) ----------------------------------


def test_an_undeclared_name_is_not_even_catalogued(project):
    assert capabilities.truth(project, "nope") is None


def test_a_capability_with_no_does_stops_at_registered(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    assert subprocess.run(EOS + ["init", str(root), "--no-ai"], capture_output=True).returncode == 0
    (root / ".eos" / "knowledge").mkdir(parents=True, exist_ok=True)
    (root / ".eos" / "knowledge" / "capabilities.toml").write_text(
        '[[capability]]\nname = "bare"\nrun = "scripts/bare.sh"\n', encoding="utf-8")
    found = capabilities.truth(root, "bare")
    assert found.state == "registered" and found.health == "unknown"


def test_a_documented_but_unreachable_capability_stops_at_configured(project):
    # "tracker" is documented (has `does`) but scripts/tracker.sh does not exist here.
    found = capabilities.truth(project, "tracker")
    assert found.state == "configured"
    assert "not on PATH" in found.detail or "script" in found.detail


def test_a_reachable_capability_without_verify_before_use_is_authorized(project):
    (project / "scripts").mkdir()
    (project / "scripts" / "tracker.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    found = capabilities.truth(project, "tracker")
    assert found.state == "authorized" and found.health == "unknown"


def test_verify_before_use_holds_a_reachable_capability_below_authorized(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    assert subprocess.run(EOS + ["init", str(root), "--no-ai"], capture_output=True).returncode == 0
    (root / ".eos" / "knowledge").mkdir(parents=True, exist_ok=True)
    (root / ".eos" / "knowledge" / "capabilities.toml").write_text(
        '[[capability]]\nname = "risky"\nrun = "scripts/risky.sh"\ndoes = "does a risky thing"\n'
        'verify_before_use = true\n', encoding="utf-8")
    (root / "scripts").mkdir()
    (root / "scripts" / "risky.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    found = capabilities.truth(root, "risky")
    assert found.state == "reachable" and found.health == "unknown"
    assert "not authorized" in found.detail


def test_a_program_on_path_is_reachable_with_no_project_file(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    assert subprocess.run(EOS + ["init", str(root), "--no-ai"], capture_output=True).returncode == 0
    (root / ".eos" / "knowledge").mkdir(parents=True, exist_ok=True)
    (root / ".eos" / "knowledge" / "capabilities.toml").write_text(
        '[[capability]]\nname = "gitcli"\nrun = "git status"\ndoes = "reads status"\n', encoding="utf-8")
    found = capabilities.truth(root, "gitcli")
    assert found.state == "authorized"


def test_a_harness_tool_capability_is_reachable_with_nothing_to_find_on_disk(tmp_path):
    """program == "" (run = "Edit tool", not a command line) -- nothing to
    check on PATH or in the project, so it is always reachable."""
    root = tmp_path / "proj"
    root.mkdir()
    assert subprocess.run(EOS + ["init", str(root), "--no-ai"], capture_output=True).returncode == 0
    (root / ".eos" / "knowledge").mkdir(parents=True, exist_ok=True)
    (root / ".eos" / "knowledge" / "capabilities.toml").write_text(
        '[[capability]]\nname = "edit"\nrun = "Edit tool"\ndoes = "edits a file"\n', encoding="utf-8")
    found = capabilities.truth(root, "edit")
    assert found.state == "authorized"


def test_truth_degrades_on_a_broken_registry_rather_than_crashing(project, monkeypatch):
    """RV (day3b, item 3-4): truth()'s own docstring promises None for 'a
    broken registry' -- but it only caught ValueError, while capabilities.toml
    reads through core.lib.config_io.ConfigIO.read_toml's plain `open()`,
    which can raise OSError (permission denied, a path that stopped being a
    file). Every other caller of capabilities.load() in this codebase
    (procedure_lint.lint, context_cost.report) already catches Exception for
    exactly this reason."""
    from core.lib.config_io import ConfigIO

    def _broken(path):
        raise OSError("permission denied")

    monkeypatch.setattr(ConfigIO, "read_toml", staticmethod(_broken))
    assert capabilities.truth(project, "tracker") is None


def test_verify_before_use_must_be_a_bool(project):
    (project / ".eos" / "knowledge" / "capabilities.toml").write_text(
        '[[capability]]\nname = "a"\nrun = "x"\nverify_before_use = "yes"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="verify_before_use"):
        capabilities.load(project)


def test_verify_before_use_marks_the_printed_line():
    marked = capabilities.Capability(name="risky", run="scripts/risky.sh", does="risky",
                                     verify_before_use=True)
    plain = capabilities.Capability(name="safe", run="scripts/safe.sh", does="safe")
    assert "verify before use" in marked.line()
    assert "verify before use" not in plain.line()


def test_the_cli_prints_the_status_of_one_capability(project):
    done = subprocess.run(EOS + ["capabilities", str(project), "--status", "tracker"],
                          capture_output=True, text=True)
    assert done.returncode == 0
    assert "configured" in done.stdout


def test_the_cli_status_of_an_unknown_name_says_not_catalogued(project):
    done = subprocess.run(EOS + ["capabilities", str(project), "--status", "nope"],
                          capture_output=True, text=True)
    assert "not catalogued" in done.stdout
