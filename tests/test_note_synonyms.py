"""Synonym groups in note search (2.x roadmap F3, night 2 N1).

A question asked in other words than the note's -- "wiki" for a note about
Confluence -- is the miss no weighting reaches (F3b). A project may name word
groups that mean the same thing to it; each group then counts as one concept,
so expanding a query never inflates what it is worth. Without a table nothing
changes.
"""
from core import notes


def _write_note(project, name, title, body="Body."):
    directory = notes.notes_dir(project)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(
        f"---\nkind: finding\ntitle: {title}\ncreated: 2026-09-01\n---\n\n{body}\n",
        encoding="utf-8")
    return path


def _project(tmp_path, config=None):
    project = tmp_path / "proj"
    (project / ".eos").mkdir(parents=True)
    if config is not None:
        (project / ".eos" / "config.toml").write_text(config, encoding="utf-8")
    _write_note(project, "a-confluence.md", "Confluence: append rows to a page table")
    _write_note(project, "b-release.md", "Release page lists the product versions")
    _write_note(project, "c-sms.md", "Notification definitions and history")
    return project


def _names(found):
    return [n.path.name for n in found]


def test_without_a_table_a_word_the_notes_never_use_finds_nothing(tmp_path):
    project = _project(tmp_path)
    assert "a-confluence.md" not in _names(notes.search_notes(project, "wiki"))


def test_a_group_in_the_config_finds_the_note_written_in_the_other_word(tmp_path):
    project = _project(tmp_path, '[notes]\nsynonyms = [["wiki", "confluence"]]\n')
    assert _names(notes.search_notes(project, "update the wiki"))[0] == "a-confluence.md"


def test_members_match_whatever_their_case(tmp_path):
    project = _project(tmp_path, '[notes]\nsynonyms = [["SMS", "Notification"]]\n')
    assert _names(notes.search_notes(project, "sms was not sent"))[0] == "c-sms.md"


def test_a_query_without_a_group_word_ranks_exactly_as_before(tmp_path):
    plain = _project(tmp_path / "plain")
    grouped = _project(tmp_path / "grouped", '[notes]\nsynonyms = [["wiki", "confluence"]]\n')
    for query in ("page table rows", "release versions", "notification history"):
        assert _names(notes.search_notes(plain, query)) == _names(notes.search_notes(grouped, query))


def test_naming_two_members_of_one_group_is_one_concept_not_two(tmp_path):
    corpus = notes.load_notes(_project(tmp_path))
    canon = notes.synonym_groups([["wiki", "confluence"]])

    def score(query):
        words = notes.canonical_words(notes._words(query), canon)
        weights = notes.word_weights(corpus, words, canon=canon)
        return [round(notes.relevance(n, words, weights, canon=canon), 9) for n in corpus]

    assert score("confluence rows") == score("wiki confluence rows")


def test_a_host_passes_its_groups_to_rank(tmp_path):
    project = _project(tmp_path)
    corpus = notes.load_notes(project)
    assert "a-confluence.md" not in _names(notes.rank(corpus, "wiki"))
    assert _names(notes.rank(corpus, "wiki", synonyms=[["wiki", "confluence"]]))[0] == "a-confluence.md"


def test_a_word_in_two_groups_belongs_to_the_first():
    groups = notes.synonym_groups([["wiki", "confluence"], ["confluence", "jira"]])
    assert groups["wiki"] == groups["confluence"]
    assert groups.get("jira") != groups["confluence"]


def test_a_malformed_table_is_no_table(tmp_path):
    for config in ('[notes]\nsynonyms = "wiki=confluence"\n',
                   '[notes]\nsynonyms = [["wiki"], 3, ["", "x"]]\n',
                   'notes = "flat"\n',
                   '[notes]\nsynonyms = [["pull request", "merge", "integrate"]]\n'):
        project = _project(tmp_path / str(abs(hash(config))), config)
        assert _names(notes.search_notes(project, "release versions"))[0] == "b-release.md"
    assert notes.synonym_groups([["wiki"], 3, ["", "x"]]) == {}


def test_a_member_of_several_words_is_dropped_and_the_rest_of_its_group_kept():
    groups = notes.synonym_groups([["pull request", "merge", "integrate"]])
    assert "pull request" not in groups and "pull" not in groups
    assert groups["merge"] == groups["integrate"]
