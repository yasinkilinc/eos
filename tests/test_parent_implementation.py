"""inspector.get_parent_implementation(): search the parent-tagged slice of
the symbol index and inline the real source."""
import subprocess
import sys
from pathlib import Path

from core import inspector

REPO = Path(__file__).resolve().parents[1]


def _run(args, **kw):
    return subprocess.run(
        [sys.executable, str(REPO / "core" / "eos.py"), *args],
        capture_output=True, text=True, **kw,
    )


def _scanned_fm_with_parent(tmp_path):
    proj = tmp_path / "acme-orders"
    proj.mkdir()
    (proj / "Overlay.java").write_text(
        "package com.acme.orders;\npublic class Overlay {\n    public void hi() {}\n}\n", encoding="utf-8",
    )
    parent = tmp_path / "upstream-orders"
    parent.mkdir()
    (parent / "Base.java").write_text(
        "package com.upstream.orders;\n\npublic class Base {\n    public void baseMethod() {\n        int x = 1;\n    }\n}\n",
        encoding="utf-8",
    )
    assert _run(["init", str(proj), "--link-parent", str(parent)]).returncode == 0
    assert _run(["scan", str(proj), "--full", "--with-parents"]).returncode == 0
    return proj


def test_get_parent_implementation_finds_a_matching_symbol_with_real_source(tmp_path):
    proj = _scanned_fm_with_parent(tmp_path)

    result = inspector.get_parent_implementation(proj, "baseMethod")

    assert result["symbol"] == "baseMethod"
    assert len(result["matches"]) == 1
    match = result["matches"][0]
    assert match["name"] == "baseMethod"
    assert "class Base" in match["source"] or "void baseMethod" in match["source"]


def test_get_parent_implementation_never_returns_the_projects_own_symbols(tmp_path):
    proj = _scanned_fm_with_parent(tmp_path)

    result = inspector.get_parent_implementation(proj, "hi")

    assert result["matches"] == []


def test_get_parent_implementation_is_empty_without_a_with_parents_scan(tmp_path):
    proj = tmp_path / "acme-orders"
    proj.mkdir()
    (proj / "Overlay.java").write_text("class Overlay {}\n", encoding="utf-8")
    assert _run(["init", str(proj)]).returncode == 0
    assert _run(["scan", str(proj), "--full"]).returncode == 0

    result = inspector.get_parent_implementation(proj, "anything")

    assert result["matches"] == []
