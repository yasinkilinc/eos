"""core.citations: references in an answer that are certainly wrong (2.x roadmap E3a).

Only what cannot be right is reported: an absolute path that does not exist, and
a line past the end of a file that does. A relative path found nowhere may be
another repository's and is left alone -- a false alarm here would stop a
subagent that did nothing wrong.
"""
from pathlib import Path

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


def test_paths_that_are_not_local_absolute_paths_are_not_flagged(tmp_path):
    """Branch review finding 2: a home-relative, placeholder, variable or drive
    path was read as a missing absolute path, and a container path cannot be
    judged here."""
    text = ("See ~/.claude/settings.json:12, <repo>/core/x.py:40, ${ROOT}/a.py:1, C:/x/y.py:3 "
            "and /app/src/main.py:88 from the container log.")
    assert citations.wrong(text, [tmp_path]) == []


def test_a_missing_path_is_reported_only_where_this_machine_could_have_it(tmp_path, monkeypatch):
    # Home counts as "this machine", so pin it: on a GitHub Linux runner home IS
    # /home/runner, and the path below would rightly be reported there.
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    text = "/usr/src/app/x.py:3 /home/runner/work/r/y.py:9 /opt/tool/z.py:1 /var/lib/app/w.py:2"
    assert citations.wrong(text, [tmp_path]) == []


# --- depth 1: quotes checked against the lines they cite (2.x roadmap E3) ----------------


def _source(tmp_path):
    source = tmp_path / "src" / "billing.py"
    source.parent.mkdir(parents=True)
    source.write_text("import os\n\n\ndef charge(amount):\n    total = amount * 1.2\n    return round(total, 2)\n",
                      encoding="utf-8")
    return source


def test_a_quote_is_checked_against_the_lines_it_cites(tmp_path):
    _source(tmp_path)
    right = "src/billing.py:5 `total = amount * 1.2` computes it"
    wrong_quote = "src/billing.py:5 `total = amount * 1.5` computes it"
    assert citations.misquoted(right, [tmp_path]) == []
    assert citations.misquoted(wrong_quote, [tmp_path]) == [
        "src/billing.py:5 -- `total = amount * 1.5` is not at those lines"]


def test_a_quote_near_the_cited_line_is_still_right(tmp_path):
    _source(tmp_path)
    assert citations.misquoted("src/billing.py:4 `return round(total, 2)`", [tmp_path]) == []


def test_a_fenced_block_after_a_reference_is_a_quote(tmp_path):
    _source(tmp_path)
    right = "In src/billing.py:4-6:\n```python\ndef charge(amount):\n    total = amount * 1.2\n```\n"
    wrong = "In src/billing.py:4-6:\n```python\ndef charge(amount, tax):\n```\n"
    assert citations.misquoted(right, [tmp_path]) == []
    assert citations.misquoted(wrong, [tmp_path]) == [
        "src/billing.py:4-6 -- `def charge(amount, tax):` is not at those lines"]


def test_prose_after_a_reference_in_backticks_is_not_a_quote(tmp_path):
    """Measured on 6,018 real answers: `path:5` states `x` read `states ` as a quote."""
    _source(tmp_path)
    assert citations.misquoted("`src/billing.py:5` states the rate and `charge` rounds it", [tmp_path]) == []
    assert citations.misquoted("`src/billing.py:5` `total = amount * 7.0`", [tmp_path]) == [
        "src/billing.py:5 -- `total = amount * 7.0` is not at those lines"]


def test_a_reference_or_a_path_after_a_reference_is_not_a_quote(tmp_path):
    _source(tmp_path)
    assert citations.misquoted("src/billing.py:5 `docs/other.md:12-40` and src/billing.py:4 `docs/{a,b}.md`",
                               [tmp_path]) == []


def test_short_or_distant_backticks_are_not_quotes(tmp_path):
    _source(tmp_path)
    assert citations.misquoted("src/billing.py:5 `x` and later on the line `something else entirely`",
                               [tmp_path]) == []


def test_a_planted_hallucinated_citation_is_caught(tmp_path):
    """The roadmap's acceptance for depth 1: one right, three planted wrong."""
    source = _source(tmp_path)
    answer = (f"Charging happens in {source}:4 `def charge(amount):`.\n"
              f"The rate is applied at {source}:5 `total = amount * 1.25`.\n"
              f"Rounding is in {source}:60.\n"
              f"Refunds are in {tmp_path}/src/refund.py:10.\n")
    problems = citations.check(answer, [tmp_path])
    assert len(problems) == 3
    assert any("1.25" in p for p in problems) and any(":60" in p for p in problems)
    assert any("refund.py:10 -- no such file" in p for p in problems)


def test_eos_cite_reads_an_answer_and_exits_non_zero_on_a_problem(tmp_path):
    import subprocess
    import sys

    _source(tmp_path)
    eos = [sys.executable, str(Path(__file__).resolve().parents[1] / "core" / "eos.py")]
    good = subprocess.run(eos + ["cite", str(tmp_path)], input="src/billing.py:5 `total = amount * 1.2`",
                          capture_output=True, text=True)
    assert good.returncode == 0 and "no problem" in good.stdout
    bad = subprocess.run(eos + ["cite", str(tmp_path)], input="src/billing.py:5 `total = amount * 9`",
                         capture_output=True, text=True)
    assert bad.returncode == 1 and "is not at those lines" in bad.stdout
