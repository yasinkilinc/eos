"""The per-service SQLite index: `eos index`, `eos query`, and the nexus post-merge hook.

The index is derived. Markdown notes in git stay the source of truth, so every
test here builds from files on disk and checks what an agent can read back.
The legacy `.eos/eos.db` left by an unrelated tool is never read or written.
"""
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

from core import eos as eos_cli
from core import index

REPO = Path(__file__).resolve().parents[1]
EOS = [sys.executable, str(REPO / "core" / "eos.py")]
SERVICE = "acme-wallet-topup"
LEGACY_BYTES = b"legacy yos database, never touched"

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


def _run(args, **kw):
    return subprocess.run(EOS + args, capture_output=True, text=True, encoding="utf-8", **kw)


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _chain(config_id, bi, flow, bean, impl_cls=None, sort_id=10):
    return {
        "bi": bi, "flow": flow, "state": "productOrder", "phase": "DURING",
        "sort_id": sort_id, "bean_name": bean, "cmd_short_code": bean, "impl_cls": impl_cls,
        "config_active": 1, "def_active": 1, "next_struct": 1, "managed_by": None,
        "cmd_config_id": config_id, "cmd_def_id": "d" + config_id,
        "updated_at": None, "updated_by": None,
    }


HAND_WRITTEN_NOTE = """---
kind: defect
title: Wallet is credited twice on top-up
created: 2026-09-01
updated: 2026-09-05
source: PROJ-123
tags:
  - topup
  - wallet
scope:
  - src/main/java/com/fm/FmFooCommand.java
  - FmMissingClass
scope_hashes:
  - {hash}
  - null
session: abc123
---

## Root cause
FmCalculateMsisdnCommand posts the credit twice.

## Solution
Guard the second credit.

## Metric
2 credits -> 1 credit.
""".format(hash="a" * 64)

GENERATED_NOTE = """---
kind: finding
title: REST endpoints of acme-wallet-topup
created: 2026-09-02
source: api-inventory
tags:
  - api
---

POST /cpq/customerOrder/initialize
"""

CRLF_NOTE = (
    "---\r\nkind: finding\r\ntitle: Checked out with CRLF\r\ncreated: 2026-09-04\r\n"
    "updated: 2026-09-06\r\ntags:\r\n  - windows\r\n---\r\n\r\nGit for Windows rewrote the line endings.\r\n"
)

GRAPH = {
    "languages": ["java"],
    "tech_stack": ["java", "spring-boot"],
    "entry_points": ["file:src/main/java/com/fm/App.java"],
    "nodes": [
        {"id": "folder:src", "type": "folder", "label": "src", "path": "src",
         "language": None, "tags": [], "metadata": {}, "doc": None},
        {"id": "file:src/main/java/com/fm/App.java", "type": "entry-point", "label": "App",
         "path": "src/main/java/com/fm/App.java", "language": "java", "tags": ["entry-point", "java"],
         "metadata": {"exports": ["com.fm.App"], "top_symbols": ["App", "main"]}, "doc": "Boots the service."},
        {"id": "file:src/main/java/com/fm/FmFooCommand.java", "type": "component", "label": "FmFooCommand",
         "path": "src/main/java/com/fm/FmFooCommand.java", "language": "java", "tags": ["component", "java"],
         "metadata": {"exports": ["com.fm.FmFooCommand"], "top_symbols": ["FmFooCommand"]}, "doc": None},
        {"id": "file:@parent:parent/src/main/java/com/example/Base.java", "type": "component", "label": "Base",
         "path": "@parent:parent/src/main/java/com/example/Base.java", "language": "java",
         "tags": ["component", "java"], "metadata": {"exports": ["com.example.Base"], "top_symbols": ["Base"]},
         "doc": None},
    ],
    "edges": [
        {"source": "file:src/main/java/com/fm/App.java", "target": "file:src/main/java/com/fm/FmFooCommand.java",
         "kind": "import", "weight": 1, "metadata": {"imported": "FmFooCommand"}},
        {"source": "file:src/main/java/com/fm/App.java", "target": "file:src/main/java/com/fm/FmFooCommand.java",
         "kind": "import", "weight": 1, "metadata": {"imported": "FmFooCommand"}},
        {"source": "file:src/main/java/com/fm/FmFooCommand.java",
         "target": "file:@parent:parent/src/main/java/com/example/Base.java",
         "kind": "import", "weight": 1, "metadata": {"imported": "Base"}},
        {"source": "folder:src", "target": "file:src/main/java/com/fm/App.java",
         "kind": "folder-hierarchy", "weight": 1, "metadata": {}},
    ],
}

BRAIN_DOCS = {
    "AI_SUMMARY.md": "# acme-wallet-topup\n\nOrchestrates wallet top-up orders through FmCalculateMsisdnCommand.\n",
    "Architecture.md": "# Architecture\n\n## Hub Components\n- FmFooCommand\n",
    "EntryPoints.md": "# Entry Points\n",
    "TechStack.md": "# Tech Stack\n- java\n",
    "_index.md": "---\ncomponents: 3\n---\n# Index\n",
}


def _write_brain(root: Path, last_scan_nodes: int | None = None) -> None:
    brain = root / ".eos" / "data" / "brain"
    for name, text in BRAIN_DOCS.items():
        _write(brain / name, text)
    _write(brain / "graph.json", json.dumps(GRAPH))
    nodes = len(GRAPH["nodes"]) if last_scan_nodes is None else last_scan_nodes
    last_scan = {"languages": ["java"], "files_parsed": 3, "nodes": nodes, "edges": len(GRAPH["edges"])}
    _write(root / ".eos" / "data" / "last_scan.json", json.dumps(last_scan))


def _eos_dir(root: Path, service: str) -> None:
    _write(
        root / ".eos" / "config.toml",
        f'[knowledge]\ndir = "../../nexus/.devin/knowledge/{service}"\n',
    )


def _workspace(tmp_path: Path, nexus_git_dir: bool = True) -> dict:
    ws = tmp_path / "ws"
    nexus = ws / "nexus"
    if nexus_git_dir:
        (nexus / ".git").mkdir(parents=True)
    knowledge = nexus / ".devin" / "knowledge"
    _write(knowledge / SERVICE / "20260901-wallet-double-credit.md", HAND_WRITTEN_NOTE)
    _write(knowledge / SERVICE / "20260902-rest-endpoints.md", GENERATED_NOTE)
    _write(knowledge / SERVICE / "20260903-conflicted.md", "<<<<<<< HEAD\nours\n=======\ntheirs\n>>>>>>> incoming\n")
    (knowledge / SERVICE / "20260904-crlf.md").write_bytes(CRLF_NOTE.encode("utf-8"))
    _write(knowledge / "acme-customer-profile" / "20260901-customer.md",
           "---\nkind: finding\ntitle: Customer note\ncreated: 2026-09-01\n---\n\nUnrelated.\n")

    journeys = nexus / "docs" / "journeys"
    _write(journeys / "00-platform.md", "# The order-capture platform\n\nShared ground.\n")
    _write(journeys / "10-top-up.md", """---
journey: topup
services: [acme-wallet-topup, acme-customer-profile]
bis: [TOP_UP]
flows: [TOP_UP]
verified_on: env1
verified_at: 2026-08-23
verified_by: chain snapshot
---

# Top-up — end to end

The wallet is credited by Order Management.
""")
    _write(journeys / "20-activation.md", """---
journey: activation
services: [acme-orders]
bis: [REAL_SALE]
flows: [MAIN_ORDER]
---

# Activation
""")
    env1 = journeys / "_snapshots" / "env1"
    _write(env1 / "_meta.json", json.dumps({
        "generated_at": "2026-09-10T09:14:41Z", "environment": "env1", "schema": "bss_common_dc_fm_env1",
        "scope": "journey business interactions", "generated_by": "dump-config-chains.sh", "row_counts": {},
    }))
    _write(env1 / "chains.json", json.dumps([
        _chain("c1", "TOP_UP", "TOP_UP", "fmFooCommand", sort_id=10),
        _chain("c2", "TOP_UP", "TOP_UP", "customName", sort_id=20),
        _chain("c3", "TOP_UP", "TOP_UP", "parentStepCommand", sort_id=30),
        _chain("c4", "TOP_UP", "TOP_UP", "commonStepCommand", sort_id=40),
        _chain("c5", "TOP_UP", "TOP_UP", "PaymentMethodValidationCommand",
               impl_cls="com.example.app.cpq.ordercapture.command.PaymentMethodValidationCommand", sort_id=50),
        _chain("c6", "TOP_UP", "TOP_UP", "legacyGoneCommand",
               impl_cls="com.example.app.upstreambulkordercapture.LegacyGoneCommand", sort_id=60),
        _chain("c7", "TOP_UP", "TOP_UP", None, sort_id=70),
        _chain("c8", "TOP_UP", "TOP_UP", "searchStepCommand", sort_id=80),
        _chain("c9", "TOP_UP", "TOP_UP", "testOnlyCommand", sort_id=90),
        _chain("c10", "REAL_SALE", "MAIN_ORDER", "customerOnlyCommand", sort_id=10),
    ]))
    _write(env1 / "flow-steps.json", json.dumps([
        {"bi": "TOP_UP", "flow": "TOP_UP", "state": "productOrder", "sale_channel_id": None, "sort_id": 10,
         "active": 1, "optional": 0, "checkout": 0, "next_struct": 1, "visibility_class": None,
         "managed_by": "SOLUTION", "config_id": "f1"},
        {"bi": "REAL_SALE", "flow": "MAIN_ORDER", "state": "productOrder", "sale_channel_id": None, "sort_id": 10,
         "active": 1, "optional": 0, "checkout": 0, "next_struct": 1, "visibility_class": None,
         "managed_by": "SOLUTION", "config_id": "f2"},
    ]))

    fm = ws / "microservices"
    service = fm / SERVICE
    java = "src/main/java/com/fm"
    _write(service / java / "FmFooCommand.java", "public class FmFooCommand {}\n")
    _write(service / java / "BarCommand.java", '@Component("customName")\npublic class BarCommand {}\n')
    _write(service / "src/test/java/com/fm/TestOnlyCommand.java", "public class TestOnlyCommand {}\n")
    _eos_dir(service, SERVICE)
    (service / ".eos" / "eos.db").write_bytes(LEGACY_BYTES)
    _write_brain(service)

    customer = fm / "acme-customer-profile"
    _write(customer / java / "PaymentMethodValidationCommand.java", "public class PaymentMethodValidationCommand {}\n")
    _write(customer / java / "CustomerOnlyCommand.java", "public class CustomerOnlyCommand {}\n")
    _eos_dir(customer, "acme-customer-profile")
    (fm / "acme-search-service").mkdir(parents=True)

    parents = ws / "parent-microservices"
    pkg_dir = "src/main/java/com/example"
    _write(parents / "upstream-wallet-topup" / pkg_dir / "ParentStepCommand.java", "public class ParentStepCommand {}\n")
    _write(parents / "upstream-wallet-topup" / pkg_dir / "PaymentMethodValidationCommand.java",
           "public class PaymentMethodValidationCommand {}\n")
    _write(parents / "upstream-common" / pkg_dir / "CommonStepCommand.java", "public class CommonStepCommand {}\n")
    _write(parents / "upstream-search-service" / pkg_dir / "SearchStepCommand.java",
           "public class SearchStepCommand {}\n")
    return {"ws": ws, "nexus": nexus, "service": service, "customer": customer, "knowledge": knowledge}


def _git_env(tmp_path: Path) -> dict:
    config = tmp_path / "gitconfig"
    if not config.exists():
        config.write_text("", encoding="utf-8")
    env = os.environ.copy()
    env.update({
        "GIT_CONFIG_GLOBAL": str(config),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "Dev One",
        "GIT_AUTHOR_EMAIL": "Dev.One@Example.COM",
        "GIT_COMMITTER_NAME": "Dev One",
        "GIT_COMMITTER_EMAIL": "dev.one@example.com",
    })
    return env


def _git(cwd: Path, env: dict, *args: str) -> str:
    done = subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    return done.stdout.strip()


def _commit_history(service: Path, env: dict) -> None:
    def on(day: int) -> dict:
        # Distinct dates: commits made within one second have no defined order in `git log`.
        stamp = f"2026-09-0{day}T10:00:00+03:00"
        return {**env, "GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp}

    _write(service / ".gitignore", ".eos/\n")
    _git(service, env, "init", "-q", "-b", "master")
    _git(service, env, "add", "-A")
    _git(service, on(1), "commit", "-q", "-m", "feat(PROJ-101): add the foo command")
    _git(service, env, "checkout", "-q", "-b", "feature/PROJ-202")
    _write(service / "src/main/java/com/fm/BarCommand.java", '@Component("customName")\npublic class BarCommand { }\n')
    _git(service, env, "add", "-A")
    message = service.parent / "message.txt"
    message.write_bytes(b"fix(ACME-7): guard the wallet credit\r\n\r\nSee NFR-1 and OPS-9 for context.\r\n")
    _git(service, on(2), "commit", "-q", "--cleanup=verbatim", "-F", str(message))
    _git(service, env, "checkout", "-q", "master")
    _git(service, on(3), "merge", "-q", "--no-ff", "feature/PROJ-202",
         "-m", "Pull request #12: fix(ACME-7): guard the wallet credit",
         "-m", "Merge in TEAM/repo from feature/PROJ-202 to master")


def _rows(db: Path, sql: str, params=()) -> list[tuple]:
    conn = sqlite3.connect(db)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def _meta(db: Path) -> dict:
    return dict(_rows(db, "SELECT key, value FROM meta"))


# --- build ---------------------------------------------------------------------


def test_build_writes_under_data_and_never_touches_the_legacy_db(tmp_path):
    paths = _workspace(tmp_path)
    service = paths["service"]
    legacy = service / ".eos" / "eos.db"
    before = legacy.stat().st_mtime_ns

    result = index.build(service)

    assert result.path == service / ".eos" / "data" / "eos.db"
    assert result.path.is_file()
    assert legacy.read_bytes() == LEGACY_BYTES
    assert legacy.stat().st_mtime_ns == before
    assert sorted(p.name for p in (service / ".eos" / "data").iterdir()) == ["brain", "eos.db", "last_scan.json"]


def test_notes_carry_scope_tags_and_the_generated_flag(tmp_path):
    service = _workspace(tmp_path)["service"]
    db = index.build(service).path

    notes = {row[0]: row[1:] for row in _rows(
        db, "SELECT file, kind, title, created, updated, source, session, generated FROM note")}
    assert notes["20260901-wallet-double-credit.md"] == (
        "defect", "Wallet is credited twice on top-up", "2026-09-01", "2026-09-05", "PROJ-123", "abc123", 0)
    assert notes["20260902-rest-endpoints.md"][-1] == 1
    assert notes["20260904-crlf.md"][:4] == ("finding", "Checked out with CRLF", "2026-09-04", "2026-09-06")
    assert "20260903-conflicted.md" not in notes

    scope = _rows(db, """
        SELECT s.ord, s.entry, s.hash FROM note_scope s JOIN note n ON n.id = s.note_id
        WHERE n.file = '20260901-wallet-double-credit.md' ORDER BY s.ord""")
    assert scope == [(0, "src/main/java/com/fm/FmFooCommand.java", "a" * 64), (1, "FmMissingClass", None)]
    tags = _rows(db, """
        SELECT t.tag FROM note_tag t JOIN note n ON n.id = t.note_id
        WHERE n.file = '20260901-wallet-double-credit.md' ORDER BY t.tag""")
    assert tags == [("topup",), ("wallet",)]
    body = _rows(db, "SELECT body FROM note WHERE file = '20260904-crlf.md'")[0][0]
    assert "\r" not in body

    issues = _rows(db, "SELECT source, ref, problem FROM build_issue WHERE source = 'note'")
    assert [(source, ref) for source, ref, _ in issues] == [("note", "20260903-conflicted.md")]


def test_brain_docs_file_nodes_and_deduplicated_import_edges(tmp_path):
    service = _workspace(tmp_path)["service"]
    db = index.build(service).path

    assert [row[0] for row in _rows(db, "SELECT name FROM brain_doc ORDER BY name")] == sorted(BRAIN_DOCS)
    nodes = _rows(db, "SELECT id, type, path, dir, origin, is_entry, doc FROM node ORDER BY id")
    assert nodes == [
        ("file:@parent:parent/src/main/java/com/example/Base.java", "component",
         "@parent:parent/src/main/java/com/example/Base.java", "@parent:parent/src/main/java/com/example",
         "parent", 0, None),
        ("file:src/main/java/com/fm/App.java", "entry-point", "src/main/java/com/fm/App.java",
         "src/main/java/com/fm", "own", 1, "Boots the service."),
        ("file:src/main/java/com/fm/FmFooCommand.java", "component", "src/main/java/com/fm/FmFooCommand.java",
         "src/main/java/com/fm", "own", 0, None),
    ]
    edges = _rows(db, """
        SELECT s.label, d.label, e.kind, e.imported FROM edge e
        JOIN node s ON s.nid = e.src JOIN node d ON d.nid = e.dst ORDER BY s.label""")
    assert edges == [("App", "FmFooCommand", "import", "FmFooCommand"), ("FmFooCommand", "Base", "import", "Base")]
    symbols = _rows(db, """
        SELECT y.role, y.name FROM node_symbol y JOIN node n ON n.nid = y.nid
        WHERE n.label = 'App' ORDER BY y.role, y.name""")
    assert symbols == [("export", "com.fm.App"), ("top", "App"), ("top", "main")]
    meta = _meta(db)
    assert json.loads(meta["languages"]) == ["java"]
    assert meta["with_parents"] == "1"


@pytest.mark.parametrize("breakage", ["count mismatch", "graph newer than last_scan"])
def test_an_incomplete_scan_is_left_out_and_reported(tmp_path, breakage):
    service = _workspace(tmp_path)["service"]
    if breakage == "count mismatch":
        _write_brain(service, last_scan_nodes=99)
    else:
        graph = service / ".eos" / "data" / "brain" / "graph.json"
        future = time.time() + 60
        os.utime(graph, (future, future))

    db = index.build(service).path

    assert _rows(db, "SELECT COUNT(*) FROM brain_doc") == [(0,)]
    assert _rows(db, "SELECT COUNT(*) FROM node") == [(0,)]
    assert _rows(db, "SELECT COUNT(*) FROM build_issue WHERE source = 'brain'") == [(1,)]
    assert _rows(db, "SELECT COUNT(*) FROM note") == [(3,)]


@needs_git
def test_history_parses_ticket_keys_prs_and_strips_crlf(tmp_path):
    service = _workspace(tmp_path)["service"]
    env = _git_env(tmp_path)
    _commit_history(service, env)
    head = _git(service, env, "rev-parse", "HEAD")

    db = index.build(service).path

    commits = _rows(db, "SELECT ord, subject, body, is_merge, pr, author_email FROM git_commit ORDER BY ord")
    assert [c[0] for c in commits] == [0, 1, 2]
    merge, crlf, first = commits
    assert merge[1] == "Pull request #12: fix(ACME-7): guard the wallet credit"
    assert merge[3:5] == (1, 12)
    assert crlf[1] == "fix(ACME-7): guard the wallet credit"
    assert all("\r" not in (c[1] + c[2]) for c in commits)
    assert first[5] == "dev.one@example.com"

    tickets = _rows(db, """
        SELECT c.ord, t.key, t.source FROM git_commit_ticket t JOIN git_commit c ON c.sha = t.sha
        ORDER BY c.ord, t.key""")
    # No default merge_branch_pattern: the merge commit's ticket key is still
    # found (it's also spelled out in the raw merge body text), but tagged
    # "body" rather than "branch". The generic TICKET_PATTERN also picks up
    # NFR-1, which the old company-specific allowlist used to exclude.
    assert tickets == [
        (0, "ACME-7", "subject"), (0, "PROJ-202", "body"),
        (1, "ACME-7", "subject"), (1, "NFR-1", "body"), (1, "OPS-9", "body"),
        (2, "PROJ-101", "subject"),
    ]
    files = _rows(db, """
        SELECT f.path FROM git_commit_file f JOIN git_commit c ON c.sha = f.sha WHERE c.ord = 2 ORDER BY f.path""")
    assert ("src/main/java/com/fm/FmFooCommand.java",) in files
    assert _meta(db)["git_head"] == head


@needs_git
def test_history_reads_the_project_repository_even_inside_a_git_hook(tmp_path, monkeypatch):
    """An inherited GIT_DIR outranks `git -C <service>`. Anything that runs
    `eos index` or `eos scan` with GIT_DIR exported for another repository
    would otherwise record that repository's history as the service's own."""
    service = _workspace(tmp_path)["service"]
    env = _git_env(tmp_path)
    _commit_history(service, env)
    other = tmp_path / "other"
    other.mkdir()
    _git(other, env, "init", "-q", "-b", "master")
    _git(other, env, "commit", "-q", "--allow-empty", "-m", "chore(PROJ-1): another repository")
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(other))

    db = index.build(service).path

    subjects = [row[0] for row in _rows(db, "SELECT subject FROM git_commit ORDER BY ord")]
    assert len(subjects) == 3
    assert subjects[-1] == "feat(PROJ-101): add the foo command"


def test_meta_records_build_time_schema_and_an_inputs_hash(tmp_path):
    paths = _workspace(tmp_path)
    service = paths["service"]

    first = _meta(index.build(service).path)
    second = _meta(index.build(service).path)
    _write(paths["knowledge"] / SERVICE / "20260905-new.md",
           "---\nkind: finding\ntitle: New\ncreated: 2026-09-05\n---\n\nNew body.\n")
    third = _meta(index.build(service).path)

    assert first["schema_version"] == str(index.SCHEMA_VERSION)
    assert first["built_at"].endswith("+00:00")
    assert len(first["inputs_sha256"]) == 64
    assert first["inputs_sha256"] == second["inputs_sha256"]
    assert third["inputs_sha256"] != first["inputs_sha256"]
    assert first["service"] == SERVICE


# --- search --------------------------------------------------------------------


def _refs(rows):
    return {(row[0], row[1]) for row in rows}


def test_full_text_search_over_notes_and_brain_docs(tmp_path):
    service = _workspace(tmp_path)["service"]
    db = index.build(service).path
    assert _meta(db)["fts5"] == "1"

    _, rows = index.search(db, "credited twice")
    assert ("note", "20260901-wallet-double-credit.md") in _refs(rows)
    _, rows = index.search(db, "Msisdn")
    assert {("note", "20260901-wallet-double-credit.md"), ("brain", "AI_SUMMARY.md")} <= _refs(rows)
    _, rows = index.search(db, 'PROJ-123 "unbalanced OR (')
    assert rows == []


def test_search_falls_back_to_like_when_fts5_is_missing(tmp_path, monkeypatch):
    service = _workspace(tmp_path)["service"]
    monkeypatch.setattr(index, "fts5_available", lambda conn: False)

    db = index.build(service).path

    assert _meta(db)["fts5"] == "0"
    assert _rows(db, "SELECT type FROM sqlite_master WHERE name = 'search'") == [("table",)]
    columns, rows = index.search(db, "credited twice")
    assert columns[:2] == ["source", "ref"]
    assert ("note", "20260901-wallet-double-credit.md") in _refs(rows)
    _, rows = index.search(db, "Msisdn")
    assert ("brain", "AI_SUMMARY.md") in _refs(rows)
    _, rows = index.search(db, "100%")
    assert rows == []


# --- atomic replacement ---------------------------------------------------------


def test_a_failed_build_keeps_the_previous_index_and_no_temp_file(tmp_path, monkeypatch):
    service = _workspace(tmp_path)["service"]
    db = index.build(service).path
    previous = db.read_bytes()

    def boom(conn):
        raise RuntimeError("crashed halfway")

    monkeypatch.setattr(index, "fts5_available", boom)
    with pytest.raises(RuntimeError):
        index.build(service)

    assert db.read_bytes() == previous
    assert [p.name for p in db.parent.iterdir() if p.name.startswith("eos.db")] == ["eos.db"]


def test_a_locked_index_is_kept_and_reported_in_one_line(tmp_path, monkeypatch, capsys):
    service = _workspace(tmp_path)["service"]
    db = index.build(service).path
    previous = db.read_bytes()

    def refuse(src, dst):
        raise PermissionError(13, "The process cannot access the file", str(dst))

    monkeypatch.setattr(index.os, "replace", refuse)
    with pytest.raises(index.IndexBuildError, match="kept the previous index"):
        index.build(service)
    assert db.read_bytes() == previous
    assert [p.name for p in db.parent.iterdir() if p.name.startswith("eos.db")] == ["eos.db"]

    capsys.readouterr()
    assert eos_cli.main(["index", str(service)]) == 1
    err = capsys.readouterr().err
    assert len(err.strip().splitlines()) == 1
    assert "kept the previous index" in err


# --- CLI ------------------------------------------------------------------------


def test_index_command_builds_and_summarizes(tmp_path):
    service = _workspace(tmp_path)["service"]

    done = _run(["index", str(service)])

    assert done.returncode == 0, done.stderr
    assert "Index written to" in done.stdout
    assert (service / ".eos" / "data" / "eos.db").is_file()


def test_index_command_without_eos_directory_fails_in_one_line(tmp_path):
    done = _run(["index", str(tmp_path)])

    assert done.returncode == 1
    assert len(done.stderr.strip().splitlines()) == 1


def test_query_prints_a_header_and_rows(tmp_path):
    service = _workspace(tmp_path)["service"]
    index.build(service)

    done = _run(["query", str(service), "SELECT kind, title FROM note WHERE generated = 0 ORDER BY file"])

    assert done.returncode == 0, done.stderr
    lines = done.stdout.splitlines()
    assert lines[0] == "kind\ttitle"
    assert "defect\tWallet is credited twice on top-up" in lines


def test_query_rejects_writes(tmp_path):
    service = _workspace(tmp_path)["service"]
    db = index.build(service).path
    before = db.read_bytes()
    attached = tmp_path / "attached.db"

    for sql in (
        "DELETE FROM note",
        "DROP TABLE note",
        "CREATE TEMP TABLE scratch(x INTEGER)",
        f"ATTACH DATABASE '{attached}' AS side",
        "INSERT INTO meta(key, value) VALUES ('x', 'y')",
    ):
        done = _run(["query", str(service), sql])
        assert done.returncode == 1, sql
        assert done.stderr.startswith("error:"), done.stderr

    assert not attached.exists()
    assert db.read_bytes() == before


def test_query_opens_paths_that_need_uri_escaping(tmp_path):
    paths = _workspace(tmp_path / "dir with space #1 %20")
    index.build(paths["service"])

    done = _run(["query", str(paths["service"]), "SELECT COUNT(*) AS notes FROM note"])

    assert done.returncode == 0, done.stderr
    assert done.stdout.splitlines() == ["notes", "3"]


def test_query_search_option(tmp_path):
    service = _workspace(tmp_path)["service"]
    index.build(service)

    done = _run(["query", str(service), "--search", "credited twice"])

    assert done.returncode == 0, done.stderr
    assert "20260901-wallet-double-credit.md" in done.stdout


def test_query_without_an_index_says_how_to_build_one(tmp_path):
    service = _workspace(tmp_path)["service"]

    done = _run(["query", str(service), "SELECT 1"])

    assert done.returncode == 1
    assert "eos index" in done.stderr


def _reword_generated_note(paths: dict, body: str) -> None:
    note = paths["knowledge"] / SERVICE / "20260902-rest-endpoints.md"
    note.write_text(GENERATED_NOTE.replace("POST /cpq/customerOrder/initialize", body), encoding="utf-8")


def test_query_rebuilds_an_index_whose_sources_changed_since_it_was_built(tmp_path):
    """A rebuild the hook never started, or one that was killed, must not leave
    an agent reading old notes with no sign that they are old."""
    paths = _workspace(tmp_path)
    service = paths["service"]
    index.build(service)
    _reword_generated_note(paths, "changed by a teammate")
    sql = "SELECT body FROM note WHERE file = '20260902-rest-endpoints.md'"

    stale = _run(["query", str(service), sql])
    fresh = _run(["query", str(service), sql])

    assert stale.returncode == 0, stale.stderr
    assert stale.stdout.splitlines() == ["body", "changed by a teammate"]
    assert len(stale.stderr.strip().splitlines()) == 1 and "rebuilt" in stale.stderr, stale.stderr
    assert fresh.stdout == stale.stdout
    assert fresh.stderr == ""


@pytest.mark.skipif(os.name == "nt" or getattr(os, "geteuid", lambda: 1)() == 0,
                    reason="needs a directory this user cannot write to")
def test_query_warns_when_a_stale_index_cannot_be_rebuilt(tmp_path):
    paths = _workspace(tmp_path)
    service = paths["service"]
    index.build(service)
    _reword_generated_note(paths, "changed by a teammate")
    data = service / ".eos" / "data"
    data.chmod(0o555)
    try:
        done = _run(["query", str(service), "SELECT body FROM note WHERE file = '20260902-rest-endpoints.md'"])
    finally:
        data.chmod(0o755)

    assert done.returncode == 0, done.stderr
    assert done.stdout.splitlines() == ["body", "POST /cpq/customerOrder/initialize"]
    lines = done.stderr.strip().splitlines()
    assert len(lines) == 1 and lines[0].startswith("warning:") and "stale" in lines[0], done.stderr


def test_query_still_answers_when_its_sources_cannot_be_checked(tmp_path):
    service = _workspace(tmp_path)["service"]
    index.build(service)
    config = service / ".eos" / "config.toml"
    config.write_text(config.read_text(encoding="utf-8") + "[broken\n", encoding="utf-8")

    done = _run(["query", str(service), "SELECT COUNT(*) AS notes FROM note"])

    assert done.returncode == 0, done.stderr
    assert done.stdout.splitlines() == ["notes", "3"]
    assert len(done.stderr.strip().splitlines()) == 1 and done.stderr.startswith("warning:"), done.stderr


def test_query_and_index_escape_what_a_non_utf8_stdout_cannot_encode(tmp_path):
    """Windows gives piped or redirected output its ANSI code page -- cp1252 in
    the West -- and commit subjects, brain docs and journey docs here carry
    emoji, arrows and Turkish letters it has no byte for."""
    paths = _workspace(tmp_path / "Kılınç")
    service = paths["service"]
    _write(paths["knowledge"] / SERVICE / "20260906-release.md",
           "---\nkind: finding\ntitle: \U0001f680 Product Release → v4.0.0\ncreated: 2026-09-06\n---\n\nShipped.\n")
    env = {**os.environ, "PYTHONIOENCODING": "cp1252"}

    indexed = subprocess.run(EOS + ["index", str(service)], capture_output=True, env=env)
    queried = subprocess.run(EOS + ["query", str(service), "SELECT title FROM note WHERE file = '20260906-release.md'"],
                             capture_output=True, env=env)

    assert (indexed.returncode, queried.returncode) == (0, 0), (
        indexed.stderr.decode("cp1252", "replace"), queried.stderr.decode("cp1252", "replace"))
    assert queried.stdout.decode("cp1252").splitlines() == ["title", "\\U0001f680 Product Release \\u2192 v4.0.0"]
    assert "K\\u0131l\\u0131nç" in indexed.stdout.decode("cp1252")


def test_scan_builds_the_index(tmp_path):
    project = tmp_path / "project"
    _write(project / "main.py", "import helper\nhelper.run()\n")
    _write(project / "helper.py", "def run():\n    return 1\n")
    assert _run(["init", str(project)]).returncode == 0

    done = _run(["scan", str(project), "--full"])

    assert done.returncode == 0, done.stderr
    db = project / ".eos" / "data" / "eos.db"
    assert db.is_file()
    assert _rows(db, "SELECT COUNT(*) FROM brain_doc") == [(5,)]
    assert _rows(db, "SELECT COUNT(*) FROM node")[0][0] >= 2


def test_clean_removes_the_index_but_not_the_legacy_db(tmp_path):
    service = _workspace(tmp_path)["service"]
    index.build(service)

    done = _run(["clean", str(service)])

    assert done.returncode == 0, done.stderr
    assert not (service / ".eos" / "data" / "eos.db").exists()
    assert (service / ".eos" / "eos.db").read_bytes() == LEGACY_BYTES


# --- post-merge hook --------------------------------------------------------------


def _hook_workspace(tmp_path: Path) -> tuple[dict, dict]:
    paths = _workspace(tmp_path, nexus_git_dir=False)
    nexus = paths["nexus"]
    shutil.copytree(REPO / "core", nexus / "tools" / "eos" / "core",
                    ignore=shutil.ignore_patterns("__pycache__"))
    env = _git_env(tmp_path)
    _git(nexus, env, "init", "-q", "-b", "master")
    _git(nexus, env, "add", "-A")
    _git(nexus, env, "commit", "-q", "-m", "base")
    _git(nexus, env, "checkout", "-q", "-b", "incoming")
    _write(paths["knowledge"] / SERVICE / "20260910-pulled.md",
           "---\nkind: finding\ntitle: Pulled from a teammate\ncreated: 2026-09-10\n---\n\nArrived by merge.\n")
    _git(nexus, env, "add", "-A")
    _git(nexus, env, "commit", "-q", "-m", "docs(knowledge): a teammate's note")
    _git(nexus, env, "checkout", "-q", "master")
    return paths, env


@needs_git
@pytest.mark.parametrize("arrival", ["branch checkout", "conflicted merge resolved by commit"])
def test_query_answers_from_what_git_put_in_the_tree_without_a_post_merge_rebuild(tmp_path, arrival):
    """post-merge runs only for a merge that finished cleanly. A checkout and a
    merge completed by `git commit` after a conflict both move the sources
    without it."""
    paths, env = _hook_workspace(tmp_path)
    nexus, service = paths["nexus"], paths["service"]
    no_hooks = tmp_path / "no-hooks"
    no_hooks.mkdir()
    _git(nexus, env, "config", "core.hooksPath", str(no_hooks))
    notes_dir = paths["knowledge"] / SERVICE
    rest = notes_dir / "20260902-rest-endpoints.md"

    if arrival == "branch checkout":
        _git(nexus, env, "checkout", "-q", "incoming")
        index.build(service)
        _git(nexus, env, "checkout", "-q", "master")
        sql = "SELECT COUNT(*) AS pulled FROM note WHERE file = '20260910-pulled.md'"
        expected = ["pulled", "0"]
    else:
        _git(nexus, env, "checkout", "-q", "incoming")
        _reword_generated_note(paths, "theirs")
        _write(notes_dir / "20260910-with-conflict.md",
               "---\nkind: finding\ntitle: Clean note from the same merge\ncreated: 2026-09-10\n---\n\nNo conflict.\n")
        _git(nexus, env, "add", "-A")
        _git(nexus, env, "commit", "-q", "-m", "theirs")
        _git(nexus, env, "checkout", "-q", "master")
        _reword_generated_note(paths, "ours")
        _git(nexus, env, "commit", "-q", "-am", "ours")
        index.build(service)
        merged = subprocess.run(["git", "merge", "--no-edit", "incoming"], cwd=nexus, env=env,
                                capture_output=True, text=True)
        assert merged.returncode != 0, merged.stdout
        _git(nexus, env, "checkout", "--theirs", "--", rest.relative_to(nexus).as_posix())
        _git(nexus, env, "add", "-A")
        _git(nexus, env, "commit", "-q", "--no-edit")
        sql = ("SELECT file, body FROM note WHERE file IN ('20260902-rest-endpoints.md', '20260910-with-conflict.md') "
               "ORDER BY file")
        expected = ["file\tbody", "20260902-rest-endpoints.md\ttheirs", "20260910-with-conflict.md\tNo conflict."]

    done = _run(["query", str(service), sql])

    assert done.returncode == 0, done.stderr
    assert done.stdout.splitlines() == expected, done.stderr
