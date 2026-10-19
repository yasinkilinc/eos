"""`--summary`: one line a note carries, shown after its title in search, briefs and digests."""
import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

from core import brief, hooks, inject, notes

EOS = [sys.executable, str(Path(__file__).resolve().parents[1] / "core" / "eos.py")]
SUMMARY = "The queue stalls when the retry window closes before the worker wakes."


def _eos(*args):
    return subprocess.run(EOS + [str(a) for a in args], capture_output=True, text=True)


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("EOS_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("EOS_HOOK_STATE_DIR", raising=False)
    project = tmp_path / "proj"
    project.mkdir()
    assert _eos("init", project, "--no-ai").returncode == 0
    return project


def _add(root, title="Staging queue stalls", summary=SUMMARY, extra=(),
         body="The staging queue worker sleeps past the retry window, so messages wait."):
    args = ["note", "add", root, "--kind", "finding", "--title", title, "--body", body,
            "--tags", "queue,staging", *extra]
    if summary is not None:
        args += ["--summary", summary]
    return _eos(*args)


def _note(root):
    return notes.load_notes(root)[0]


def test_add_writes_the_summary_to_the_front_matter(root):
    assert _add(root).returncode == 0
    assert _note(root).summary == SUMMARY


def test_a_161_character_summary_is_refused_and_nothing_is_written(root):
    done = _add(root, summary="x" * 161)
    assert done.returncode != 0 and "160" in done.stderr
    assert notes.load_notes(root) == []


def test_a_160_character_summary_is_accepted(root):
    assert _add(root, summary="y" * 160).returncode == 0
    assert len(_note(root).summary) == 160


def test_a_summary_with_a_newline_is_refused(root):
    done = _add(root, summary="one\ntwo")
    assert done.returncode != 0 and "one line" in done.stderr
    assert notes.load_notes(root) == []


def test_amend_sets_the_summary_and_refuses_a_long_one(root):
    _add(root, summary=None)
    path = _note(root).path
    assert _eos("note", "amend", path, root, "--summary", "x" * 161).returncode != 0
    assert _note(root).summary is None
    assert _eos("note", "amend", path, root, "--summary", SUMMARY).returncode == 0
    assert _note(root).summary == SUMMARY


@pytest.mark.parametrize("flags", [("--append", "A later round found the same."),
                                   ("--reaffirm", "Still true on env1."),
                                   ("--body", "The worker now wakes early, so messages no longer wait."),
                                   ("--alias", "kuyruk"), ("--valid-until", "2099-01-01")])
def test_every_amend_keeps_the_summary(root, flags):
    _add(root)
    done = _eos("note", "amend", _note(root).path, root, *flags)
    assert done.returncode == 0, done.stderr
    assert _note(root).summary == SUMMARY


def test_search_shows_the_summary_as_a_fourth_column_only_when_present(root):
    _add(root)
    _add(root, title="Staging queue drains late", summary=None, extra=["--differs", "another symptom"],
         body="Backlog metrics for the drain job lag by a full minute after midnight.")
    rows = {line.split("\t")[2]: line.split("\t") for line in
            _eos("note", "search", root, "staging queue").stdout.splitlines()}
    assert rows["Staging queue stalls"][3] == SUMMARY
    assert len(rows["Staging queue drains late"]) == 3


def test_related_and_linked_lines_carry_the_summary(root):
    _add(root)
    text = brief.build(root, task="why does the staging queue stall", task_only=True)
    assert f"Staging queue stalls  [finding, today] — {SUMMARY}" in text


def test_a_note_without_a_summary_shows_nothing_new_in_the_brief(root):
    _add(root, summary=None)
    text = brief.build(root, task="why does the staging queue stall", task_only=True)
    assert "Staging queue stalls" in text and " — " not in text.split("RELATED NOTES", 1)[1].splitlines()[1]


def test_the_summary_goes_before_the_title_when_the_brief_is_full(root):
    _add(root, summary="z" * 160)
    full = brief.build(root, task="why does the staging queue stall", task_only=True)
    assert "z" * 160 in full
    cap = len("\n".join(full.splitlines()[:-1]).rstrip()) - 100  # a budget the summary would overrun
    tight = brief.build(root, task="why does the staging queue stall", task_only=True,
                        reserve=int(brief.TASK_BUDGET * brief.CHARS_PER_TOKEN) - cap)
    assert "Staging queue stalls" in tight and "z" * 160 not in tight


def test_the_inject_digest_line_carries_the_summary_and_drops_it_first(root, tmp_path, monkeypatch, capsys):
    (root / "src").mkdir()
    (root / "src" / "A.java").write_text("class A {}\n", encoding="utf-8")
    (root / "src" / "B.java").write_text("class B {}\n", encoding="utf-8")
    _add(root, extra=["--scope", "src/B.java"])

    def touch(session, name):
        payload = {"session_id": session, "cwd": str(root), "tool_calls": [
            {"tool_name": "Read", "tool_input": {"file_path": str(root / "src" / name)}}]}
        monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
        assert hooks.main(["post-batch"]) == 0
        out = capsys.readouterr().out
        return json.loads(out)["hookSpecificOutput"]["additionalContext"] if out.strip() else ""

    assert f"- Staging queue stalls — {SUMMARY}" in touch("d1", "A.java")
    lines = [f"- Title {n} — " + "w" * 160 for n in range(40)]
    assert inject._drop_summaries(lines)[0] == "- Title 0"
    assert inject._drop_summaries(["- Plain title"]) == ["- Plain title"]


def test_a_digest_over_the_session_budget_loses_its_summaries_not_its_titles():
    lines = [f"- Digest title number {n:02d} — " + "s" * 150 for n in range(20)]
    text = "### Other notes on svc\n" + "\n".join(lines)
    whole, spent, _ = inject.budgeted(text, 0, 0, pointer="p {path} {words}")
    assert whole == text
    cut, _, _ = inject.budgeted(text, int(inject.BODY_MAX_TOKENS * inject.CHARS_PER_TOKEN) - 1000, 0,
                                pointer="p {path} {words}")
    assert "- Digest title number 19" in cut and "s" * 150 not in cut
