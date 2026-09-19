"""impact() answered from the index, not by re-parsing graph.json.

Measured on a real 255-file service with a 2,397-file linked parent: graph.json
is 25,466,819 bytes holding 72,139 edges, and every impact() call re-parsed all
of it and then linear-scanned 6,040 nodes -- 0.14 s per call, against 0.0013 s
from the index. The `edge_by_dst(dst, kind)` index the schema has always
carried was never used by anything.

Depth exists because one hop is not the question people ask. UNION rather than
UNION ALL because Java packages import each other in cycles.
"""
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from core import index, inspector

REPO = Path(__file__).resolve().parents[1]
EOS = [sys.executable, str(REPO / "core" / "eos.py")]
FIXTURE = Path(__file__).parent / "fixtures" / "polyglot_project"


def _run(args, **kw):
    return subprocess.run(EOS + args, capture_output=True, text=True, encoding="utf-8", **kw)


def _scanned(tmp_path: Path) -> Path:
    root = tmp_path / "polyglot"
    shutil.copytree(FIXTURE, root)
    assert _run(["init", str(root), "--no-ai"]).returncode == 0
    done = _run(["scan", str(root), "--full"])
    assert done.returncode == 0, done.stderr
    return root


def _chain(tmp_path: Path) -> Path:
    """a -> b -> c, so depth 1 and depth 2 give different answers."""
    root = tmp_path / "chain"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "a.py").write_text("import pkg.b\n", encoding="utf-8")
    (root / "pkg" / "b.py").write_text("import pkg.c\n", encoding="utf-8")
    (root / "pkg" / "c.py").write_text("VALUE = 1\n", encoding="utf-8")
    assert _run(["init", str(root), "--no-ai"]).returncode == 0
    assert _run(["scan", str(root), "--full"]).returncode == 0
    return root


def test_impact_is_answered_from_the_index_when_one_exists(tmp_path):
    root = _scanned(tmp_path)

    answer = inspector.impact(root, "web/src/index.ts")

    assert answer["source"] == "index", answer
    assert [d["path"] for d in answer["dependencies"]], answer
    assert all("depth" in d for d in answer["dependencies"]), answer


def test_impact_falls_back_to_graph_json_without_an_index(tmp_path):
    root = _scanned(tmp_path)
    index.db_path(root).unlink()

    answer = inspector.impact(root, "web/src/index.ts")

    assert answer["source"] == "graph.json", answer
    assert [d["path"] for d in answer["dependencies"]], (
        f"the fallback must still answer: {answer}"
    )


def test_depth_two_reaches_a_transitive_dependent(tmp_path):
    root = _chain(tmp_path)

    one = inspector.impact(root, "pkg/c.py", depth=1)
    two = inspector.impact(root, "pkg/c.py", depth=2)

    direct = {d["path"] for d in one["dependents"]}
    reached = {d["path"] for d in two["dependents"]}
    assert direct == {"pkg/b.py"}, one
    assert reached == {"pkg/b.py", "pkg/a.py"}, two
    assert {d["depth"] for d in two["dependents"]} == {1, 2}, (
        f"a transitive hop must be distinguishable from a direct one: {two['dependents']}"
    )


def test_impact_terminates_on_an_import_cycle(tmp_path):
    root = tmp_path / "cycle"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "a.py").write_text("import pkg.b\n", encoding="utf-8")
    (root / "pkg" / "b.py").write_text("import pkg.a\n", encoding="utf-8")
    assert _run(["init", str(root), "--no-ai"]).returncode == 0
    assert _run(["scan", str(root), "--full"]).returncode == 0

    answer = inspector.impact(root, "pkg/a.py", depth=5)

    assert {d["path"] for d in answer["dependencies"]} == {"pkg/b.py"}, answer


def test_depth_is_bounded_rather_than_refused(tmp_path):
    root = _chain(tmp_path)

    answer = inspector.impact(root, "pkg/c.py", depth=99)

    assert answer["depth"] == index.MAX_IMPACT_DEPTH, answer


def test_impact_reports_whether_the_index_is_behind_the_scan(tmp_path):
    root = _scanned(tmp_path)
    assert inspector.impact(root, "web/src/index.ts")["stale"] is False

    graph = root / ".eos" / "data" / "brain" / "graph.json"
    # Two seconds past the index's whole-second built_at, so this is a real
    # rewrite rather than the sub-second gap every fresh scan leaves.
    future = time.time() + 2
    os.utime(graph, (future, future))

    answer = inspector.impact(root, "web/src/index.ts")

    assert answer["stale"] is True, (
        f"a rewritten graph must make the index look behind it: {answer}"
    )
    assert answer["dependencies"], "a stale index still answers; it just says so"


def test_cli_impact_accepts_depth_and_include(tmp_path):
    root = _scanned(tmp_path)

    done = _run(["impact", str(root), "web/src/index.ts", "--depth", "2",
                 "--include", "facts", "--include", "coverage"])

    assert done.returncode == 0, done.stderr
    answer = json.loads(done.stdout)
    assert answer["facts"], answer
    assert answer["coverage"], answer
    assert answer["facts"][0]["detector"], answer["facts"][0]


def test_why_refuses_an_index_that_predates_provenance(tmp_path):
    """The failure an agent would otherwise see is a raw 'no such table: fact'."""
    root = _scanned(tmp_path)
    import sqlite3
    conn = sqlite3.connect(index.db_path(root))
    try:
        conn.execute("DROP TABLE fact")
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(ValueError, match="predates provenance"):
        inspector.why(root, "web/src/index.ts")
