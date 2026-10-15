"""A `status` note records how things stand right now ("the staging queue is
stuck"); it expires by itself so a state that changed stops being offered as
if it still held."""
import datetime
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
EOS = [sys.executable, str(REPO / "core" / "eos.py")]
sys.path.insert(0, str(REPO))

from core.brief import note_label  # noqa: E402
from core.notes.store import load_dir  # noqa: E402


def _run(args):
    return subprocess.run(EOS + args, capture_output=True, text=True, cwd=REPO)


def _init(tmp_path):
    proj = tmp_path / "demo"
    proj.mkdir()
    assert _run(["init", str(proj), "--no-ai"]).returncode == 0
    return proj


def _status(proj, title, body, *extra):
    return _run(["note", "add", str(proj), "--kind", "status", "--title", title, "--body", body,
                 "--tags", "t", *extra])


def _in_days(days):
    return (datetime.date.today() + datetime.timedelta(days=days)).isoformat()


def test_a_status_note_gets_fourteen_days_by_default(tmp_path):
    proj = _init(tmp_path)
    r = _status(proj, "Staging queue is stuck", "Three jobs wait since 09:00.")
    assert r.returncode == 0, r.stderr
    assert f"valid_until: {_in_days(14)}" in Path(r.stdout.split()[-1]).read_text(encoding="utf-8")


def test_a_status_note_takes_a_shorter_life(tmp_path):
    proj = _init(tmp_path)
    r = _status(proj, "VPN down", "No route to the office network.", "--valid-until", _in_days(2))
    assert r.returncode == 0, r.stderr
    assert f"valid_until: {_in_days(2)}" in Path(r.stdout.split()[-1]).read_text(encoding="utf-8")


def test_a_status_note_in_the_past_is_still_refused(tmp_path):
    proj = _init(tmp_path)
    r = _status(proj, "Old state", "It was like this once.", "--valid-until", "2000-01-01")
    assert r.returncode != 0 and "past" in r.stderr


def test_a_status_note_needs_a_body(tmp_path):
    proj = _init(tmp_path)
    r = _status(proj, "Queue stuck", "   ")
    assert r.returncode != 0 and "body" in r.stderr


def test_a_status_note_near_its_end_says_until_when_in_the_brief(tmp_path):
    proj = _init(tmp_path)
    near = Path(_status(proj, "VPN down", "No route.", "--valid-until", _in_days(2)).stdout.split()[-1])
    far = Path(_status(proj, "Build farm slow", "Builds take an hour.").stdout.split()[-1])
    by_name = {n.path.name: n for n in load_dir(near.parent)}
    assert note_label(by_name[near.name]) == f"[status, today, until {_in_days(2)}]"
    assert note_label(by_name[far.name]) == "[status, today]"
