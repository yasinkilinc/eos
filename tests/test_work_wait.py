"""A blocked item can record a checkable wait (`--until`) so a runner can resume it."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from core import work

EOS = [sys.executable, str(Path(__file__).resolve().parents[1] / "core" / "eos.py")]


def _eos(*args):
    return subprocess.run(EOS + list(args), capture_output=True, text=True)


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("EOS_STATE_DIR", str(tmp_path / "state"))
    project = tmp_path / "proj"
    project.mkdir()
    for command in (["git", "init", "-q", "-b", "main"], ["git", "config", "user.email", "t@example.com"],
                    ["git", "config", "user.name", "t"], ["git", "commit", "-q", "--allow-empty", "-m", "init"]):
        subprocess.run(command, cwd=project, check=True, capture_output=True)
    assert _eos("init", str(project), "--no-ai").returncode == 0
    return project


def _item(root, title="Top-up retry"):
    done = _eos("work", "add", str(root), "--title", title, "--claim", "--session", "s1")
    assert done.returncode == 0, done.stderr
    return done.stdout.split()[0]


def _block(root, item, *extra):
    return _eos("work", "block", str(root), item, "--reason", "env1 asleep", "--session", "s1", *extra)


def test_block_records_wait(root, tmp_path):
    item = _item(root)
    done = _block(root, item, "--until", "env:env1", "--check", "automation/env-ready.sh env1",
                  "--resume", "continue the top-up retry", "--cwd", str(tmp_path))
    assert done.returncode == 0, done.stderr
    found = work.parked(root)
    assert [p.id for p in found] == [item]
    assert (found[0].until, found[0].check, found[0].resume, found[0].cwd) == (
        "env:env1", "automation/env-ready.sh env1", "continue the top-up retry", str(tmp_path))


def test_unblock_clears_wait(root):
    item = _item(root)
    assert _block(root, item, "--until", "vpn", "--check", "vpn.sh status", "--resume", "go on").returncode == 0
    assert _eos("work", "unblock", str(root), item, "--session", "s1").returncode == 0
    assert work.parked(root) == []
    folded = work.resolve(work.fold(work.load(root)), item)
    assert (folded.until, folded.check, folded.resume, folded.cwd) == (None, None, None, None)


@pytest.mark.parametrize("closer", ["done", "drop"])
def test_done_and_drop_clear_wait(root, closer):
    item = _item(root)
    assert _block(root, item, "--until", "user").returncode == 0
    extra = ["--reason", "no"] if closer == "drop" else []
    assert _eos("work", closer, str(root), item, "--session", "s1", *extra).returncode == 0
    folded = work.resolve(work.fold(work.load(root)), item)
    assert folded.until is None


def test_parse_until():
    assert work.parse_until("vpn") == "vpn"
    assert work.parse_until("env:env1") == "env:env1"
    assert work.parse_until("user") == "user"
    assert work.parse_until("time:2026-10-30T05:20:00Z") == "time:2026-10-30T05:20:00Z"
    assert work.parse_until("time:2026-10-30T05:20:00+03:00") == "time:2026-10-30T05:20:00+03:00"
    for bad in ("env:", "time:2026-10-30T05:20", "soon", "time:", "vpn:x", ""):
        with pytest.raises(ValueError):
            work.parse_until(bad)
    assert work.WAIT_KINDS == ("vpn", "env", "user", "time")


def test_vpn_needs_check_and_resume(root):
    item = _item(root)
    for extra in (["--until", "vpn"], ["--until", "vpn", "--check", "vpn.sh status"],
                  ["--until", "env:env1", "--resume", "go"]):
        done = _block(root, item, *extra)
        assert done.returncode == 1 and "needs --check" in done.stderr
    assert work.parked(root) == []


def test_time_needs_resume_only(root):
    item = _item(root)
    assert _block(root, item, "--until", "time:2026-10-30T05:20:00Z").returncode == 1
    assert _block(root, item, "--until", "time:2026-10-30T05:20:00Z", "--resume", "go on").returncode == 0


def test_user_refuses_check(root):
    item = _item(root)
    done = _block(root, item, "--until", "user", "--check", "true")
    assert done.returncode == 1 and "refuses --check" in done.stderr


def test_wait_fields_need_until(root):
    item = _item(root)
    for extra in (["--check", "true"], ["--resume", "go on"]):
        assert _block(root, item, *extra).returncode == 1


def test_cwd_must_be_existing_absolute_directory(root, tmp_path):
    item = _item(root)
    base = ["--until", "vpn", "--check", "vpn.sh status", "--resume", "go on"]
    assert _block(root, item, *base, "--cwd", "relative/dir").returncode == 1
    assert _block(root, item, *base, "--cwd", str(tmp_path / "missing")).returncode == 1


def test_wait_strings_refuse_placeholders_and_credentials(root):
    item = _item(root)
    done = _block(root, item, "--until", "vpn", "--check", "vpn.sh status", "--resume", "<fill in>")
    assert done.returncode == 1
    done = _block(root, item, "--until", "vpn", "--check", "check.sh password=hunter2hunter2",
                  "--resume", "go on")
    assert done.returncode == 1


def test_old_block_line_is_not_parked(root):
    ledger = work.path_for(root)
    ledger.parent.mkdir(parents=True, exist_ok=True)
    lines = [{"id": "old-1", "event": "open", "at": "2026-09-01T00:00:00+00:00", "title": "Old"},
             {"id": "old-1", "event": "block", "at": "2026-09-01T01:00:00+00:00", "body": "waiting"}]
    ledger.write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")
    folded = work.fold(work.load(root))
    assert folded[0].status == "blocked" and folded[0].until is None
    assert work.parked(root) == []


def test_parked_json_carries_auto(root):
    item = _item(root)
    assert _block(root, item, "--until", "user").returncode == 0
    on = json.loads(_eos("work", "parked", str(root), "--format", "json").stdout)
    assert on["auto"] is True and [i["id"] for i in on["items"]] == [item]
    (root / ".eos" / "config.toml").write_text("[resume]\nauto = false\n", encoding="utf-8")
    off = json.loads(_eos("work", "parked", str(root), "--format", "json").stdout)
    assert off["auto"] is False


def test_parked_text_line(root):
    item = _item(root)
    assert _block(root, item, "--until", "vpn", "--check", "vpn.sh status", "--resume", "go on").returncode == 0
    assert _eos("work", "parked", str(root)).stdout.split() == [item, "vpn", "vpn.sh", "status"]


def test_brief_lists_parked_first(root):
    item = _item(root)
    assert _block(root, item, "--until", "env:env1", "--check", "automation/env-ready.sh env1",
                  "--resume", "continue it").returncode == 0
    shown = _eos("brief", str(root)).stdout
    assert shown.index("PARKED (1)") < shown.index("KNOWN HERE")
    assert "PARKED (1)" in shown.split("\n\n")[1]
    assert "env:env1" in shown and "automation/env-ready.sh env1" in shown
