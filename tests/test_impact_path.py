"""impact() must find a node whose path begins with a dot.

`_node_for_path` normalized with `relative_path.lstrip("./")`, which strips
every leading "." and "/" *character* rather than the "./" prefix. So
".github/workflows/ci.py" became "github/workflows/ci.py", matched no node, and
impact() raised `No graph node found for file`. Verified against a real service:
every dot-prefixed path in the graph was unreachable.
"""
import shutil
from pathlib import Path

import pytest

from core import inspector
from core.generators.json.graph_index import GraphIndexGenerator
from core.knowledge.builder import KnowledgeBuilder
from core.lib.cache_store import CacheStore
from core.scanner import Scanner

FIXTURE = Path(__file__).parent / "fixtures" / "polyglot_project"


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "polyglot"
    shutil.copytree(FIXTURE, root)
    return root


def _build_graph(root: Path) -> None:
    project = Scanner(root, CacheStore(root / ".eos" / "data" / "cache")).scan(full=True)
    graph = KnowledgeBuilder().build(project, root=root)
    brain = root / ".eos" / "data" / "brain"
    brain.mkdir(parents=True, exist_ok=True)
    GraphIndexGenerator(graph).generate(brain / "graph.json")


def test_impact_finds_a_node_whose_path_starts_with_a_dot(tmp_path):
    root = _project(tmp_path)
    dotted = root / ".config" / "helper.py"
    dotted.parent.mkdir(parents=True, exist_ok=True)
    dotted.write_text("def help():\n    return 1\n", encoding="utf-8")
    _build_graph(root)

    result = inspector.impact(root, ".config/helper.py")

    assert result["file"] == ".config/helper.py", result


def test_impact_still_accepts_an_explicit_dot_slash_prefix(tmp_path):
    root = _project(tmp_path)
    _build_graph(root)

    result = inspector.impact(root, "./svc/src/main/java/com/example/api/GreetService.java")

    assert result["file"].endswith("GreetService.java"), result


def test_impact_still_refuses_a_path_that_is_not_in_the_graph(tmp_path):
    root = _project(tmp_path)
    _build_graph(root)

    with pytest.raises(ValueError, match="No graph node found"):
        inspector.impact(root, "does/not/exist.py")
