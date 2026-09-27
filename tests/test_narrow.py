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
    assert data["lines"] == _big_module().count("\n")


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


# --- review of C3a ------------------------------------------------------------------


def test_context_stops_at_a_neighbouring_block_rather_than_cut_it():
    code = ["```python"] + [f"x = compute_value_{i}(alpha, beta, gamma)" for i in range(40)] + ["```"]
    table = ["| name | limit |", "|---|---|"] + [f"| row{i} | {i} |" for i in range(40)]
    for block in (code, table):
        text = "\n".join(block + ["needle here"] + block) + "\n"
        result = narrow.narrow_by_terms(text, ["needle"], limit=16000)
        assert result.spans == [(len(block) + 1, len(block) + 1)], result.spans


def test_shrinking_takes_a_block_whole_or_leaves_it():
    table = ["| name | limit |", "|---|---|"] + [f"| row{i} | {i} |" for i in range(40)]
    text = "\n".join(["intro line"] * 3 + table + ["needle here"] + ["tail line"] * 30) + "\n"
    result = narrow.narrow_by_terms(text, ["needle", "table"], limit=200)
    body = [line for line in result.text.splitlines() if not line.startswith("@@")]
    assert not any(line.startswith("|") for line in body)


def test_line_numbers_count_newlines_only():
    result = narrow.narrow_by_terms("a\x0cb\nx\ntarget\n", ["target"], limit=16000)
    assert result.spans == [(1, 3)]
    assert "a\x0cb\n" in result.text
    crlf = narrow.narrow_by_terms("one\r\ntwo target\r\nthree\r\n", ["target"], limit=16000)
    assert crlf.spans == [(1, 3)]
    assert "\r" not in crlf.text


def test_a_cut_line_says_so_and_ends_its_line():
    result = narrow.narrow_by_terms("needle " + "x" * 5000 + "\n", ["needle"], limit=500)
    assert len(result.text) <= 500
    assert result.text.endswith("(line cut)\n")


def _target_section(context: str) -> str:
    rest = context.split("## Target File", 1)[1]
    return "## Target File" + rest.split("\n# ", 1)[0].split("\n## ", 1)[0]


def test_a_narrowed_target_never_costs_more_than_the_head_cut(tmp_path):
    body = "import os\n" + "".join(f"def handler_{i}(event):\n    return event.get('refund_{i}')\n\n"
                                   for i in range(400))
    proj = _project(tmp_path, "big.py", body)
    narrowed = _target_section(inspector.build_context(proj, budget=100000, task="refund", target="big.py"))
    head = _target_section(inspector.build_context(proj, budget=100000, task="zzz", target="big.py"))
    assert "truncated" in head
    assert len(narrowed) <= len(head), (len(narrowed), len(head))


def test_a_long_line_target_keeps_its_fence_closed(tmp_path):
    proj = _project(tmp_path, "big.py", "refund = '" + "x" * 20000 + "'\n")
    context = inspector.build_context(proj, budget=100000, task="refund", target="big.py")
    assert "x```" not in context
    assert "(line cut)" in context


def test_terms_must_be_strings(tmp_path):
    import pytest
    proj = _project(tmp_path, "big.py", _big_module())
    with pytest.raises(ValueError, match="terms"):
        inspector.read_file(proj, "big.py", terms=[1, "refund"])
    with pytest.raises(ValueError, match="terms"):
        inspector.read_file(proj, "big.py", terms={"refund": 1})


def test_fuzz_limit_order_and_whole_blocks():
    """A kept sample of the 20,000-case fuzz run for the C3a review fixes."""
    import random
    rng = random.Random(7)
    pool = ["plain words here", "needle in the text", "| a | b |", "|---|---|", "```", "~~~", "",
            "x" * 300, "alpha needle beta", "\r", "tab\tneedle", "needle_suffix", "form\x0cfeed needle"]
    for _ in range(2000):
        text = "\n".join(rng.choice(pool) for _ in range(rng.randint(0, 60))) + rng.choice(["", "\n"])
        limit = rng.choice([50, 200, 1200, 16000])
        result = narrow.narrow_by_terms(text, ["needle", "beta"], context=rng.choice([0, 100, 600]), limit=limit)
        if result is None:
            continue
        assert len(result.text) <= limit and result.text.endswith("\n")
        assert all(a <= b for a, b in result.spans)
        assert all(result.spans[i][1] < result.spans[i + 1][0] for i in range(len(result.spans) - 1))
        blocks = set(narrow._blocks(narrow.split_lines(text)).values())
        for a, b in result.spans:
            for first, last in blocks:
                assert last < a - 1 or first > b - 1 or (first >= a - 1 and last <= b - 1), (text, result.spans)


# --- review of C3b ------------------------------------------------------------------


def test_the_rarest_term_decides_which_span_wins():
    lines = []
    for i in range(40):
        lines.append(f"Why does step {i} run after the cache warms? It waits for the queue to drain."
                     if i % 5 == 0 else f"Step {i} ordinary detail about the pipeline and its settings.")
    lines[23] = "The refund worker crashes on restart when the ledger is locked."
    result = narrow.narrow_by_terms("\n".join(lines) + "\n",
                                    ["why", "does", "the", "refund", "worker", "crash", "after", "restart"])
    assert "The refund worker crashes on restart" in result.text


def test_a_shrunk_span_claims_only_the_terms_it_kept():
    lines = [f"line {i:02d} " + "padding text " * 9 for i in range(40)]
    lines[2] += "restart"
    lines[10] += "refund"
    result = narrow.narrow_by_terms("\n".join(lines) + "\n", ["refund", "restart"], limit=500)
    kept = [line for line in result.text.splitlines() if not line.startswith("@@")]
    for term in result.terms:
        assert any(term in line for line in kept), (term, result.terms)
    assert result.omitted >= 1


def test_a_cut_line_keeps_the_term_it_was_kept_for():
    result = narrow.narrow_by_terms("x" * 5000 + " needle " + "y" * 5000 + "\n", ["needle"], limit=300)
    assert "needle" in result.text and len(result.text) <= 300
