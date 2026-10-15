"""A new note is held to a shape a reader can use: a title that fits a line,
a body that is a note and not a pasted log, and -- for the kinds a brief
matches by tag -- at least one tag, with the tags already in use suggested."""
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
EOS = [sys.executable, str(REPO / "core" / "eos.py")]


def _run(args):
    return subprocess.run(EOS + args, capture_output=True, text=True, cwd=REPO)


def _init(tmp_path):
    proj = tmp_path / "demo"
    proj.mkdir()
    assert _run(["init", str(proj), "--no-ai"]).returncode == 0
    return proj


def _add(proj, title, body, tags):
    r = _run(["note", "add", str(proj), "--kind", "finding", "--title", title, "--body", body,
              "--tags", tags, "--differs", "fixture"])
    assert r.returncode == 0, r.stderr
    return Path(r.stdout.split()[-1])


def test_a_long_title_is_refused(tmp_path):
    proj = _init(tmp_path)
    r = _run(["note", "add", str(proj), "--kind", "finding", "--title", "x " * 71, "--body", "b c d",
              "--tags", "t"])
    assert r.returncode != 0 and "140" in r.stderr


def test_a_title_of_exactly_the_limit_is_kept(tmp_path):
    proj = _init(tmp_path)
    r = _run(["note", "add", str(proj), "--kind", "finding", "--title", "ab " * 46 + "ab", "--body", "b c d",
              "--tags", "t"])
    assert r.returncode == 0, r.stderr


def test_a_dump_is_refused(tmp_path):
    proj = _init(tmp_path)
    r = _run(["note", "add", str(proj), "--kind", "finding", "--title", "Log of the run",
              "--body", "".join(f"line {i} of the log\n" for i in range(600)), "--tags", "t"])
    assert r.returncode != 0 and "8000" in r.stderr


def test_an_untagged_finding_is_refused_with_suggestions(tmp_path):
    proj = _init(tmp_path)
    for i, word in enumerate(("alpha", "beta", "gamma")):
        _add(proj, f"Export note {word}", f"Export answers {i}.", "export")
    r = _run(["note", "add", str(proj), "--kind", "finding", "--title", "Export retries twice",
              "--body", "The export call retries twice before it gives up."])
    assert r.returncode != 0
    assert "--tags" in r.stderr and "export" in r.stderr


def test_an_untagged_status_is_refused(tmp_path):
    proj = _init(tmp_path)
    r = _run(["note", "add", str(proj), "--kind", "status", "--title", "Queue stuck", "--body", "Jobs wait."])
    assert r.returncode != 0 and "--tags" in r.stderr


def test_a_decision_needs_no_tag(tmp_path):
    proj = _init(tmp_path)
    r = _run(["note", "add", str(proj), "--kind", "decision", "--title", "Use the bulk API",
              "--body", "Bulk.\n\n## Why\nSpeed.\n\n## When\nAlways.\n\n## Component\nexport"])
    assert r.returncode == 0, r.stderr
