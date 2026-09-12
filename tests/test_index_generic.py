"""The index must build on a project with no journey/flow concepts at all."""
import os
import re
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest

from core import index

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


def _minimal_project(tmp_path: Path) -> Path:
    root = tmp_path / "acme-orders"
    (root / ".eos" / "data").mkdir(parents=True)
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text("def handler():\n    return 1\n", encoding="utf-8")
    (root / ".eos" / "config.toml").write_text(
        '[project]\nname = "acme-orders"\n', encoding="utf-8"
    )
    return root


def test_build_succeeds_without_journey_tables(tmp_path):
    root = _minimal_project(tmp_path)
    result = index.build(root)
    assert result is not None

    conn = sqlite3.connect(index.db_path(root))
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    conn.close()

    assert "note" in tables
    assert "git_commit" in tables
    for gone in ("journey_snapshot", "journey_step", "flow_step", "journey_doc"):
        assert gone not in tables, f"{gone} should no longer exist"


def test_counts_have_no_journey_row(tmp_path):
    assert "journey steps" not in index._COUNTS


def test_default_ticket_pattern_is_generic():
    pattern = re.compile(index.TICKET_PATTERN)
    assert pattern.search("PROJ-123 fix the thing")
    assert pattern.search("ACME-4567 refactor")
    assert not pattern.search("no ticket here")


def test_default_ticket_pattern_has_no_company_prefixes():
    # A company-specific allowlist needs regex alternation to enumerate more
    # than one exact prefix; a default with no "|" cannot be doing that. This
    # asserts the property without naming any prefix literally, so the test
    # itself never carries a token a repository-wide denylist gate would flag.
    assert "|" not in index.TICKET_PATTERN
    assert index.TICKET_PATTERN == r"\b[A-Z][A-Z0-9]+-[0-9]+\b"


def _write_index_config(root: Path, text: str) -> None:
    (root / ".eos").mkdir(parents=True, exist_ok=True)
    (root / ".eos" / "config.toml").write_text(text, encoding="utf-8")


def _git_env(tmp_path: Path) -> dict:
    config = tmp_path / "gitconfig"
    if not config.exists():
        config.write_text("", encoding="utf-8")
    env = os.environ.copy()
    env.update({
        "GIT_CONFIG_GLOBAL": str(config),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "Dev One",
        "GIT_AUTHOR_EMAIL": "dev.one@example.com",
        "GIT_COMMITTER_NAME": "Dev One",
        "GIT_COMMITTER_EMAIL": "dev.one@example.com",
    })
    return env


def _git(cwd: Path, env: dict, *args: str) -> str:
    done = subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    return done.stdout.strip()


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_merge_branch_pattern_is_none_when_unset(tmp_path):
    _write_index_config(tmp_path, "[index]\nticket_pattern = '[A-Z]+-[0-9]+'\n")
    assert index._merge_branch_pattern(tmp_path) is None


def test_merge_branch_pattern_returns_the_configured_pattern(tmp_path):
    _write_index_config(
        tmp_path, "[index]\nmerge_branch_pattern = '^Merge in \\S+ from (\\S+) to \\S+'\n"
    )
    pattern = index._merge_branch_pattern(tmp_path)
    assert pattern is not None
    # A real commit body rarely puts the anchored line at position 0 -- a
    # trailer or a blank line usually precedes it. Proves the pattern is
    # compiled with re.MULTILINE, not just anchored to the start of the body.
    body = "Approved-by: reviewer\n\nMerge in TEAM/repo from feature/PROJ-1 to master"
    assert pattern.findall(body) == ["feature/PROJ-1"]


def test_merge_branch_pattern_rejects_an_invalid_regex(tmp_path):
    _write_index_config(tmp_path, "[index]\nmerge_branch_pattern = '('\n")
    with pytest.raises(index.IndexBuildError, match="merge_branch_pattern"):
        index._merge_branch_pattern(tmp_path)


def test_sources_digest_changes_when_merge_branch_pattern_is_configured(tmp_path):
    root = _minimal_project(tmp_path)
    index.build(root)
    before = index._sources_digest(root)

    config = root / ".eos" / "config.toml"
    config.write_text(
        config.read_text(encoding="utf-8")
        + "\n[index]\nmerge_branch_pattern = '^Merge in \\S+ from (\\S+) to \\S+'\n",
        encoding="utf-8",
    )
    after = index._sources_digest(root)

    assert after != before


@needs_git
def test_merge_branch_pattern_extracts_a_branch_ticket_end_to_end(tmp_path):
    """A real merge commit's body rarely puts the anchored line at position 0
    -- a trailer or a blank line commonly precedes it. Drives a real
    index.build() over real git history shaped exactly that way, so this only
    passes if merge_branch_pattern is applied with re.MULTILINE."""
    root = _minimal_project(tmp_path)
    env = _git_env(tmp_path)
    _write(root / ".gitignore", ".eos/\n")
    _git(root, env, "init", "-q", "-b", "master")
    _git(root, env, "add", "-A")
    _git(root, env, "commit", "-q", "-m", "initial commit")
    _git(root, env, "checkout", "-q", "-b", "feature/PROJ-9")
    (root / "src" / "extra.py").write_text("def extra():\n    return 2\n", encoding="utf-8")
    _git(root, env, "add", "-A")
    _git(root, env, "commit", "-q", "-m", "add extra handler")
    _git(root, env, "checkout", "-q", "master")
    _git(
        root, env, "merge", "-q", "--no-ff", "feature/PROJ-9",
        "-m", "Merge feature branch",
        "-m", "Approved-by: reviewer",
        "-m", "Merge in TEAM/repo from feature/PROJ-9 to master",
    )
    config = root / ".eos" / "config.toml"
    config.write_text(
        config.read_text(encoding="utf-8")
        + "\n[index]\nmerge_branch_pattern = '^Merge in \\S+ from (\\S+) to \\S+'\n",
        encoding="utf-8",
    )

    db = index.build(root).path
    conn = sqlite3.connect(db)
    tickets = conn.execute("SELECT key, source FROM git_commit_ticket WHERE source = 'branch'").fetchall()
    conn.close()

    assert tickets == [("PROJ-9", "branch")]
