import os
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "tools" / "check-clean.sh"


def _run(target: Path, deny: str | None = None):
    env = {k: v for k, v in os.environ.items() if k != "EOS_CLEAN_DENY"}
    if deny is not None:
        env["EOS_CLEAN_DENY"] = deny
    return subprocess.run(
        ["bash", str(SCRIPT), str(target)],
        capture_output=True, text=True, env=env,
    )


def test_clean_tree_passes(tmp_path):
    (tmp_path / "a.py").write_text("def handler():\n    return 1\n", encoding="utf-8")
    assert _run(tmp_path, deny="acmecorp").returncode == 0


def test_planted_identifier_fails(tmp_path):
    (tmp_path / "b.py").write_text("# see ACME-1363 for context\n", encoding="utf-8")
    result = _run(tmp_path, deny="acme-[0-9]+")
    assert result.returncode == 1
    assert "ACME" in result.stdout + result.stderr


def test_private_denylist_takes_one_entry_per_line(tmp_path):
    (tmp_path / "b.py").write_text("vendor = 'globex'\n", encoding="utf-8")
    deny = "# company names\nacmecorp\n\nglobex\n"
    assert _run(tmp_path, deny=deny).returncode == 1


def test_identifier_is_not_flagged_without_the_private_denylist(tmp_path):
    (tmp_path / "b.py").write_text("vendor = 'globex'\n", encoding="utf-8")
    assert _run(tmp_path, deny="").returncode == 0


def test_planted_workspace_path_fails(tmp_path):
    (tmp_path / "c.py").write_text("ROOT = '/Volumes/Data/workspace/x'\n", encoding="utf-8")
    assert _run(tmp_path).returncode == 1


def test_repository_itself_is_clean():
    assert _run(REPO).returncode == 0, "the repo still contains denylisted identifiers"


def test_a_worktree_git_file_is_not_content(tmp_path):
    # In a `git worktree`, .git is a file naming the main checkout's path.
    (tmp_path / ".git").write_text("gitdir: /Volumes/Data/workspace/x/.git/worktrees/y\n", encoding="utf-8")
    (tmp_path / "a.py").write_text("def handler():\n    return 1\n", encoding="utf-8")
    assert _run(tmp_path).returncode == 0
