"""Index extensions: the contract in core/extensions.py.

A project declares extensions in `.eos/config.toml`; they add tables to the one
index and fill them. The behaviour worth pinning down is the failure split --
a configuration mistake stops the build loudly, a broken extension costs only
its own tables -- and that an extension's own file is a source of the index, so
editing it makes the index stale.
"""
import sqlite3
import textwrap
from pathlib import Path

import pytest

from core import extensions, index, notes

EXTENSION = """
SCHEMA = "CREATE TABLE widget (name TEXT PRIMARY KEY, size INTEGER NOT NULL);"
COUNTS = {"widgets": "SELECT COUNT(*) FROM widget"}


def sources(root, notes_dir):
    for path in sorted((root / "widgets").glob("*.txt")):
        yield ("widget", path.name, path.read_text(encoding="utf-8").strip())


def load(build):
    build.meta["widget_dir"] = str(build.root / "widgets")
    for path in sorted((build.root / "widgets").glob("*.txt")):
        name = path.stem
        build.conn.execute("INSERT INTO widget(name, size) VALUES (?, ?)",
                           (name, len(path.read_text(encoding="utf-8").strip())))
        build.search("widget", name, name, path.read_text(encoding="utf-8"))
"""


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text), encoding="utf-8")
    return path


def _project(tmp_path: Path, extension_body: str | None = EXTENSION, declared: str | None = None) -> Path:
    root = tmp_path / "project"
    (root / ".eos").mkdir(parents=True)
    if extension_body is not None:
        _write(root / "ext" / "widgets_ext.py", extension_body)
    _write(root / "widgets" / "hinge.txt", "a brass hinge")
    _write(root / "widgets" / "spring.txt", "a coiled spring")
    _write(root / ".eos" / "config.toml",
           f'[index]\nextensions = ["{declared if declared is not None else "ext/widgets_ext.py"}"]\n')
    _write(notes.notes_dir(root) / "20260901-first.md",
           "---\nkind: finding\ntitle: A first note\ncreated: 2026-09-01\n---\n\nBody.\n")
    return root


def _rows(db: Path, sql: str) -> list[tuple]:
    conn = sqlite3.connect(db)
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


def test_an_extension_adds_its_tables_counts_search_rows_and_meta(tmp_path):
    root = _project(tmp_path)

    result = index.build(root)

    assert _rows(result.path, "SELECT name, size FROM widget ORDER BY name") == [("hinge", 13), ("spring", 15)]
    assert result.counts["widgets"] == 2
    # The core counts are still there: an extension adds, it does not replace.
    assert result.counts["notes"] == 1
    _, found = index.search(result.path, "coiled")
    assert [(row[0], row[1]) for row in found] == [("widget", "spring")]
    meta = dict(_rows(result.path, "SELECT key, value FROM meta"))
    assert meta["widget_dir"].endswith("widgets")
    # Provenance: which extension wrote those rows, at which content digest.
    assert meta["extensions"].startswith("widgets_ext@")


def test_an_extensions_own_sources_make_the_index_stale(tmp_path):
    root = _project(tmp_path)
    index.build(root)

    assert index.refresh(root) is None
    _write(root / "widgets" / "hinge.txt", "a brass hinge, replaced")

    assert index.refresh(root) is not None
    assert _rows(index.db_path(root), "SELECT size FROM widget WHERE name = 'hinge'") == [(23,)]


def test_editing_the_extension_itself_makes_the_index_stale(tmp_path):
    """Otherwise a change to what is indexed lands only on whoever next deletes
    the database by hand."""
    root = _project(tmp_path)
    index.build(root)
    assert index.refresh(root) is None

    _write(root / "ext" / "widgets_ext.py", EXTENSION.replace("len(", "1 + len("))

    assert index.refresh(root) is not None
    assert _rows(index.db_path(root), "SELECT size FROM widget ORDER BY name") == [(14,), (16,)]


def test_a_configured_extension_that_is_missing_stops_the_build(tmp_path):
    """Loudly: silently indexing less than the project asked for is the failure
    this is here to prevent."""
    root = _project(tmp_path, declared="ext/typo.py")

    with pytest.raises(index.IndexBuildError, match="ext/typo.py"):
        index.build(root)
    assert not index.db_path(root).exists()


def test_an_extension_that_cannot_be_imported_stops_the_build(tmp_path):
    root = _project(tmp_path, extension_body="def load(build:\n    return 1\n")

    with pytest.raises(index.IndexBuildError, match="failed to import"):
        index.build(root)


def test_a_non_list_extensions_value_stops_the_build(tmp_path):
    root = tmp_path / "project"
    (root / ".eos").mkdir(parents=True)
    _write(root / ".eos" / "config.toml", '[index]\nextensions = "ext/widgets_ext.py"\n')

    with pytest.raises(index.IndexBuildError, match="must be a list"):
        index.build(root)


def test_an_extension_that_raises_costs_only_its_own_tables(tmp_path):
    """The rest of the index -- notes, brain, history -- is worth more than the
    extension's rows, and a build issue says exactly what was lost."""
    root = _project(tmp_path, extension_body=EXTENSION + """
def load(build):  # noqa: F811 -- replaces the one above
    raise RuntimeError("the snapshot was half-written")
""")

    result = index.build(root)

    assert _rows(result.path, "SELECT COUNT(*) FROM widget") == [(0,)]
    assert _rows(result.path, "SELECT COUNT(*) FROM note") == [(1,)]
    issues = _rows(result.path, "SELECT ref, problem FROM build_issue WHERE source = 'extension'")
    assert issues == [("widgets_ext", "load failed (the snapshot was half-written); its tables are empty")]


def test_an_extension_with_a_broken_schema_is_skipped_not_half_created(tmp_path):
    root = _project(tmp_path, extension_body=EXTENSION.replace(
        'SCHEMA = "CREATE TABLE widget (name TEXT PRIMARY KEY, size INTEGER NOT NULL);"',
        'SCHEMA = "CREATE TABLE widget (this is not sql);"'))

    result = index.build(root)

    assert _rows(result.path, "SELECT COUNT(*) FROM note") == [(1,)]
    assert _rows(result.path, "SELECT name FROM sqlite_master WHERE name = 'widget'") == []
    problems = [row[1] for row in _rows(result.path, "SELECT ref, problem FROM build_issue WHERE source = 'extension'")]
    assert problems and "extension skipped" in problems[0]
    # Its count query cannot run either, and that is reported rather than raised.
    assert "widgets" not in result.counts


def test_a_project_with_no_extensions_configured_is_untouched(tmp_path):
    root = tmp_path / "project"
    (root / ".eos").mkdir(parents=True)
    _write(notes.notes_dir(root) / "20260901-first.md",
           "---\nkind: finding\ntitle: A first note\ncreated: 2026-09-01\n---\n\nBody.\n")

    result = index.build(root)

    assert result.counts["notes"] == 1
    assert dict(_rows(result.path, "SELECT key, value FROM meta")).get("extensions") is None
    assert extensions.load_all(root) == []


QUESTIONING = EXTENSION + '''
QUESTIONS = {
    "big-widgets": {
        "help": "Widgets whose description is longer than a given size.",
        "sql": "SELECT name, size FROM widget WHERE size > ? ORDER BY name",
    },
    "all-widgets": {
        "help": "Every widget.",
        "sql": "SELECT name FROM widget ORDER BY name",
    },
}
'''


def test_an_extension_can_ship_a_named_question(tmp_path):
    """The alternative was a SQL snippet in a document. The useful questions on
    a project-shaped artifact are joins between an extension's tables and the
    core facts, and nobody types a four-way join twice."""
    from core import inspector

    root = _project(tmp_path, extension_body=QUESTIONING)
    index.build(root)

    available = inspector.questions(root)
    assert set(available) == {"big-widgets", "all-widgets"}, available
    assert available["big-widgets"]["extension"] == "widgets_ext"

    columns, rows = inspector.ask(root, "all-widgets")
    assert columns == ["name"] and [row[0] for row in rows] == ["hinge", "spring"]


def test_a_question_that_takes_an_argument_gets_one(tmp_path):
    from core import inspector

    root = _project(tmp_path, extension_body=QUESTIONING)
    index.build(root)

    _, rows = inspector.ask(root, "big-widgets", "14")

    assert [row[0] for row in rows] == ["spring"], rows


def test_a_question_that_needs_an_argument_says_so(tmp_path):
    from core import inspector

    root = _project(tmp_path, extension_body=QUESTIONING)
    index.build(root)

    with pytest.raises(ValueError, match="needs an argument"):
        inspector.ask(root, "big-widgets")


def test_an_unknown_question_lists_the_known_ones(tmp_path):
    from core import inspector

    root = _project(tmp_path, extension_body=QUESTIONING)
    index.build(root)

    with pytest.raises(ValueError, match="all-widgets"):
        inspector.ask(root, "no-such-question")


def test_a_question_cannot_write(tmp_path):
    """Questions run on the same read-only connection every other reader uses."""
    from core import inspector

    body = EXTENSION + '''
QUESTIONS = {"bad": {"help": "", "sql": "DELETE FROM widget"}}
'''
    root = _project(tmp_path, extension_body=body)
    index.build(root)

    with pytest.raises(sqlite3.DatabaseError):
        inspector.ask(root, "bad")
    assert _rows(index.db_path(root), "SELECT COUNT(*) FROM widget") == [(2,)]


def test_a_malformed_question_is_refused_by_name(tmp_path):
    from core import inspector

    body = EXTENSION + '''
QUESTIONS = {"broken": {"help": "no sql here"}}
'''
    root = _project(tmp_path, extension_body=body)

    with pytest.raises(extensions.ExtensionError, match="broken"):
        inspector.questions(root)
