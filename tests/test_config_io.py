"""Tests for the hand-rolled TOML serializer."""
from core.lib.config_io import ConfigIO, _to_toml


def test_single_level_table_unaffected():
    # Regression guard: cmd_init's existing [project]/[scan] shape.
    text = _to_toml({"project": {"name": "acme-orders"}, "scan": {"max_depth": 15}})
    assert "[project]" in text
    assert 'name = "acme-orders"' in text
    assert "[scan]" in text
    assert "max_depth = 15" in text


def test_two_level_nested_table_gets_dotted_header():
    text = _to_toml({"links": {"parent": {"path": "../x", "role": "parent"}}})

    assert "[links.parent]" in text
    assert "[links]\n" not in text, "links must not appear as its own empty section"
    assert "[parent]" not in text, "parent must not leak out as a root-level table"


def test_two_level_nested_table_roundtrips_through_tomllib(tmp_path):
    data = {
        "project": {"name": "acme-orders"},
        "links": {"parent": {"path": "../../parent-microservices/upstream-orders", "role": "parent", "ref": "v4.0.6-fm"}},
    }
    path = tmp_path / "config.toml"

    ConfigIO.write_toml(path, data)
    loaded = ConfigIO.read_toml(path)

    assert loaded == data
