"""Tests for the workspace-level / cross-project Graphify projections.

Builds small synthetic artifact dirs (manifest.json + graph.json + report + html)
under tmp_path and points EOS_GRAPHIFY_ROOT at them, so nothing touches the real
graphify-out tree.
"""
import json
import os
from pathlib import Path

import pytest

from ui.app import workspace


@pytest.fixture(autouse=True)
def _clear_cache():
    workspace.reset_cache()
    yield
    workspace.reset_cache()


def _node(node_id, label, source_file="", community=0, community_name="Community 0"):
    return {
        "id": node_id,
        "label": label,
        "file_type": "code",
        "source_file": source_file,
        "source_location": "L1",
        "metadata": {"language": "java", "kind": "file"},
        "community": community,
        "community_name": community_name,
    }


def _link(source, target, relation="references"):
    return {
        "source": source,
        "target": target,
        "relation": relation,
        "confidence": "EXTRACTED",
        "confidence_score": 1.0,
        "source_file": "",
        "source_location": "L1",
    }


def _write_project(root, key, project_path, nodes, links, status="ready", community_count=1):
    artifact_dir = root / key
    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / "graph.json").write_text(
        json.dumps({"nodes": nodes, "links": links}), encoding="utf-8"
    )
    (artifact_dir / "GRAPH_REPORT.md").write_text(f"# {key}\n", encoding="utf-8")
    (artifact_dir / "graph.html").write_text("<html></html>\n", encoding="utf-8")
    (artifact_dir / "manifest.json").write_text(
        json.dumps(
            {
                "project_key": key,
                "project_id": key,
                "project_path": str(project_path),
                "graphify_version": "0.9.38",
                "source_revision": f"rev-{key}",
                "generated_at": "2026-08-10T00:00:00+00:00",
                "node_count": len(nodes),
                "edge_count": len(links),
                "community_count": community_count,
                "status": status,
                "error": None if status == "ready" else "graphify exited 1",
                "has_graph": True,
                "has_html": True,
                "has_report": True,
            }
        ),
        encoding="utf-8",
    )
    return artifact_dir


@pytest.fixture
def graphify_root(tmp_path, monkeypatch):
    """Four projects: alpha -> beta -> gamma, plus a failed delta."""
    root = tmp_path / "graphify" / "projects"
    services = tmp_path / "microservices"

    # alpha references beta through an outbound client package path.
    _write_project(
        root,
        "fm-alpha-gateway",
        services / "fm-alpha-gateway",
        [
            _node(
                "fm_alpha_gateway_application",
                "AlphaGatewayApplication",
                "src/main/java/com/fm/alpha/gateway/AlphaGatewayApplication.java",
                0,
                "Gateway",
            ),
            _node(
                "fm_alpha_gateway_config",
                "GatewayConfig",
                "src/main/java/com/fm/alpha/gateway/GatewayConfig.java",
                0,
                "Gateway",
            ),
            _node(
                "fm_alpha_gateway_outbound_client",
                "BetaOrdersClient",
                "src/main/java/com/fm/alpha/gateway/adapter/rest/outbound/betaorders/BetaOrdersClient.java",
                1,
                "Outbound",
            ),
            # Only the path carries the neighbour identity, not the label.
            _node(
                "fm_alpha_gateway_order_sync",
                "OrderSyncJob",
                "src/main/java/com/fm/alpha/gateway/adapter/rest/outbound/betaorders/OrderSyncJob.java",
                1,
                "Outbound",
            ),
            _node(
                "org_springframework_boot_autoconfigure_springbootapplication",
                "org.springframework.boot.autoconfigure.SpringBootApplication",
                "",
                2,
                "External",
            ),
            _node("lombok_extern_slf4j_slf4j", "lombok.extern.slf4j.Slf4j", "", 2, "External"),
        ],
        [
            _link("fm_alpha_gateway_application", "fm_alpha_gateway_config"),
            _link("fm_alpha_gateway_application", "fm_alpha_gateway_outbound_client", "calls"),
            _link("fm_alpha_gateway_application", "fm_alpha_gateway_order_sync", "calls"),
            _link(
                "fm_alpha_gateway_application",
                "org_springframework_boot_autoconfigure_springbootapplication",
                "imports",
            ),
            _link("fm_alpha_gateway_outbound_client", "lombok_extern_slf4j_slf4j", "imports"),
        ],
        community_count=3,
    )

    # beta references gamma through an external client dependency label.
    _write_project(
        root,
        "beta-orders",
        services / "beta-orders",
        [
            _node(
                "fm_beta_orders_application",
                "BetaOrdersApplication",
                "src/main/java/com/fm/beta/orders/BetaOrdersApplication.java",
                0,
                "Orders",
            ),
            _node(
                "com_example_fm_gamma_billing_client_billingclient",
                "com.example.fm.gamma.billing.client.BillingClient",
                "",
                1,
                "External",
            ),
            _node(
                "org_springframework_boot_autoconfigure_springbootapplication",
                "org.springframework.boot.autoconfigure.SpringBootApplication",
                "",
                1,
                "External",
            ),
            _node("com_example_upstream_core_base", "com.example.upstream.core.Base", "", 1, "External"),
        ],
        [
            _link(
                "fm_beta_orders_application",
                "com_example_fm_gamma_billing_client_billingclient",
                "imports",
            ),
            _link(
                "fm_beta_orders_application",
                "org_springframework_boot_autoconfigure_springbootapplication",
                "imports",
            ),
            _link("fm_beta_orders_application", "com_example_upstream_core_base", "imports"),
        ],
        community_count=2,
    )

    # gamma is a leaf.
    _write_project(
        root,
        "fm-gamma-billing",
        services / "fm-gamma-billing",
        [
            _node(
                "fm_gamma_billing_application",
                "GammaBillingApplication",
                "src/main/java/com/fm/gamma/billing/GammaBillingApplication.java",
                0,
                "Billing",
            ),
            _node(
                "org_springframework_boot_web_servletcomponentscan",
                "org.springframework.boot.web.ServletComponentScan",
                "",
                1,
                "External",
            ),
            _node("lombok_getter", "lombok.Getter", "", 1, "External"),
        ],
        [
            _link(
                "fm_gamma_billing_application",
                "org_springframework_boot_web_servletcomponentscan",
                "imports",
            ),
            _link("fm_gamma_billing_application", "lombok_getter", "imports"),
        ],
        community_count=2,
    )

    # delta failed: it references alpha and springframework but must not project.
    _write_project(
        root,
        "fm-delta-legacy",
        services / "fm-delta-legacy",
        [
            _node(
                "fm_delta_legacy_client",
                "AlphaGatewayLegacyClient",
                "src/main/java/com/fm/delta/legacy/alphagateway/AlphaGatewayLegacyClient.java",
                0,
                "Legacy",
            ),
            _node(
                "org_springframework_boot_autoconfigure_springbootapplication",
                "org.springframework.boot.autoconfigure.SpringBootApplication",
                "",
                0,
                "Legacy",
            ),
        ],
        [
            _link(
                "fm_delta_legacy_client",
                "org_springframework_boot_autoconfigure_springbootapplication",
                "imports",
            )
        ],
        status="failed",
    )

    monkeypatch.setenv("EOS_GRAPHIFY_ROOT", str(root))
    return root


def _keys(rows):
    return [row["project_key"] for row in rows]


def test_overview_lists_every_artifact_with_aggregates(graphify_root):
    result = workspace.workspace_overview()

    assert _keys(result["projects"]) == [
        "beta-orders",
        "fm-alpha-gateway",
        "fm-delta-legacy",
        "fm-gamma-billing",
    ]
    assert result["truncated"] is False
    assert result["include_parent"] is False

    alpha = result["projects"][1]
    assert alpha["status"] == "ready"
    assert alpha["node_count"] == 6
    assert alpha["edge_count"] == 5
    assert alpha["community_count"] == 3
    assert alpha["generated_at"] == "2026-08-10T00:00:00+00:00"
    assert alpha["source_revision"] == "rev-fm-alpha-gateway"
    assert (alpha["has_graph"], alpha["has_html"], alpha["has_report"]) == (True, True, True)
    assert alpha["artifact_bytes"] == sum(
        entry.stat().st_size for entry in (graphify_root / "fm-alpha-gateway").iterdir()
    )

    summary = result["summary"]
    assert summary["project_count"] == 4
    assert summary["returned_count"] == 4
    assert summary["ready_count"] == 3
    assert summary["problem_count"] == 1
    assert summary["total_nodes"] == 6 + 4 + 3 + 2
    assert summary["total_edges"] == 5 + 3 + 2 + 1
    assert summary["total_bytes"] == sum(row["artifact_bytes"] for row in result["projects"])


def test_failed_artifact_is_listed_but_never_projected(graphify_root):
    overview = workspace.workspace_overview()
    delta = next(row for row in overview["projects"] if row["project_key"] == "fm-delta-legacy")
    assert delta["status"] == "failed"
    assert delta["error"] == "graphify exited 1"

    relations = workspace.service_relations()
    assert "fm-delta-legacy" not in _keys(relations["nodes"])
    assert all(edge["source"] != "fm-delta-legacy" for edge in relations["edges"])
    assert all(edge["target"] != "fm-delta-legacy" for edge in relations["edges"])
    assert {"project_key": "fm-delta-legacy", "status": "failed", "reason": "not_ready", "detail": None} in relations[
        "skipped"
    ]

    # delta also imports springframework; it must not inflate the dependency tally.
    dependencies = workspace.common_dependencies()
    spring = next(
        row for row in dependencies["dependencies"] if row["dependency"] == "org.springframework.boot"
    )
    assert "fm-delta-legacy" not in spring["projects"]


def test_stale_artifact_is_excluded_from_projections(graphify_root, monkeypatch):
    manifest_path = graphify_root / "fm-gamma-billing" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["status"] = "stale"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    overview = workspace.workspace_overview()
    gamma = next(row for row in overview["projects"] if row["project_key"] == "fm-gamma-billing")
    assert gamma["status"] == "stale"

    relations = workspace.service_relations()
    assert "fm-gamma-billing" not in _keys(relations["nodes"])
    assert all(edge["target"] != "fm-gamma-billing" for edge in relations["edges"])

    with pytest.raises(FileNotFoundError):
        workspace.project_communities("fm-gamma-billing")


def test_service_relations_derive_edges_with_evidence(graphify_root):
    result = workspace.service_relations()

    assert _keys(result["nodes"]) == ["beta-orders", "fm-alpha-gateway", "fm-gamma-billing"]
    pairs = {(edge["source"], edge["target"]): edge for edge in result["edges"]}
    assert set(pairs) == {
        ("fm-alpha-gateway", "beta-orders"),
        ("beta-orders", "fm-gamma-billing"),
    }

    alpha_beta = pairs[("fm-alpha-gateway", "beta-orders")]
    assert alpha_beta["weight"] == 2
    by_kind = {example["kind"]: example for example in alpha_beta["examples"]}
    assert set(by_kind) == {"label", "source_file"}
    assert by_kind["label"]["matched"] == "betaorders"
    assert by_kind["label"]["node_id"] == "fm_alpha_gateway_outbound_client"
    assert by_kind["source_file"]["node_id"] == "fm_alpha_gateway_order_sync"
    assert by_kind["source_file"]["source_file"].endswith("betaorders/OrderSyncJob.java")

    # The beta -> gamma link comes from an external client dependency label.
    beta_gamma = pairs[("beta-orders", "fm-gamma-billing")]
    assert beta_gamma["examples"][0]["kind"] == "dependency"
    assert beta_gamma["examples"][0]["label"] == "com.example.fm.gamma.billing.client.BillingClient"
    assert beta_gamma["examples"][0]["source_file"] is None

    assert result["edge_count"] == 2
    assert result["truncated"] is False


def test_shortest_path_found_and_not_found(graphify_root):
    found = workspace.shortest_path("fm-alpha-gateway", "fm-gamma-billing")
    assert found["found"] is True
    assert found["path"] == ["fm-alpha-gateway", "beta-orders", "fm-gamma-billing"]
    assert [(hop["source"], hop["target"]) for hop in found["hops"]] == [
        ("fm-alpha-gateway", "beta-orders"),
        ("beta-orders", "fm-gamma-billing"),
    ]
    assert found["hops"][0]["examples"][0]["matched"] == "betaorders"
    assert found["reason"] is None

    # The projection is directed, so the reverse direction is unreachable.
    reverse = workspace.shortest_path("fm-gamma-billing", "fm-alpha-gateway")
    assert reverse["found"] is False
    assert reverse["path"] == []
    assert reverse["hops"] == []
    assert reverse["reason"] == "unreachable"

    assert workspace.shortest_path("nope", "beta-orders")["reason"] == "unknown_source"
    assert workspace.shortest_path("fm-alpha-gateway", "nope")["reason"] == "unknown_target"
    assert workspace.shortest_path("fm-delta-legacy", "beta-orders")["reason"] == "unknown_source"


def test_shortest_path_max_hops_cap(graphify_root):
    capped = workspace.shortest_path("fm-alpha-gateway", "fm-gamma-billing", max_hops=1)
    assert capped["found"] is False
    assert capped["truncated"] is True
    assert capped["reason"] == "max_hops_exceeded"


def test_common_dependencies_ranked_by_project_count(graphify_root):
    result = workspace.common_dependencies()

    rows = {row["dependency"]: row for row in result["dependencies"]}
    assert list(rows) == ["org.springframework.boot", "lombok"]
    assert rows["org.springframework.boot"]["project_count"] == 3
    assert rows["org.springframework.boot"]["projects"] == [
        "beta-orders",
        "fm-alpha-gateway",
        "fm-gamma-billing",
    ]
    assert rows["lombok"]["project_count"] == 2
    assert rows["lombok"]["projects"] == ["fm-alpha-gateway", "fm-gamma-billing"]
    assert "lombok.Getter" in rows["lombok"]["examples"]

    # Single-project dependencies are filtered out by default.
    assert "com.example.upstream" not in rows
    assert workspace.common_dependencies(min_projects=1)["dependency_count"] > result["dependency_count"]
    assert result["truncated"] is False


def test_project_communities_returns_summary_nodes(graphify_root):
    result = workspace.project_communities("fm-alpha-gateway")

    assert result["project_key"] == "fm-alpha-gateway"
    assert result["status"] == "ready"
    assert result["node_count"] == 6
    assert result["edge_count"] == 5
    assert result["community_count"] == 3
    assert result["truncated"] is False

    by_name = {community["name"]: community for community in result["communities"]}
    assert set(by_name) == {"Gateway", "Outbound", "External"}
    assert by_name["Gateway"]["node_count"] == 2
    assert by_name["Gateway"]["edge_count"] == 1
    assert by_name["Outbound"]["node_count"] == 2
    # Members are representative nodes, highest degree first.
    assert by_name["Gateway"]["members"][0]["id"] == "fm_alpha_gateway_application"
    assert by_name["Gateway"]["members"][0]["degree"] == 4

    with pytest.raises(LookupError):
        workspace.project_communities("fm-nope")


def test_caps_are_enforced_server_side(graphify_root):
    overview = workspace.workspace_overview(limit=2)
    assert len(overview["projects"]) == 2
    assert overview["truncated"] is True
    assert overview["summary"]["project_count"] == 4
    assert overview["summary"]["returned_count"] == 2

    relations = workspace.service_relations(limit=1)
    assert len(relations["edges"]) == 1
    assert relations["edge_count"] == 2
    assert relations["truncated"] is True

    dependencies = workspace.common_dependencies(limit=1)
    assert len(dependencies["dependencies"]) == 1
    assert dependencies["dependency_count"] == 2
    assert dependencies["truncated"] is True

    few_communities = workspace.project_communities("fm-alpha-gateway", limit=1)
    assert len(few_communities["communities"]) == 1
    assert few_communities["truncated"] is True

    few_members = workspace.project_communities("fm-alpha-gateway", members=1)
    assert all(len(c["members"]) <= 1 for c in few_members["communities"])
    assert few_members["truncated"] is True


def test_caps_are_clamped_to_module_maximums(graphify_root):
    assert workspace.workspace_overview(limit=10**6)["summary"]["returned_count"] == 4
    assert len(workspace.project_communities("fm-alpha-gateway", members=10**6)["communities"]) == 3


def test_parent_repositories_are_opt_in_by_project_path(tmp_path, monkeypatch, graphify_root):
    monkeypatch.setenv(workspace.PARENT_SEGMENT_ENV, "parent-microservices")
    _write_project(
        graphify_root,
        "fm-parent-core",
        tmp_path / "parent-microservices" / "fm-parent-core",
        [_node("fm_parent_core_application", "ParentCoreApplication", "src/Main.java")],
        [],
    )

    default_keys = _keys(workspace.workspace_overview()["projects"])
    assert "fm-parent-core" not in default_keys

    opted_in = _keys(workspace.workspace_overview(include_parent=True)["projects"])
    assert "fm-parent-core" in opted_in
    parent_row = next(
        row
        for row in workspace.workspace_overview(include_parent=True)["projects"]
        if row["project_key"] == "fm-parent-core"
    )
    assert parent_row["is_parent"] is True


def test_parent_repositories_are_opt_in_by_artifact_root(tmp_path, monkeypatch, graphify_root):
    """A second, independently configured artifact root can also hold a parent repo.

    EOS_GRAPHIFY_ROOT accepts an ``os.pathsep``-separated list (like
    EOS_GRAPHIFY_COORDINATOR_ROOT does), so this project is reachable without
    any project-path-relative discovery.
    """
    monkeypatch.setenv(workspace.PARENT_SEGMENT_ENV, "parent-microservices")
    parent_root = tmp_path / "parent-microservices" / "other-root" / "projects"
    _write_project(
        parent_root,
        "fm-root-parent",
        tmp_path / "microservices" / "fm-root-parent",
        [_node("fm_root_parent_application", "RootParentApplication", "src/Main.java")],
        [],
    )
    monkeypatch.setenv(
        workspace.graphify.GRAPHIFY_ROOT_ENV,
        os.pathsep.join([str(graphify_root), str(parent_root)]),
    )
    project_paths = [str(tmp_path / "parent-microservices" / "svc")]

    default_keys = _keys(workspace.workspace_overview(project_paths=project_paths)["projects"])
    assert "fm-root-parent" not in default_keys
    assert "fm-alpha-gateway" in default_keys

    opted_in = _keys(
        workspace.workspace_overview(project_paths=project_paths, include_parent=True)["projects"]
    )
    assert "fm-root-parent" in opted_in


def test_index_cache_is_reused_across_calls(graphify_root, monkeypatch):
    workspace.service_relations()
    cached = len(workspace._INDEX_CACHE)
    assert cached == 3

    calls = []
    real_load_graph = workspace.graphify.load_graph

    def _counting_load_graph(artifact):
        calls.append(artifact["artifact_dir"])
        return real_load_graph(artifact)

    monkeypatch.setattr(workspace.graphify, "load_graph", _counting_load_graph)
    workspace.service_relations()
    workspace.common_dependencies()
    assert calls == []
    assert len(workspace._INDEX_CACHE) == cached


def test_regenerated_artifact_replaces_its_cache_entry(graphify_root):
    """Every refresh writes a new generated_at; the caches must stay bounded."""
    manifest_path = graphify_root / "fm-alpha-gateway" / "manifest.json"
    for index in range(5):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["generated_at"] = f"2026-08-1{index}T00:00:00+00:00"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        workspace.project_communities("fm-alpha-gateway")
        workspace.service_relations()

    # One entry per artifact dir: three ready projects, one community summary.
    assert len(workspace._INDEX_CACHE) == 3
    assert len(workspace._COMMUNITY_CACHE) == 1
    cached_at, _ = workspace._COMMUNITY_CACHE[str(graphify_root / "fm-alpha-gateway")]
    assert cached_at == "2026-08-14T00:00:00+00:00"


def test_project_communities_prefers_the_resolved_artifact(tmp_path, monkeypatch):
    """Two workspaces can hold a repository of the same name; the key is ambiguous."""
    root = tmp_path / "graphify" / "projects"
    _write_project(root, "aaa-id", tmp_path / "b" / "shared", [_node("B_SECRET", "B")], [])
    ours = _write_project(
        root, "zzz-id", tmp_path / "a" / "shared", [_node("A_PUBLIC", "A")], []
    )
    for artifact_dir in (root / "aaa-id", ours):
        manifest_path = artifact_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["project_key"] = "shared"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setenv(workspace.graphify.GRAPHIFY_ROOT_ENV, str(root))

    artifact = workspace.graphify.find_artifact("zzz-id", str(tmp_path / "a" / "shared"))
    assert artifact is not None
    result = workspace.project_communities("shared", artifact=artifact)
    members = [
        member["id"]
        for community in result["communities"]
        for member in community["members"]
    ]
    assert members == ["A_PUBLIC"]
