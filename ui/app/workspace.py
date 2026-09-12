"""Workspace-level and cross-project projections over Graphify artifacts.

The per-project Graphify artifacts (``<root>/<id>/{manifest.json,graph.json,...}``)
describe one repository each. This module projects the whole set:

* :func:`workspace_overview` - one row per artifact, served from manifests alone.
* :func:`service_relations` - a derived project-to-project graph. Raw node graphs
  are never merged; an edge only means "project A's graph mentions project B's
  identity", and every edge carries example references for explainability.
* :func:`shortest_path` - unweighted BFS over that derived projection.
* :func:`common_dependencies` - external packages shared by several projects.
* :func:`project_communities` - community summary nodes for a single project, so a
  19k-node graph never has to reach the browser.

``graph.json`` files reach 8 MB, so they are loaded lazily and the derived
per-project index is cached in-process. Only ``ready`` artifacts feed any
projection; failed/stale ones stay visible in the overview and nowhere else.
"""
import hashlib
import json
import os
import re
from collections import Counter, deque
from pathlib import Path
from typing import Any, Optional, Sequence

from ui.app import graphify


PARENT_SEGMENT_ENV = "EOS_PARENT_SEGMENT"

MAX_PROJECTS = 200
MAX_RELATION_EDGES = 500
MAX_EDGE_EXAMPLES = 5
MAX_DEPENDENCIES = 200
MAX_DEPENDENCY_EXAMPLES = 5
MAX_COMMUNITIES = 200
MAX_COMMUNITY_MEMBERS = 10
MAX_PATH_HOPS = 8

# Compact aliases are matched as substrings, so very short ones are dropped to
# avoid matching unrelated words. Separator-delimited aliases stay unrestricted
# because they are matched on token boundaries.
MIN_COMPACT_ALIAS = 6

# Package labels starting with one of these are grouped three segments deep
# (``org.springframework.boot``); anything else is grouped by its first segment
# (``lombok``).
_GROUP_PREFIXES = frozenset({"com", "org", "io", "net", "edu", "gov", "co", "dev", "me"})

_SEGMENT_RE = re.compile(r"[A-Za-z0-9_$]+")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")

# Both caches hold at most one entry per artifact dir: a regenerated artifact
# (new ``generated_at``) replaces the superseded entry instead of adding one.
_INDEX_CACHE: dict[str, tuple[str, str, dict[str, Any]]] = {}
_COMMUNITY_CACHE: dict[str, tuple[str, dict[str, Any]]] = {}


def reset_cache() -> None:
    """Drop the in-process derived caches (tests, or after a rebuild)."""
    _INDEX_CACHE.clear()
    _COMMUNITY_CACHE.clear()


# --------------------------------------------------------------------------- #
# discovery
# --------------------------------------------------------------------------- #


def _bounded(value: Any, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return maximum
    return max(1, min(parsed, maximum))


def _int_field(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _artifact_roots(project_paths: Optional[Sequence[str]] = None) -> list[Path]:
    """Artifact roots configured via EOS_GRAPHIFY_ROOT; empty when it is unset.

    ``project_paths`` no longer influences root discovery (there is no more
    project-relative layout to walk); it stays a parameter because callers
    thread it through from instance paths regardless.
    """
    return graphify._configured_roots()


def _artifact_from_manifest(manifest_path: Path) -> Optional[dict[str, Any]]:
    manifest = graphify._read_manifest(manifest_path)
    if manifest is None:
        return None
    artifact_dir = manifest_path.parent.resolve()
    files = {
        "graph_path": artifact_dir / "graph.json",
        "report_path": artifact_dir / "GRAPH_REPORT.md",
        "html_path": artifact_dir / "graph.html",
    }
    artifact = {**manifest, "artifact_dir": str(artifact_dir)}
    for key, path in files.items():
        artifact[key] = str(path) if path.is_file() else None
    return artifact


def _project_key(artifact: dict[str, Any]) -> str:
    for key in ("project_key", "project_id"):
        value = artifact.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return Path(artifact["artifact_dir"]).name


def _is_parent(artifact: dict[str, Any]) -> bool:
    """True when the artifact root or the scanned repository is a parent repo.

    The parent path segment is read fresh from EOS_PARENT_SEGMENT on every call
    (mirrors how graphify.py reads its own env vars). Unset means no segment
    name is recognized as "parent": the feature is simply off, not broken.
    """
    segment = os.environ.get(PARENT_SEGMENT_ENV)
    if not segment:
        return False
    for value in (artifact.get("artifact_dir"), artifact.get("project_path")):
        if isinstance(value, str) and segment in Path(value).parts:
            return True
    return False


def _discover(
    project_paths: Optional[Sequence[str]], include_parent: bool, limit: int
) -> tuple[list[dict[str, Any]], int]:
    """Read every reachable manifest. Returns (artifacts capped at limit, total)."""
    artifacts: list[dict[str, Any]] = []
    seen_dirs: set[str] = set()
    seen_keys: set[str] = set()
    for root in _artifact_roots(project_paths):
        try:
            manifest_paths = sorted(root.glob("*/manifest.json"))
        except OSError:
            continue
        for manifest_path in manifest_paths:
            artifact = _artifact_from_manifest(manifest_path)
            if artifact is None:
                continue
            if artifact["artifact_dir"] in seen_dirs:
                continue
            is_parent = _is_parent(artifact)
            if is_parent and not include_parent:
                continue
            key = _project_key(artifact)
            if key in seen_keys:
                continue
            seen_dirs.add(artifact["artifact_dir"])
            seen_keys.add(key)
            artifact["project_key"] = key
            artifact["is_parent"] = is_parent
            artifacts.append(artifact)
    artifacts.sort(key=lambda item: item["project_key"])
    return artifacts[:limit], len(artifacts)


def discover_artifacts(
    project_paths: Optional[Sequence[str]] = None,
    include_parent: bool = False,
    limit: int = MAX_PROJECTS,
) -> list[dict[str, Any]]:
    """Every Graphify artifact reachable from the configured roots.

    Each entry is a manifest enriched with ``artifact_dir``/``graph_path``/
    ``report_path``/``html_path``/``project_key``/``is_parent``, in the same shape
    ``graphify.find_artifact`` returns, so it can be passed straight to
    ``graphify.is_ready``/``artifact_file``/``load_graph``.
    """
    artifacts, _ = _discover(project_paths, include_parent, _bounded(limit, MAX_PROJECTS))
    return artifacts


def _artifact_bytes(artifact_dir: Path) -> int:
    """Bytes of the regular files directly in the artifact dir (``cache/`` excluded)."""
    total = 0
    try:
        entries = list(artifact_dir.iterdir())
    except OSError:
        return 0
    for entry in entries:
        try:
            if entry.is_file() and not entry.is_symlink():
                total += entry.stat().st_size
        except OSError:
            continue
    return total


# --------------------------------------------------------------------------- #
# identity matching
# --------------------------------------------------------------------------- #


def _sep_form(value: str) -> str:
    return _NON_ALNUM_RE.sub("_", value.casefold()).strip("_")


def _compact_form(value: str) -> str:
    return _NON_ALNUM_RE.sub("", value.casefold())


def _identity_aliases(project_key: str, project_path: Any) -> dict[str, tuple[str, ...]]:
    """Strings that identify a project inside another project's graph."""
    identities = {project_key}
    if isinstance(project_path, str) and project_path:
        identities.add(Path(project_path).name)
    sep_aliases: set[str] = set()
    compact_aliases: set[str] = set()
    for identity in identities:
        sep = _sep_form(identity)
        if not sep:
            continue
        sep_aliases.add(sep)
        compact = _compact_form(identity)
        if len(compact) >= MIN_COMPACT_ALIAS:
            compact_aliases.add(compact)
    return {"sep": tuple(sorted(sep_aliases)), "compact": tuple(sorted(compact_aliases))}


def _match_alias(sep: str, compact: str, aliases: dict[str, tuple[str, ...]]) -> Optional[str]:
    if sep:
        padded = f"_{sep}_"
        for alias in aliases["sep"]:
            if f"_{alias}_" in padded:
                return alias
    if compact:
        for alias in aliases["compact"]:
            if alias in compact:
                return alias
    return None


def _dependency_key(label: str) -> Optional[str]:
    """Group a fully qualified package label into a comparable dependency key."""
    parts = label.split(".")
    if len(parts) < 2 or not all(_SEGMENT_RE.fullmatch(part) for part in parts):
        return None
    depth = 3 if parts[0].casefold() in _GROUP_PREFIXES else 1
    return ".".join(parts[:depth]).casefold()


# --------------------------------------------------------------------------- #
# per-project index (lazy, cached)
# --------------------------------------------------------------------------- #


def _roster(artifacts: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "project_key": artifact["project_key"],
            "aliases": _identity_aliases(artifact["project_key"], artifact.get("project_path")),
        }
        for artifact in artifacts
    ]


def _roster_signature(roster: Sequence[dict[str, Any]]) -> str:
    """The index is a reduction against the roster, so the roster is part of its key."""
    payload = json.dumps(
        [
            [entry["project_key"], list(entry["aliases"]["sep"]), list(entry["aliases"]["compact"])]
            for entry in roster
        ],
        sort_keys=True,
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def _build_index(artifact: dict[str, Any], roster: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Reduce one full graph to cross-project references plus external dependencies."""
    graph = graphify.load_graph(artifact)
    own_key = artifact["project_key"]
    targets = [entry for entry in roster if entry["project_key"] != own_key]
    references: dict[str, dict[str, Any]] = {}
    dependencies: dict[str, dict[str, Any]] = {}

    for node in graph.get("nodes", []):
        if not isinstance(node, dict):
            continue
        node_id = str(node.get("id") or "")
        label = str(node.get("label") or "")
        source_file = str(node.get("source_file") or "")
        dependency = _dependency_key(label) if not source_file else None
        if dependency is not None:
            bucket = dependencies.setdefault(dependency, {"count": 0, "examples": []})
            bucket["count"] += 1
            if len(bucket["examples"]) < MAX_DEPENDENCY_EXAMPLES and label not in bucket["examples"]:
                bucket["examples"].append(label)

        fields = (
            ("dependency" if dependency is not None else "label", label),
            ("node_id", node_id),
            ("source_file", source_file),
        )
        prepared = [
            (kind, _sep_form(text), _compact_form(text)) for kind, text in fields if text
        ]
        if not prepared:
            continue
        for entry in targets:
            for kind, sep, compact in prepared:
                alias = _match_alias(sep, compact, entry["aliases"])
                if alias is None:
                    continue
                bucket = references.setdefault(
                    entry["project_key"], {"count": 0, "examples": []}
                )
                bucket["count"] += 1
                if len(bucket["examples"]) < MAX_EDGE_EXAMPLES:
                    bucket["examples"].append(
                        {
                            "node_id": node_id,
                            "label": label,
                            "source_file": source_file or None,
                            "matched": alias,
                            "kind": kind,
                        }
                    )
                break
    return {"project_key": own_key, "references": references, "dependencies": dependencies}


def _project_index(
    artifact: dict[str, Any], roster: Sequence[dict[str, Any]], signature: str
) -> dict[str, Any]:
    key = artifact["artifact_dir"]
    generated_at = str(artifact.get("generated_at") or "")
    cached = _INDEX_CACHE.get(key)
    if cached is not None and cached[0] == generated_at and cached[1] == signature:
        return cached[2]
    index = _build_index(artifact, roster)
    _INDEX_CACHE[key] = (generated_at, signature, index)
    return index


def _projection_node(artifact: dict[str, Any]) -> dict[str, Any]:
    return {
        "project_key": artifact["project_key"],
        "project_id": artifact.get("project_id"),
        "project_path": artifact.get("project_path"),
        "status": str(artifact.get("status") or "unknown"),
        "node_count": _int_field(artifact.get("node_count")),
        "edge_count": _int_field(artifact.get("edge_count")),
        "community_count": _int_field(artifact.get("community_count")),
        "generated_at": artifact.get("generated_at"),
        "is_parent": bool(artifact.get("is_parent")),
    }


def _projection(
    project_paths: Optional[Sequence[str]], include_parent: bool
) -> dict[str, Any]:
    """Build (or reuse) the derived project graph. Only ready artifacts contribute."""
    artifacts, total = _discover(project_paths, include_parent, MAX_PROJECTS)
    ready = [artifact for artifact in artifacts if graphify.is_ready(artifact)]
    roster = _roster(ready)
    signature = _roster_signature(roster)

    nodes: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    indexes: dict[str, dict[str, Any]] = {}
    for artifact in artifacts:
        key = artifact["project_key"]
        status = str(artifact.get("status") or "unknown")
        if not graphify.is_ready(artifact):
            skipped.append(
                {"project_key": key, "status": status, "reason": "not_ready", "detail": None}
            )
            continue
        try:
            indexes[key] = _project_index(artifact, roster, signature)
        except (FileNotFoundError, ValueError, OSError) as error:
            skipped.append(
                {
                    "project_key": key,
                    "status": status,
                    "reason": "unreadable",
                    "detail": str(error),
                }
            )
            continue
        nodes.append(_projection_node(artifact))

    edges: list[dict[str, Any]] = []
    for source_key, index in indexes.items():
        for target_key, bucket in index["references"].items():
            if target_key not in indexes:
                continue
            edges.append(
                {
                    "source": source_key,
                    "target": target_key,
                    "weight": bucket["count"],
                    "examples": list(bucket["examples"]),
                }
            )
    edges.sort(key=lambda edge: (-edge["weight"], edge["source"], edge["target"]))
    return {
        "nodes": nodes,
        "edges": edges,
        "indexes": indexes,
        "skipped": skipped,
        "projects_truncated": total > len(artifacts),
    }


# --------------------------------------------------------------------------- #
# public projections
# --------------------------------------------------------------------------- #


def workspace_overview(
    project_paths: Optional[Sequence[str]] = None,
    include_parent: bool = False,
    limit: int = MAX_PROJECTS,
) -> dict[str, Any]:
    """One row per Graphify artifact plus workspace aggregates, from manifests only."""
    limit = _bounded(limit, MAX_PROJECTS)
    artifacts, total = _discover(project_paths, include_parent, MAX_PROJECTS)
    rows: list[dict[str, Any]] = []
    for artifact in artifacts:
        rows.append(
            {
                "project_key": artifact["project_key"],
                "project_id": artifact.get("project_id"),
                "project_path": artifact.get("project_path"),
                "status": str(artifact.get("status") or "unknown"),
                "node_count": _int_field(artifact.get("node_count")),
                "edge_count": _int_field(artifact.get("edge_count")),
                "community_count": _int_field(artifact.get("community_count")),
                "generated_at": artifact.get("generated_at"),
                "source_revision": artifact.get("source_revision"),
                "graphify_version": artifact.get("graphify_version"),
                "error": artifact.get("error"),
                "has_graph": bool(artifact.get("graph_path")),
                "has_html": bool(artifact.get("html_path")),
                "has_report": bool(artifact.get("report_path")),
                "artifact_dir": artifact["artifact_dir"],
                "artifact_bytes": _artifact_bytes(Path(artifact["artifact_dir"])),
                "is_parent": bool(artifact.get("is_parent")),
            }
        )
    returned = rows[:limit]
    summary = {
        "project_count": len(rows),
        "returned_count": len(returned),
        "ready_count": sum(1 for row in rows if row["status"] == "ready"),
        "problem_count": sum(1 for row in rows if row["status"] != "ready"),
        "total_nodes": sum(row["node_count"] for row in rows),
        "total_edges": sum(row["edge_count"] for row in rows),
        "total_communities": sum(row["community_count"] for row in rows),
        "total_bytes": sum(row["artifact_bytes"] for row in rows),
    }
    return {
        "projects": returned,
        "summary": summary,
        "truncated": len(rows) > limit or total > len(artifacts),
        "include_parent": bool(include_parent),
    }


def service_relations(
    project_paths: Optional[Sequence[str]] = None,
    include_parent: bool = False,
    limit: int = MAX_RELATION_EDGES,
) -> dict[str, Any]:
    """Directed project-to-project projection derived from identity references."""
    limit = _bounded(limit, MAX_RELATION_EDGES)
    projection = _projection(project_paths, include_parent)
    edges = projection["edges"]
    return {
        "nodes": projection["nodes"],
        "edges": edges[:limit],
        "edge_count": len(edges),
        "skipped": projection["skipped"],
        "truncated": len(edges) > limit or projection["projects_truncated"],
        "include_parent": bool(include_parent),
    }


def shortest_path(
    source_key: str,
    target_key: str,
    project_paths: Optional[Sequence[str]] = None,
    include_parent: bool = False,
    max_hops: int = MAX_PATH_HOPS,
) -> dict[str, Any]:
    """Unweighted BFS over the directed service projection."""
    max_hops = _bounded(max_hops, MAX_PATH_HOPS)
    projection = _projection(project_paths, include_parent)
    result: dict[str, Any] = {
        "source": source_key,
        "target": target_key,
        "found": False,
        "path": [],
        "hops": [],
        "reason": None,
        "truncated": False,
        "include_parent": bool(include_parent),
    }
    known = {node["project_key"] for node in projection["nodes"]}
    if source_key not in known:
        result["reason"] = "unknown_source"
        return result
    if target_key not in known:
        result["reason"] = "unknown_target"
        return result
    if source_key == target_key:
        result["found"] = True
        result["path"] = [source_key]
        return result

    adjacency: dict[str, list[str]] = {}
    edge_map: dict[tuple[str, str], dict[str, Any]] = {}
    for edge in projection["edges"]:
        adjacency.setdefault(edge["source"], []).append(edge["target"])
        edge_map[(edge["source"], edge["target"])] = edge

    previous: dict[str, Optional[str]] = {source_key: None}
    frontier = deque([(source_key, 0)])
    truncated = False
    while frontier and target_key not in previous:
        current, depth = frontier.popleft()
        if depth >= max_hops:
            truncated = True
            continue
        for neighbor in adjacency.get(current, ()):
            if neighbor in previous:
                continue
            previous[neighbor] = current
            if neighbor == target_key:
                break
            frontier.append((neighbor, depth + 1))

    if target_key not in previous:
        result["reason"] = "max_hops_exceeded" if truncated else "unreachable"
        result["truncated"] = truncated
        return result

    path: list[str] = []
    cursor: Optional[str] = target_key
    while cursor is not None:
        path.append(cursor)
        cursor = previous[cursor]
    path.reverse()
    result["found"] = True
    result["path"] = path
    result["hops"] = [
        {
            "source": first,
            "target": second,
            "weight": edge_map[(first, second)]["weight"],
            "examples": list(edge_map[(first, second)]["examples"]),
        }
        for first, second in zip(path, path[1:])
    ]
    return result


def common_dependencies(
    project_paths: Optional[Sequence[str]] = None,
    include_parent: bool = False,
    limit: int = MAX_DEPENDENCIES,
    min_projects: int = 2,
) -> dict[str, Any]:
    """External packages / client artifacts used by at least ``min_projects`` projects."""
    limit = _bounded(limit, MAX_DEPENDENCIES)
    min_projects = max(1, _bounded(min_projects, MAX_PROJECTS))
    projection = _projection(project_paths, include_parent)
    tally: dict[str, dict[str, Any]] = {}
    for project_key, index in projection["indexes"].items():
        for dependency, bucket in index["dependencies"].items():
            entry = tally.setdefault(
                dependency, {"projects": set(), "reference_count": 0, "examples": []}
            )
            entry["projects"].add(project_key)
            entry["reference_count"] += bucket["count"]
            for label in bucket["examples"]:
                if len(entry["examples"]) < MAX_DEPENDENCY_EXAMPLES and label not in entry["examples"]:
                    entry["examples"].append(label)

    rows = [
        {
            "dependency": dependency,
            "project_count": len(entry["projects"]),
            "reference_count": entry["reference_count"],
            "projects": sorted(entry["projects"]),
            "examples": sorted(entry["examples"]),
        }
        for dependency, entry in tally.items()
        if len(entry["projects"]) >= min_projects
    ]
    rows.sort(
        key=lambda row: (-row["project_count"], -row["reference_count"], row["dependency"])
    )
    return {
        "dependencies": rows[:limit],
        "dependency_count": len(rows),
        "skipped": projection["skipped"],
        "min_projects": min_projects,
        "truncated": len(rows) > limit or projection["projects_truncated"],
        "include_parent": bool(include_parent),
    }


def _build_communities(artifact: dict[str, Any]) -> dict[str, Any]:
    graph = graphify.load_graph(artifact)
    nodes = [node for node in graph.get("nodes", []) if isinstance(node, dict)]
    edges = graphify._raw_edges(graph)

    degree: Counter[str] = Counter()
    for edge in edges:
        degree[str(edge.get("source", ""))] += 1
        degree[str(edge.get("target", ""))] += 1

    community_of: dict[str, str] = {}
    buckets: dict[str, dict[str, Any]] = {}
    for node in nodes:
        raw = node.get("community")
        community_id = "unassigned" if raw is None else str(raw)
        node_id = str(node.get("id") or "")
        community_of[node_id] = community_id
        bucket = buckets.setdefault(
            community_id,
            {
                "id": community_id,
                "name": str(node.get("community_name") or f"Community {community_id}"),
                "node_count": 0,
                "edge_count": 0,
                "members": [],
            },
        )
        bucket["node_count"] += 1
        bucket["members"].append(
            {
                "id": node_id,
                "label": str(node.get("label") or node_id),
                "source_file": node.get("source_file") or None,
                "degree": degree[node_id],
            }
        )

    for edge in edges:
        source = community_of.get(str(edge.get("source", "")))
        if source is not None and source == community_of.get(str(edge.get("target", ""))):
            buckets[source]["edge_count"] += 1

    for bucket in buckets.values():
        bucket["members"].sort(key=lambda member: (-member["degree"], member["label"], member["id"]))
        bucket["members"] = bucket["members"][:MAX_COMMUNITY_MEMBERS]

    communities = sorted(
        buckets.values(), key=lambda bucket: (-bucket["node_count"], bucket["name"])
    )
    return {"node_count": len(nodes), "edge_count": len(edges), "communities": communities}


def _community_summary(artifact: dict[str, Any]) -> dict[str, Any]:
    key = artifact["artifact_dir"]
    generated_at = str(artifact.get("generated_at") or "")
    cached = _COMMUNITY_CACHE.get(key)
    if cached is not None and cached[0] == generated_at:
        return cached[1]
    summary = _build_communities(artifact)
    _COMMUNITY_CACHE[key] = (generated_at, summary)
    return summary


def project_communities(
    project_key: str,
    project_paths: Optional[Sequence[str]] = None,
    include_parent: bool = False,
    limit: int = MAX_COMMUNITIES,
    members: int = MAX_COMMUNITY_MEMBERS,
    artifact: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Community summary nodes for one project instead of its raw node graph.

    Pass ``artifact`` when the caller already resolved it: discovery can only
    match on the project key, which is just the repository directory name, so two
    workspaces holding a repository of the same name would collide.

    Raises ``LookupError`` when the project key is unknown and ``FileNotFoundError``
    when its artifact is not ready.
    """
    limit = _bounded(limit, MAX_COMMUNITIES)
    members = _bounded(members, MAX_COMMUNITY_MEMBERS)
    if artifact is None:
        artifacts = discover_artifacts(project_paths, include_parent)
        artifact = next(
            (item for item in artifacts if item["project_key"] == project_key), None
        )
    if artifact is None:
        raise LookupError(f"Unknown Graphify project: {project_key}")
    if not graphify.is_ready(artifact):
        raise FileNotFoundError(f"Graphify artifact is not ready: {project_key}")

    summary = _community_summary(artifact)
    communities = summary["communities"]
    returned = [
        {**community, "members": community["members"][:members]}
        for community in communities[:limit]
    ]
    return {
        "project_key": project_key,
        "status": str(artifact.get("status") or "unknown"),
        "generated_at": artifact.get("generated_at"),
        "node_count": summary["node_count"],
        "edge_count": summary["edge_count"],
        "community_count": len(communities),
        "communities": returned,
        "truncated": len(communities) > limit
        or any(community["node_count"] > members for community in returned),
        "include_parent": bool(include_parent),
    }
