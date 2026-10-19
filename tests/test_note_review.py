"""`[REVIEW: Nd]`: a freshness signal for notes without a hash-tracked scope (computed, never stored)."""
import datetime
import hashlib
import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

from core import hooks, notes
from core.brief import note_label

EOS = [sys.executable, str(Path(__file__).resolve().parents[1] / "core" / "eos.py")]
TODAY = datetime.date.today()


def _days_ago(days: int) -> str:
    return (TODAY - datetime.timedelta(days=days)).isoformat()


def _init(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    assert subprocess.run(EOS + ["init", str(root), "--no-ai"], capture_output=True).returncode == 0
    return root


def _write(root: Path, name: str, title: str, *, kind="status", created=None, extra=(), body="Queue is stuck.",
           scope=(), hashes=()) -> Path:
    store = root / ".eos" / "knowledge"
    store.mkdir(parents=True, exist_ok=True)
    lines = ["---", f"kind: {kind}", f"title: {title}", f"created: {created or _days_ago(0)}", *extra]
    if scope:
        lines += ["scope:"] + [f"  - {s}" for s in scope]
    if hashes:
        lines += ["scope_hashes:"] + [f"  - {h}" for h in hashes]
    path = store / name
    path.write_text("\n".join(lines + ["---", "", body]) + "\n", encoding="utf-8")
    return path


def _config(root: Path, text: str) -> None:
    with open(root / ".eos" / "config.toml", "a", encoding="utf-8") as handle:
        handle.write("\n" + text + "\n")


def _search(root: Path, query: str) -> str:
    return subprocess.run(EOS + ["note", "search", str(root), query], capture_output=True, text=True).stdout


def _note(root: Path, name: str):
    return notes.parse_note(root / ".eos" / "knowledge" / name)


def test_a_week_old_status_note_without_scope_is_overdue_by_the_excess_in_search(tmp_path):
    root = _init(tmp_path)
    _write(root, "a.md", "Staging queue stuck", created=_days_ago(10))
    _write(root, "b.md", "Staging queue recovering", created=_days_ago(3))
    lines = {line.split("\t")[2]: line for line in _search(root, "staging queue").splitlines()}
    assert "[REVIEW: 3d]" in lines["Staging queue stuck"]
    assert "[REVIEW" not in lines["Staging queue recovering"]


def test_the_most_recent_known_date_is_updated_not_created(tmp_path):
    root = _init(tmp_path)
    _write(root, "a.md", "Staging queue stuck", created=_days_ago(30), extra=[f"updated: {_days_ago(8)}"])
    assert notes.review_due(_note(root, "a.md"), TODAY, notes.review_after_days(root)) == 1


def test_other_kinds_are_off_until_configured(tmp_path):
    root = _init(tmp_path)
    _write(root, "a.md", "Pipeline size", kind="finding", created=_days_ago(40))
    defaults = notes.review_after_days(root)
    assert defaults == {"status": 7}
    assert notes.review_due(_note(root, "a.md"), TODAY, defaults) is None
    _config(root, "[notes]\nreview_after_days = { finding = 30 }")
    configured = notes.review_after_days(root)
    assert configured == {"status": 7, "finding": 30}
    assert notes.review_due(_note(root, "a.md"), TODAY, configured) == 10


@pytest.mark.parametrize("bad", ['"soon"', "-3", "true", "2.5"])
def test_a_bad_configured_value_is_ignored_for_that_kind_never_an_error(tmp_path, bad):
    root = _init(tmp_path)
    _config(root, f"[notes]\nreview_after_days = {{ finding = {bad}, status = {bad} }}")
    assert notes.review_after_days(root) == {"status": 7}


def test_a_config_that_is_not_a_table_is_ignored(tmp_path):
    root = _init(tmp_path)
    _config(root, "[notes]\nreview_after_days = 5")
    assert notes.review_after_days(root) == {"status": 7}


def test_review_by_overrides_the_age_rule_on_any_kind(tmp_path):
    root = _init(tmp_path)
    _write(root, "a.md", "Pipeline size", kind="finding", extra=["review_by: 2026-01-01"])
    _write(root, "b.md", "Fresh status", created=_days_ago(0), extra=[f"review_by: {_days_ago(-5)}"])
    defaults = notes.review_after_days(root)
    assert notes.review_due(_note(root, "a.md"), TODAY, defaults) == (TODAY - datetime.date(2026, 1, 1)).days
    assert notes.review_due(_note(root, "b.md"), TODAY, defaults) is None
    # review_by in the future beats an old creation date too
    _write(root, "c.md", "Old status", created=_days_ago(30), extra=[f"review_by: {_days_ago(-5)}"])
    assert notes.review_due(_note(root, "c.md"), TODAY, defaults) is None


def test_an_unparseable_review_by_is_ignored(tmp_path):
    root = _init(tmp_path)
    _write(root, "a.md", "Pipeline size", kind="finding", extra=["review_by: next tuesday"])
    _write(root, "b.md", "Old status", created=_days_ago(10), extra=["review_by: 2026-13-45"])
    defaults = notes.review_after_days(root)
    assert notes.review_due(_note(root, "a.md"), TODAY, defaults) is None
    assert notes.review_due(_note(root, "b.md"), TODAY, defaults) == 3


def test_a_hash_tracked_scope_is_never_review_due_by_age_but_review_by_still_applies(tmp_path):
    root = _init(tmp_path)
    (root / "A.java").write_text("class A {}\n", encoding="utf-8")
    digest = hashlib.sha256(b"class A {}\n").hexdigest()
    _write(root, "a.md", "Scoped status", created=_days_ago(30), scope=["A.java"], hashes=[digest])
    _write(root, "b.md", "Scoped with date", created=_days_ago(0), scope=["A.java"], hashes=[digest],
           extra=["review_by: 2026-01-01"])
    _write(root, "c.md", "Scope never resolved", created=_days_ago(10), scope=["Gone"], hashes=["null"])
    defaults = notes.review_after_days(root)
    assert notes.review_due(_note(root, "a.md"), TODAY, defaults) is None
    assert notes.review_due(_note(root, "b.md"), TODAY, defaults) is not None
    # a scope entry that never hashed tracks nothing, so age still applies
    assert notes.review_due(_note(root, "c.md"), TODAY, defaults) == 3


def test_a_superseded_or_expired_note_is_never_review_due(tmp_path):
    root = _init(tmp_path)
    _write(root, "old.md", "Staging queue stuck", created=_days_ago(10))
    _write(root, "new.md", "Staging queue stuck again", created=_days_ago(1), extra=["supersedes: old.md"])
    _write(root, "exp.md", "Gone queue", created=_days_ago(10), extra=[f"valid_until: {_days_ago(1)}"])
    defaults = notes.review_after_days(root)
    assert notes.review_due(_note(root, "exp.md"), TODAY, defaults) is None
    shown = _search(root, "staging queue")
    assert "old.md" not in shown and "[REVIEW" not in shown
    shown = subprocess.run(EOS + ["note", "show", str(root), "old.md"], capture_output=True, text=True).stdout
    assert "[REVIEW" not in shown


def test_the_brief_label_carries_the_mark(tmp_path):
    root = _init(tmp_path)
    _write(root, "a.md", "Staging queue stuck", created=_days_ago(10))
    assert "[REVIEW: 3d]" in note_label(_note(root, "a.md"), root)
    assert "[REVIEW" not in note_label(_note(root, "a.md"))


def test_note_show_names_the_review(tmp_path):
    root = _init(tmp_path)
    _write(root, "a.md", "Staging queue stuck", created=_days_ago(10))
    shown = subprocess.run(EOS + ["note", "show", str(root), "a.md"], capture_output=True, text=True).stdout
    assert "[REVIEW: 3d]" in shown.splitlines()[0]


def test_the_inject_digest_and_body_carry_the_mark(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("EOS_STATE_DIR", str(tmp_path / "state"))
    root = _init(tmp_path)
    (root / "src").mkdir()
    (root / "src" / "A.java").write_text("class A {}\n", encoding="utf-8")
    (root / "src" / "B.java").write_text("class B {}\n", encoding="utf-8")
    _write(root, "a.md", "Exact status note", created=_days_ago(10), scope=["src/A.java"])
    _write(root, "b.md", "Folder status note", created=_days_ago(10), scope=["src/Other.java"])
    payload = {"session_id": "r1", "cwd": str(root), "hook_event_name": "PostToolBatch",
               "tool_calls": [{"tool_name": "Read", "tool_input": {"file_path": str(root / "src" / "A.java")}}]}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    assert hooks.main(["post-batch"]) == 0
    text = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]
    assert "Exact status note" in text and "[REVIEW: 3d]" in text
    assert "- Folder status note [REVIEW: 3d]" in text
