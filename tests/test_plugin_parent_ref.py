from pathlib import Path

from core.plugins.base import LanguagePlugin
from core.plugins.java.plugin import JavaPlugin
from core.plugins.python.plugin import PythonPlugin


def test_base_plugin_returns_none(tmp_path):
    assert PythonPlugin().parent_ref(tmp_path) is None


def test_java_plugin_reads_product_version(tmp_path):
    (tmp_path / "pom.xml").write_text(
        "<project><properties>"
        "<product.version>2.4.1</product.version>"
        "</properties></project>",
        encoding="utf-8",
    )
    assert JavaPlugin().parent_ref(tmp_path) == "2.4.1"


def test_java_plugin_without_pom(tmp_path):
    assert JavaPlugin().parent_ref(tmp_path) is None


def test_links_no_longer_exposes_pom_reader():
    from core import links
    assert not hasattr(links, "read_product_version_from_pom")


def test_the_cli_serves_parent_source_for_a_symbol(tmp_path):
    """`get_parent_implementation` existed only over MCP, and on an overlay
    codebase it is the tool most often needed: the class that decides the
    behaviour is in the parent under a different name. Two eval sessions
    stopped at exactly this wall and named it as where the work ended.
    """
    import subprocess
    import sys
    from pathlib import Path as _Path

    repo = _Path(__file__).resolve().parents[1]
    eos = [sys.executable, str(repo / "core" / "eos.py")]

    def run(args):
        return subprocess.run(eos + args, capture_output=True, text=True, encoding="utf-8")

    proj = tmp_path / "overlay"
    proj.mkdir()
    (proj / "Overlay.java").write_text(
        "package com.acme;\npublic class Overlay { public void hi() {} }\n", encoding="utf-8")
    parent = tmp_path / "upstream"
    parent.mkdir()
    (parent / "Engine.java").write_text(
        "package com.upstream;\npublic class Engine {\n"
        "    public void decide() { }\n}\n", encoding="utf-8")
    assert run(["init", str(proj), "--link-parent", str(parent), "--no-ai"]).returncode == 0
    assert run(["scan", str(proj), "--full", "--with-parents"]).returncode == 0

    done = run(["parent", str(proj), "Engine"])

    assert done.returncode == 0, done.stderr
    assert "Engine" in done.stdout, done.stdout
    assert "public void decide()" in done.stdout, (
        "the command must inline the real source, not just locate it\n" + done.stdout)


def test_the_cli_separates_a_missing_symbol_from_an_unscanned_parent(tmp_path):
    """An empty answer here has two causes with different fixes, and they
    produce the same empty list."""
    import subprocess
    import sys
    from pathlib import Path as _Path

    repo = _Path(__file__).resolve().parents[1]
    eos = [sys.executable, str(repo / "core" / "eos.py")]

    def run(args):
        return subprocess.run(eos + args, capture_output=True, text=True, encoding="utf-8")

    proj = tmp_path / "unlinked"
    proj.mkdir()
    (proj / "Solo.java").write_text("package a;\npublic class Solo {}\n", encoding="utf-8")
    assert run(["init", str(proj), "--no-ai"]).returncode == 0

    done = run(["parent", str(proj), "Engine"])

    assert done.returncode == 1
    assert "--link-parent" in done.stderr, done.stderr
