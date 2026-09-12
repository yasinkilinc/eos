"""Tests for parent-project link configuration."""
from pathlib import Path

from core import links
from core.lib.config_io import ConfigIO


def _project(tmp_path, config=None):
    proj = tmp_path / "demo"
    (proj / ".eos").mkdir(parents=True)
    if config is not None:
        ConfigIO.write_toml(proj / ".eos" / "config.toml", config)
    return proj


def test_read_links_is_empty_without_config(tmp_path):
    assert links.read_links(_project(tmp_path)) == {}


def test_write_link_then_read_it_back(tmp_path):
    proj = _project(tmp_path)

    links.write_link(proj, "parent", "../../parent-microservices/upstream-orders", "parent", ref="v4.0.6-fm")
    result = links.read_links(proj)

    assert set(result) == {"parent"}
    link = result["parent"]
    assert link.path == "../../parent-microservices/upstream-orders"
    assert link.role == "parent"
    assert link.ref == "v4.0.6-fm"


def test_write_link_preserves_other_config_sections(tmp_path):
    proj = _project(tmp_path, {"project": {"name": "acme-orders"}, "scan": {"max_depth": 15}})

    links.write_link(proj, "parent", "../x", "parent")

    cfg = ConfigIO.read_toml(proj / ".eos" / "config.toml")
    assert cfg["project"]["name"] == "acme-orders"
    assert cfg["scan"]["max_depth"] == 15
    assert cfg["links"]["parent"]["path"] == "../x"


def test_resolve_link_path_is_relative_to_project_root(tmp_path):
    proj = _project(tmp_path)
    (tmp_path / "parent-microservices" / "upstream-orders").mkdir(parents=True)
    link = links.LinkedProject(path="../parent-microservices/upstream-orders", role="parent", ref=None)

    resolved = links.resolve_link_path(proj, link)

    assert resolved == (tmp_path / "parent-microservices" / "upstream-orders").resolve()


def test_resolve_link_path_accepts_an_absolute_path(tmp_path):
    proj = _project(tmp_path)
    absolute = tmp_path / "elsewhere"
    absolute.mkdir()
    link = links.LinkedProject(path=str(absolute), role="parent", ref=None)

    assert links.resolve_link_path(proj, link) == absolute.resolve()


def test_relative_link_parent_is_stored_relative(tmp_path, monkeypatch):
    """A committed .eos/config.toml has to work on someone else's machine.

    cmd_init resolved the argument against the cwd before storing it, so every
    developer on a team had to re-run init with their own absolute layout --
    even though resolve_link_path has always accepted a relative path.
    """
    workspace = tmp_path / "ws"
    (workspace / "vendor" / "lib").mkdir(parents=True)
    overlay = workspace / "overlay"
    overlay.mkdir()
    monkeypatch.chdir(workspace)

    from core import eos as eos_cli

    assert eos_cli.main(["init", "overlay", "--link-parent", "../vendor/lib"]) == 0

    config = (overlay / ".eos" / "config.toml").read_text(encoding="utf-8")
    assert str(tmp_path) not in config, f"an absolute path was stored:\n{config}"
    assert "../vendor/lib" in config

    configured = links.read_links(overlay)
    assert links.resolve_link_path(overlay, configured["parent"]) == (
        workspace / "vendor" / "lib"
    ).resolve()
