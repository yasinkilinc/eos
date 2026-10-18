"""Every prompt brief decision is logged (no prompt text, ADR-019), and a RELATED
note needs a minimum relevance, not one shared word."""
import io
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from core import brief, hooks

EOS = [sys.executable, str(Path(__file__).resolve().parents[1] / "core" / "eos.py")]


def _project(tmp_path, config=""):
    root = tmp_path / "proj"
    root.mkdir()
    assert subprocess.run(EOS + ["init", str(root), "--no-ai"], capture_output=True).returncode == 0
    if config:
        path = root / ".eos" / "config.toml"
        path.write_text(path.read_text(encoding="utf-8") + "\n" + config, encoding="utf-8")
    return root


def _note(root, title, body, tags="t"):
    done = subprocess.run(EOS + ["note", "add", str(root), "--kind", "finding", "--tags", tags,
                                 "--title", title, "--body", body], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


def _prompt(monkeypatch, capsys, root, text):
    monkeypatch.setenv("EOS_STATE_DIR", str(root.parent / "state"))
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(
        {"session_id": "s1", "cwd": str(root), "prompt": text})))
    assert hooks.main(["user-prompt"]) == 0
    return capsys.readouterr().out


def _log(root):
    path = root / ".eos" / "data" / "briefs.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_filler_prompt_logs_abstained(tmp_path, monkeypatch, capsys):
    root = _project(tmp_path)
    _prompt(monkeypatch, capsys, root, "ok, continue")
    entry = _log(root)[-1]
    assert entry["outcome"] == "abstained" and entry["reason"] == "filler"
    assert set(entry) >= {"ts", "session", "outcome", "reason", "sections", "best_score", "chars"}
    assert "continue" not in json.dumps(entry)


def test_injected_prompt_logs_sections(tmp_path, monkeypatch, capsys):
    root = _project(tmp_path)
    _note(root, "Refund queue stalls after settlement", "The worker stops.", tags="refund")
    _prompt(monkeypatch, capsys, root, "refund queue stalls")
    entry = _log(root)[-1]
    assert entry["outcome"] == "injected" and entry["reason"] == "injected"
    assert "RELATED NOTES" in entry["sections"] and entry["chars"] > 0 and entry["best_score"] > 0


def test_nothing_found_logs_abstained(tmp_path, monkeypatch, capsys):
    root = _project(tmp_path)
    decision = {}
    assert brief.build(root, task="zzqx quuxify wibble", task_only=True, decision=decision) == ""
    assert decision["outcome"] == "abstained" and decision["reason"] == "nothing-found"


def test_related_needs_min_score(tmp_path):
    root = _project(tmp_path)
    _note(root, "Settlement reconciliation drifts nightly", "The invoice export fails when the payment retries.",
          tags="settlement")
    _note(root, "Invoice export retry payment fails", "Detail.", tags="invoice")
    text = brief.build(root, task="invoice export payment", task_only=True)
    related = text.split("RELATED NOTES")[1] if "RELATED NOTES" in text else ""
    assert "Invoice export retry payment fails" in related
    assert "Settlement reconciliation" not in related


def test_one_common_word_is_not_related(tmp_path):
    root = _project(tmp_path)
    _note(root, "Gateway timeout while uploading attachments to storage bucket", "Detail.", tags="gateway")
    text = brief.build(root, task="storage quota alerting", task_only=True)
    assert "Gateway timeout" not in text


def test_decision_log_off(tmp_path, monkeypatch, capsys):
    root = _project(tmp_path, "[brief]\ndecision_log = false\n")
    _prompt(monkeypatch, capsys, root, "ok, continue")
    assert not (root / ".eos" / "data" / "briefs.jsonl").exists()


def test_unwritable_data_dir_does_not_raise(tmp_path, monkeypatch, capsys):
    root = _project(tmp_path)
    data = root / ".eos" / "data"
    shutil.rmtree(data, ignore_errors=True)
    data.write_text("not a directory", encoding="utf-8")
    assert _prompt(monkeypatch, capsys, root, "ok, continue") == ""
