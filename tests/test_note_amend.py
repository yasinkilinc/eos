"""amend_note: the only way to bring a stale note back into agreement."""
import datetime
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import notes


@pytest.fixture
def project(tmp_path):
    """A project with one note, and the file it scopes changed underneath it."""
    watched = tmp_path / "src" / "main" / "java" / "A.java"
    watched.parent.mkdir(parents=True)
    watched.write_text("class A { int v = 1; }", encoding="utf-8")
    notes.add_note(
        tmp_path, kind="finding", title="A holds one",
        body="The field starts at one.", scope=["src/main/java/A.java"],
        source="PROJ-123", session="s-original",
    )
    watched.write_text("class A { int v = 2; }", encoding="utf-8")
    return tmp_path, watched, notes.load_notes(tmp_path)[0].path


@pytest.fixture
def two_scope_project(tmp_path):
    """A note scoped to two real files, the first changed underneath it.

    The shape every scope-set escape needs: a stale flag to clear, and a
    second entry to permute against.
    """
    a = tmp_path / "src" / "A.java"
    b = tmp_path / "src" / "B.java"
    a.parent.mkdir(parents=True)
    a.write_text("class A { int v = 1; }", encoding="utf-8")
    b.write_text("class B {}", encoding="utf-8")
    notes.add_note(
        tmp_path, kind="finding", title="A and B",
        body="Both of these matter today.",
        scope=["src/A.java", "src/B.java"],
    )
    a.write_text("class A { int v = 2; }", encoding="utf-8")
    return tmp_path, a, b, notes.load_notes(tmp_path)[0].path


def test_amend_body_clears_the_stale_flag(project):
    """Re-hashing scope_hashes is what clears the flag."""
    root, _watched, note_path = project
    assert notes.stale_notes(root), "must be stale before amend"

    notes.amend_note(note_path, root, body="The field now starts at two.",
                     session="s-new")

    assert notes.stale_notes(root) == [], "no staleness should remain after amend"
    note = notes.parse_note(note_path)
    assert note.body.strip() == "The field now starts at two."
    assert note.session == "s-new", "an explicit session overwrites the old one"


def test_amend_records_updated_and_preserves_created(project):
    root, _watched, note_path = project
    before = notes.parse_note(note_path).created

    notes.amend_note(note_path, root, body="Different text entirely.")

    raw = note_path.read_text(encoding="utf-8")
    today = datetime.date.today().isoformat()
    assert f"updated: {today}" in raw
    assert notes.parse_note(note_path).created == before, "created is preserved"
    assert "source: PROJ-123" in raw, "source is preserved"
    assert "scope:" in raw and "src/main/java/A.java" in raw, "scope is preserved"


def test_amend_refuses_an_unchanged_body(project):
    """Against silencing: re-hashing without touching the body would close
    the flag without doing the work."""
    root, _watched, note_path = project
    same = notes.parse_note(note_path).body

    with pytest.raises(ValueError, match="unchanged"):
        notes.amend_note(note_path, root, body=same)

    assert notes.stale_notes(root), "a refused amend must not clear the flag"


def test_reaffirm_clears_the_flag_without_rewriting_the_body(project):
    """The file may have changed in a way that does not touch what the note
    claims. Without this escape the only way out would be to alter the body
    artificially -- the dead end `skip` became in phase 1."""
    root, _watched, note_path = project
    original = notes.parse_note(note_path).body

    notes.amend_note(note_path, root, reaffirm="Only a comment changed.")

    assert notes.stale_notes(root) == []
    body = notes.parse_note(note_path).body
    assert original in body, "the original body is preserved"
    assert "Only a comment changed." in body
    assert datetime.date.today().isoformat() in body, "a dated line is appended"


def test_amend_refuses_a_credential(project):
    root, _watched, note_path = project
    with pytest.raises(ValueError, match="credential"):
        notes.amend_note(
            note_path, root,
            body="Authorization: Basic Zm11c2VyOlMzY3IzdFA0c3N3MHJk",
        )


def test_amend_needs_at_least_one_of_body_reaffirm_or_scope(project):
    root, _watched, note_path = project
    with pytest.raises(ValueError, match="at least one"):
        notes.amend_note(note_path, root)


def test_amend_refuses_both_body_and_reaffirm_together(project):
    root, _watched, note_path = project
    with pytest.raises(ValueError, match="only one"):
        notes.amend_note(note_path, root, body="x", reaffirm="y")


# --- Critical 1 + 2: a wrong/omitted --path, or a non-note target, must
# refuse loudly instead of silently un-monitoring a note or destroying a
# file that was never a note in the first place. -----------------------

def test_amend_refuses_a_target_outside_the_notes_directory(project):
    """The scoped .java file itself is a real, existing file -- but it is
    not a note, and amend must never be pointed at one directly. Before this
    guard, amend_note happily overwrote it with front matter plus a body."""
    root, watched, _note_path = project

    with pytest.raises(ValueError, match="notes directory"):
        notes.amend_note(watched, root, body="Hijacked note body.")

    assert watched.read_text(encoding="utf-8") == "class A { int v = 2; }"


def test_amend_refuses_when_path_names_the_wrong_project(project):
    """Reproduces a wrong or omitted --path: the note is real, but
    project_root names a different project, so the note is not inside
    THAT project's notes directory either. Before this guard this silently
    wrote `scope_hashes: - null`, permanently un-monitoring the note."""
    root, _watched, note_path = project
    other_root = root.parent / "unrelated-project"
    other_root.mkdir()

    with pytest.raises(ValueError, match="notes directory"):
        notes.amend_note(note_path, other_root, body="Anything at all.")

    assert notes.stale_notes(root), "a refused amend must not clear the flag"


def test_amend_refuses_a_file_with_no_note_front_matter(project):
    """A stray .md sitting directly in the notes directory -- never written
    by add_note or amend_note -- must not be silently rewritten either."""
    root, _watched, note_path = project
    stray = note_path.parent / "not-a-note.md"
    stray.write_text("Just some prose, no front matter.", encoding="utf-8")

    with pytest.raises(ValueError, match="does not look like a note"):
        notes.amend_note(stray, root, body="Anything at all.")

    assert stray.read_text(encoding="utf-8") == "Just some prose, no front matter."


# --- Important 3: a scope hash that was recorded must never silently
# become null; that is "the file I described is gone" and must reach a
# human. -----------------------------------------------------------------

def test_amend_refuses_when_a_hashed_scope_file_is_gone(project):
    root, watched, note_path = project
    watched.unlink()

    with pytest.raises(ValueError, match="no longer resolves"):
        notes.amend_note(note_path, root, body="The field now starts at two.")

    assert notes.stale_notes(root), "a refused amend must not clear the flag"


# --- Important 4: an empty body must not silently produce a note that
# still looks recorded. ----------------------------------------------------

def test_amend_refuses_an_empty_body(project):
    root, _watched, note_path = project

    with pytest.raises(ValueError, match="empty"):
        notes.amend_note(note_path, root, body="   ")

    assert notes.stale_notes(root), "a refused amend must not clear the flag"


def test_amend_refuses_a_whitespace_only_reaffirm(project):
    """New-5: I4 (empty body refused) was fixed on one branch only. A
    whitespace-only --reaffirm reason still writes '[date] Still holds: '
    and clears the flag -- the same 'looks recorded, says nothing' state."""
    root, _watched, note_path = project

    with pytest.raises(ValueError, match="empty"):
        notes.amend_note(note_path, root, reaffirm="   ")

    assert notes.stale_notes(root), "a refused amend must not clear the flag"


# --- Important 5: a defect note must keep its Root cause / Solution /
# Metric schema; amend must not let a revision drop it. --------------------

def test_amend_of_a_defect_note_requires_the_full_schema(tmp_path):
    watched = tmp_path / "src" / "B.java"
    watched.parent.mkdir(parents=True)
    watched.write_text("class B { int v = 1; }", encoding="utf-8")
    notes.add_note(
        tmp_path, kind="defect", title="B leaks",
        cause="Listener never removed.", solution="Removed on close().",
        metric="0 leaks after 1h soak, was 40.",
        scope=["src/B.java"],
    )
    watched.write_text("class B { int v = 2; }", encoding="utf-8")
    note_path = notes.load_notes(tmp_path)[0].path

    with pytest.raises(ValueError, match="Root cause"):
        notes.amend_note(note_path, tmp_path, body="Just a plain revised body.")
    assert notes.stale_notes(tmp_path), "a refused amend must not clear the flag"

    ok_body = (
        "Revised summary.\n\n"
        "## Root cause\n\nStill the listener.\n\n"
        "## Solution\n\nStill removed on close().\n\n"
        "## Metric\n\n0 leaks after 2h soak."
    )
    notes.amend_note(note_path, tmp_path, body=ok_body)
    assert notes.stale_notes(tmp_path) == []


def _defect_note(tmp_path):
    """A defect note plus a scoped file, changed underneath it -- the same
    shape test_amend_of_a_defect_note_requires_the_full_schema sets up,
    factored out for the New-4 heading-matching tests below."""
    watched = tmp_path / "src" / "B.java"
    watched.parent.mkdir(parents=True)
    watched.write_text("class B { int v = 1; }", encoding="utf-8")
    notes.add_note(
        tmp_path, kind="defect", title="B leaks",
        cause="Listener never removed.", solution="Removed on close().",
        metric="0 leaks after 1h soak, was 40.",
        scope=["src/B.java"],
    )
    watched.write_text("class B { int v = 2; }", encoding="utf-8")
    return notes.load_notes(tmp_path)[0].path


def test_amend_of_a_defect_note_accepts_a_differently_cased_heading(tmp_path):
    """New-4, direction 1: a raw case-sensitive substring check refused
    '## Root Cause' (capital C) as missing. A real heading, regardless of
    case, must be recognised."""
    note_path = _defect_note(tmp_path)
    body = (
        "Revised summary.\n\n"
        "## Root Cause\n\nStill the listener.\n\n"
        "## SOLUTION\n\nStill removed on close().\n\n"
        "## metric\n\n0 leaks after 2h soak."
    )

    notes.amend_note(note_path, tmp_path, body=body)

    assert notes.stale_notes(tmp_path) == []


def test_amend_of_a_defect_note_is_not_fooled_by_a_heading_mentioned_in_prose(tmp_path):
    """New-4, direction 2: a raw substring check accepted any body that
    merely CONTAINS the literal text '## Root cause' anywhere, including
    inside an ordinary sentence that quotes it rather than a real heading
    line. Anchoring to the start of a line closes that."""
    note_path = _defect_note(tmp_path)
    body = (
        "See the ## Root cause note from the ticket for background.\n\n"
        "## Solution\n\nStill removed on close().\n\n"
        "## Metric\n\n0 leaks after 2h soak."
    )

    with pytest.raises(ValueError, match="Root cause"):
        notes.amend_note(note_path, tmp_path, body=body)


# --- New-1: a deleted/moved scoped file makes BOTH --body and --reaffirm
# refuse (Important 3, round 1) -- a real dead end unless there is a way to
# re-point the note. --scope is that way. -----------------------------------

def test_amend_can_rescope_away_from_a_deleted_file(project):
    """The dead end: the scoped file is gone, so re-hashing the old path can
    only ever refuse, on either escape. --scope, pointed at where the code
    moved, is the actual remedy."""
    root, watched, note_path = project
    watched.unlink()
    moved = root / "src" / "main" / "java" / "A2.java"
    moved.write_text("class A { int v = 2; }", encoding="utf-8")

    with pytest.raises(ValueError, match="no longer resolves"):
        notes.amend_note(note_path, root, reaffirm="Renamed, still holds.")

    notes.amend_note(note_path, root, scope=["src/main/java/A2.java"])

    assert notes.stale_notes(root) == []
    note = notes.parse_note(note_path)
    assert note.scope == ["src/main/java/A2.java"]


def test_amend_scope_alone_does_not_require_body_or_reaffirm(project):
    """Re-pointing a note whose prose is still correct must not force a
    prose edit."""
    root, watched, note_path = project
    moved = root / "src" / "main" / "java" / "A2.java"
    watched.rename(moved)
    original_body = notes.parse_note(note_path).body

    notes.amend_note(note_path, root, scope=["src/main/java/A2.java"])

    note = notes.parse_note(note_path)
    assert note.body.strip() == original_body.strip(), "prose is left untouched"
    assert note.scope == ["src/main/java/A2.java"]
    assert notes.stale_notes(root) == []


def test_amend_scope_combines_with_body_in_one_call(project):
    root, watched, note_path = project
    watched.unlink()
    moved = root / "src" / "main" / "java" / "A2.java"
    moved.write_text("class A { int v = 3; }", encoding="utf-8")

    notes.amend_note(
        note_path, root, body="The field now starts at three.",
        scope=["src/main/java/A2.java"],
    )

    note = notes.parse_note(note_path)
    assert note.body.strip() == "The field now starts at three."
    assert note.scope == ["src/main/java/A2.java"]
    assert notes.stale_notes(root) == []


def test_amend_omitted_scope_leaves_the_recorded_scope_untouched(project):
    """Omitting --scope must behave exactly as it did before --scope
    existed: the recorded scope list is unchanged."""
    root, _watched, note_path = project

    notes.amend_note(note_path, root, body="The field now starts at two.")

    assert notes.parse_note(note_path).scope == ["src/main/java/A.java"]


# --- Round-3 Critical: an explicitly empty --scope must never be read as
# "clear the scope". `_split_csv(',')` returns `[]`, not None, so this is
# reachable from the CLI on one comma, and it would leave the note with
# neither scope: nor scope_hashes: (both empty/None, both dropped by
# _front_matter) -- un-monitored, with no trace, on exit 0. -----------------

def test_amend_refuses_an_empty_scope_list(project):
    root, _watched, note_path = project

    with pytest.raises(ValueError, match="empty"):
        notes.amend_note(note_path, root, scope=[])

    assert notes.stale_notes(root), "a refused amend must not clear the flag"
    raw = note_path.read_text(encoding="utf-8")
    assert "scope:" in raw and "scope_hashes:" in raw, "front matter must be untouched"


def test_cli_amend_refuses_scope_comma_verbatim(project):
    """The reviewer's exact reproduction: `--scope ,` split to `[]` and
    silently un-monitored the note, exit 0."""
    from core import eos as eos_cli

    root, _watched, note_path = project

    rc = eos_cli.main(["note", "amend", str(note_path), "--path", str(root), "--scope", ","])

    assert rc == 1
    assert notes.stale_notes(root), "a refused amend must not clear the flag"
    raw = note_path.read_text(encoding="utf-8")
    assert "scope:" in raw and "scope_hashes:" in raw, "front matter must be untouched"


def test_cli_amend_refuses_scope_empty_string_without_the_wrong_message(project, capsys):
    """Minor, folded into the Critical's fix: `_split_csv("")` returns
    None, the same value as --scope never being passed -- so a caller who
    DID pass --scope (just with nothing in it) must not be told to pass
    --body, --reaffirm, or --scope, having already passed the third one."""
    from core import eos as eos_cli

    root, _watched, note_path = project

    rc = eos_cli.main(["note", "amend", str(note_path), "--path", str(root), "--scope", ""])

    assert rc == 1
    captured = capsys.readouterr()
    assert "at least one of" not in captured.err, "must not contradict the invocation"
    assert "empty" in captured.err
    assert notes.stale_notes(root), "a refused amend must not clear the flag"


# --- Round-3 Important, widened in round 4: a --scope that names the
# note's existing scope re-hashes and clears the stale flag while recording
# neither a revision nor a reason. "Names the existing scope" is a question
# about the SET of entries: scope order carries no meaning anywhere in this
# system (stale_notes zips, add_note hashes entry by entry, nothing sorts or
# reads position), and a repeated entry is hashed twice to the same value.
# A literal list comparison therefore left the two cheapest escapes open --
# swap two entries, or name one of them twice. ------------------------------

def test_amend_refuses_a_no_op_scope(project):
    root, _watched, note_path = project

    with pytest.raises(ValueError, match="already has"):
        notes.amend_note(note_path, root, scope=["src/main/java/A.java"])

    assert notes.stale_notes(root), "a refused amend must not clear the flag"


def test_amend_refuses_a_reordered_scope(two_scope_project):
    """The set is byte-identical: no file is re-pointed, no body revised, no
    reason given -- only `updated:` moves, and the stale flag clears."""
    root, _a, _b, note_path = two_scope_project

    with pytest.raises(ValueError, match="already has") as excinfo:
        notes.amend_note(note_path, root, scope=["src/B.java", "src/A.java"])

    assert notes.stale_notes(root), "a refused amend must not clear the flag"
    message = str(excinfo.value)
    assert "--body" in message and "--reaffirm" in message, (
        "the refusal must name the two options that record something"
    )
    assert "actually differs" not in message, (
        "the old wording coached toward the cheapest thing that differs -- "
        "swapping two entries -- which is this very escape"
    )


def test_amend_refuses_a_scope_that_only_repeats_an_entry(two_scope_project):
    """The same set, one entry named twice: also a no-op, also cleared the
    flag while recording nothing."""
    root, _a, _b, note_path = two_scope_project

    with pytest.raises(ValueError, match="already has"):
        notes.amend_note(
            note_path, root, scope=["src/A.java", "src/A.java", "src/B.java"]
        )

    assert notes.stale_notes(root), "a refused amend must not clear the flag"


def test_cli_amend_refuses_a_reordered_scope(two_scope_project):
    """End to end, the way it was reproduced: exit 0 with the stale flag
    cleared was the bug."""
    from core import eos as eos_cli

    root, _a, _b, note_path = two_scope_project

    rc = eos_cli.main([
        "note", "amend", str(note_path), "--path", str(root),
        "--scope", "src/B.java,src/A.java",
    ])

    assert rc == 1
    assert notes.stale_notes(root), "a refused amend must not clear the flag"


# --- Round-4 Critical: a --scope whose entries all resolve to nothing
# reaches the same forbidden end state as an empty --scope by another door.
# stale_notes skips null hashes, so a note left with only null hashes can
# never be flagged again -- un-monitored, with a `scope:` line as the only
# trace. A recorded reason (--body or --reaffirm) buys that transition;
# nothing else does. ---------------------------------------------------------

def test_amend_refuses_a_scope_that_resolves_to_nothing(project):
    root, _watched, note_path = project
    before = note_path.read_text(encoding="utf-8")

    with pytest.raises(ValueError, match="resolves to a file"):
        notes.amend_note(note_path, root, scope=["JunkOnly"])

    assert notes.stale_notes(root), "a refused amend must not clear the flag"
    assert note_path.read_text(encoding="utf-8") == before, "note must be untouched"


def test_cli_amend_refuses_a_scope_that_resolves_to_nothing(project):
    """`--scope JunkOnly` wrote `scope_hashes: [null]` on exit 0."""
    from core import eos as eos_cli

    root, _watched, note_path = project

    rc = eos_cli.main([
        "note", "amend", str(note_path), "--path", str(root), "--scope", "JunkOnly",
    ])

    assert rc == 1
    assert notes.stale_notes(root), "a refused amend must not clear the flag"
    assert notes.parse_note(note_path).scope == ["src/main/java/A.java"]


def test_amend_may_leave_a_note_unresolving_when_a_body_records_why(project):
    """The ruling's escape hatch: the code a note described can genuinely be
    gone, and saying so in prose is exactly the human record the guard is
    protecting. It must not become a dead end -- that was round 1's mistake."""
    root, _watched, note_path = project

    notes.amend_note(
        note_path, root, scope=["RemovedGateway"],
        body="The gateway class this note described was deleted in PROJ-123.",
    )

    note = notes.parse_note(note_path)
    assert note.scope == ["RemovedGateway"]
    assert note.scope_hashes == [None]
    assert notes.stale_notes(root) == []


def test_amend_does_not_refuse_a_note_that_never_resolved(tmp_path):
    """Five real notes are scoped to class names and have never had a
    non-null hash. Re-pointing one at another class name takes nothing
    away, so the guard must not fire: it is about LOSING monitoring, not
    about never having had it."""
    notes.add_note(
        tmp_path, kind="finding", title="Only class names",
        body="This note is about a type, not a file.", scope=["ClassA"],
    )
    note_path = notes.load_notes(tmp_path)[0].path

    notes.amend_note(note_path, tmp_path, scope=["ClassB"])

    assert notes.parse_note(note_path).scope == ["ClassB"]


def test_amend_does_not_refuse_while_one_entry_still_resolves(two_scope_project):
    """Half the scope going to a class name is not un-monitoring: B still
    resolves, so the note can still be flagged."""
    root, _a, _b, note_path = two_scope_project

    notes.amend_note(
        note_path, root, scope=["src/B.java", "ClassA"],
        reaffirm="A moved out of scope; what the note says about B still holds.",
    )

    note = notes.parse_note(note_path)
    assert note.scope == ["src/B.java", "ClassA"]
    assert note.scope_hashes[0] is not None and note.scope_hashes[1] is None
    assert notes.stale_notes(root) == []


# --- Round-4: a scope-only amend must not clear the stale flag of an entry
# it KEEPS. Appending a second file to the scope changes the set, so the
# no-op guard lets it through -- and the entry that actually went stale gets
# re-hashed with nothing recorded about it. Re-pointing AWAY from an entry
# retires that entry's flag legitimately (the `scope:` diff is the record);
# keeping it and re-hashing it does not. ------------------------------------

def test_amend_scope_only_cannot_clear_a_changed_entry_it_keeps(two_scope_project):
    root, _a, _b, note_path = two_scope_project
    (root / "src" / "C.java").write_text("class C {}", encoding="utf-8")

    with pytest.raises(ValueError, match="changed since this note"):
        notes.amend_note(
            note_path, root, scope=["src/A.java", "src/B.java", "src/C.java"]
        )

    assert notes.stale_notes(root), "a refused amend must not clear the flag"


def test_amend_scope_only_cannot_drop_a_changed_entry_that_still_exists(two_scope_project):
    """The dropped-entry arm of the same rule. Narrowing the note off A --
    which still exists and changed underneath it -- clears A's stale flag and
    records nothing at all: the note simply stops claiming to be about the
    file that moved. `eos note skip` taught this in phase 1: under gate
    pressure the cheapest exit becomes the default."""
    root, _a, _b, note_path = two_scope_project

    with pytest.raises(ValueError, match="changed since this note"):
        notes.amend_note(note_path, root, scope=["src/B.java"])

    assert notes.stale_notes(root), "a refused amend must not clear the flag"
    assert notes.parse_note(note_path).scope == ["src/A.java", "src/B.java"]


def test_amend_scope_only_may_drop_an_entry_whose_file_is_gone(two_scope_project):
    """The waiver that keeps --scope's original purpose intact: dropping an
    entry whose file is GONE is legitimate maintenance -- it is why --scope
    was added at all -- and needs no body and no reason. Only a file that
    still exists and changed is avoidance."""
    root, a, _b, note_path = two_scope_project
    a.unlink()

    notes.amend_note(note_path, root, scope=["src/B.java"])

    assert notes.stale_notes(root) == []
    assert notes.parse_note(note_path).scope == ["src/B.java"]


def test_amend_may_drop_a_changed_entry_when_a_reason_is_recorded(two_scope_project):
    root, _a, _b, note_path = two_scope_project

    notes.amend_note(
        note_path, root, scope=["src/B.java"],
        reaffirm="A moved to another module; what this note says about B holds.",
    )

    assert notes.stale_notes(root) == []
    assert notes.parse_note(note_path).scope == ["src/B.java"]


def test_amend_may_drop_an_entry_whose_stored_hash_was_null(tmp_path):
    """An entry that never resolved was never monitored -- stale_notes skips
    a null hash -- so dropping it retires no flag and needs no reason. The
    entry's file EXISTS by now (written after the note was), which is the
    case that separates "was never monitored" from "changed": comparing the
    current hash against a null one would call every such entry changed."""
    b = tmp_path / "src" / "B.java"
    b.parent.mkdir(parents=True)
    b.write_text("class B {}", encoding="utf-8")
    notes.add_note(
        tmp_path, kind="finding", title="Two files, one not written yet",
        body="B today, Later once it lands.",
        scope=["src/Later.java", "src/B.java"],
    )
    note_path = notes.load_notes(tmp_path)[0].path
    assert notes.parse_note(note_path).scope_hashes[0] is None
    (tmp_path / "src" / "Later.java").write_text("class Later {}", encoding="utf-8")

    notes.amend_note(note_path, tmp_path, scope=["src/B.java"])

    assert notes.parse_note(note_path).scope == ["src/B.java"]


def test_amend_scope_may_keep_a_changed_entry_when_a_reason_is_recorded(two_scope_project):
    root, _a, _b, note_path = two_scope_project
    (root / "src" / "C.java").write_text("class C {}", encoding="utf-8")

    notes.amend_note(
        note_path, root, scope=["src/A.java", "src/B.java", "src/C.java"],
        reaffirm="A only gained a comment; C belongs here too.",
    )

    assert notes.stale_notes(root) == []
    assert notes.parse_note(note_path).scope == [
        "src/A.java", "src/B.java", "src/C.java",
    ]


# --- Round-3 Important: the "was hashed, now null" guard must key off the
# scope ENTRY STRING, not its position. Once --scope replaces the list
# wholesale, index i in the new list has no relationship to index i in the
# old one. -------------------------------------------------------------------

def test_amend_scope_reorder_with_a_new_entry_is_not_falsely_refused(tmp_path):
    """The old index-based guard refused a permutation -- nothing deleted,
    every entry exactly as valid as before -- claiming the class-name entry
    had a recorded hash. It never did; `src/A.java`, at a different
    position, did. A bare reorder is now a no-op refusal, so the permutation
    is carried by a genuine addition."""
    watched = tmp_path / "src" / "A.java"
    watched.parent.mkdir(parents=True)
    watched.write_text("class A {}", encoding="utf-8")
    notes.add_note(
        tmp_path, kind="finding", title="Reorder me",
        body="Two things about this area.",
        scope=["SomeClass", "src/A.java"],
    )
    note_path = notes.load_notes(tmp_path)[0].path

    notes.amend_note(
        note_path, tmp_path, scope=["src/A.java", "SomeClass", "OtherClass"]
    )

    assert notes.stale_notes(tmp_path) == []
    assert notes.parse_note(note_path).scope == [
        "src/A.java", "SomeClass", "OtherClass",
    ]


def test_amend_scope_by_position_silently_accepted_a_deleted_file(tmp_path):
    """The precise failure of position-keying: a genuinely deleted,
    previously-hashed file slipped through because it landed on a
    position that used to hold an unrelated null-hash (class-name) entry,
    which had nothing to do with it. Five real notes carry a null hash, so
    this precondition exists in the corpus."""
    a = tmp_path / "src" / "A.java"
    c = tmp_path / "src" / "C.java"
    for f in (a, c):
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("x", encoding="utf-8")
    notes.add_note(
        tmp_path, kind="finding", title="Mixed scope",
        body="A class and two files.",
        scope=["ClassB", "src/A.java", "src/C.java"],
    )
    note_path = notes.load_notes(tmp_path)[0].path
    a.unlink()

    with pytest.raises(ValueError, match="no longer resolves"):
        notes.amend_note(note_path, tmp_path, scope=["src/A.java", "src/C.java"])

    assert notes.stale_notes(tmp_path), "a refused amend must not clear the flag"


def test_amend_narrowing_may_drop_a_hashed_entry_while_another_resolves(tmp_path):
    """Ruling: shrinking the list is a requested narrowing, not a silent
    drop. An entry absent from the new list has nothing to compare against
    and is simply not checked -- even if its file is gone. Keyed by entry,
    not position: index-keying pairs the new list's first entry (`ClassB`,
    which never resolved) with the old list's first hash (`src/A.java`'s,
    which is real) and refuses this."""
    a = tmp_path / "src" / "A.java"
    c = tmp_path / "src" / "C.java"
    for f in (a, c):
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("x", encoding="utf-8")
    notes.add_note(
        tmp_path, kind="finding", title="Three things",
        body="All three matter today.",
        scope=["src/A.java", "ClassB", "src/C.java"],
    )
    note_path = notes.load_notes(tmp_path)[0].path
    a.unlink()

    notes.amend_note(note_path, tmp_path, scope=["ClassB", "src/C.java"])

    assert notes.stale_notes(tmp_path) == []
    assert notes.parse_note(note_path).scope == ["ClassB", "src/C.java"]


def test_amend_narrowing_onto_a_class_name_is_refused_as_un_monitoring(tmp_path):
    """The discriminating narrowing case, and the one where round 4's
    un-monitoring guard and the entry-keyed guard meet. Round-2 (index-keyed)
    code refused it for the wrong reason -- claiming `ClassB` had a recorded
    hash, which it never had. Round-3 code accepted it outright and left the
    note with `scope_hashes: [null]`. Both are wrong: the refusal is correct,
    the reason is that the note would be left with nothing to monitor."""
    a = tmp_path / "src" / "A.java"
    a.parent.mkdir(parents=True)
    a.write_text("class A {}", encoding="utf-8")
    notes.add_note(
        tmp_path, kind="finding", title="A and a class",
        body="One file and one type.", scope=["src/A.java", "ClassB"],
    )
    note_path = notes.load_notes(tmp_path)[0].path
    assert notes.parse_note(note_path).scope_hashes[1] is None

    with pytest.raises(ValueError) as excinfo:
        notes.amend_note(note_path, tmp_path, scope=["ClassB"])

    message = str(excinfo.value)
    assert "resolves to a file" in message
    assert "had a recorded hash" not in message, (
        "ClassB never had one -- that is the index-keyed misattribution"
    )
    assert notes.parse_note(note_path).scope == ["src/A.java", "ClassB"]


# --- New-2: a project whose notes live outside its own tree -- the real
# FM shape (a service directory with `[knowledge] dir` pointing at a
# sibling) -- must stay amendable. The containment check has to key off
# notes_dir(), not project_root itself. --------------------------------------

def test_amend_works_when_the_notes_dir_resolves_outside_the_project_root(tmp_path):
    from core.lib.config_io import ConfigIO

    service = tmp_path / "fm-svc"
    shared = tmp_path / "shared-notes" / "fm-svc"
    (service / "src").mkdir(parents=True)
    (service / ".eos").mkdir()
    ConfigIO.write_toml(
        service / ".eos" / "config.toml",
        {"knowledge": {"dir": "../shared-notes/fm-svc"}},
    )
    assert notes.notes_dir(service) == shared.resolve()

    watched = service / "src" / "A.java"
    watched.write_text("class A { int v = 1; }", encoding="utf-8")
    notes.add_note(
        service, kind="finding", title="A holds one",
        body="The field starts at one.", scope=["src/A.java"],
    )
    note_path = notes.load_notes(service)[0].path
    assert shared.resolve() in note_path.parents, "note must actually land outside service root"

    watched.write_text("class A { int v = 2; }", encoding="utf-8")
    assert notes.stale_notes(service), "must be stale before amend"

    notes.amend_note(note_path, service, body="The field now starts at two.")

    assert notes.stale_notes(service) == []


# --- Minor 1: an omitted --session must preserve the note's existing
# session, not erase it. ----------------------------------------------------

def test_amend_preserves_existing_session_when_none_given(project):
    root, _watched, note_path = project
    assert notes.parse_note(note_path).session == "s-original"

    notes.amend_note(note_path, root, body="The field now starts at two.")

    assert notes.parse_note(note_path).session == "s-original"


# --- Minor 5: the same corpus-quality rule add_note enforces must hold
# for amend -- revising a body into a restatement of another note's body
# must be refused, not silently produce a second copy. Restricted to
# --body only (ruling): --reaffirm's dated line makes its content
# near-unique by construction, so this check has no upside there and only
# the downside of misfiring on a pre-existing twin the caller cannot see,
# on the one escape from a blocking gate. ------------------------------------

def test_amend_refuses_producing_a_duplicate_body_elsewhere_in_the_store(project):
    """New-3: amend gets its own refusal tail, not add_note's -- the
    amender is revising a note, not writing a second one, and is not
    told to hand-edit a different file's YAML."""
    root, _watched, note_path = project
    other_body = "The gateway retries five times before giving up."
    other_path = notes.add_note(root, kind="finding", title="Gateway retry count",
                                body=other_body)

    with pytest.raises(notes.DuplicateNoteError) as excinfo:
        notes.amend_note(note_path, root, body=other_body)

    message = str(excinfo.value)
    assert "Gateway retry count" in message
    assert str(other_path) in message
    assert "second note" not in message.lower(), "that is add_note's phrasing, not amend's"
    assert "session:" not in message, "add_note's escape does not apply to a revision"


def test_amend_does_not_duplicate_check_a_reaffirm(project):
    """Ruling: the duplicate check applies to --body only, never
    --reaffirm. Constructs a genuine collision (a twin note whose body
    exactly equals what the reaffirmed content becomes) to prove the check
    truly does not run there, rather than merely being unlikely to fire."""
    root, _watched, note_path = project
    note = notes.parse_note(note_path)
    stamp = datetime.date.today().isoformat()
    reaffirm_reason = "Still true."
    twin_body = f"{note.body.strip()}\n\n[{stamp}] Still holds: {reaffirm_reason}"
    notes.add_note(root, kind="finding", title="Unrelated twin", body=twin_body)

    notes.amend_note(note_path, root, reaffirm=reaffirm_reason)

    assert notes.stale_notes(root) == []
    assert notes.parse_note(note_path).body.strip() == twin_body


# --- Minor 3: CLI wiring, driven through eos.main -- not just the manual
# transcript. This subcommand adds a second positional (`note`) alongside
# --path, which is exactly the shape that once clobbered --path's default
# for every subcommand. -----------------------------------------------------

def test_cli_amend_writes_to_the_directory_path_names(tmp_path):
    """Strengthened per the re-review's (e): under the round-1 containment
    bug, a wrong/omitted --path made scope_hashes come back [None], which
    ALSO makes stale_notes report []. So asserting only 'not stale' passes
    on the catastrophic path too. scope_hashes must equal the scoped
    file's real sha256, the field that actually distinguishes success from
    silent un-monitoring."""
    import hashlib

    from core import eos as eos_cli

    watched = tmp_path / "src" / "A.java"
    watched.parent.mkdir(parents=True)
    watched.write_text("class A { int v = 1; }", encoding="utf-8")
    notes.add_note(tmp_path, kind="finding", title="A holds one",
                   body="The field starts at one.", scope=["src/A.java"])
    watched.write_text("class A { int v = 2; }", encoding="utf-8")
    note_path = notes.load_notes(tmp_path)[0].path

    rc = eos_cli.main([
        "note", "amend", str(note_path), "--path", str(tmp_path),
        "--body", "The field now starts at two.",
    ])

    assert rc == 0
    assert notes.stale_notes(tmp_path) == []
    assert notes.parse_note(note_path).body.strip() == "The field now starts at two."
    expected_hash = hashlib.sha256(watched.read_bytes()).hexdigest()
    assert notes.parse_note(note_path).scope_hashes == [expected_hash]


def test_cli_amend_refuses_a_target_outside_the_notes_directory(tmp_path):
    from core import eos as eos_cli

    stray = tmp_path / "not_a_note.md"
    stray.write_text("not a note", encoding="utf-8")

    rc = eos_cli.main([
        "note", "amend", str(stray), "--path", str(tmp_path),
        "--body", "anything at all",
    ])

    assert rc == 1
    assert stray.read_text(encoding="utf-8") == "not a note"


# --- an unsubstituted placeholder is not a body ---------------------------
#
# The Stop-hook gate prints runnable commands and an agent runs them as
# printed. `--body '<what is true now>'` reached amend_note intact, passed
# every existing refusal (not empty, not unchanged, not a duplicate), and
# amend re-hashes: the note's real claim was overwritten AND its stale flag
# cleared, taking it permanently off the gate's radar on exit 0.

def test_amend_refuses_a_body_that_is_only_a_placeholder(project):
    tmp_path, _watched, note_path = project
    before = notes.parse_note(note_path).body

    with pytest.raises(ValueError) as exc:
        notes.amend_note(note_path, tmp_path, body="<what is true now>")

    assert "placeholder" in str(exc.value)
    assert "<what is true now>" in str(exc.value), "name what to replace"
    assert notes.parse_note(note_path).body == before, "and write nothing"
    assert notes.stale_notes(tmp_path), "the stale flag must survive the refusal"


def test_amend_refuses_a_reaffirm_reason_that_is_only_a_placeholder(project):
    tmp_path, _watched, note_path = project

    with pytest.raises(ValueError) as exc:
        notes.amend_note(note_path, tmp_path, reaffirm="<why it still holds>")

    assert "placeholder" in str(exc.value)
    assert notes.stale_notes(tmp_path)


def test_amend_refuses_a_bare_ellipsis_body(project):
    tmp_path, _watched, note_path = project

    with pytest.raises(ValueError) as exc:
        notes.amend_note(note_path, tmp_path, body="...")

    assert "placeholder" in str(exc.value)


def test_amend_accepts_a_real_body_that_merely_mentions_angle_brackets(project):
    """Anchored on the WHOLE value: a body carrying `List<String>` or a
    generic parameter is a real claim and must not be caught."""
    tmp_path, _watched, note_path = project

    notes.amend_note(
        note_path, tmp_path,
        body="The field is now 2, and callers take a List<String> of the old values.",
    )

    assert "List<String>" in notes.parse_note(note_path).body
    assert notes.stale_notes(tmp_path) == []


def test_add_refuses_a_placeholder_title_and_body(tmp_path):
    """The gate's fallback message prints `--title '...' --body '...'`, and
    that runs verbatim too -- it wrote a note titled `...`."""
    with pytest.raises(ValueError) as exc:
        notes.add_note(tmp_path, kind="finding", title="...", body="A real body.")
    assert "placeholder" in str(exc.value)

    with pytest.raises(ValueError) as exc:
        notes.add_note(tmp_path, kind="finding", title="A real title", body="...")
    assert "placeholder" in str(exc.value)

    assert notes.load_notes(tmp_path) == [], "neither refusal may write"


def test_skip_refuses_a_placeholder_reason(tmp_path):
    """`eos note skip <root> --reason '...'` is the one printed command whose
    argument IS quoted, so it ran verbatim to exit 0 -- silencing the gate for
    the whole session on a reason that says nothing. Found by running every
    command the gate prints, in every shape, through a real shell."""
    with pytest.raises(ValueError) as exc:
        notes.record_skip(tmp_path, reason="...", session="s1")

    assert "placeholder" in str(exc.value)
    assert not notes.was_skipped(tmp_path, "s1"), "and nothing may be recorded"

    notes.record_skip(tmp_path, reason="Config-only change, nothing to learn.", session="s1")
    assert notes.was_skipped(tmp_path, "s1"), "a real reason still works"


def test_cli_skip_reports_a_placeholder_reason_without_a_traceback(tmp_path, capsys):
    """`record_skip` had no refusal path until the placeholder guard, so
    `cmd_note_skip` never needed a handler -- and its absence turned a
    one-sentence refusal into a six-frame traceback with the sentence at the
    bottom. Its two neighbours, cmd_note_add and cmd_note_amend, already
    report the same class of error as a plain `error:` line."""
    from core import eos as eos_cli

    rc = eos_cli.main(["note", "skip", str(tmp_path), "--reason", "...", "--session", "s1"])
    captured = capsys.readouterr()

    assert rc == 1
    assert captured.err.startswith("error: "), f"no traceback: {captured.err!r}"
    assert "placeholder" in captured.err
    assert "Traceback" not in captured.err
    assert not notes.was_skipped(tmp_path, "s1")
