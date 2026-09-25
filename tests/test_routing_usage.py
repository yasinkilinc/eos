"""Data collection without a habit (ADR-025, 1.2.2).

Two writers that need nobody to remember them: the task brief records a
decision per task prompt where `record_prompts` is on, and a Stop hook folds
the harness transcript into per-session model and token totals. Neither
keeps a word of what was typed.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

import core.routing as routing
from core import brief
from core.routing import trace, usage

REPO = Path(__file__).resolve().parents[1]
EOS = [sys.executable, str(REPO / "core" / "eos.py")]
SENTINEL = "zanzibarquux"


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    (root / ".eos").mkdir(parents=True)
    monkeypatch.setenv("EOS_STATE_DIR", str(tmp_path / "state"))
    for name in ("EOS_EXECUTION", "EOS_EXECUTION_LEDGER", routing.MODEL_ENV, routing.EFFORT_ENV):
        monkeypatch.delenv(name, raising=False)
    return root


def _configure(root, table):
    (root / ".eos" / "config.toml").write_text(table, encoding="utf-8")


def _assistant(message_id, model, output, text=SENTINEL):
    return json.dumps({"type": "assistant", "uuid": message_id + "-u",
                       "message": {"id": message_id, "model": model,
                                   "content": [{"type": "text", "text": text}],
                                   "usage": {"input_tokens": 10, "output_tokens": output,
                                             "cache_read_input_tokens": 100,
                                             "cache_creation_input_tokens": 5}}})


def _transcript(tmp_path) -> Path:
    path = tmp_path / "sess-1.jsonl"
    lines = [
        json.dumps({"type": "user", "message": {"role": "user", "content": SENTINEL}}),
        _assistant("m1", "model-big", 7),
        _assistant("m1", "model-big", 7),  # same message, second content block
        _assistant("m2", "model-small", 3),
        "{not json",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    subagents = tmp_path / "sess-1" / "subagents"
    subagents.mkdir(parents=True)
    (subagents / "agent-a.jsonl").write_text(_assistant("s1", "model-small", 2) + "\n", encoding="utf-8")
    return path


# --- the brief records task prompts --------------------------------------------------


def test_record_prompts_keeps_one_decision_per_task_per_session(project):
    _configure(project, "[model_routing]\nrecord_prompts = true\n")
    for _ in range(3):
        brief.build(project, task=f"refactor the {SENTINEL} flow and update tests", session="s1")
    brief.build(project, task=f"refactor the {SENTINEL} flow and update tests", session="s2")

    lines = trace.load(project)
    assert [line["session"] for line in lines] == ["s1", "s2"]
    assert lines[0]["type"] == "refactoring"
    stored = (project / ".eos" / "data" / trace.FILENAME).read_text(encoding="utf-8")
    assert SENTINEL not in stored


def test_a_prompt_with_no_task_words_is_not_recorded(project):
    _configure(project, "[model_routing]\nrecord_prompts = true\n")
    for prompt in ("ok", "evet devam et", "tamam"):
        brief.build(project, task=prompt, session="s1")
    assert trace.load(project) == []


def test_without_record_prompts_the_brief_records_nothing(project):
    _configure(project, "[model_routing]\n")
    brief.build(project, task="refactor the auth flow and update tests", session="s1")
    assert trace.load(project) == []


def test_record_prompts_works_even_when_the_line_is_hidden(project):
    _configure(project, '[model_routing]\nbrief = "never"\nrecord_prompts = true\n')
    text = brief.build(project, task="refactor the auth flow and update tests", session="s1")
    assert "ROUTE" not in text
    assert len(trace.load(project)) == 1


# --- model and token usage from the transcript ---------------------------------------


def test_a_transcript_folds_into_per_model_totals_counted_by_message(tmp_path):
    summary = usage.summarize(_transcript(tmp_path))

    assert summary["models"]["model-big"] == {"messages": 1, "input_tokens": 10, "output_tokens": 7,
                                              "cache_read_input_tokens": 100,
                                              "cache_creation_input_tokens": 5}
    assert summary["models"]["model-small"]["output_tokens"] == 3
    assert summary["subagent_models"] == {"model-small": {"messages": 1, "input_tokens": 10,
                                                          "output_tokens": 2,
                                                          "cache_read_input_tokens": 100,
                                                          "cache_creation_input_tokens": 5}}


def test_one_line_per_session_replaced_on_each_record(project, tmp_path):
    transcript = _transcript(tmp_path)
    assert usage.record(project, "s1", transcript)
    assert usage.record(project, "s1", transcript)
    assert usage.record(project, "s2", transcript)

    lines = usage.load(project)
    assert [line["session"] for line in lines] == ["s1", "s2"]
    assert SENTINEL not in usage.path_for(project).read_text(encoding="utf-8")


def test_usage_needs_a_session_and_an_eos_directory(tmp_path):
    transcript = _transcript(tmp_path)
    bare = tmp_path / "bare"
    bare.mkdir()
    assert not usage.record(bare, "s1", transcript)
    assert not (bare / ".eos").exists()
    eos_root = tmp_path / "p"
    (eos_root / ".eos").mkdir(parents=True)
    assert not usage.record(eos_root, None, transcript)
    assert not usage.record(eos_root, "s1", tmp_path / "missing.jsonl")


def test_the_cli_records_usage_and_always_exits_zero(project, tmp_path):
    transcript = _transcript(tmp_path)
    done = subprocess.run(EOS + ["route", str(project), "--usage-from", str(transcript),
                                 "--session", "s9"], capture_output=True, text=True)
    assert done.returncode == 0 and "s9" in done.stdout
    missing = subprocess.run(EOS + ["route", str(project), "--usage-from", str(tmp_path / "nope.jsonl"),
                                    "--session", "s9"], capture_output=True, text=True)
    assert missing.returncode == 0

    stats = subprocess.run(EOS + ["route", str(project), "--stats"], capture_output=True, text=True)
    assert "model usage for 1 session(s)" in stats.stdout
