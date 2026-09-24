"""Tool and change memory (M5): which tools a task used, and what a run changed.

Tool names are normalised so a wrapper counted under three spellings is one
tool; a run's changes are references -- recorded paths and a commit range
resolved through git -- never content.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

from core import executions

REPO = Path(__file__).resolve().parents[1]
EOS = [sys.executable, str(REPO / "core" / "eos.py")]


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    (root / ".eos").mkdir(parents=True)
    monkeypatch.setenv("EOS_STATE_DIR", str(tmp_path / "state"))
    return root


@pytest.mark.parametrize("spelling", ["jenkins", "Jenkins", "automation/jenkins.sh", "./jenkins.sh",
                                      "/abs/path/JENKINS.py"])
def test_every_spelling_of_a_wrapper_is_one_tool(spelling):
    assert executions.normalize_tool(spelling) == "jenkins"


def test_a_tool_the_shell_helper_spelled_with_a_path_is_normalised_on_read(project, tmp_path):
    executions.start(project, "Deploy", session="s-sh")
    subprocess.run(["/bin/sh", str(REPO / "bin" / "eos-event"), "--kind", "ran",
                    "--tool", "automation/Jenkins.sh", "--target", "ENV1"],
                   env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path),
                        "EOS_STATE_DIR": os.environ["EOS_STATE_DIR"], "CLAUDE_CODE_SESSION_ID": "s-sh"})
    event = executions.load(project)[0].events[0]
    assert (event.tool, event.target) == ("jenkins", "env1")


def _run(project, outcome, calls, target="env1"):
    run = executions.start(project, "Deploy", procedure="deploy", target=target, session="s")
    for tool, code in calls:
        executions.event(project, run.id, kind="ran", tool=tool, target=target, exit_code=code)
    executions.finish(project, run.id, outcome=outcome, lesson="broke" if outcome == "failed" else None)
    return run


def test_tools_counts_calls_runs_failures_and_the_last_outcome(project):
    _run(project, "ok", [("jenkins", 0), ("argo", 0)])
    _run(project, "failed", [("jenkins", 1), ("jenkins", 0)])

    used = {u.tool: u for u in executions.tools(project, procedure="deploy")}

    assert (used["jenkins"].count, used["jenkins"].runs, used["jenkins"].failures) == (3, 2, 1)
    assert used["jenkins"].last_outcome == "failed" and used["argo"].last_outcome == "ok"
    assert [u.tool for u in executions.tools(project)] == ["jenkins", "argo"]


def test_tools_filters_by_target(project):
    _run(project, "ok", [("jenkins", 0)], target="env0")
    _run(project, "ok", [("argo", 0)], target="env1")
    assert [u.tool for u in executions.tools(project, target="ENV1")] == ["argo"]


def _git(root, *args):
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})


def test_diff_lists_recorded_paths_and_the_commits_and_files_of_the_run(project):
    _git(project, "init", "-q")
    (project / "App.java").write_text("class App {}\n")
    _git(project, "add", "App.java")
    _git(project, "commit", "-q", "-m", "before the run")

    run = executions.start(project, "Change the app", session="s")
    (project / "App.java").write_text("class App { int v; }\n")
    (project / "Other.java").write_text("class Other {}\n")
    _git(project, "add", "-A")
    _git(project, "commit", "-q", "-m", "during the run")
    executions.event(project, run.id, kind="changed", ref="App.java")
    executions.finish(project, run.id, outcome="ok")

    delta = executions.diff(project, run.id)

    assert delta["paths"] == ["App.java"]
    assert [subject for _, subject in delta["commits"]] == ["during the run"]
    assert set(delta["committed_paths"]) >= {"App.java", "Other.java"}


def test_diff_of_a_run_that_committed_nothing_says_so(project):
    _git(project, "init", "-q")
    (project / "a").write_text("x")
    _git(project, "add", "a")
    _git(project, "commit", "-q", "-m", "one")
    run = executions.start(project, "Look around", session="s")
    executions.finish(project, run.id, outcome="ok")

    shown = subprocess.run(EOS + ["run", "diff", str(project), run.id], capture_output=True, text=True)

    assert "no commit made during the run" in shown.stdout


def test_the_cli_prints_tool_memory(project):
    _run(project, "ok", [("jenkins", 0)])
    shown = subprocess.run(EOS + ["run", "tools", str(project)], capture_output=True, text=True)
    assert shown.stdout.startswith("jenkins") and "last run ok" in shown.stdout
