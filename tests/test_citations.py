"""core.citations: references in an answer that are certainly wrong (2.x roadmap E3a).

Only what cannot be right is reported: an absolute path that does not exist, and
a line past the end of a file that does. A relative path found nowhere may be
another repository's and is left alone -- a false alarm here would stop a
subagent that did nothing wrong.
"""
from core import citations


def test_references_are_read_in_three_shapes():
    text = ("See core/a.py:12 and core/b.py:3-9, the [config](docs/x.md#L40) and "
            "[range](docs/y.md#L5-L7); version 1.2.3 and 10:30 are not references.")
    assert citations.references(text) == [("core/a.py", 12), ("core/b.py", 9), ("docs/x.md", 40), ("docs/y.md", 7)]


def test_only_certain_mistakes_are_reported(tmp_path):
    source = tmp_path / "svc" / "Order.java"
    source.parent.mkdir()
    source.write_text("class Order {\n}\n", encoding="utf-8")
    text = (f"{source}:2 is fine, {source}:40 is past the end, svc/Order.java:1 is fine, "
            f"{tmp_path}/gone/Missing.java:3 does not exist, other/repo/File.kt:9 is unknown here, "
            f"{tmp_path}/.../Order.java:99 is shorthand.")
    problems = citations.wrong(text, [tmp_path])
    assert problems == [f"{source}:40 -- the file has 2 lines",
                        f"{tmp_path}/gone/Missing.java:3 -- no such file"]
