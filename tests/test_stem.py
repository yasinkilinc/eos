"""Optional stemming and word folding (a Turkish prompt against English notes)."""
import pytest

from core.notes import render
from core.notes.render import fold, stem


def test_turkish_and_english_inflections_meet_their_base():
    assert stem("siparişi") == stem("sipariş")
    assert stem("siparişleri") == stem("sipariş")
    assert stem("testler") == stem("test")
    assert stem("failures") == stem("failure")


def test_a_fixed_point_makes_every_form_agree():
    assert stem("updates") == stem("update")
    assert stem("classes") == stem("class")
    assert stem("queries") == stem("query")
    assert stem("testing") == stem("test")


def test_short_words_and_protected_endings_stay():
    assert stem("plan") == "plan"
    assert stem("ekle") == "ekle"
    assert stem("notes") != "not"
    assert stem("status") == "status"
    assert stem("class") == "class"
    assert stem("analysis") == "analysis"


def test_stripping_never_leaves_fewer_than_three_letters():
    assert all(len(stem(word)) >= 3 for word in ("ender", "ables", "using", "aeds"))


def test_fold_maps_the_dotted_capital_i():
    assert fold("İPTAL") == "iptal"
    assert fold("Order") == "order"


def test_words_stem_only_when_asked():
    assert render._words("testler failures") == {"testler", "failures"}
    assert render._words("testler failures", True) == {stem("test"), stem("failure")}


def test_stem_setting_reads_one_project(tmp_path):
    (tmp_path / ".eos").mkdir()
    (tmp_path / ".eos" / "config.toml").write_text("[notes]\nstem = true\n", encoding="utf-8")
    assert render.stem_setting(tmp_path) is True
    assert render.stem_setting(tmp_path / "other") is False


def _project(tmp_path, name, stem_on):
    root = tmp_path / name
    (root / ".eos" / "knowledge").mkdir(parents=True)
    (root / ".eos" / "config.toml").write_text(f"[notes]\nstem = {'true' if stem_on else 'false'}\n",
                                               encoding="utf-8")
    (root / ".eos" / "knowledge" / "20260101-a.md").write_text(
        "---\nkind: finding\ntitle: Order tests\ncreated: 2026-01-01\ntags:\n  - flow\n---\n\nbody one\n",
        encoding="utf-8")
    return root


def test_two_projects_each_keep_their_own_setting_in_one_process(tmp_path):
    from core import notes

    on, off = _project(tmp_path, "on", True), _project(tmp_path, "off", False)
    for _ in range(2):
        assert [n.title for n in notes.search_notes(on, "testler")] == ["Order tests"]
        assert notes.search_notes(off, "testler") == []
        assert [n.title for n in notes.search_notes(off, "tests")] == ["Order tests"]


def test_a_path_that_does_not_read_the_setting_is_unaffected(tmp_path):
    from core import capabilities, notes

    on = _project(tmp_path, "on", True)
    assert notes.search_notes(on, "testler")
    assert render._words("issues") == {"issues"}
    cap = capabilities.Capability(name="tracker", run="tracker.sh", words=("issues",))
    assert capabilities.for_task("issues", [cap]) == [cap]
