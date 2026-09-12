"""Tests for the eos-ui SQLite layer + ID-first reconciliation."""
import json
import shutil
import tempfile
from pathlib import Path

import pytest

from ui.app import db, reconcile


@pytest.fixture
def conn(tmp_path):
    return db.connect(tmp_path / "test.db")


@pytest.fixture
def fixture_project(tmp_path):
    """A sandboxed copy of the sample_python_project fixture."""
    src = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "sample_python_project"
    dest = tmp_path / "proj"
    shutil.copytree(src, dest)
    return dest


def test_schema_applied_on_connect(conn):
    # Tables exist
    names = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    assert "eos_instances" in names
    assert "eos_scan_roots" in names


def test_scan_root_upsert_is_idempotent(conn, fixture_project):
    db.upsert_scan_root(conn, str(fixture_project))
    db.upsert_scan_root(conn, str(fixture_project))
    roots = db.list_scan_roots(conn)
    assert len(roots) == 1
    assert roots[0]["path"] == str(fixture_project)


def test_reconcile_inserts_instance(conn, fixture_project):
    db.upsert_scan_root(conn, str(fixture_project.parent))
    report = reconcile.reconcile(conn)
    assert len(report.added) == 1
    assert report.updated == []

    instances = db.list_instances(conn)
    assert len(instances) == 1
    inst = instances[0]
    assert inst["id"] == (fixture_project / ".eos" / "id.txt").read_text().strip()
    assert inst["path"] == str(fixture_project)
    assert inst["name"] == fixture_project.name
    assert inst["engine_version"] == "0.1.0"
    # tech_stack stored as JSON text
    assert "python" in json.loads(inst["tech_stack"])
    assert inst["status"] == "active"


def test_move_keeps_id_updates_path(conn, fixture_project):
    parent = fixture_project.parent
    db.upsert_scan_root(conn, str(parent))
    reconcile.reconcile(conn)

    before = db.list_instances(conn)
    assert len(before) == 1
    old_id = before[0]["id"]

    renamed = fixture_project.parent / "renamed_proj"
    shutil.move(str(fixture_project), str(renamed))

    if Path(fixture_project).exists():
        # On some platforms the old reference may still resolve; ignore sentinel.
        pass

    report = reconcile.reconcile(conn)
    after = db.list_instances(conn)
    assert len(after) == 1, "move must not create a new row"
    assert after[0]["id"] == old_id, "ID must survive a rename"
    assert str(renamed) == after[0]["path"], "path must be updated"


def test_missing_eos_marked(conn, fixture_project):
    parent = fixture_project.parent
    db.upsert_scan_root(conn, str(parent))
    reconcile.reconcile(conn)

    # Remove the .eos dir (simulate project cleared out).
    shutil.rmtree(fixture_project / ".eos")
    report = reconcile.reconcile(conn)
    assert report.missing_marked == 1

    inst = db.list_instances(conn)[0]
    assert inst["status"] == "missing"
    # Row must NOT be deleted — history survives.
    assert inst["id"]


def test_unmanaged_when_eos_present_but_root_removed(conn, fixture_project):
    parent = fixture_project.parent
    db.upsert_scan_root(conn, str(parent))
    reconcile.reconcile(conn)
    assert db.list_instances(conn)[0]["status"] == "active"

    # Remove the scan-root (not the .eos dir): instance still on disk but no
    # longer under any scanned root -> 'unmanaged', not 'missing'.
    roots = db.list_scan_roots(conn)
    db.delete_scan_root(conn, roots[0]["path"])
    report = reconcile.reconcile(conn)
    assert report.unmanaged_marked == 1
    assert report.missing_marked == 0

    inst = db.list_instances(conn)[0]
    assert inst["status"] == "unmanaged"


def test_delete_instance_keeps_others(conn, fixture_project, tmp_path):
    # Two copies so we can delete one without touching the other.
    second = tmp_path / "second_proj"
    shutil.copytree(fixture_project, second)
    # Give second a distinct id by rewriting id.txt.
    (second / ".eos" / "id.txt").write_text("11111111-2222-3333-4444-555555555555\n")

    db.upsert_scan_root(conn, str(tmp_path))
    reconcile.reconcile(conn)
    assert len(db.list_instances(conn)) == 2

    db.delete_instance(conn, "11111111-2222-3333-4444-555555555555")
    remaining = db.list_instances(conn)
    assert len(remaining) == 1
    assert remaining[0]["id"] != "11111111-2222-3333-4444-555555555555"