"""End-to-end: a note's scope file changing after the fact surfaces as a
doctor warning, not a failure -- same non-fatal shape as the existing
is_notes_dir_gitignored warning this mirrors.
"""
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
EOS = [sys.executable, str(REPO / "core" / "eos.py")]


def _run(args, **kw):
    return subprocess.run(EOS + args, capture_output=True, text=True, cwd=REPO, **kw)


def _init(tmp_path):
    proj = tmp_path / "demo"
    proj.mkdir()
    r = _run(["init", str(proj)])
    assert r.returncode == 0, r.stderr
    return proj


def test_doctor_warns_when_a_notes_scope_file_changed_since_it_was_written(tmp_path):
    proj = _init(tmp_path)
    scope_file = proj / "Boot.java"
    scope_file.write_text("class Boot {}\n", encoding="utf-8")

    add = _run([
        "note", "add", str(proj),
        "--kind", "finding", "--title", "Boot note", "--body", "Boots.",
        "--scope", "Boot.java",
    ])
    assert add.returncode == 0, add.stderr

    scope_file.write_text("class Boot { void x() {} }\n", encoding="utf-8")

    r = _run(["doctor", str(proj)])

    assert r.returncode == 0, "a stale note is a warning, not a broken instance"
    assert "boot.java" in r.stdout.lower()
    assert "changed" in r.stdout.lower()


def test_doctor_reports_stale_notes_in_one_block_not_two(tmp_path):
    """Hand-written and generated stale notes must be reported in a single
    warning block -- one heading, one style -- not a detailed dump followed
    by a second, differently-capitalized summary line repeating the same
    information.
    """
    proj = _init(tmp_path)
    scope_file = proj / "Boot.java"
    scope_file.write_text("class Boot {}\n", encoding="utf-8")

    handwritten = _run([
        "note", "add", str(proj),
        "--kind", "finding", "--title", "Boot note", "--body", "Boots.",
        "--scope", "Boot.java",
    ])
    assert handwritten.returncode == 0, handwritten.stderr

    generated = _run([
        "note", "add", str(proj),
        "--kind", "finding", "--title", "Generated boot map", "--body", "Bulk table.",
        "--scope", "Boot.java", "--source", "api-inventory",
    ])
    assert generated.returncode == 0, generated.stderr

    scope_file.write_text("class Boot { void x() {} }\n", encoding="utf-8")

    r = _run(["doctor", str(proj)])
    out = r.stdout

    assert r.returncode == 0, "a stale note is a warning, not a broken instance"
    assert out.lower().count("warning:") == 1, (
        "one warning paragraph, not a detailed block plus a separate summary line"
    )
    assert "generated note(s)" in out, "the generated half's remedy (regenerate) must still be named"
    assert "eos note audit" in out
    assert "boot.java" in out.lower(), "the hand-written entry is still listed in detail"
