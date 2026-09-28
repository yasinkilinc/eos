"""`post-batch`: what is already known about a touched file, in front of the agent (2.x roadmap C5).

A workspace fixture: a root, two projects under projects.toml, and a worktree
directory holding a second checkout of one of them.
"""
import hashlib
import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

from core import hooks, inject

EOS = [sys.executable, str(Path(__file__).resolve().parents[1] / "core" / "eos.py")]


def _project(root: Path) -> Path:
    root.mkdir(parents=True)
    assert subprocess.run(EOS + ["init", str(root), "--no-ai"], capture_output=True).returncode == 0
    return root


def _note(project: Path, name: str, title: str, scope=(), source="", body="Body text.", hashes=None,
          kind="finding"):
    store = project / ".eos" / "knowledge"
    store.mkdir(parents=True, exist_ok=True)
    lines = ["---", f"kind: {kind}", f"title: {title}", "created: 2026-09-01"]
    if source:
        lines.append(f"source: {source}")
    if scope:
        lines += ["scope:"] + [f"  - {s}" for s in scope]
    if hashes:
        lines += ["scope_hashes:"] + [f"  - {h}" for h in hashes]
    (store / name).write_text("\n".join(lines + ["---", "", body]) + "\n", encoding="utf-8")


@pytest.fixture
def ws(tmp_path, monkeypatch):
    monkeypatch.setenv("EOS_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("EOS_HOOK_STATE_DIR", raising=False)
    hub = _project(tmp_path / "hub")
    svc = _project(tmp_path / "services" / "svc-a")
    (hub / ".eos" / "projects.toml").write_text(
        'worktrees = ["../services/.worktrees"]\n\n[[project]]\nroot = "../services/svc-a"\n', encoding="utf-8")
    _note(svc, "20260901-claude.md", "About the service CLAUDE.md", ["CLAUDE.md"])
    _note(svc, "20260902-api.md", "svc-a API map, 40 endpoints", ["pom.xml"], source="api-inventory",
          body="| GET | /a |\n" * 50)
    _note(svc, "20260903-near.md", "Other class in the same package", ["src/pkg/Other.java"])
    for n in range(10, 22):
        _note(svc, f"202609{n}-far-{n}.md", f"Far note {n}", [f"src/other{n}/X.java"])
    _note(svc, "20260930-guide.md", "svc-a AGENTS.md: Critical Rules", source="agents-md")
    return tmp_path, hub, svc


def _touch(monkeypatch, capsys, hub, session, *paths, tool="Read"):
    payload = {"session_id": session, "cwd": str(hub), "hook_event_name": "PostToolBatch",
               "tool_calls": [{"tool_name": tool, "tool_input": {"file_path": str(p)}} for p in paths]}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    assert hooks.main(["post-batch"]) == 0
    out = capsys.readouterr().out
    return json.loads(out)["hookSpecificOutput"]["additionalContext"] if out.strip() else ""


def test_an_instruction_file_injects_nothing(ws, monkeypatch, capsys):
    _, hub, svc = ws
    assert _touch(monkeypatch, capsys, hub, "s1", svc / "CLAUDE.md") == ""
    assert _touch(monkeypatch, capsys, hub, "s1", hub / ".claude" / "agents" / "atlas.md") == ""


def test_the_digest_is_capped_near_first_then_newest_agents_md_last(ws, monkeypatch, capsys):
    _, hub, svc = ws
    text = _touch(monkeypatch, capsys, hub, "s2", svc / "src" / "pkg" / "Foo.java")
    lines = [line for line in text.splitlines() if line.startswith("- ") and "more on svc-a" not in line]
    assert text.startswith("### Other notes on svc-a")
    assert len(lines) == inject.MAX_DIGEST_LINES and "more on svc-a" in text
    assert lines[0] == "- Other class in the same package" and lines[1] == "- Far note 21"
    assert "AGENTS.md: Critical Rules" not in text
    assert 'eos note search ../services/svc-a "<words>"' in text


def test_a_bulk_table_takes_no_body_slot_but_is_listed(ws, monkeypatch, capsys):
    _, hub, svc = ws
    text = _touch(monkeypatch, capsys, hub, "s3", svc / "pom.xml")
    assert "### Known about pom.xml" not in text and "svc-a API map, 40 endpoints" in text


def test_a_scoped_note_arrives_whole_once_per_session_and_stale_is_marked(ws, monkeypatch, capsys):
    _, hub, svc = ws
    source = svc / "src" / "pkg" / "Other.java"
    source.parent.mkdir(parents=True)
    source.write_text("class Other {}\n", encoding="utf-8")
    _note(svc, "20260903-near.md", "Other class in the same package", ["src/pkg/Other.java"],
          body="Never construct it twice.", hashes=[hashlib.sha256(b"old").hexdigest()])
    text = _touch(monkeypatch, capsys, hub, "s4", source)
    assert "### Known about src/pkg/Other.java" in text and "Never construct it twice." in text
    assert "[STALE: this note was written on 2026-09-01" in text
    assert "Never construct it twice." not in _touch(monkeypatch, capsys, hub, "s4", source, tool="Edit")


def test_a_worktree_checkout_is_its_projects_and_relative_to_the_checkout(ws, monkeypatch, capsys):
    tmp_path, hub, svc = ws
    _note(svc, "20260904-foo.md", "Foo is built by the factory only", ["src/pkg/Foo.java"], body="Use the factory.")
    checkout = tmp_path / "services" / ".worktrees" / "svc-a-PROJ-12" / "src" / "pkg" / "Foo.java"
    text = _touch(monkeypatch, capsys, hub, "s5", checkout)
    assert "### Known about src/pkg/Foo.java" in text and "Use the factory." in text
    assert _touch(monkeypatch, capsys, hub, "s6", tmp_path / "services" / ".worktrees" / "other-1" / "a.txt") == ""


def test_the_workspace_roots_own_files_have_its_own_notes(ws, monkeypatch, capsys):
    _, hub, _ = ws
    _note(hub, "20260905-script.md", "The deploy script needs a clean tree", ["scripts/deploy.sh"],
          body="Commit first.")
    text = _touch(monkeypatch, capsys, hub, "s7", hub / "scripts" / "deploy.sh", tool="Edit")
    assert "### Known about scripts/deploy.sh" in text and "Commit first." in text


# --- C4: dedup against the harness's own always-loaded files (Day 4) -------------------


def test_a_body_already_in_an_always_loaded_file_is_left_out(ws, monkeypatch, capsys):
    _, hub, svc = ws
    (svc / "CLAUDE.md").write_text(
        "The retry queue drains itself on boot, never call drain() by hand.\n", encoding="utf-8")
    (svc / ".eos" / "config.toml").write_text(
        (svc / ".eos" / "config.toml").read_text(encoding="utf-8") + '\n[ai]\nloaded = ["CLAUDE.md"]\n',
        encoding="utf-8")
    source = svc / "src" / "pkg" / "Retry.java"
    source.parent.mkdir(parents=True)
    source.write_text("class Retry {}\n", encoding="utf-8")
    _note(svc, "20260906-retry.md", "Retry queue self-drains", ["src/pkg/Retry.java"],
          body="The retry queue drains itself on boot, never call drain() by hand.")
    text = _touch(monkeypatch, capsys, hub, "s8", source)
    assert "never call drain() by hand." not in text


# --- C3: a long body narrowed to what the user last asked about -------------------------


_FILLER = "".join(f"Line {n} is about the ledger layout and nothing else.\n" for n in range(40))
_LONG = _FILLER + "The refund is retried by the scheduler, never by the caller.\n" + _FILLER


def _prompt(monkeypatch, capsys, hub, session, text):
    payload = {"session_id": session, "cwd": str(hub), "hook_event_name": "UserPromptSubmit", "prompt": text}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    assert hooks.main(["user-prompt"]) == 0
    capsys.readouterr()


def _refund_source(svc: Path) -> Path:
    source = svc / "src" / "pkg" / "Refund.java"
    source.parent.mkdir(parents=True)
    source.write_text("class Refund {}\n", encoding="utf-8")
    return source


def test_a_long_finding_is_narrowed_to_the_last_prompts_words(ws, monkeypatch, capsys):
    _, hub, svc = ws
    source = _refund_source(svc)
    _note(svc, "20260907-refund.md", "Refund retries", ["src/pkg/Refund.java"], body=_LONG)
    _prompt(monkeypatch, capsys, hub, "c3a", "why is the refund sent twice")
    text = _touch(monkeypatch, capsys, hub, "c3a", source)
    assert "The refund is retried by the scheduler" in text
    assert "Narrowed to the lines the task's terms hit (refund)" in text
    assert "eos note show" in text and "20260907-refund.md" in text
    assert text.count("ledger layout") < 40


def test_without_a_prompt_a_long_finding_arrives_whole(ws, monkeypatch, capsys):
    _, hub, svc = ws
    source = _refund_source(svc)
    _note(svc, "20260907-refund.md", "Refund retries", ["src/pkg/Refund.java"], body=_LONG)
    text = _touch(monkeypatch, capsys, hub, "c3b", source)
    assert text.count("ledger layout") == 80 and "Narrowed" not in text


def test_a_lesson_is_never_narrowed(ws, monkeypatch, capsys):
    _, hub, svc = ws
    source = _refund_source(svc)
    _note(svc, "20260907-refund.md", "Refund retries", ["src/pkg/Refund.java"], body=_LONG, kind="lesson")
    _prompt(monkeypatch, capsys, hub, "c3c", "why is the refund sent twice")
    text = _touch(monkeypatch, capsys, hub, "c3c", source)
    assert text.count("ledger layout") == 80 and "Narrowed" not in text


def test_a_two_letter_acronym_in_the_prompt_survives_until_injection(ws, monkeypatch, capsys):
    _, hub, svc = ws
    source = _refund_source(svc)
    _note(svc, "20260907-refund.md", "Refund retries", ["src/pkg/Refund.java"],
          body=_FILLER + "The CI pipeline reruns this export on every merge.\n" + _FILLER)
    _prompt(monkeypatch, capsys, hub, "c3e", "why did CI fail")
    assert "terms hit (ci)" in _touch(monkeypatch, capsys, hub, "c3e", source)


def test_the_latest_prompt_decides_the_terms(ws, monkeypatch, capsys):
    _, hub, svc = ws
    source = _refund_source(svc)
    _note(svc, "20260907-refund.md", "Refund retries", ["src/pkg/Refund.java"], body=_LONG)
    _prompt(monkeypatch, capsys, hub, "c3d", "the scheduler config")
    _prompt(monkeypatch, capsys, hub, "c3d", "why is the refund sent twice")
    assert "terms hit (refund)" in _touch(monkeypatch, capsys, hub, "c3d", source)


def test_inject_off_in_config_says_nothing(ws, monkeypatch, capsys):
    _, hub, svc = ws
    config = hub / ".eos" / "config.toml"
    config.write_text(config.read_text(encoding="utf-8") + "\n[hooks]\ninject = false\n", encoding="utf-8")
    assert _touch(monkeypatch, capsys, hub, "s8", svc / "src" / "pkg" / "Foo.java") == ""


def test_the_session_budget_holds_across_touches():
    digest = "### Other notes on svc-a\n" + "\n".join(f"- Title {n}" for n in range(8))
    used, spent, titles = 0, 0, 0
    pointer = 'eos note search {path} "{words}"'
    for _ in range(5):
        out, spent, used = inject.budgeted(digest, spent, used, pointer=pointer)
        titles += sum(1 for line in out.splitlines() if line.startswith("- Title"))
    assert titles == inject.SESSION_DIGEST_LINES
    big = "### Known about src/A.java\nTitle\n\n" + "x" * 4000
    spent = int(inject.SESSION_MAX_TOKENS * inject.CHARS_PER_TOKEN) - 100
    out, _, _ = inject.budgeted(big + "\n\n" + big, spent, 0, pointer=pointer)
    assert out.count("notes withheld") == 1 and "xxxx" not in out
    assert 'eos note search . "src/A.java"' in out
    assert inject.budgeted(big, 0, 0, pointer=pointer)[0] == big
