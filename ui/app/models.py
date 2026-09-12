"""Pydantic DTOs exposed by the eos-ui HTTP API."""
import json
from typing import Any, List, Optional

from pydantic import BaseModel, Field


class ScanRootIn(BaseModel):
    paths: List[str] = Field(..., description="Absolute or relative roots to scan")


class ScanRootOut(BaseModel):
    id: int
    path: str
    added_at: Optional[str] = None
    last_scan_at: Optional[str] = None


class InstanceOut(BaseModel):
    id: str
    path: str
    name: Optional[str] = None
    engine_version: str
    tech_stack: List[str] = Field(default_factory=list)
    status: str
    created_at: Optional[str] = None
    last_scanned_at: Optional[str] = None
    last_updated_at: Optional[str] = None
    metadata: Optional[Any] = None


class ReconcileReportOut(BaseModel):
    added: List[str] = Field(default_factory=list)
    updated: List[str] = Field(default_factory=list)
    missing_marked: int = 0
    unmanaged_marked: int = 0
    roots_scanned: List[str] = Field(default_factory=list)


class GraphifyArtifactOut(BaseModel):
    provider: str = "graphify"
    status: str = "missing"
    project_id: Optional[str] = None
    project_key: Optional[str] = None
    generated_at: Optional[str] = None
    source_revision: Optional[str] = None
    node_count: int = 0
    edge_count: int = 0
    community_count: int = 0
    has_html: bool = False
    has_report: bool = False


class GraphifySummaryOut(GraphifyArtifactOut):
    communities: List[dict] = Field(default_factory=list)


def parse_json_field(value: Any) -> Any:
    """SQLite stores JSON columns as TEXT; deserialize on the way out."""
    if value is None:
        return None
    if isinstance(value, (list, dict)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, ValueError):
            return value
    return value


def row_to_instance(row: dict) -> InstanceOut:
    return InstanceOut(
        id=row["id"],
        path=row["path"],
        name=row.get("name"),
        engine_version=row.get("engine_version") or "unknown",
        tech_stack=parse_json_field(row.get("tech_stack")) or [],
        status=row.get("status") or "active",
        created_at=row.get("created_at"),
        last_scanned_at=row.get("last_scanned_at"),
        last_updated_at=row.get("last_updated_at"),
        metadata=parse_json_field(row.get("metadata")),
    )


def row_to_scan_root(row: dict) -> ScanRootOut:
    return ScanRootOut(
        id=row["id"],
        path=row["path"],
        added_at=row.get("added_at"),
        last_scan_at=row.get("last_scan_at"),
    )


class WorkspaceProjectOut(BaseModel):
    project_key: str
    project_id: Optional[str] = None
    project_path: Optional[str] = None
    status: str = "unknown"
    node_count: int = 0
    edge_count: int = 0
    community_count: int = 0
    generated_at: Optional[str] = None
    source_revision: Optional[str] = None
    graphify_version: Optional[str] = None
    error: Optional[str] = None
    has_graph: bool = False
    has_html: bool = False
    has_report: bool = False
    artifact_dir: str
    artifact_bytes: int = 0
    is_parent: bool = False


class WorkspaceSummaryOut(BaseModel):
    project_count: int = 0
    returned_count: int = 0
    ready_count: int = 0
    problem_count: int = 0
    total_nodes: int = 0
    total_edges: int = 0
    total_communities: int = 0
    total_bytes: int = 0


class WorkspaceOverviewOut(BaseModel):
    projects: List[WorkspaceProjectOut] = Field(default_factory=list)
    summary: WorkspaceSummaryOut = Field(default_factory=WorkspaceSummaryOut)
    truncated: bool = False
    include_parent: bool = False


class WorkspaceNodeOut(BaseModel):
    project_key: str
    project_id: Optional[str] = None
    project_path: Optional[str] = None
    status: str = "unknown"
    node_count: int = 0
    edge_count: int = 0
    community_count: int = 0
    generated_at: Optional[str] = None
    is_parent: bool = False


class WorkspaceEdgeExampleOut(BaseModel):
    node_id: str
    label: str
    source_file: Optional[str] = None
    matched: str
    kind: str


class WorkspaceEdgeOut(BaseModel):
    source: str
    target: str
    weight: int = 0
    examples: List[WorkspaceEdgeExampleOut] = Field(default_factory=list)


class WorkspaceSkippedOut(BaseModel):
    project_key: str
    status: str = "unknown"
    reason: str
    detail: Optional[str] = None


class WorkspaceRelationsOut(BaseModel):
    nodes: List[WorkspaceNodeOut] = Field(default_factory=list)
    edges: List[WorkspaceEdgeOut] = Field(default_factory=list)
    edge_count: int = 0
    skipped: List[WorkspaceSkippedOut] = Field(default_factory=list)
    truncated: bool = False
    include_parent: bool = False


class WorkspacePathOut(BaseModel):
    source: str
    target: str
    found: bool = False
    path: List[str] = Field(default_factory=list)
    hops: List[WorkspaceEdgeOut] = Field(default_factory=list)
    reason: Optional[str] = None
    truncated: bool = False
    include_parent: bool = False


class WorkspaceDependencyOut(BaseModel):
    dependency: str
    project_count: int = 0
    reference_count: int = 0
    projects: List[str] = Field(default_factory=list)
    examples: List[str] = Field(default_factory=list)


class WorkspaceDependenciesOut(BaseModel):
    dependencies: List[WorkspaceDependencyOut] = Field(default_factory=list)
    dependency_count: int = 0
    skipped: List[WorkspaceSkippedOut] = Field(default_factory=list)
    min_projects: int = 2
    truncated: bool = False
    include_parent: bool = False


class GraphifyCommunityMemberOut(BaseModel):
    id: str
    label: str
    source_file: Optional[str] = None
    degree: int = 0


class GraphifyCommunityOut(BaseModel):
    id: str
    name: str
    node_count: int = 0
    edge_count: int = 0
    members: List[GraphifyCommunityMemberOut] = Field(default_factory=list)


class GraphifyCommunitiesOut(BaseModel):
    project_key: str
    status: str = "unknown"
    generated_at: Optional[str] = None
    node_count: int = 0
    edge_count: int = 0
    community_count: int = 0
    communities: List[GraphifyCommunityOut] = Field(default_factory=list)
    truncated: bool = False
    include_parent: bool = False


class GraphifyQueueEntryOut(BaseModel):
    project_path: str
    project_key: str
    state: str = "idle"
    attempts: int = 0
    last_error: Optional[str] = None
    last_started_at: Optional[str] = None
    last_finished_at: Optional[str] = None
    queued_at: Optional[str] = None


class GraphifyQueueOut(BaseModel):
    enabled: bool = False
    entries: List[GraphifyQueueEntryOut] = Field(default_factory=list)
