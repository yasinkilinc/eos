"""Deciding a session has no durable insight is a valid outcome, and it has to
be recorded -- both so the gate stops asking and so the skip rate can be
measured. A gate with no escape gets disabled, and a disabled gate writes
nothing at all."""
import json

from core import notes


def test_a_skip_is_recorded_and_findable_by_session(tmp_path):
    project = tmp_path / "proj"
    (project / ".eos").mkdir(parents=True)

    path = notes.record_skip(project, reason="routine dependency bump", session="sess-1")

    assert path.name == ".skips.jsonl"
    entry = json.loads(path.read_text(encoding="utf-8").strip())
    assert entry["reason"] == "routine dependency bump"
    assert entry["session"] == "sess-1"
    assert notes.was_skipped(project, "sess-1") is True
    assert notes.was_skipped(project, "sess-2") is False


def test_skips_append_rather_than_overwrite(tmp_path):
    project = tmp_path / "proj"
    (project / ".eos").mkdir(parents=True)
    notes.record_skip(project, reason="one", session="a")
    notes.record_skip(project, reason="two", session="b")

    lines = notes.skips_path(project).read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2


def test_a_corrupt_skips_file_reads_as_no_skip_rather_than_raising(tmp_path):
    """`.skips.jsonl` sits in a directory of 271 git-tracked files but is
    itself gitignored (.gitignore:191), so a bad line is one machine's
    problem -- and one machine is enough: the gate reads this file on every
    Stop. `was_skipped` read it with `read_text()`
    and no `errors=`, and called `.get()` on whatever `json.loads` returned:
    non-UTF-8 bytes raised UnicodeDecodeError, a bare JSON scalar raised
    AttributeError, and the gate's broad `except` turned both into "cannot
    check -> do not block". Measured: gate 2 with no skips file, gate 0 with
    either of these. A skip lookup that cannot read its file has recorded no
    skip; it must fail closed, not open."""
    project = tmp_path / "proj"
    (project / ".eos").mkdir(parents=True)
    path = notes.skips_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)

    path.write_bytes(b"\xff\xfe not utf-8 at all\n")
    assert notes.was_skipped(project, "sess-1") is False

    path.write_text('"a scalar"\n', encoding="utf-8")
    assert notes.was_skipped(project, "sess-1") is False

    path.write_text("[1, 2, 3]\n", encoding="utf-8")
    assert notes.was_skipped(project, "sess-1") is False


def test_one_bad_line_does_not_hide_a_real_skip(tmp_path):
    """The hardening must be per-line, the same way the `*.md` path already
    is: a truncated or hand-mangled line must cost that line, not the file."""
    project = tmp_path / "proj"
    (project / ".eos").mkdir(parents=True)
    notes.record_skip(project, reason="routine", session="sess-1")
    with open(notes.skips_path(project), "a", encoding="utf-8") as handle:
        handle.write("not json at all\n")
        handle.write('"a scalar"\n')
    notes.record_skip(project, reason="also routine", session="sess-2")

    assert notes.was_skipped(project, "sess-1") is True
    assert notes.was_skipped(project, "sess-2") is True
    assert notes.was_skipped(project, "sess-3") is False


def test_a_skip_is_not_a_note(tmp_path):
    project = tmp_path / "proj"
    (project / ".eos").mkdir(parents=True)
    notes.record_skip(project, reason="routine", session="a")

    assert notes.load_notes(project) == []


# --- add_note's session marker, symmetric with record_skip's -----------
#
# mtime is not authorship: this store is git-tracked, so a pull, checkout,
# stash pop or reinstall rewrites every file's mtime at once, and 252 of 271
# real notes were measured sharing just three mtime minutes -- bulk
# filesystem events, not writes. A note that records which session wrote it
# lets a reader (the Stop-hook gate) ask that question exactly.

def test_a_note_written_with_a_session_is_found_by_it(tmp_path):
    project = tmp_path / "proj"
    (project / ".eos").mkdir(parents=True)

    path = notes.add_note(
        project, kind="finding", title="Some finding", body="Some body.", session="sess-1",
    )

    (note,) = notes.load_notes(project)
    assert note.path == path
    assert note.session == "sess-1"


def test_a_note_written_without_a_session_carries_none(tmp_path):
    """A note added without --session must not silently satisfy a session
    check it was never meant to answer."""
    project = tmp_path / "proj"
    (project / ".eos").mkdir(parents=True)

    notes.add_note(project, kind="finding", title="Some finding", body="Some body.")

    (note,) = notes.load_notes(project)
    assert note.session is None


def test_a_legacy_note_with_no_session_key_still_parses(tmp_path):
    """The 271 notes in the real corpus predate this field and carry no
    `session:` line at all -- parse_note must not choke on their absence."""
    project = tmp_path / "proj"
    directory = project / ".eos" / "knowledge"
    directory.mkdir(parents=True)
    (directory / "20260101-legacy.md").write_text(
        "---\nkind: finding\ntitle: \"Legacy note\"\ncreated: 2026-01-01\n---\n\nBody text.\n",
        encoding="utf-8",
    )

    (note,) = notes.load_notes(project)
    assert note.title == "Legacy note"
    assert note.session is None


# --- final round, IMPORTANT 2: a skip reason was not credential-guarded ----
#
# `add_note` refuses a credential-shaped body; `record_skip` wrote whatever it
# was given, verbatim, on exit 0. And this branch is what routes an agent to
# `--reason` under gate pressure: the block message prints `eos note skip
# <root> --reason '...'` as the way out of phase 1, so the string an agent is
# most likely to paste -- what broke it, connection URI and all -- lands in
# the one writer that never looked. Bounded by .skips.jsonl being gitignored
# (.gitignore:191), not by the guard.

def test_a_skip_reason_carrying_a_credential_is_refused(tmp_path):
    import pytest

    project = tmp_path / "proj"
    (project / ".eos").mkdir(parents=True)
    # Assembled at run time, never a literal: .githooks/pre-commit refuses a
    # staged line assigning a credential-shaped value, the same way the other
    # credential fixtures in this suite are built.
    secret = "S" * 20
    reason = f"mongodb://fmuser:{secret}@mongo-env1:27017/db is what broke it"

    with pytest.raises(ValueError, match="credential"):
        notes.record_skip(project, reason=reason, session="sess-1")

    assert not notes.skips_path(project).exists(), "nothing may be written"
    assert notes.was_skipped(project, "sess-1") is False


def test_an_ordinary_skip_reason_naming_auth_is_still_recorded(tmp_path):
    """The guard must not over-refuse: prose about credentials is the normal
    way a skip reason describes auth work."""
    project = tmp_path / "proj"
    (project / ".eos").mkdir(parents=True)

    notes.record_skip(project, reason="routine token refresh, nothing durable",
                      session="sess-1")
    notes.record_skip(project, reason="password: <redacted> in the fixture only",
                      session="sess-2")

    assert notes.was_skipped(project, "sess-1") is True
    assert notes.was_skipped(project, "sess-2") is True
