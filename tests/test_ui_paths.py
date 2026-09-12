"""Configuration-surface guards for the eos-ui backend.

These assert that the UI carries no dead code and no literal paths/names from
the workspace EOS was extracted from: Graphify discovery, the Graphify
coordinator, and the workspace parent-segment convention are all
configuration-only (EOS_GRAPHIFY_ROOT / EOS_GRAPHIFY_REFRESH_CMD /
EOS_GRAPHIFY_COORDINATOR_ROOT / EOS_PARENT_SEGMENT), and absent configuration
means the feature is off, not broken.
"""
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def test_no_dead_second_server():
    assert not (REPO / "ui" / "app" / "main.py").exists()


def test_graphify_has_no_hardcoded_workspace(monkeypatch):
    """No literal names from the origin workspace's directory layout remain.

    "graphify-out" itself is deliberately not in this list: it is Graphify's
    own output-directory convention, used unconditionally for a project's own
    local artifacts (tests/test_api.py::test_graphify_direct_artifact_reports_its_manifest_status
    relies on this working with zero configuration) and carries no information
    about the workspace EOS was extracted from. What was removed is the
    *workspace*-relative discovery it used to sit inside: candidates rooted at
    a sibling "nexus" (or "test/nexus") directory, found by walking each
    scanned project's own ancestry.
    """
    source = (REPO / "ui" / "app" / "graphify.py").read_text(encoding="utf-8")
    for leaked in ("nexus", '"test"'):
        assert leaked not in source


def test_graphify_queue_has_no_coordinator_literals():
    source = (REPO / "ui" / "app" / "graphify_queue.py").read_text(encoding="utf-8")
    assert "ensure-graphify-projects.sh" not in source


def test_workspace_parent_segment_is_configurable():
    source = (REPO / "ui" / "app" / "workspace.py").read_text(encoding="utf-8")
    assert 'PARENT_SEGMENT = "parent-microservices"' not in source


def test_gitignore_covers_the_real_build_output():
    text = (REPO / ".gitignore").read_text(encoding="utf-8")
    assert "ui/static/" in text
