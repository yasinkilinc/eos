import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "tools" / "check-clean.sh"


def _run(target: Path):
    return subprocess.run(
        ["bash", str(SCRIPT), str(target)],
        capture_output=True, text=True,
    )


def test_clean_tree_passes(tmp_path):
    (tmp_path / "a.py").write_text("def handler():\n    return 1\n", encoding="utf-8")
    assert _run(tmp_path).returncode == 0


def test_planted_identifier_fails(tmp_path):
    (tmp_path / "b.py").write_text("# see FEMBS2-1363 for context\n", encoding="utf-8")
    result = _run(tmp_path)
    assert result.returncode == 1
    assert "FEMBS" in result.stdout + result.stderr


def test_planted_workspace_path_fails(tmp_path):
    (tmp_path / "c.py").write_text("ROOT = '/Volumes/Data/workspace/x'\n", encoding="utf-8")
    assert _run(tmp_path).returncode == 1


def test_repository_itself_is_clean():
    assert _run(REPO).returncode == 0, "the repo still contains denylisted identifiers"
