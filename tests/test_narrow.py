"""Deterministic narrowing of a long text to the lines a task's terms hit (2.x roadmap C3)."""
import subprocess
import sys
from pathlib import Path

from core import inspector
from core.context import narrow

REPO = Path(__file__).resolve().parents[1]


def _numbered(count: int, every: int = 0, word: str = "") -> str:
    lines = []
    for i in range(1, count + 1):
        extra = f" {word}" if every and i % every == 0 else ""
        lines.append(f"line {i} ordinary filler text{extra}")
    return "\n".join(lines) + "\n"


def test_nothing_matches_returns_none():
    assert narrow.narrow_by_terms(_numbered(50), ["absent"]) is None
    assert narrow.narrow_by_terms(_numbered(50), []) is None


def test_a_hit_comes_back_as_whole_lines_with_its_numbers():
    text = _numbered(400).replace("line 200 ordinary", "line 200 the retry budget")
    result = narrow.narrow_by_terms(text, ["retry"])
    assert result is not None
    (first, last), = result.spans
    assert first < 200 < last
    assert "line 200 the retry budget" in result.text
    assert f"@@ L{first}-L{last}" in result.text
    body = [line for line in result.text.splitlines() if not line.startswith("@@")]
    # whole lines only, each one a real line of the source
    assert all(line in text.splitlines() for line in body)
    # bounded context: about 600 characters each side, not the file
    assert len(result.text) < 1600


def test_terms_match_whole_words_only():
    text = "the catalog is empty\nnothing here\n" * 3
    assert narrow.narrow_by_terms(text, ["log"]) is None


def test_a_term_on_most_lines_does_not_decide_the_spans():
    text = _numbered(300, every=1, word="service")
    text = text.replace("line 150 ordinary", "line 150 timeout ordinary")
    result = narrow.narrow_by_terms(text, ["service", "timeout"])
    assert result is not None
    assert result.terms == ["timeout"]
    assert len(result.spans) == 1


def test_only_ubiquitous_terms_still_narrow_rather_than_give_up():
    text = _numbered(30, every=1, word="service")
    result = narrow.narrow_by_terms(text, ["service"], limit=400)
    assert result is not None
    assert len(result.text) <= 400


def test_a_table_with_a_hit_is_never_cut():
    table = ["| name | limit |", "|---|---|"] + [f"| row{i} | {i} |" for i in range(60)]
    table[40] = "| quota | 7 |"
    text = _numbered(100) + "\n".join(table) + "\n" + _numbered(100)
    result = narrow.narrow_by_terms(text, ["quota"], limit=16000)
    assert result is not None
    for row in table:
        assert row in result.text


def test_the_limit_holds_and_the_best_spans_win():
    lines = _numbered(2000).splitlines()
    lines[100] += " alpha"
    lines[900] += " alpha beta"
    lines[1800] += " alpha"
    result = narrow.narrow_by_terms("\n".join(lines) + "\n", ["alpha", "beta"], limit=1300)
    assert result is not None
    assert len(result.text) <= 1300
    assert "alpha beta" in result.text          # two terms outrank one
    assert result.omitted == 2


def test_spans_are_emitted_in_file_order_and_merge_when_they_touch():
    lines = _numbered(400).splitlines()
    lines[100] += " gamma"
    lines[104] += " gamma"
    lines[300] += " gamma"
    result = narrow.narrow_by_terms("\n".join(lines) + "\n", ["gamma"], limit=16000)
    assert len(result.spans) == 2
    assert result.spans[0][1] < result.spans[1][0]


def test_a_stem_is_found_when_no_whole_word_is():
    text = _numbered(100).replace("line 50 ordinary", "line 50 retrying ordinary")
    result = narrow.narrow_by_terms(text, ["retry"])
    assert result is not None
    assert "retrying" in result.text


def test_a_single_long_line_is_cut_to_the_limit():
    text = "short\n" + "needle " + "x" * 5000 + "\nshort\n"
    result = narrow.narrow_by_terms(text, ["needle"], limit=500)
    assert result is not None
    assert len(result.text) <= 500


def _project(tmp_path: Path, name: str, body: str) -> Path:
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / name).write_text(body, encoding="utf-8")
    for args in (["init", str(proj)], ["scan", str(proj), "--full"]):
        r = subprocess.run([sys.executable, str(REPO / "core" / "eos.py"), *args],
                           capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
    return proj


def _big_module() -> str:
    parts = ["import os\n"]
    for i in range(300):
        parts.append(f"def handler_{i}(event):\n    return event.get('field_{i}')\n\n")
    parts[150] = "def refund_payment(order):\n    return order.refund()\n\n"
    return "".join(parts)


def test_get_file_without_terms_is_unchanged(tmp_path):
    proj = _project(tmp_path, "big.py", _big_module())
    data = inspector.read_file(proj, "big.py", max_chars=500)
    assert set(data) == {"path", "content", "truncated"}
    assert data["truncated"] is True


def test_get_file_with_terms_gives_the_outline_first_then_the_spans(tmp_path):
    proj = _project(tmp_path, "big.py", _big_module())
    data = inspector.read_file(proj, "big.py", terms="refund")
    assert list(data)[:2] == ["path", "outline"]
    assert any("def handler_0" in row for row in data["outline"])
    assert data["narrowed"] is True
    assert "def refund_payment(order):" in data["content"]
    assert "handler_10(" not in data["content"]
    assert data["terms"] == ["refund"]


def test_get_file_with_terms_that_miss_reads_as_before(tmp_path):
    proj = _project(tmp_path, "big.py", _big_module())
    data = inspector.read_file(proj, "big.py", max_chars=500, terms=["nowhere"])
    assert data["narrowed"] is False
    assert data["truncated"] is True
    assert len(data["content"]) == 500


def test_the_mcp_tool_takes_terms():
    from core import mcp_server
    source = Path(mcp_server.__file__).read_text(encoding="utf-8")
    assert '"terms"' in source


def test_a_long_target_file_is_narrowed_to_the_task(tmp_path):
    proj = _project(tmp_path, "big.py", _big_module())
    context = inspector.build_context(proj, budget=100000, task="fix the refund flow", target="big.py")
    assert "def refund_payment(order):" in context
    assert "field_10'" not in context          # the outline names it; its body is not read
    assert "narrowed" in context.lower()
