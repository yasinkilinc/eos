"""Tests for the accumulated-knowledge store.

Everything else EOS writes is derived from a source scan and is regenerated on
the next one. Notes are the exception: they are authored, they are the only
content a scan must never touch, and they are the reason this module exists.
"""
import hashlib
from pathlib import Path

import pytest

from core import notes
from core.lib.config_io import ConfigIO


def _project(tmp_path, config=None):
    """A minimal .eos instance; `config` is written to config.toml when given."""
    proj = tmp_path / "demo"
    (proj / ".eos").mkdir(parents=True)
    if config is not None:
        ConfigIO.write_toml(proj / ".eos" / "config.toml", config)
    return proj


def test_notes_dir_defaults_to_eos_knowledge(tmp_path):
    proj = _project(tmp_path)

    assert notes.notes_dir(proj) == proj / ".eos" / "knowledge"


def test_notes_dir_honours_configured_relative_path(tmp_path):
    proj = _project(tmp_path, {"knowledge": {"dir": "../shared-notes"}})

    assert notes.notes_dir(proj) == (tmp_path / "shared-notes").resolve()


def test_add_note_writes_a_file_named_after_the_title(tmp_path):
    proj = _project(tmp_path)

    path = notes.add_note(
        proj,
        kind="finding",
        title="Topup needs a payment method",
        body="Submit returns 200 but the order stalls without one.",
    )

    assert path.is_file()
    assert path.parent == notes.notes_dir(proj)
    assert path.name.endswith("-topup-needs-a-payment-method.md")


def test_add_note_records_front_matter_and_keeps_the_body(tmp_path):
    proj = _project(tmp_path)

    path = notes.add_note(
        proj,
        kind="finding",
        title="Cache warms on boot",
        body="The first request pays for it otherwise.",
        tags=["cache", "startup"],
        scope=["src/main/java/Boot.java"],
        source="PROJ-123",
    )

    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    assert "\n---\n" in text, "front matter must be delimited"
    for expected in ("kind: finding", "cache", "startup", "src/main/java/Boot.java", "PROJ-123"):
        assert expected in text, f"missing from note: {expected}"
    assert text.rstrip().endswith("The first request pays for it otherwise.")


def test_defect_note_without_cause_solution_and_metric_is_rejected(tmp_path):
    # The one schema that ever produced complete records made all of these
    # mandatory. A defect recorded without them is the kind of note that reads
    # as knowledge and carries none.
    proj = _project(tmp_path)

    with pytest.raises(ValueError) as excinfo:
        notes.add_note(proj, kind="defect", title="Pool exhausted", body="It broke.")

    message = str(excinfo.value)
    assert "cause" in message and "solution" in message and "metric" in message
    assert list(notes.notes_dir(proj).glob("*.md")) == [], "nothing may be written on rejection"


def test_defect_note_records_cause_solution_and_metric(tmp_path):
    proj = _project(tmp_path)

    path = notes.add_note(
        proj,
        kind="defect",
        title="Pool exhausted",
        cause="External validation ran inside the transaction boundary.",
        solution="Moved the call outside @Transactional.",
        metric="Pool wait 5000ms -> under 5ms.",
    )

    text = path.read_text(encoding="utf-8")
    assert "## Root cause" in text
    assert "## Solution" in text
    assert "## Metric" in text
    assert "Moved the call outside @Transactional." in text


def test_add_note_refuses_to_overwrite_an_existing_note(tmp_path):
    proj = _project(tmp_path)
    first = notes.add_note(proj, kind="finding", title="Same title", body="Original insight.")

    # A same-day, same-title collision now trips the title-duplicate check
    # (core/notes.py) before path.exists() is ever reached.
    with pytest.raises(notes.DuplicateNoteError):
        notes.add_note(proj, kind="finding", title="Same title", body="Different insight.")

    assert first.read_text(encoding="utf-8").endswith("Original insight.\n")


def test_title_cannot_escape_the_notes_directory(tmp_path):
    proj = _project(tmp_path)

    path = notes.add_note(proj, kind="finding", title="../../etc/passwd", body="Nice try.")

    assert path.parent == notes.notes_dir(proj)
    assert not (tmp_path / "etc").exists()


def test_add_note_rejects_a_body_carrying_a_credential(tmp_path):
    # Notes are committed and read by everyone; a pasted token in one is a leak
    # that outlives the session that created it. Built at run time, not written
    # as a literal: .githooks/pre-commit refuses a staged line that assigns a
    # credential-shaped value, and _find_credential now requires the same
    # 12-character floor, so a short word like "hunter2" no longer qualifies.
    proj = _project(tmp_path)
    secret = "S" * 24

    with pytest.raises(ValueError) as excinfo:
        notes.add_note(
            proj,
            kind="finding",
            title="Auth call",
            body=f"Reproduce with password={secret} against env1.",
        )

    assert "credential" in str(excinfo.value).lower()
    assert list(notes.notes_dir(proj).glob("*.md")) == []


def test_load_notes_reads_back_what_add_note_wrote(tmp_path):
    proj = _project(tmp_path)
    notes.add_note(
        proj,
        kind="finding",
        title="Cache warms on boot",
        body="The first request pays for it otherwise.",
        tags=["cache", "startup"],
        scope=["src/main/java/Boot.java"],
        source="PROJ-123",
    )

    loaded = notes.load_notes(proj)

    assert len(loaded) == 1
    note = loaded[0]
    assert note.kind == "finding"
    assert note.title == "Cache warms on boot"
    assert note.tags == ["cache", "startup"]
    assert note.scope == ["src/main/java/Boot.java"]
    assert note.source == "PROJ-123"
    assert "The first request pays for it otherwise." in note.body


def test_load_notes_is_empty_when_nothing_was_ever_written(tmp_path):
    assert notes.load_notes(_project(tmp_path)) == []


def _two_notes(tmp_path):
    proj = _project(tmp_path)
    notes.add_note(
        proj,
        kind="finding",
        title="Connection pool exhausted",
        body="The pool ran out of connections during deposits.",
        tags=["pool", "database"],
    )
    notes.add_note(
        proj,
        kind="finding",
        title="Cache warms on boot",
        body="Unrelated startup behaviour.",
        tags=["cache"],
    )
    return proj


def test_search_notes_puts_the_closer_note_first(tmp_path):
    proj = _two_notes(tmp_path)

    hits = notes.search_notes(proj, "connections exhausted in the database pool")

    assert hits, "expected at least one match"
    assert hits[0].title == "Connection pool exhausted"


def test_search_notes_drops_notes_below_the_relevance_threshold(tmp_path):
    # Injecting weakly-related notes into an agent's context spends budget that
    # the code itself needs. Silence is the right answer when nothing matches.
    proj = _two_notes(tmp_path)

    assert notes.search_notes(proj, "kafka consumer group lag") == []


def test_render_context_section_is_empty_when_there_are_no_notes(tmp_path):
    proj = _project(tmp_path)

    assert notes.render_context_section(proj, query="anything", max_chars=1000) == ""


def test_render_context_section_lists_notes_matching_the_query(tmp_path):
    proj = _two_notes(tmp_path)

    section = notes.render_context_section(proj, query="database connection pool", max_chars=2000)

    assert "## Accumulated Knowledge" in section
    assert "Connection pool exhausted" in section
    assert "Cache warms on boot" not in section, "unrelated note must not be injected"


def test_render_context_section_falls_back_to_recent_notes_without_a_query(tmp_path):
    proj = _two_notes(tmp_path)

    section = notes.render_context_section(proj, query=None, max_chars=2000)

    assert "Connection pool exhausted" in section
    assert "Cache warms on boot" in section


def test_render_context_section_drops_the_lowest_ranked_notes_over_budget_and_says_so(tmp_path):
    proj = _project(tmp_path)
    # Titles and bodies vary per note: normalized_title_key masks digits to a
    # single token, so "Finding number 0" and "Finding number 1" collide;
    # identical bodies trip the body-duplicate check too (core/notes.py).
    topics = ["auth", "cache", "queue", "retry", "timeout", "socket", "thread",
              "buffer", "cursor", "index"]
    for i, topic in enumerate(topics):
        notes.add_note(proj, kind="finding", title=f"Finding about {topic}", body=f"Short body {i}.")

    # Each note renders to roughly 60-70 chars; a 200 char budget fits a few,
    # not all ten.
    section = notes.render_context_section(proj, query=None, max_chars=200)

    assert "omitted" in section.lower(), section
    included_titles = [f"Finding about {topic}" for topic in topics if f"Finding about {topic}" in section]
    assert 0 < len(included_titles) < 10, "budget must neither admit everything nor nothing"


def test_add_note_allows_prose_that_merely_mentions_credentials(tmp_path):
    # A guard that fires on ordinary sentences gets worked around rather than
    # obeyed. These notes are about an auth-heavy system; discussing the fields
    # by name has to stay possible.
    proj = _project(tmp_path)

    path = notes.add_note(
        proj,
        kind="finding",
        title="Auth fields",
        body="The client_secret is required and the token: optional in this call.",
    )

    assert path.is_file()




def test_notes_dir_is_reported_gitignored_when_eos_is_ignored(tmp_path):
    proj = _project(tmp_path)
    (proj / ".gitignore").write_text(".eos/\n", encoding="utf-8")

    assert notes.is_notes_dir_gitignored(proj) is True


def test_notes_dir_is_not_gitignored_when_reconfigured_outside_eos(tmp_path):
    proj = _project(tmp_path, {"knowledge": {"dir": "../shared-notes"}})
    (proj / ".gitignore").write_text(".eos/\n", encoding="utf-8")

    assert notes.is_notes_dir_gitignored(proj) is False


def test_notes_dir_is_not_gitignored_when_there_is_no_gitignore(tmp_path):
    proj = _project(tmp_path)

    assert notes.is_notes_dir_gitignored(proj) is False


def test_add_note_stores_a_sha256_hash_for_an_existing_scope_file(tmp_path):
    proj = _project(tmp_path)
    scope_file = proj / "Boot.java"
    scope_file.write_text("class Boot {}\n", encoding="utf-8")
    expected = hashlib.sha256(scope_file.read_bytes()).hexdigest()

    notes.add_note(
        proj, kind="finding", title="Boot note", body="Boots.", scope=["Boot.java"],
    )

    [note] = notes.load_notes(proj)
    assert note.scope_hashes == [expected]


def test_add_note_stores_none_for_a_scope_entry_that_is_not_a_real_file(tmp_path):
    proj = _project(tmp_path)

    notes.add_note(
        proj, kind="finding", title="Class scope", body="About a class.",
        scope=["com.example.SomeClass"],
    )

    [note] = notes.load_notes(proj)
    assert note.scope_hashes == [None]


def test_add_note_rejects_a_scope_entry_naming_a_sensitive_file(tmp_path):
    # scope is hashed, not content-scanned like body, so a sensitive in-project
    # file would otherwise slip a verification oracle (its sha256) into a
    # committed note without ever tripping the credential guard.
    proj = _project(tmp_path)
    (proj / ".env").write_text("SECRET=hunter2\n", encoding="utf-8")

    with pytest.raises(ValueError) as excinfo:
        notes.add_note(
            proj, kind="finding", title="Env note", body="About config.",
            scope=[".env"],
        )

    assert list(notes.notes_dir(proj).glob("*.md")) == [], "nothing may be written on rejection"


def test_add_note_rejects_a_scope_entry_that_is_an_absolute_path_outside_the_project(tmp_path):
    proj = _project(tmp_path)

    with pytest.raises(ValueError):
        notes.add_note(
            proj, kind="finding", title="Passwd note", body="About the OS.",
            scope=["/etc/passwd"],
        )

    assert list(notes.notes_dir(proj).glob("*.md")) == []


def test_add_note_rejects_a_scope_entry_that_walks_outside_the_project_with_dotdot(tmp_path):
    proj = _project(tmp_path)

    with pytest.raises(ValueError):
        notes.add_note(
            proj, kind="finding", title="Traversal note", body="About paths.",
            scope=["../../../../etc/passwd"],
        )

    assert list(notes.notes_dir(proj).glob("*.md")) == []


def test_add_note_still_hashes_an_ordinary_in_project_scope_file(tmp_path):
    proj = _project(tmp_path)
    scope_file = proj / "Service.java"
    scope_file.write_text("class Service {}\n", encoding="utf-8")
    expected = hashlib.sha256(scope_file.read_bytes()).hexdigest()

    notes.add_note(
        proj, kind="finding", title="Service note", body="About the service.",
        scope=["Service.java"],
    )

    [note] = notes.load_notes(proj)
    assert note.scope_hashes == [expected]


def test_add_note_still_stores_none_for_a_bare_class_name_scope_entry(tmp_path):
    proj = _project(tmp_path)

    notes.add_note(
        proj, kind="finding", title="Class scope again", body="About a class.",
        scope=["SomeClass"],
    )

    [note] = notes.load_notes(proj)
    assert note.scope_hashes == [None]


def test_stale_notes_is_silent_when_scope_files_are_unchanged(tmp_path):
    proj = _project(tmp_path)
    scope_file = proj / "Boot.java"
    scope_file.write_text("class Boot {}\n", encoding="utf-8")
    notes.add_note(
        proj, kind="finding", title="Boot note", body="Boots.", scope=["Boot.java"],
    )

    assert notes.stale_notes(proj) == []


def test_stale_notes_flags_a_scope_file_whose_content_changed(tmp_path):
    proj = _project(tmp_path)
    scope_file = proj / "Boot.java"
    scope_file.write_text("class Boot {}\n", encoding="utf-8")
    notes.add_note(
        proj, kind="finding", title="Boot note", body="Boots.", scope=["Boot.java"],
    )

    scope_file.write_text("class Boot { void x() {} }\n", encoding="utf-8")

    stale = notes.stale_notes(proj)
    assert len(stale) == 1
    assert stale[0]["issues"] == [{"scope": "Boot.java", "reason": "changed"}]


def test_stale_notes_flags_a_scope_file_that_was_deleted(tmp_path):
    proj = _project(tmp_path)
    scope_file = proj / "Boot.java"
    scope_file.write_text("class Boot {}\n", encoding="utf-8")
    notes.add_note(
        proj, kind="finding", title="Boot note", body="Boots.", scope=["Boot.java"],
    )

    scope_file.unlink()

    stale = notes.stale_notes(proj)
    assert len(stale) == 1
    assert stale[0]["issues"] == [{"scope": "Boot.java", "reason": "removed"}]


def test_stale_notes_skips_a_note_written_before_this_feature_existed(tmp_path):
    # The single most important test in this set: a note with a scope: list
    # but no scope_hashes key at all (written before scope_hashes existed)
    # must never be flagged. zip(note.scope, note.scope_hashes) produces
    # nothing when scope_hashes is empty, which is exactly the point.
    proj = _project(tmp_path)
    directory = notes.notes_dir(proj)
    directory.mkdir(parents=True)
    (directory / "20200101-legacy-note.md").write_text(
        "---\n"
        "kind: finding\n"
        "title: Legacy note\n"
        "created: 2020-01-01\n"
        "scope:\n"
        "  - Gone.java\n"
        "---\n\n"
        "This note predates scope_hashes.\n",
        encoding="utf-8",
    )

    assert notes.stale_notes(proj) == []
