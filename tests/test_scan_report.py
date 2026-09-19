"""A scan must say what it did not index.

Silence reads as absence: a scan of nuitka reported 803 files and said nothing
about the 730 real source modules it dropped under build/, so the artifacts
described a project a third of its actual size as if that were the whole of it.
"""
import shutil
from pathlib import Path

from core.lib.cache_store import CacheStore
from core.scanner import Scanner

FIXTURE = Path(__file__).parent / "fixtures" / "polyglot_project"


def _copy(tmp_path: Path) -> Path:
    root = tmp_path / "polyglot"
    shutil.copytree(FIXTURE, root)
    return root


def _scan(root: Path):
    return Scanner(root, CacheStore(root / ".eos" / "data" / "cache")).scan(full=True)


def test_scan_reports_paths_dropped_by_ignore_rules(tmp_path):
    project = _scan(_copy(tmp_path))

    # build/real_source.py is real source excluded by the default ignore list.
    assert project.report.skipped_by_ignore.get("build", 0) >= 1, (
        f"build/ was dropped without being reported: {project.report.skipped_by_ignore}"
    )
    assert project.report.files_parsed > 0


def test_scan_reports_files_in_languages_it_cannot_parse(tmp_path):
    root = _copy(tmp_path)
    (root / "native").mkdir()
    (root / "native" / "engine.c").write_text("int main(void) { return 0; }\n", encoding="utf-8")
    (root / "native" / "engine.h").write_text("int main(void);\n", encoding="utf-8")

    project = _scan(root)

    unsupported = project.report.unsupported_extensions
    assert unsupported.get(".c", 0) == 1, f"unindexed languages not reported: {unsupported}"
    assert unsupported.get(".h", 0) == 1


def test_gitignore_directory_pattern_is_honoured(tmp_path):
    """`generated/` is gitignore's canonical directory form, and what GitHub's
    own templates emit. fnmatch cannot match it against generated/stub.js
    unless the trailing slash is stripped first."""
    root = _copy(tmp_path)
    assert (root / ".gitignore").read_text(encoding="utf-8").strip() == "generated/"

    paths = set(Scanner(root, CacheStore(root / ".eos" / "data" / "cache"))._walk(root))

    assert "generated/stub.js" not in paths, (
        "a .gitignore directory pattern was silently ignored"
    )


def test_a_default_ignore_can_be_turned_off(tmp_path):
    """nuitka keeps 730 modules under build/. Without a way to un-ignore a
    default, such a project can never be indexed at all."""
    root = _copy(tmp_path)
    (root / ".eos").mkdir(parents=True, exist_ok=True)
    (root / ".eos" / "config.toml").write_text(
        '[project]\nname = "polyglot"\n\n[scan]\nunignore = ["build"]\n',
        encoding="utf-8",
    )

    paths = set(Scanner(root, CacheStore(root / ".eos" / "data" / "cache"))._walk(root))

    assert "build/real_source.py" in paths, "[scan] unignore had no effect"


def test_unignore_also_reaches_gitignore_rules(tmp_path):
    """cmd_scan prints '[scan] unignore' as the remedy for every skipped path,
    but unignore only removed from ignore_dirs while .gitignore lines live in
    ignore_patterns -- so for a gitignore rule the user follows the printed
    instruction, nothing changes, and there is no next step."""
    root = _copy(tmp_path)
    (root / ".eos").mkdir(parents=True, exist_ok=True)
    (root / ".eos" / "config.toml").write_text(
        '[project]\nname = "polyglot"\n\n[scan]\nunignore = ["generated"]\n', encoding="utf-8"
    )

    paths = set(Scanner(root, CacheStore(root / ".eos" / "data" / "cache"))._walk(root))

    assert "generated/stub.js" in paths, "unignore could not reach a .gitignore rule"


def test_a_git_worktree_inside_the_project_is_not_indexed_twice(tmp_path):
    """A per-ticket `git worktree` checked out inside the repository is another
    checkout of the same code, not more code.

    Measured on a 255-file service before this was excluded: the worktree held
    406 .java files against src/'s 403, so 687 of 6,040 graph nodes were
    duplicates and EntryPoints.md listed every controller twice -- the worktree
    copy first, because ".worktrees" sorts before "src".
    """
    root = _copy(tmp_path)
    worktree = root / ".worktrees" / "svc-PROJ-1"
    shutil.copytree(root / "svc", worktree / "svc")

    project = _scan(root)

    indexed = [file.path for file in project.files]
    assert not any(path.startswith(".worktrees/") for path in indexed), (
        f"a worktree copy reached the model: {[p for p in indexed if p.startswith('.worktrees/')]}"
    )
    assert project.report.skipped_by_ignore.get(".worktrees", 0) >= 1, (
        f"the exclusion was not reported; skipped_by_ignore={project.report.skipped_by_ignore}"
    )
