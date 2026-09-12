"""Tests for CacheStore's scope-aware pruning."""
from core.lib.cache_store import CacheStore


def test_remove_missing_prunes_ordinary_stale_entries(tmp_path):
    cache = CacheStore(tmp_path)
    cache._data = {"a.py": {}, "b.py": {}}

    cache.remove_missing({"a.py"})

    assert set(cache._data) == {"a.py"}


def test_remove_missing_leaves_parent_entries_alone_when_parents_were_not_scanned(tmp_path):
    # The real bug this guards: a plain `eos scan` (no --with-parents) must
    # not delete a previous --with-parents run's parent-tagged entries just
    # because they are absent from *this* run's present_paths.
    cache = CacheStore(tmp_path)
    cache._data = {"a.py": {}, "@parent:parent/Base.java": {}}

    cache.remove_missing({"a.py"})  # parent_labels_scanned defaults to None

    assert set(cache._data) == {"a.py", "@parent:parent/Base.java"}


def test_remove_missing_prunes_a_parent_entry_when_that_label_was_scanned(tmp_path):
    cache = CacheStore(tmp_path)
    cache._data = {"a.py": {}, "@parent:parent/Deleted.java": {}}

    cache.remove_missing({"a.py"}, parent_labels_scanned={"parent"})

    assert set(cache._data) == {"a.py"}


def test_remove_missing_leaves_an_unscanned_labels_entries_alone(tmp_path):
    # Two linked parents; only one was walked this run (e.g. --with-parents
    # was given but a link was temporarily unresolvable) -- the other
    # label's entries must survive.
    cache = CacheStore(tmp_path)
    cache._data = {"@parent:parent/A.java": {}, "@parent:sibling/B.java": {}}

    cache.remove_missing(set(), parent_labels_scanned={"parent"})

    assert set(cache._data) == {"@parent:sibling/B.java"}


def test_role_survives_a_cache_round_trip(tmp_path):
    """A file served from cache must classify the same as one just parsed.

    role is what carries @RestController into the artifacts. Without it in the
    cache, a full scan found 2 entry points in the fixture and the incremental
    scan that followed found 1 -- and the watcher only ever runs incremental
    scans, so the entry points would decay away in normal use.
    """
    from core.knowledge.semantic import FileSemantic
    from core.lib.cache_store import CacheStore

    store = CacheStore(tmp_path / "cache")
    sem = FileSemantic(path="Api.java", language="java", role="entry-point")
    store.store_semantic("Api.java", sem)
    store.save()

    reloaded = CacheStore(tmp_path / "cache").get_semantic("Api.java")

    assert reloaded is not None
    assert reloaded.role == "entry-point"


def test_a_cache_from_an_older_format_is_not_reused(tmp_path):
    """0.4.1 wrote no `role` and no import `level`.

    Reusing such an entry is not merely incomplete, it is wrong: a relative
    import cached without a level reads back as level 0, which resolves
    `from .. import x` as though it were `from . import x` and produces an edge
    to the wrong file, silently and forever, because the file never changes.
    """
    import json

    from core.lib.cache_store import CacheStore

    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True)
    (cache_dir / "file_cache.json").write_text(
        json.dumps(
            {
                "pkg/leaf.py": {
                    "mtime": 1.0,
                    "size": 10,
                    "hash": "deadbeef",
                    "semantic": {
                        "path": "pkg/leaf.py",
                        "language": "python",
                        "doc": None,
                        "imports": [{"module": "", "name": "helper", "is_relative": True,
                                     "alias": None, "line": 1}],
                        "exports": [],
                        "symbols": [],
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    store = CacheStore(cache_dir)

    assert store.get("pkg/leaf.py") is None, "a pre-0.5.0 cache entry was reused"
