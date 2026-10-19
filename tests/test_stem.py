"""Optional stemming and word folding (a Turkish prompt against English notes)."""
import pytest

from core.notes import render
from core.notes.render import fold, stem


@pytest.fixture(autouse=True)
def _stem_off():
    render.set_stemming(False)
    yield
    render.set_stemming(False)


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


def test_words_stem_only_when_switched_on():
    assert render._words("testler failures") == {"testler", "failures"}
    render.set_stemming(True)
    assert render._words("testler failures") == {stem("test"), stem("failure")}


def test_config_switches_it_on_for_one_project(tmp_path):
    (tmp_path / ".eos").mkdir()
    (tmp_path / ".eos" / "config.toml").write_text("[notes]\nstem = true\n", encoding="utf-8")
    render.configure_stemming(tmp_path)
    assert render._words("testler") == {"test"}
    render.configure_stemming(tmp_path / "other")
    assert render._words("testler") == {"testler"}
