"""eos-ui FastAPI server.

Local-only HTTP API for managing EOS scan-roots, reconciling ``.eos``
instances into SQLite (ID-first, ADR-003), and reading per-instance
knowledge artifacts (``data/brain/graph.json``).

Run with::

    uvicorn ui.server:app --host 127.0.0.1 --port 8000
"""
import json
import os
from pathlib import Path
from typing import List, Optional

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from ui.app import db, graphify, graphify_queue, models, reconcile, workspace

ALLOWED_HOSTS_ENV = "EOS_UI_ALLOWED_HOSTS"
DB_PATH_ENV = "EOS_UI_DB"

# The generated graph.html inlines repository-derived text into a <script> block.
# Serving it in an opaque origin with no network access keeps that content from
# reaching this API, which creates directories and runs scans without auth.
_GRAPHIFY_HTML_HEADERS = {
    "Content-Security-Policy": (
        "sandbox allow-scripts; default-src 'none'; "
        "script-src 'unsafe-inline' 'unsafe-eval' https:; "
        "style-src 'unsafe-inline' https:; font-src https: data:; "
        "img-src data: blob:; connect-src 'none'; form-action 'none'; "
        "frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}

app = FastAPI(title="eos-ui", version="0.1.0")

# This API is unauthenticated and local-only, so a rebound Host header must not
# reach it. Widen the list explicitly when serving it from another name.
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=[
        host.strip()
        for host in os.environ.get(ALLOWED_HOSTS_ENV, "127.0.0.1,localhost").split(",")
        if host.strip()
    ],
)

# Serve the built frontend (ui/static/) when present. In dev, Vite serves the
# frontend on :5173 and proxies /api here; in prod, this mount is the UI.
_STATIC_DIR = Path(__file__).resolve().parent / "static"
if _STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(_STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache", "Expires": "0"})

else:

    @app.get("/", include_in_schema=False)
    def index_pending() -> dict:
        return {
            "name": "eos-ui",
            "status": "frontend-not-built",
            "hint": "Run: cd ui/frontend && npm ci && npm run build",
        }


def get_conn():
    """Per-request SQLite connection (FastAPI yield dependency)."""
    db_path = os.environ.get(DB_PATH_ENV)
    conn = db.connect(Path(db_path) if db_path else None)
    try:
        yield conn
    finally:
        conn.close()


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/api/roots", response_model=List[models.ScanRootOut])
def list_roots(conn=Depends(get_conn)) -> List[models.ScanRootOut]:
    return [models.row_to_scan_root(r) for r in db.list_scan_roots(conn)]


@app.post("/api/roots", response_model=List[models.ScanRootOut])
def add_roots(payload: models.ScanRootIn, conn=Depends(get_conn)) -> List[models.ScanRootOut]:
    for p in payload.paths:
        db.upsert_scan_root(conn, str(Path(p).expanduser().resolve()))
    return [models.row_to_scan_root(r) for r in db.list_scan_roots(conn)]


@app.delete("/api/roots/{root_id}", response_model=List[models.ScanRootOut])
def delete_root(root_id: int, conn=Depends(get_conn)) -> List[models.ScanRootOut]:
    match = next((r for r in db.list_scan_roots(conn) if r["id"] == root_id), None)
    if not match:
        raise HTTPException(status_code=404, detail="scan root not found")
    db.delete_scan_root(conn, match["path"])
    return [models.row_to_scan_root(r) for r in db.list_scan_roots(conn)]


@app.post("/api/reconcile", response_model=models.ReconcileReportOut)
def trigger_reconcile(conn=Depends(get_conn)) -> models.ReconcileReportOut:
    report = reconcile.reconcile(conn)
    return models.ReconcileReportOut(**report.to_dict())


@app.post("/api/instances/init_scan", response_model=models.ReconcileReportOut)
def init_scan(payload: models.ScanRootIn, conn=Depends(get_conn)) -> models.ReconcileReportOut:
    import subprocess
    import sys

    repo_root = Path(__file__).resolve().parent.parent
    eos_script = repo_root / "core" / "eos.py"

    for p in payload.paths:
        target = Path(p).expanduser().resolve()
        if not target.is_dir() and not target.parent.is_dir():
            raise HTTPException(
                status_code=400,
                detail=f"refusing to create a directory tree at {target}",
            )
        target.mkdir(parents=True, exist_ok=True)
        subprocess.run([sys.executable, str(eos_script), "init", str(target)], check=False)
        subprocess.run([sys.executable, str(eos_script), "scan", str(target)], check=False)
        db.upsert_scan_root(conn, str(target))

    report = reconcile.reconcile(conn)
    return models.ReconcileReportOut(**report.to_dict())


@app.get("/api/instances", response_model=List[models.InstanceOut])
def list_instances(conn=Depends(get_conn)) -> List[models.InstanceOut]:
    return [models.row_to_instance(r) for r in db.list_instances(conn)]


@app.get("/api/instances/{instance_id}", response_model=models.InstanceOut)
def get_instance(instance_id: str, conn=Depends(get_conn)) -> models.InstanceOut:
    row = db.get_instance(conn, instance_id)
    if not row:
        raise HTTPException(status_code=404, detail="instance not found")
    return models.row_to_instance(row)


@app.delete("/api/instances/{instance_id}")
def delete_instance(instance_id: str, conn=Depends(get_conn)) -> dict:
    n = db.delete_instance(conn, instance_id)
    if not n:
        raise HTTPException(status_code=404, detail="instance not found")
    return {"deleted": instance_id}


@app.post("/api/instances/{instance_id}/rescan")
def rescan_instance(instance_id: str, conn=Depends(get_conn)) -> dict:
    import shutil
    import subprocess
    import sys

    row = db.get_instance(conn, instance_id)
    if not row:
        raise HTTPException(status_code=404, detail="instance not found")

    target = Path(row["path"]).expanduser().resolve()
    eos_dir = target / ".eos"
    for sub in ["data", ".cache"]:
        sub_dir = eos_dir / sub
        if sub_dir.exists():
            shutil.rmtree(sub_dir)

    repo_root = Path(__file__).resolve().parent.parent
    eos_script = repo_root / "core" / "eos.py"
    subprocess.run([sys.executable, str(eos_script), "scan", str(target)], check=False)

    report = reconcile.reconcile(conn)
    return {"status": "rescanned", "reconcile": report.to_dict()}


@app.get("/api/instances/{instance_id}/graph")
def get_instance_graph(instance_id: str, conn=Depends(get_conn)) -> dict:
    """Return the instance's machine-readable graph.json for the drill-down view."""
    row = db.get_instance(conn, instance_id)
    if not row:
        raise HTTPException(status_code=404, detail="instance not found")
    graph_path = Path(row["path"]) / ".eos" / "data" / "brain" / "graph.json"
    if not graph_path.is_file():
        raise HTTPException(status_code=404, detail="graph.json not found; run 'eos scan'")
    return json.loads(graph_path.read_text(encoding="utf-8"))


@app.get("/api/instances/{instance_id}/graphify", response_model=models.GraphifyArtifactOut)
def get_graphify_artifact(instance_id: str, conn=Depends(get_conn)) -> models.GraphifyArtifactOut:
    row = db.get_instance(conn, instance_id)
    if not row:
        raise HTTPException(status_code=404, detail="instance not found")
    artifact = graphify.find_artifact(instance_id, row["path"])
    if artifact is None:
        return models.GraphifyArtifactOut(project_id=instance_id)
    return models.GraphifyArtifactOut(
        status=artifact.get("status") or "unknown",
        project_id=artifact.get("project_id", instance_id),
        project_key=artifact.get("project_key"),
        generated_at=artifact.get("generated_at"),
        source_revision=artifact.get("source_revision"),
        node_count=artifact.get("node_count", 0),
        edge_count=artifact.get("edge_count", 0),
        community_count=artifact.get("community_count", 0),
        has_html=bool(artifact.get("html_path")),
        has_report=bool(artifact.get("report_path")),
    )


@app.get("/api/instances/{instance_id}/graphify/summary", response_model=models.GraphifySummaryOut)
def get_graphify_summary(instance_id: str, conn=Depends(get_conn)) -> models.GraphifySummaryOut:
    row = db.get_instance(conn, instance_id)
    if not row:
        raise HTTPException(status_code=404, detail="instance not found")
    artifact = graphify.find_artifact(instance_id, row["path"])
    if artifact is None:
        return models.GraphifySummaryOut(project_id=instance_id)
    try:
        summary = graphify.summarize(graphify.load_graph(artifact), artifact)
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return models.GraphifySummaryOut(**summary)


@app.get("/api/instances/{instance_id}/graphify/graph")
def get_graphify_graph(
    instance_id: str,
    conn=Depends(get_conn),
    node: Optional[str] = Query(default=None, max_length=200),
    depth: int = Query(default=2, ge=0, le=4),
    limit: int = Query(default=250, ge=1, le=1000),
) -> dict:
    row = db.get_instance(conn, instance_id)
    if not row:
        raise HTTPException(status_code=404, detail="instance not found")
    artifact = graphify.find_artifact(instance_id, row["path"])
    if artifact is None:
        raise HTTPException(status_code=404, detail="Graphify artifact not found")
    try:
        return graphify.normalize_subgraph(graphify.load_graph(artifact), node, depth, limit)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/instances/{instance_id}/graphify/report", response_class=PlainTextResponse)
def get_graphify_report(instance_id: str, conn=Depends(get_conn)) -> PlainTextResponse:
    row = db.get_instance(conn, instance_id)
    if not row:
        raise HTTPException(status_code=404, detail="instance not found")
    artifact = graphify.find_artifact(instance_id, row["path"])
    path = graphify.artifact_file(artifact, "report_path") if artifact else None
    if path is None:
        raise HTTPException(status_code=404, detail="Graphify report not found")
    try:
        return PlainTextResponse(path.read_text(encoding="utf-8"), media_type="text/markdown")
    except OSError as exc:
        raise HTTPException(status_code=404, detail="Graphify report not found") from exc


@app.get("/api/instances/{instance_id}/graphify/html")
def get_graphify_html(instance_id: str, conn=Depends(get_conn)) -> FileResponse:
    row = db.get_instance(conn, instance_id)
    if not row:
        raise HTTPException(status_code=404, detail="instance not found")
    artifact = graphify.find_artifact(instance_id, row["path"])
    path = graphify.artifact_file(artifact, "html_path") if artifact else None
    if path is None:
        raise HTTPException(status_code=404, detail="Graphify HTML not found")
    return FileResponse(path, media_type="text/html", headers=_GRAPHIFY_HTML_HEADERS)


# --------------------------------------------------------------------------- #
# Graphify refresh queue (owned by the watcher process, optional)
# --------------------------------------------------------------------------- #

_GRAPHIFY_QUEUE = None


def set_graphify_queue(queue) -> None:
    """Register the process-wide GraphifyQueue; pass None to clear it."""
    global _GRAPHIFY_QUEUE
    _GRAPHIFY_QUEUE = queue


def get_graphify_queue():
    """The registered GraphifyQueue, or None when no watcher is running."""
    return _GRAPHIFY_QUEUE


def _instance_paths(conn) -> List[str]:
    """Project paths of every known instance, to widen Graphify artifact discovery."""
    return [row["path"] for row in db.list_instances(conn)]


@app.get("/api/workspace/overview", response_model=models.WorkspaceOverviewOut)
def get_workspace_overview(
    conn=Depends(get_conn),
    include_parent: bool = Query(default=False),
    limit: int = Query(default=workspace.MAX_PROJECTS, ge=1, le=workspace.MAX_PROJECTS),
) -> models.WorkspaceOverviewOut:
    overview = workspace.workspace_overview(_instance_paths(conn), include_parent, limit)
    return models.WorkspaceOverviewOut(**overview)


@app.get("/api/workspace/relations", response_model=models.WorkspaceRelationsOut)
def get_workspace_relations(
    conn=Depends(get_conn),
    include_parent: bool = Query(default=False),
    limit: int = Query(default=workspace.MAX_RELATION_EDGES, ge=1, le=workspace.MAX_RELATION_EDGES),
) -> models.WorkspaceRelationsOut:
    relations = workspace.service_relations(_instance_paths(conn), include_parent, limit)
    return models.WorkspaceRelationsOut(**relations)


@app.get("/api/workspace/path", response_model=models.WorkspacePathOut)
def get_workspace_path(
    conn=Depends(get_conn),
    source_key: str = Query(alias="from", max_length=200),
    target_key: str = Query(alias="to", max_length=200),
    include_parent: bool = Query(default=False),
    max_hops: int = Query(default=workspace.MAX_PATH_HOPS, ge=1, le=workspace.MAX_PATH_HOPS),
) -> models.WorkspacePathOut:
    result = workspace.shortest_path(
        source_key, target_key, _instance_paths(conn), include_parent, max_hops
    )
    if result["reason"] in ("unknown_source", "unknown_target"):
        raise HTTPException(status_code=404, detail=result["reason"])
    return models.WorkspacePathOut(**result)


@app.get("/api/workspace/dependencies", response_model=models.WorkspaceDependenciesOut)
def get_workspace_dependencies(
    conn=Depends(get_conn),
    include_parent: bool = Query(default=False),
    limit: int = Query(default=workspace.MAX_DEPENDENCIES, ge=1, le=workspace.MAX_DEPENDENCIES),
    min_projects: int = Query(default=2, ge=1, le=workspace.MAX_PROJECTS),
) -> models.WorkspaceDependenciesOut:
    dependencies = workspace.common_dependencies(
        _instance_paths(conn), include_parent, limit, min_projects
    )
    return models.WorkspaceDependenciesOut(**dependencies)


@app.get(
    "/api/instances/{instance_id}/graphify/communities",
    response_model=models.GraphifyCommunitiesOut,
)
def get_graphify_communities(
    instance_id: str,
    conn=Depends(get_conn),
    include_parent: bool = Query(default=False),
    limit: int = Query(default=workspace.MAX_COMMUNITIES, ge=1, le=workspace.MAX_COMMUNITIES),
    members: int = Query(
        default=workspace.MAX_COMMUNITY_MEMBERS, ge=1, le=workspace.MAX_COMMUNITY_MEMBERS
    ),
) -> models.GraphifyCommunitiesOut:
    """Community summaries for one instance instead of its raw node graph."""
    row = db.get_instance(conn, instance_id)
    if not row:
        raise HTTPException(status_code=404, detail="instance not found")
    artifact = graphify.find_artifact(instance_id, row["path"])
    if artifact is None:
        raise HTTPException(status_code=404, detail="Graphify artifact not found")
    project_key = str(
        artifact.get("project_key")
        or artifact.get("project_id")
        or Path(artifact["artifact_dir"]).name
    )
    try:
        communities = workspace.project_communities(
            project_key, [row["path"]], include_parent, limit, members, artifact=artifact
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return models.GraphifyCommunitiesOut(**communities)


def _queue_entries() -> Optional[List[dict]]:
    """Live queue snapshot, or the state a separate watcher process published."""
    queue = get_graphify_queue()
    if queue is not None:
        return queue.status()
    return graphify_queue.read_queue_state()


@app.get("/api/graphify/queue", response_model=models.GraphifyQueueOut)
def get_graphify_queue_status() -> models.GraphifyQueueOut:
    """Queue snapshot for every tracked project; empty when no watcher is running."""
    entries = _queue_entries()
    if entries is None:
        return models.GraphifyQueueOut()
    return models.GraphifyQueueOut(
        enabled=True,
        entries=[models.GraphifyQueueEntryOut(**entry) for entry in entries],
    )


@app.get("/api/graphify/queue/{instance_id}", response_model=models.GraphifyQueueEntryOut)
def get_graphify_queue_entry(
    instance_id: str, conn=Depends(get_conn)
) -> models.GraphifyQueueEntryOut:
    row = db.get_instance(conn, instance_id)
    if not row:
        raise HTTPException(status_code=404, detail="instance not found")
    wanted = graphify_queue.normalize_project_path(row["path"])
    entry = next(
        (
            item
            for item in _queue_entries() or []
            if graphify_queue.normalize_project_path(item["project_path"]) == wanted
        ),
        None,
    )
    if entry is None:
        return models.GraphifyQueueEntryOut(
            project_path=row["path"],
            project_key=graphify_queue.project_key(row["path"]),
        )
    return models.GraphifyQueueEntryOut(**entry)
