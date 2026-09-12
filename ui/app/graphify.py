import json
import os
from collections import deque
from pathlib import Path
from typing import Any, Optional


GRAPHIFY_ROOT_ENV = "EOS_GRAPHIFY_ROOT"


def _configured_roots() -> list[Path]:
    """``os.pathsep``-separated Graphify artifact roots from EOS_GRAPHIFY_ROOT.

    Unset means no root is configured: discovery then finds nothing beyond a
    project's own local artifacts, the feature is simply off, not broken.
    """
    value = os.environ.get(GRAPHIFY_ROOT_ENV)
    if not value:
        return []
    seen: list[Path] = []
    for part in value.split(os.pathsep):
        if not part:
            continue
        resolved = Path(part).expanduser().resolve()
        if resolved not in seen:
            seen.append(resolved)
    return seen


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _manifest_candidates(instance_id: str, project_path: Path) -> list[Path]:
    candidates: list[Path] = []
    direct = project_path.resolve() / "graphify-out" / "manifest.json"
    if direct.is_file():
        candidates.append(direct)
    for root in _configured_roots():
        instance_manifest = root / instance_id / "manifest.json"
        if instance_manifest.is_file():
            candidates.append(instance_manifest)
        try:
            candidates.extend(path for path in root.glob("*/manifest.json") if path.is_file())
        except OSError:
            continue
    return candidates


def _read_manifest(path: Path) -> Optional[dict[str, Any]]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _matches_project(manifest: dict[str, Any], project_path: Path) -> bool:
    value = manifest.get("project_path")
    if not isinstance(value, str):
        return False
    try:
        return Path(value).expanduser().resolve() == project_path.resolve()
    except OSError:
        return False


def find_artifact(instance_id: str, project_path: str) -> Optional[dict[str, Any]]:
    resolved_project = Path(project_path).expanduser().resolve()
    for manifest_path in _manifest_candidates(instance_id, resolved_project):
        manifest = _read_manifest(manifest_path)
        if manifest is None or not _matches_project(manifest, resolved_project):
            continue
        artifact_dir = manifest_path.parent.resolve()
        graph_path = artifact_dir / "graph.json"
        return {
            **manifest,
            "artifact_dir": str(artifact_dir),
            "graph_path": str(graph_path) if graph_path.is_file() else None,
            "report_path": str(artifact_dir / "GRAPH_REPORT.md")
            if (artifact_dir / "GRAPH_REPORT.md").is_file()
            else None,
            "html_path": str(artifact_dir / "graph.html")
            if (artifact_dir / "graph.html").is_file()
            else None,
        }

    direct_dir = resolved_project / "graphify-out"
    direct_graph = direct_dir / "graph.json"
    if direct_graph.is_file():
        manifest = _read_manifest(direct_dir / "manifest.json") or {}
        status = manifest.get("status")
        if not isinstance(status, str):
            # Graphify's own file-hash manifest states no status; then the graph
            # file next to it is the only claim there is.
            status = "ready"
        return {
            "project_id": manifest.get("project_id") or instance_id,
            "project_key": manifest.get("project_key"),
            "project_path": str(resolved_project),
            "status": status,
            "error": manifest.get("error"),
            "generated_at": manifest.get("generated_at"),
            "source_revision": manifest.get("source_revision"),
            "graph_path": str(direct_graph),
            "report_path": str(direct_dir / "GRAPH_REPORT.md")
            if (direct_dir / "GRAPH_REPORT.md").is_file()
            else None,
            "html_path": str(direct_dir / "graph.html")
            if (direct_dir / "graph.html").is_file()
            else None,
            "artifact_dir": str(direct_dir),
        }
    return None


def is_ready(artifact: dict[str, Any]) -> bool:
    """Only a ``ready`` artifact may be served.

    An artifact that states no status at all makes no readiness claim, so it is
    withheld exactly like a failed or stale one.
    """
    return artifact.get("status") == "ready"


def artifact_file(artifact: dict[str, Any], key: str) -> Optional[Path]:
    if not is_ready(artifact):
        return None
    artifact_dir = Path(artifact["artifact_dir"]).resolve()
    value = artifact.get(key)
    if not isinstance(value, str):
        return None
    path = Path(value)
    if path.is_symlink() or not _is_within(path, artifact_dir) or not path.is_file():
        return None
    return path.resolve()


def _raw_edges(graph: dict[str, Any]) -> list[dict[str, Any]]:
    value = graph.get("links", graph.get("edges", []))
    if not isinstance(value, list):
        raise ValueError("Invalid Graphify graph edges")
    return [edge for edge in value if isinstance(edge, dict)]


def load_graph(artifact: dict[str, Any]) -> dict[str, Any]:
    if not is_ready(artifact):
        raise FileNotFoundError("Graphify artifact is not ready")
    path = artifact_file(artifact, "graph_path")
    if path is None:
        raise FileNotFoundError("Graphify graph not found")
    value = json.loads(path.read_text(encoding="utf-8"))
    nodes = value.get("nodes") if isinstance(value, dict) else None
    if not isinstance(nodes, list):
        raise ValueError("Invalid Graphify graph nodes")
    node_ids = [_node_id(node) for node in nodes if isinstance(node, dict)]
    if len(node_ids) != len(nodes) or any(not node_id for node_id in node_ids):
        raise ValueError("Invalid Graphify node IDs")
    if len(set(node_ids)) != len(node_ids):
        raise ValueError("Duplicate Graphify node IDs")
    _raw_edges(value)
    return value


def _node_id(node: dict[str, Any]) -> str:
    return str(node.get("id", ""))


def _node_label(node: dict[str, Any]) -> str:
    return str(node.get("label") or node.get("id") or "")


def _node_matches(node: dict[str, Any], query: str) -> bool:
    needle = query.casefold()
    return needle in _node_id(node).casefold() or needle in _node_label(node).casefold()


def _normalize_node(node: dict[str, Any]) -> dict[str, Any]:
    metadata = node.get("metadata") if isinstance(node.get("metadata"), dict) else {}
    node_type = str(node.get("file_type") or metadata.get("kind") or "component")
    return {
        "id": _node_id(node),
        "type": node_type,
        "label": _node_label(node),
        "path": node.get("source_file"),
        "language": metadata.get("language"),
        "tags": [],
        "metadata": {
            **metadata,
            "community": node.get("community"),
            "community_name": node.get("community_name"),
            "confidence": node.get("confidence"),
        },
        "doc": node.get("doc"),
    }


def _normalize_edge(edge: dict[str, Any], index: int) -> dict[str, Any]:
    metadata = {
        "confidence": edge.get("confidence"),
        "confidence_score": edge.get("confidence_score"),
        "source_file": edge.get("source_file"),
        "source_location": edge.get("source_location"),
    }
    return {
        "source": str(edge.get("source", "")),
        "target": str(edge.get("target", "")),
        "kind": str(edge.get("relation") or "related"),
        "weight": edge.get("weight", 1),
        "metadata": metadata,
        "id": f"graphify-edge-{index}",
    }


def _overview_nodes(nodes: list[dict[str, Any]], edges: list[dict[str, Any]], limit: int) -> set[str]:
    degree: dict[str, int] = {}
    for edge in edges:
        source = str(edge.get("source", ""))
        target = str(edge.get("target", ""))
        degree[source] = degree.get(source, 0) + 1
        degree[target] = degree.get(target, 0) + 1
    ranked = sorted(nodes, key=lambda node: degree.get(_node_id(node), 0), reverse=True)
    return {_node_id(node) for node in ranked[:limit]}


def normalize_subgraph(
    graph: dict[str, Any], query: Optional[str], depth: int, limit: int
) -> dict[str, Any]:
    raw_nodes = [node for node in graph.get("nodes", []) if isinstance(node, dict)]
    raw_edges = _raw_edges(graph)
    limit = max(1, min(limit, 1000))
    depth = max(0, min(depth, 4))
    node_map = {_node_id(node): node for node in raw_nodes}

    if query:
        matches = [node for node in raw_nodes if _node_matches(node, query)]
        if not matches:
            raise LookupError("Graphify node not found")
        selected = {_node_id(matches[0])}
        frontier = deque([(matches[0], 0)])
        adjacency: dict[str, set[str]] = {}
        for edge in raw_edges:
            source = str(edge.get("source", ""))
            target = str(edge.get("target", ""))
            adjacency.setdefault(source, set()).add(target)
            adjacency.setdefault(target, set()).add(source)
        while frontier and len(selected) < limit:
            node, current_depth = frontier.popleft()
            if current_depth >= depth:
                continue
            for neighbor_id in adjacency.get(_node_id(node), set()):
                if neighbor_id in node_map and neighbor_id not in selected:
                    selected.add(neighbor_id)
                    frontier.append((node_map[neighbor_id], current_depth + 1))
                    if len(selected) >= limit:
                        break
    else:
        selected = _overview_nodes(raw_nodes, raw_edges, limit)

    normalized_nodes = [_normalize_node(node_map[node_id]) for node_id in selected if node_id in node_map]
    normalized_edges = [
        _normalize_edge(edge, index)
        for index, edge in enumerate(raw_edges)
        if str(edge.get("source", "")) in selected and str(edge.get("target", "")) in selected
    ]
    return {
        "languages": [],
        "tech_stack": [],
        "entry_points": [],
        "nodes": normalized_nodes,
        "edges": normalized_edges,
        "provider": "graphify",
        "truncated": len(selected) < len(raw_nodes),
    }


def summarize(graph: dict[str, Any], artifact: dict[str, Any]) -> dict[str, Any]:
    nodes = [node for node in graph.get("nodes", []) if isinstance(node, dict)]
    edges = _raw_edges(graph)
    community_counts: dict[str, int] = {}
    for node in nodes:
        community = node.get("community_name") or node.get("community")
        if community is not None:
            key = str(community)
            community_counts[key] = community_counts.get(key, 0) + 1
    communities = [
        {"name": name, "node_count": count}
        for name, count in sorted(community_counts.items(), key=lambda item: item[1], reverse=True)[:50]
    ]
    return {
        "provider": "graphify",
        "status": artifact.get("status", "ready"),
        "project_id": artifact.get("project_id"),
        "project_key": artifact.get("project_key"),
        "generated_at": artifact.get("generated_at"),
        "source_revision": artifact.get("source_revision"),
        "node_count": len(nodes),
        "edge_count": len(edges) if isinstance(edges, list) else 0,
        "community_count": len(community_counts),
        "communities": communities,
        "has_html": bool(artifact.get("html_path")),
        "has_report": bool(artifact.get("report_path")),
    }
