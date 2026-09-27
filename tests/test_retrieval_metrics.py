"""`eos note eval` metrics (2.x roadmap F6): MRR@k, nDCG@k, zero-hit queries in the
denominator, latency percentiles, and misses worst first."""
import math
from pathlib import Path
from types import SimpleNamespace

from core import notes, retrieval


def _with_search(monkeypatch, ranking: dict):
    corpus = {name for names in ranking.values() for name in names} | {"a.md", "b.md", "c.md"}
    monkeypatch.setattr(notes, "load_notes", lambda root: [SimpleNamespace(path=Path(n)) for n in corpus])
    monkeypatch.setattr(notes, "search_notes",
                        lambda root, query, limit: [SimpleNamespace(path=Path(n)) for n in ranking[query][:limit]])


def test_the_report_carries_mrr_ndcg_zero_hits_and_latency(monkeypatch):
    _with_search(monkeypatch, {"first": ["a.md"], "third": ["x.md", "y.md", "b.md"], "nothing": []})
    entries = [retrieval.GoldenEntry("first", "a.md", 1), retrieval.GoldenEntry("third", "b.md", 2),
               retrieval.GoldenEntry("nothing", "c.md", 3)]
    report = retrieval.evaluate(".", entries, depth=10)
    assert report["queries"] == 3 and report["zero_hit"] == 1
    assert report["mrr"] == round((1 + 1 / 3) / 3, 4)
    assert report["ndcg"] == round((1 + 1 / math.log2(4)) / 3, 4)
    assert set(report["latency_ms"]) == {"p50", "p90"}


def test_misses_are_listed_worst_first(monkeypatch):
    _with_search(monkeypatch, {"second": ["x.md", "a.md"], "fifth": ["1", "2", "3", "4", "b.md"], "gone": []})
    entries = [retrieval.GoldenEntry("second", "a.md", 1), retrieval.GoldenEntry("fifth", "b.md", 2),
               retrieval.GoldenEntry("gone", "c.md", 3)]
    text = retrieval.render(retrieval.evaluate(".", entries, depth=10))
    assert text.index("gone") < text.index("fifth") < text.index("second")
    assert "MRR@10" in text and "nDCG@10" in text and "zero-hit 1" in text


def test_a_golden_line_may_accept_several_notes(monkeypatch, tmp_path):
    """Two notes can answer one question equally (a procedure written from a
    section later): `a.md|b.md` accepts either, ranked at the first found."""
    golden = tmp_path / "g.tsv"
    golden.write_text("the question\ta.md|b.md\n", encoding="utf-8")
    [entry] = retrieval.parse_golden(golden)
    _with_search(monkeypatch, {"the question": ["x.md", "b.md", "a.md"]})
    report = retrieval.evaluate(".", [entry])
    assert report["results"][0]["rank"] == 2 and report["broken"] == []
    _with_search(monkeypatch, {"the question": ["x.md"]})
    monkeypatch.setattr(notes, "load_notes", lambda root: [SimpleNamespace(path=Path("x.md"))])
    assert retrieval.evaluate(".", [entry])["broken"]      # neither note exists: broken, not a miss


def test_alternates_may_be_written_with_spaces(monkeypatch, tmp_path):
    golden = tmp_path / "g.tsv"
    golden.write_text("the question\ta.md | b.md\n", encoding="utf-8")
    [entry] = retrieval.parse_golden(golden)
    _with_search(monkeypatch, {"the question": ["b.md"]})
    report = retrieval.evaluate(".", [entry])
    assert report["broken"] == [] and report["results"][0]["rank"] == 1
