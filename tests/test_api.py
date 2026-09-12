"""HTTP API tests for eos-ui (FastAPI).

Covers scan-root CRUD, reconcile (ID-first upsert), instance listing/detail,
graph retrieval, and 404 handling. Uses a temp SQLite DB via dependency
override so no real ~/.eos-ui/eos.db is touched.
"""
import json
import shutil
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ui.app import db, workspace
from ui.app.graphify_queue import GraphifyQueue, queue_state_path
from ui.server import app, get_conn, get_graphify_queue, set_graphify_queue

FIXTURE = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "sample_python_project"


@pytest.fixture
def client(tmp_path):
    """A TestClient wired to a temp DB with one fixture project seeded."""
    db_path = tmp_path / "api.db"
    proj = tmp_path / "proj"
    shutil.copytree(FIXTURE, proj)

    def _override():
        conn = db.connect(db_path)
        try:
            yield conn
        finally:
            conn.close()

    app.dependency_overrides[get_conn] = _override
    # The API only answers to the local names it binds to (TrustedHostMiddleware).
    yield TestClient(app, base_url="http://127.0.0.1"), str(proj.parent)
    app.dependency_overrides.clear()


def test_health(client):
    c, _ = client
    r = c.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_roots_add_list_delete(client):
    c, root_parent = client
    r = c.post("/api/roots", json={"paths": [root_parent]})
    assert r.status_code == 200
    assert any(root["path"] == root_parent for root in r.json())

    r = c.get("/api/roots")
    assert r.status_code == 200
    assert len(r.json()) == 1
    root_id = r.json()[0]["id"]

    r = c.delete(f"/api/roots/{root_id}")
    assert r.status_code == 200
    assert len(r.json()) == 0

    # deleting again returns 404
    assert c.delete(f"/api/roots/{root_id}").status_code == 404


def test_reconcile_and_instances(client):
    c, root_parent = client
    c.post("/api/roots", json={"paths": [root_parent]})

    r = c.post("/api/reconcile")
    assert r.status_code == 200
    report = r.json()
    assert len(report["added"]) == 1
    assert report["missing_marked"] == 0

    r = c.get("/api/instances")
    assert r.status_code == 200
    assert len(r.json()) == 1
    inst_id = r.json()[0]["id"]

    r = c.get(f"/api/instances/{inst_id}")
    assert r.status_code == 200
    assert r.json()["engine_version"] == "0.1.0"
    assert r.json()["status"] == "active"

    r = c.get(f"/api/instances/{inst_id}/graph")
    assert r.status_code == 200
    graph = r.json()
    assert len(graph["nodes"]) >= 2
    assert any(e["kind"] == "import" for e in graph["edges"])


def test_instance_404s(client):
    c, _ = client
    assert c.get("/api/instances/nope").status_code == 404
    assert c.get("/api/instances/nope/graph").status_code == 404
    assert c.delete("/api/instances/nope").status_code == 404


def test_delete_instance(client):
    c, root_parent = client
    c.post("/api/roots", json={"paths": [root_parent]})
    c.post("/api/reconcile")
    inst_id = c.get("/api/instances").json()[0]["id"]
    r = c.delete(f"/api/instances/{inst_id}")
    assert r.status_code == 200
    assert len(c.get("/api/instances").json()) == 0


def test_reconcile_marks_missing(client):
    c, root_parent = client
    c.post("/api/roots", json={"paths": [root_parent]})
    c.post("/api/reconcile")
    inst_id = c.get("/api/instances").json()[0]["id"]

    # Remove the .eos dir so the instance is no longer found on disk.
    shutil.rmtree(Path(c.get(f"/api/instances/{inst_id}").json()["path"]) / ".eos")

    r = c.post("/api/reconcile")
    assert r.status_code == 200
    assert r.json()["missing_marked"] >= 1
    inst = c.get("/api/instances").json()[0]
    assert inst["status"] == "missing"


def test_graphify_artifact_and_subgraph(client, monkeypatch):
    c, root_parent = client
    c.post("/api/roots", json={"paths": [root_parent]})
    c.post("/api/reconcile")
    instance = c.get("/api/instances").json()[0]
    instance_id = instance["id"]
    project_path = Path(instance["path"]).resolve()

    artifact_dir = Path(root_parent) / "graphify-projects" / "fixture"
    artifact_dir.mkdir(parents=True)
    graph = {
        "nodes": [
            {
                "id": "main",
                "label": "main.py",
                "file_type": "code",
                "source_file": "main.py",
                "metadata": {"language": "python", "kind": "file"},
                "community": 1,
                "community_name": "entry",
            },
            {
                "id": "utils",
                "label": "utils.py",
                "file_type": "code",
                "source_file": "utils.py",
                "metadata": {"language": "python", "kind": "file"},
                "community": 1,
                "community_name": "entry",
            },
            {
                "id": "isolated",
                "label": "isolated.py",
                "file_type": "code",
                "source_file": "isolated.py",
                "metadata": {"language": "python", "kind": "file"},
                "community": 2,
                "community_name": "other",
            },
        ],
        "edges": [
            {
                "source": "main",
                "target": "utils",
                "relation": "imports",
                "confidence": "EXTRACTED",
                "confidence_score": 1.0,
            }
        ],
    }
    (artifact_dir / "graph.json").write_text(json.dumps(graph), encoding="utf-8")
    (artifact_dir / "GRAPH_REPORT.md").write_text("# Fixture report\n", encoding="utf-8")
    (artifact_dir / "graph.html").write_text("<html></html>\n", encoding="utf-8")
    (artifact_dir / "manifest.json").write_text(
        json.dumps(
            {
                "project_id": instance_id,
                "project_key": "fixture",
                "project_path": str(project_path),
                "graphify_version": "0.9.38",
                "source_revision": "fixture",
                "generated_at": "2026-08-10T00:00:00+00:00",
                "node_count": 3,
                "edge_count": 1,
                "community_count": 2,
                "status": "ready",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("EOS_GRAPHIFY_ROOT", str(artifact_dir.parent))

    artifact_response = c.get(f"/api/instances/{instance_id}/graphify")
    assert artifact_response.status_code == 200
    assert artifact_response.json()["status"] == "ready"
    assert artifact_response.json()["node_count"] == 3

    summary_response = c.get(f"/api/instances/{instance_id}/graphify/summary")
    assert summary_response.status_code == 200
    assert summary_response.json()["community_count"] == 2

    graph_response = c.get(
        f"/api/instances/{instance_id}/graphify/graph",
        params={"node": "main", "depth": 1, "limit": 10},
    )
    assert graph_response.status_code == 200
    graph_response_body = graph_response.json()
    assert graph_response_body["provider"] == "graphify"
    assert {node["id"] for node in graph_response_body["nodes"]} == {"main", "utils"}
    assert graph_response_body["edges"][0]["kind"] == "imports"

    assert c.get(f"/api/instances/{instance_id}/graphify/report").status_code == 200
    assert c.get(f"/api/instances/{instance_id}/graphify/html").status_code == 200


def _seed_graphify_artifact(c, root_parent, monkeypatch, status="ready"):
    """Write a minimal Graphify artifact set for the seeded fixture instance."""
    c.post("/api/roots", json={"paths": [root_parent]})
    c.post("/api/reconcile")
    instance = c.get("/api/instances").json()[0]
    instance_id = instance["id"]
    artifact_dir = Path(root_parent) / "graphify-projects" / "fixture"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / "graph.json").write_text(
        json.dumps({"nodes": [{"id": "main", "label": "main.py"}], "edges": []}),
        encoding="utf-8",
    )
    (artifact_dir / "GRAPH_REPORT.md").write_text("# Fixture report\n", encoding="utf-8")
    (artifact_dir / "graph.html").write_text("<html></html>\n", encoding="utf-8")
    (artifact_dir / "manifest.json").write_text(
        json.dumps(
            {
                "project_id": instance_id,
                "project_key": "fixture",
                "project_path": str(Path(instance["path"]).resolve()),
                "graphify_version": "0.9.38",
                "status": status,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("EOS_GRAPHIFY_ROOT", str(artifact_dir.parent))
    return instance_id, artifact_dir


@pytest.mark.parametrize("status", ["failed", "stale"])
def test_graphify_not_ready_artifact_is_not_served(client, monkeypatch, status):
    """A failed/stale artifact must not be served as if it were current."""
    c, root_parent = client
    instance_id, _ = _seed_graphify_artifact(c, root_parent, monkeypatch, status=status)

    assert c.get(f"/api/instances/{instance_id}/graphify").json()["status"] == status
    assert c.get(f"/api/instances/{instance_id}/graphify/graph").status_code == 422
    assert c.get(f"/api/instances/{instance_id}/graphify/report").status_code == 404
    assert c.get(f"/api/instances/{instance_id}/graphify/html").status_code == 404


def test_graphify_symlinked_files_are_rejected(client, monkeypatch, tmp_path):
    """Report/HTML symlinks pointing outside the artifact dir are not served."""
    c, root_parent = client
    instance_id, artifact_dir = _seed_graphify_artifact(c, root_parent, monkeypatch)

    secret = tmp_path / "secret.md"
    secret.write_text("TOP SECRET\n", encoding="utf-8")
    (artifact_dir / "GRAPH_REPORT.md").unlink()
    (artifact_dir / "GRAPH_REPORT.md").symlink_to(secret)
    secret_html = tmp_path / "secret.html"
    secret_html.write_text("<html>secret</html>\n", encoding="utf-8")
    (artifact_dir / "graph.html").unlink()
    (artifact_dir / "graph.html").symlink_to(secret_html)

    report = c.get(f"/api/instances/{instance_id}/graphify/report")
    assert report.status_code == 404
    assert "SECRET" not in report.text
    assert c.get(f"/api/instances/{instance_id}/graphify/html").status_code == 404


def test_graphify_artifact_missing(client):
    c, root_parent = client
    c.post("/api/roots", json={"paths": [root_parent]})
    c.post("/api/reconcile")
    instance_id = c.get("/api/instances").json()[0]["id"]

    response = c.get(f"/api/instances/{instance_id}/graphify")
    assert response.status_code == 200
    assert response.json()["status"] == "missing"
    assert c.get(f"/api/instances/{instance_id}/graphify/graph").status_code == 404


_SPRING_BOOT = "org.springframework.boot.SpringApplication"


def _write_workspace_artifact(root, key, nodes, status="ready"):
    """Write a standalone Graphify artifact for a sibling project under ``root``."""
    artifact_dir = Path(root) / key
    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / "graph.json").write_text(
        json.dumps({"nodes": nodes, "edges": []}), encoding="utf-8"
    )
    (artifact_dir / "manifest.json").write_text(
        json.dumps(
            {
                "project_id": key,
                "project_key": key,
                "project_path": str(Path(root).parent / key),
                "graphify_version": "0.9.38",
                "status": status,
                "node_count": len(nodes),
                "edge_count": 0,
                "community_count": 1,
            }
        ),
        encoding="utf-8",
    )
    return artifact_dir


def _seed_workspace(c, root_parent, monkeypatch):
    """Fixture artifact plus two projects where svc-beta references svc-alpha."""
    instance_id, artifact_dir = _seed_graphify_artifact(c, root_parent, monkeypatch)
    root = artifact_dir.parent
    _write_workspace_artifact(
        root,
        "svc-alpha",
        [
            {"id": "alpha-main", "label": "AlphaMain", "source_file": "src/alpha/Main.java"},
            {"id": "dep-spring-a", "label": _SPRING_BOOT},
        ],
    )
    _write_workspace_artifact(
        root,
        "svc-beta",
        [
            {
                "id": "beta-client",
                "label": "SvcAlphaClient",
                "source_file": "src/beta/Client.java",
            },
            {"id": "dep-spring-b", "label": _SPRING_BOOT},
        ],
    )
    workspace.reset_cache()
    return instance_id


def test_workspace_overview(client, monkeypatch):
    c, root_parent = client
    _seed_workspace(c, root_parent, monkeypatch)

    response = c.get("/api/workspace/overview")
    assert response.status_code == 200
    body = response.json()
    assert {project["project_key"] for project in body["projects"]} == {
        "fixture",
        "svc-alpha",
        "svc-beta",
    }
    assert body["summary"]["project_count"] == 3
    assert body["summary"]["ready_count"] == 3
    assert body["include_parent"] is False
    assert body["truncated"] is False
    assert all(project["has_graph"] for project in body["projects"])


def test_workspace_overview_limit_is_capped(client, monkeypatch):
    c, root_parent = client
    _seed_workspace(c, root_parent, monkeypatch)

    assert c.get("/api/workspace/overview", params={"limit": 201}).status_code == 422
    assert c.get("/api/workspace/overview", params={"limit": 0}).status_code == 422

    truncated = c.get("/api/workspace/overview", params={"limit": 1})
    assert truncated.status_code == 200
    assert len(truncated.json()["projects"]) == 1
    assert truncated.json()["truncated"] is True
    assert truncated.json()["summary"]["project_count"] == 3


def test_workspace_relations(client, monkeypatch):
    c, root_parent = client
    _seed_workspace(c, root_parent, monkeypatch)

    response = c.get("/api/workspace/relations")
    assert response.status_code == 200
    body = response.json()
    assert {node["project_key"] for node in body["nodes"]} == {
        "fixture",
        "svc-alpha",
        "svc-beta",
    }
    assert body["edge_count"] == 1
    edge = body["edges"][0]
    assert (edge["source"], edge["target"]) == ("svc-beta", "svc-alpha")
    assert edge["weight"] == 1
    assert edge["examples"][0]["matched"] == "svcalpha"
    assert body["skipped"] == []
    assert body["include_parent"] is False

    assert c.get("/api/workspace/relations", params={"limit": 501}).status_code == 422
    assert c.get("/api/workspace/relations", params={"limit": 0}).status_code == 422


def test_workspace_relations_skips_not_ready_projects(client, monkeypatch):
    c, root_parent = client
    _seed_workspace(c, root_parent, monkeypatch)
    _write_workspace_artifact(
        Path(root_parent) / "graphify-projects",
        "svc-broken",
        [{"id": "broken", "label": "Broken"}],
        status="failed",
    )
    workspace.reset_cache()

    body = c.get("/api/workspace/relations").json()
    assert {entry["project_key"] for entry in body["skipped"]} == {"svc-broken"}
    assert body["skipped"][0]["reason"] == "not_ready"
    assert "svc-broken" not in {node["project_key"] for node in body["nodes"]}


def test_workspace_path(client, monkeypatch):
    c, root_parent = client
    _seed_workspace(c, root_parent, monkeypatch)

    response = c.get("/api/workspace/path", params={"from": "svc-beta", "to": "svc-alpha"})
    assert response.status_code == 200
    body = response.json()
    assert body["found"] is True
    assert body["path"] == ["svc-beta", "svc-alpha"]
    assert body["hops"][0]["weight"] == 1
    assert body["reason"] is None

    unreachable = c.get(
        "/api/workspace/path", params={"from": "svc-alpha", "to": "svc-beta"}
    )
    assert unreachable.status_code == 200
    assert unreachable.json()["found"] is False
    assert unreachable.json()["reason"] == "unreachable"


def test_workspace_path_unknown_project_is_404(client, monkeypatch):
    c, root_parent = client
    _seed_workspace(c, root_parent, monkeypatch)

    missing_source = c.get("/api/workspace/path", params={"from": "nope", "to": "svc-alpha"})
    assert missing_source.status_code == 404
    assert missing_source.json()["detail"] == "unknown_source"

    missing_target = c.get("/api/workspace/path", params={"from": "svc-alpha", "to": "nope"})
    assert missing_target.status_code == 404
    assert missing_target.json()["detail"] == "unknown_target"

    assert c.get("/api/workspace/path", params={"from": "a"}).status_code == 422
    assert (
        c.get(
            "/api/workspace/path",
            params={"from": "svc-beta", "to": "svc-alpha", "max_hops": 9},
        ).status_code
        == 422
    )


def test_workspace_dependencies(client, monkeypatch):
    c, root_parent = client
    _seed_workspace(c, root_parent, monkeypatch)

    response = c.get("/api/workspace/dependencies")
    assert response.status_code == 200
    body = response.json()
    assert body["min_projects"] == 2
    shared = {row["dependency"]: row for row in body["dependencies"]}
    assert "org.springframework.boot" in shared
    assert shared["org.springframework.boot"]["project_count"] == 2
    assert shared["org.springframework.boot"]["projects"] == ["svc-alpha", "svc-beta"]
    assert shared["org.springframework.boot"]["examples"] == [_SPRING_BOOT]
    assert body["include_parent"] is False

    assert c.get("/api/workspace/dependencies", params={"limit": 201}).status_code == 422
    assert c.get("/api/workspace/dependencies", params={"min_projects": 0}).status_code == 422
    assert c.get("/api/workspace/dependencies", params={"min_projects": 3}).json()[
        "dependencies"
    ] == []


def test_graphify_communities(client, monkeypatch):
    c, root_parent = client
    instance_id, _ = _seed_graphify_artifact(c, root_parent, monkeypatch)
    workspace.reset_cache()

    response = c.get(f"/api/instances/{instance_id}/graphify/communities")
    assert response.status_code == 200
    body = response.json()
    assert body["project_key"] == "fixture"
    assert body["status"] == "ready"
    assert body["node_count"] == 1
    assert body["community_count"] == 1
    community = body["communities"][0]
    assert community["id"] == "unassigned"
    assert [member["id"] for member in community["members"]] == ["main"]
    assert body["truncated"] is False

    assert (
        c.get(
            f"/api/instances/{instance_id}/graphify/communities", params={"limit": 201}
        ).status_code
        == 422
    )
    assert (
        c.get(
            f"/api/instances/{instance_id}/graphify/communities", params={"members": 11}
        ).status_code
        == 422
    )


def test_graphify_communities_404_and_422(client, monkeypatch):
    c, root_parent = client
    assert c.get("/api/instances/nope/graphify/communities").status_code == 404

    monkeypatch.delenv("EOS_GRAPHIFY_ROOT", raising=False)
    c.post("/api/roots", json={"paths": [root_parent]})
    c.post("/api/reconcile")
    instance_id = c.get("/api/instances").json()[0]["id"]
    assert c.get(f"/api/instances/{instance_id}/graphify/communities").status_code == 404

    _seed_graphify_artifact(c, root_parent, monkeypatch, status="failed")
    workspace.reset_cache()
    assert c.get(f"/api/instances/{instance_id}/graphify/communities").status_code == 422


def test_graphify_queue_without_watcher(client, monkeypatch):
    c, root_parent = client
    instance_id, _ = _seed_graphify_artifact(c, root_parent, monkeypatch)

    response = c.get("/api/graphify/queue")
    assert response.status_code == 200
    assert response.json() == {"enabled": False, "entries": []}

    entry = c.get(f"/api/graphify/queue/{instance_id}")
    assert entry.status_code == 200
    assert entry.json()["state"] == "idle"
    assert entry.json()["attempts"] == 0
    assert entry.json()["last_error"] is None

    assert c.get("/api/graphify/queue/nope").status_code == 404


def test_graphify_queue_with_registered_queue(client, monkeypatch):
    c, root_parent = client
    _seed_graphify_artifact(c, root_parent, monkeypatch)
    instance = c.get("/api/instances").json()[0]

    # A long debounce keeps the entry 'pending' so the snapshot is deterministic.
    queue = GraphifyQueue(refresh=lambda path: None, delay=30.0)
    set_graphify_queue(queue)
    try:
        queue.enqueue(instance["path"])
        body = c.get("/api/graphify/queue").json()
        assert body["enabled"] is True
        assert [entry["state"] for entry in body["entries"]] == ["pending"]
        assert body["entries"][0]["queued_at"] is not None

        entry = c.get(f"/api/graphify/queue/{instance['id']}").json()
        assert entry["state"] == "pending"
        assert entry["project_key"] == Path(instance["path"]).name

        assert c.get("/api/graphify/queue/nope").status_code == 404
    finally:
        queue.stop()
        set_graphify_queue(None)


def test_rebound_host_header_is_rejected(client):
    """An unauthenticated local API must not answer a rebound Host."""
    c, _ = client
    assert c.get("/api/health", headers={"host": "evil.example.com"}).status_code == 400
    assert c.get("/api/health").status_code == 200


def test_init_scan_refuses_to_create_a_directory_tree(client, tmp_path):
    c, _ = client
    target = tmp_path / "attacker" / "controlled" / "deep"
    response = c.post("/api/instances/init_scan", json={"paths": [str(target)]})
    assert response.status_code == 400
    assert not target.exists()


def test_db_connection_is_usable_from_another_thread(tmp_path):
    """FastAPI runs the dependency and the path function on different workers."""
    conn = db.connect(tmp_path / "threads.db")
    outcome: dict = {}

    def read():
        try:
            outcome["rows"] = db.list_instances(conn)
        except Exception as exc:  # pragma: no cover - only reached on the defect
            outcome["error"] = repr(exc)

    thread = threading.Thread(target=read)
    thread.start()
    thread.join()
    conn.close()

    assert outcome.get("error") is None
    assert outcome["rows"] == []


def test_graphify_direct_artifact_reports_its_manifest_status(client, monkeypatch):
    """A project-local graphify-out/ that failed must not be served as ready."""
    c, root_parent = client
    monkeypatch.delenv("EOS_GRAPHIFY_ROOT", raising=False)
    c.post("/api/roots", json={"paths": [root_parent]})
    c.post("/api/reconcile")
    instance = c.get("/api/instances").json()[0]

    out = Path(instance["path"]) / "graphify-out"
    out.mkdir()
    (out / "graph.json").write_text(
        json.dumps({"nodes": [{"id": "stale"}], "edges": []}), encoding="utf-8"
    )
    (out / "GRAPH_REPORT.md").write_text("# STALE FAILED REPORT\n", encoding="utf-8")
    (out / "graph.html").write_text("<html></html>\n", encoding="utf-8")
    # A failed run whose project was renamed afterwards: the path no longer matches.
    (out / "manifest.json").write_text(
        json.dumps({"status": "failed", "error": "boom", "project_path": "/ws/old-name"}),
        encoding="utf-8",
    )

    instance_id = instance["id"]
    assert c.get(f"/api/instances/{instance_id}/graphify").json()["status"] == "failed"
    assert c.get(f"/api/instances/{instance_id}/graphify/graph").status_code == 422
    assert c.get(f"/api/instances/{instance_id}/graphify/summary").status_code == 422
    report = c.get(f"/api/instances/{instance_id}/graphify/report")
    assert report.status_code == 404
    assert "STALE" not in report.text
    assert c.get(f"/api/instances/{instance_id}/graphify/html").status_code == 404


def test_graphify_artifact_without_a_status_is_withheld(client, monkeypatch):
    """A manifest that states no status makes no readiness claim."""
    c, root_parent = client
    instance_id, artifact_dir = _seed_graphify_artifact(c, root_parent, monkeypatch)
    manifest = json.loads((artifact_dir / "manifest.json").read_text(encoding="utf-8"))
    del manifest["status"]
    (artifact_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    workspace.reset_cache()

    assert c.get(f"/api/instances/{instance_id}/graphify").json()["status"] == "unknown"
    assert c.get(f"/api/instances/{instance_id}/graphify/graph").status_code == 422
    assert c.get(f"/api/instances/{instance_id}/graphify/html").status_code == 404
    assert c.get(f"/api/instances/{instance_id}/graphify/communities").status_code == 422


def test_graphify_html_is_served_sandboxed(client, monkeypatch):
    """Generated HTML inlines repo text; it must not run on the API's origin."""
    c, root_parent = client
    instance_id, _ = _seed_graphify_artifact(c, root_parent, monkeypatch)

    response = c.get(f"/api/instances/{instance_id}/graphify/html")
    assert response.status_code == 200
    csp = response.headers["content-security-policy"]
    assert "sandbox" in csp
    assert "connect-src 'none'" in csp
    assert response.headers["x-content-type-options"] == "nosniff"


def test_graphify_communities_serve_the_instance_own_artifact(client, monkeypatch):
    """Two checkouts of a repo share a project key but not a graph."""
    c, root_parent = client
    instance_id, artifact_dir = _seed_graphify_artifact(c, root_parent, monkeypatch)
    root = artifact_dir.parent

    other = _write_workspace_artifact(root, "aaa-id", [{"id": "B_SECRET", "label": "B"}])
    other_manifest = json.loads((other / "manifest.json").read_text(encoding="utf-8"))
    other_manifest["project_key"] = "fixture"
    (other / "manifest.json").write_text(json.dumps(other_manifest), encoding="utf-8")
    workspace.reset_cache()

    body = c.get(f"/api/instances/{instance_id}/graphify/communities").json()
    members = [
        member["id"]
        for community in body["communities"]
        for member in community["members"]
    ]
    assert members == ["main"], "another project's graph was served for this instance"


def test_graphify_queue_reports_state_published_by_the_watcher(client, monkeypatch):
    """The watcher runs in another process; failures reach the API through a file."""
    c, root_parent = client
    instance_id, _ = _seed_graphify_artifact(c, root_parent, monkeypatch)
    instance = c.get("/api/instances").json()[0]

    def refresh(project_path: str) -> None:
        raise RuntimeError("graphify blew up")

    queue = GraphifyQueue(
        refresh=refresh,
        delay=0.05,
        max_attempts=1,
        retry_delay=0.01,
        state_path=queue_state_path(),
    )
    try:
        queue.enqueue(instance["path"])
        deadline = time.time() + 2.0
        while time.time() < deadline:
            if queue.status_for(instance["path"])["state"] == "failed":
                break
            time.sleep(0.01)

        # Nothing is registered in-process: the API reads the published state.
        assert get_graphify_queue() is None
        body = c.get("/api/graphify/queue").json()
        assert body["enabled"] is True
        assert [entry["state"] for entry in body["entries"]] == ["failed"]

        entry = c.get(f"/api/graphify/queue/{instance_id}").json()
        assert entry["state"] == "failed"
        assert entry["last_error"] == "graphify blew up"
    finally:
        queue.stop()

    assert c.get("/api/graphify/queue").json() == {"enabled": False, "entries": []}
