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
