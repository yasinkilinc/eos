"""Work and runs are linked (2.x roadmap E4): a run started on a ticket's branch
belongs to that ticket's work item, and the item shows its runs."""
import subprocess
import sys
from pathlib import Path

EOS = [sys.executable, str(Path(__file__).resolve().parents[1] / "core" / "eos.py")]


def _eos(*args, **kw):
    return subprocess.run(EOS + list(args), capture_output=True, text=True, **kw)


def _repo(tmp_path, monkeypatch):
    monkeypatch.setenv("EOS_STATE_DIR", str(tmp_path / "state"))
    root = tmp_path / "proj"
    root.mkdir()
    for command in (["git", "init", "-q", "-b", "feature/PAY-42"], ["git", "config", "user.email", "t@example.com"],
                    ["git", "config", "user.name", "t"], ["git", "commit", "-q", "--allow-empty", "-m", "init"]):
        subprocess.run(command, cwd=root, check=True, capture_output=True)
    assert _eos("init", str(root), "--no-ai").returncode == 0
    return root


def test_a_run_on_a_tickets_branch_joins_its_work_item_and_the_item_lists_it(tmp_path, monkeypatch):
    root = _repo(tmp_path, monkeypatch)
    added = _eos("work", "add", str(root), "--title", "Payment retries", "--ticket", "PAY-42", "--claim",
                 "--session", "s1")
    assert added.returncode == 0, added.stderr
    item = added.stdout.split()[0]
    started = _eos("run", "start", str(root), "--title", "Fix the retry", "--session", "s1")
    assert started.returncode == 0, started.stderr
    assert f"linked to work item {item}" in started.stdout
    run = started.stdout.split()[0]
    shown = _eos("work", "show", str(root), item)
    assert shown.returncode == 0 and run in shown.stdout


def test_no_link_is_guessed_without_a_matching_item(tmp_path, monkeypatch):
    root = _repo(tmp_path, monkeypatch)
    started = _eos("run", "start", str(root), "--title", "Fix the retry", "--session", "s1")
    assert started.returncode == 0 and "linked to work item" not in started.stdout
