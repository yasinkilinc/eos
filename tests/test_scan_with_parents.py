"""Multi-root scanning: a linked parent's source is indexed alongside the
project's own, tagged so the two are distinguishable but both searchable."""
import subprocess
import sys
from pathlib import Path

from core.lib.cache_store import CacheStore
from core.links import PARENT_PREFIX

REPO = Path(__file__).resolve().parents[1]


def _run(args, **kw):
    return subprocess.run(
        [sys.executable, str(REPO / "core" / "eos.py"), *args],
        capture_output=True, text=True, **kw,
    )


def _fm_with_linked_parent(tmp_path):
    proj = tmp_path / "acme-orders"
    proj.mkdir()
    (proj / "Overlay.java").write_text(
        "package com.acme.orders;\npublic class Overlay {\n    public void hi() {}\n}\n", encoding="utf-8",
    )
    parent = tmp_path / "upstream-orders"
    parent.mkdir()
    (parent / "Base.java").write_text(
        # The Java plugin's method regex requires an access modifier
        # (core/plugins/java/plugin.py:23-26) -- a bare "void baseMethod()"
        # is invisible to it, unrelated to anything this task changes.
        "package com.upstream.orders;\npublic class Base {\n    public void baseMethod() {}\n}\n", encoding="utf-8",
    )
    assert _run(["init", str(proj), "--link-parent", str(parent)]).returncode == 0
    return proj, parent


def test_scan_with_parents_indexes_parent_files_under_a_tagged_key(tmp_path):
    proj, parent = _fm_with_linked_parent(tmp_path)

    r = _run(["scan", str(proj), "--full", "--with-parents"])

    assert r.returncode == 0, r.stderr
    cache = CacheStore(proj / ".eos" / "data" / "cache")
    keys = set(cache._data)
    assert "Overlay.java" in keys
    assert f"{PARENT_PREFIX}parent/Base.java" in keys


def test_plain_scan_never_touches_parent_files(tmp_path):
    proj, parent = _fm_with_linked_parent(tmp_path)
    assert _run(["scan", str(proj), "--full"]).returncode == 0

    cache = CacheStore(proj / ".eos" / "data" / "cache")
    assert not any(k.startswith(PARENT_PREFIX) for k in cache._data)


def test_scan_with_parents_then_plain_scan_keeps_the_parent_index(tmp_path):
    # The bug Task 5 fixed, proven end-to-end: forgetting --with-parents on a
    # later scan must not wipe out an earlier one's parent data.
    proj, parent = _fm_with_linked_parent(tmp_path)
    assert _run(["scan", str(proj), "--full", "--with-parents"]).returncode == 0

    r = _run(["scan", str(proj)])  # no --with-parents this time

    assert r.returncode == 0, r.stderr
    cache = CacheStore(proj / ".eos" / "data" / "cache")
    assert f"{PARENT_PREFIX}parent/Base.java" in cache._data


def test_find_symbol_sees_parent_symbols_after_a_with_parents_scan(tmp_path):
    proj, parent = _fm_with_linked_parent(tmp_path)
    assert _run(["scan", str(proj), "--full", "--with-parents"]).returncode == 0

    from core import inspector
    hits = inspector.find_symbols(proj, "baseMethod")

    assert any(h["path"] == f"{PARENT_PREFIX}parent/Base.java" for h in hits)


def test_brain_listings_exclude_parent_files_while_graph_keeps_them():
    """Parent files are lookup material, not inventory.

    After parent linking shipped they flooded the brain's enumerations --
    4,545 of acme-catalog's 4,751 component entries were parent
    paths -- pushing the project's own files out of sight. They stay in
    graph.json, which is what get_graph and impact_analysis read and where
    the inheritance edges actually matter.
    """
    import json
    import tempfile

    from core.generators.json.graph_index import GraphIndexGenerator
    from core.generators.markdown.brain import BrainGenerator
    from core.knowledge.model import Dependency, KnowledgeGraph, KnowledgeNode

    parent_file = f"{PARENT_PREFIX}parent/base/Base.java"
    graph = KnowledgeGraph(languages=["java"])
    graph.add_node(KnowledgeNode(
        id="file:src/Overlay.java", type="component", label="Overlay",
        path="src/Overlay.java", language="java",
    ))
    graph.add_node(KnowledgeNode(id="folder:src", type="folder", label="src", path="src"))
    graph.add_node(KnowledgeNode(
        id=f"file:{parent_file}", type="component", label="Base",
        path=parent_file, language="java",
    ))
    graph.add_node(KnowledgeNode(
        id=f"folder:{PARENT_PREFIX}parent/base", type="folder", label="base",
        path=f"{PARENT_PREFIX}parent/base",
    ))
    graph.add_edge(Dependency(
        source_id="file:src/Overlay.java", target_id=f"file:{parent_file}", kind="import",
    ))
    graph.entry_points = ["file:src/Overlay.java"]

    with tempfile.TemporaryDirectory() as tmp:
        brain = Path(tmp) / "brain"
        BrainGenerator(graph).generate(brain)
        GraphIndexGenerator(graph).generate(brain / "graph.json")

        # The enumerations live in these two documents; EntryPoints.md is a
        # short targeted list, not an inventory, and is deliberately untouched.
        listings = "\n".join(
            (brain / name).read_text(encoding="utf-8") for name in ("_index.md", "Architecture.md")
        )
        assert "src/Overlay.java" in listings, "the project's own file must still be listed"
        assert PARENT_PREFIX not in listings, "parent files must not appear in brain listings"

        indexed = json.loads((brain / "graph.json").read_text(encoding="utf-8"))

    assert {n["path"] for n in indexed["nodes"]} == {
        "src/Overlay.java", "src", parent_file, f"{PARENT_PREFIX}parent/base",
    }
    assert [e["target"] for e in indexed["edges"]] == [f"file:{parent_file}"], (
        "the edge into the parent must survive for impact_analysis"
    )


def test_ai_summary_does_not_count_the_linked_parent_as_the_project(tmp_path):
    """A 15-file overlay described itself as "1891 files ... 1891 java", every
    hub component belonging to the platform it extends. The parent is indexed
    so it can be searched, not so it can be mistaken for the project."""
    proj, parent = _fm_with_linked_parent(tmp_path)
    for i in range(4):
        (parent / f"Extra{i}.java").write_text(
            f"package com.upstream.orders;\npublic class Extra{i} {{}}\n", encoding="utf-8"
        )

    assert _run(["scan", str(proj), "--full", "--with-parents"]).returncode == 0

    summary = (proj / ".eos" / "data" / "brain" / "AI_SUMMARY.md").read_text(encoding="utf-8")
    scale = next(line for line in summary.splitlines() if " files across " in line)

    assert scale.startswith("1 files across"), f"parent counted as the project: {scale}"
    assert "Plus 5 file(s) indexed from linked parent" in summary


def test_brain_does_not_claim_the_parents_entry_points_as_its_own(tmp_path):
    """Architecture.md reported "Files: 4" and "Entry points: 14" in one block,
    all fourteen belonging to the linked platform."""
    proj, parent = _fm_with_linked_parent(tmp_path)
    (parent / "Api.java").write_text(
        "package com.upstream.orders;\n\n@RestController\npublic class Api {\n"
        "    public String hi() { return \"x\"; }\n}\n",
        encoding="utf-8",
    )

    assert _run(["scan", str(proj), "--full", "--with-parents"]).returncode == 0

    brain = proj / ".eos" / "data" / "brain"
    architecture = (brain / "Architecture.md").read_text(encoding="utf-8")
    entry_points = (brain / "EntryPoints.md").read_text(encoding="utf-8")

    assert "- **Entry points:** 0" in architecture, architecture
    assert PARENT_PREFIX not in entry_points, entry_points


def test_a_plain_scan_keeps_the_facts_the_parent_contributed(tmp_path):
    """Keeping the parent in the cache was never the point -- keeping it in
    the answers was.

    The cache has survived a plain scan since Task 5, but everything a reader
    sees is built from the ProjectSemantic the scan returns, and that was
    built from the project's own files alone. Measured on one service: `eos
    rules` answered 403 behaviour codes, a plain scan ran, and the same
    command answered 70, confidently and with no warning. Most of the refusals
    in an overlay codebase are the parent's.
    """
    import json

    proj, parent = _fm_with_linked_parent(tmp_path)
    (parent / "Rules.java").write_text(
        "package com.upstream.orders;\npublic class Rules {\n"
        '  public void check() { throw new ValidationException("PARENT_ONLY_CODE", "no"); }\n}\n',
        encoding="utf-8")
    assert _run(["scan", str(proj), "--full", "--with-parents"]).returncode == 0
    before = json.loads(_run(["rules", str(proj), "--format", "json"]).stdout)
    assert [c["code"] for c in before["codes"]] == ["PARENT_ONLY_CODE"], before

    assert _run(["scan", str(proj)]).returncode == 0  # no --with-parents

    after = json.loads(_run(["rules", str(proj), "--format", "json"]).stdout)
    assert [c["code"] for c in after["codes"]] == ["PARENT_ONLY_CODE"], (
        "a plain scan dropped the parent's rules; the answer silently halved")
