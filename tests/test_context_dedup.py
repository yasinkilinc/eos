"""Dedup against the harness's own always-loaded files (2.x roadmap C4)."""
from core.context import dedup


def _project(tmp_path):
    root = tmp_path / "proj"
    (root / ".eos").mkdir(parents=True)
    return root


def test_unset_key_changes_nothing(tmp_path):
    root = _project(tmp_path)
    (root / ".eos" / "config.toml").write_text("", encoding="utf-8")
    assert dedup.always_loaded_text(root) == ""
    assert dedup.already_loaded("anything at all, long enough to matter here", "") is False


def test_no_config_file_changes_nothing(tmp_path):
    root = _project(tmp_path)
    assert dedup.always_loaded_text(root) == ""


def test_a_glob_that_resolves_is_read_and_normalised(tmp_path):
    root = _project(tmp_path)
    (root / "CLAUDE.md").write_text("Always   run   tests\nbefore   committing.\n", encoding="utf-8")
    (root / ".eos" / "config.toml").write_text('[ai]\nloaded = ["CLAUDE.md"]\n', encoding="utf-8")
    text = dedup.always_loaded_text(root)
    assert "always run tests before committing." in text


def test_a_note_body_already_in_the_loaded_text_is_flagged(tmp_path):
    root = _project(tmp_path)
    (root / "CLAUDE.md").write_text("Never force push to main; always ask first before any rebase.\n",
                                    encoding="utf-8")
    (root / ".eos" / "config.toml").write_text('[ai]\nloaded = ["CLAUDE.md"]\n', encoding="utf-8")
    loaded = dedup.always_loaded_text(root)
    assert dedup.already_loaded("Never force push to main; always ask first before any rebase.", loaded)


def test_a_note_body_not_in_the_loaded_text_is_not_flagged(tmp_path):
    root = _project(tmp_path)
    (root / "CLAUDE.md").write_text("Some unrelated instruction about formatting.\n", encoding="utf-8")
    (root / ".eos" / "config.toml").write_text('[ai]\nloaded = ["CLAUDE.md"]\n', encoding="utf-8")
    loaded = dedup.always_loaded_text(root)
    assert not dedup.already_loaded("This is a completely different piece of information entirely.", loaded)


def test_a_short_body_never_counts_as_contained(tmp_path):
    root = _project(tmp_path)
    (root / "CLAUDE.md").write_text("ok\n", encoding="utf-8")
    (root / ".eos" / "config.toml").write_text('[ai]\nloaded = ["CLAUDE.md"]\n', encoding="utf-8")
    loaded = dedup.always_loaded_text(root)
    assert not dedup.already_loaded("ok", loaded)


def test_a_glob_over_a_directory_matches_every_file(tmp_path):
    root = _project(tmp_path)
    rules = root / ".claude" / "rules"
    rules.mkdir(parents=True)
    (rules / "a.md").write_text("Rule A: keep functions short and well named.\n", encoding="utf-8")
    (rules / "b.md").write_text("Rule B: write a test before the fix.\n", encoding="utf-8")
    (root / ".eos" / "config.toml").write_text('[ai]\nloaded = [".claude/rules/*.md"]\n', encoding="utf-8")
    loaded = dedup.always_loaded_text(root)
    assert "rule a: keep functions short" in loaded and "rule b: write a test before the fix." in loaded


def test_a_malformed_config_leaves_dedup_off(tmp_path):
    root = _project(tmp_path)
    (root / ".eos" / "config.toml").write_text('[ai]\nloaded = "not-a-list"\n', encoding="utf-8")
    assert dedup.always_loaded_text(root) == ""


def test_a_broken_toml_leaves_dedup_off(tmp_path):
    root = _project(tmp_path)
    (root / ".eos" / "config.toml").write_text('[ai\nloaded = [', encoding="utf-8")
    assert dedup.always_loaded_text(root) == ""
