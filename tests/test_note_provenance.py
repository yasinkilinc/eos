"""Provenance and validity on notes written from now on (2.x roadmap M1, night 2 N2).

Forward-only: a new note can say who wrote it (a person, an agent, a generator)
and until when it holds. Notes written before carry neither and read exactly as
they did. `created` is where a note's validity starts; there is no second field
for it.
"""
import datetime
import subprocess
import sys
from pathlib import Path

import pytest

from core import consolidate, notes

REPO = Path(__file__).resolve().parents[1]


def _project(tmp_path):
    project = tmp_path / "proj"
    (project / ".eos").mkdir(parents=True)
    return project


def _add(project, title, **kw):
    return notes.add_note(project, kind="finding", title=title,
                          body=kw.pop("body", f"What {title} found, in its own words."), **kw)


def _raw(project, name, title, extra=""):
    directory = notes.notes_dir(project)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text(
        f"---\nkind: finding\ntitle: {title}\ncreated: 2026-09-01\n{extra}---\n\nWallet body.\n",
        encoding="utf-8")


def test_a_note_records_who_wrote_it(tmp_path):
    project = _project(tmp_path)
    path = _add(project, "Wallet cache warms on boot", provenance="agent", agent="claude")
    note = notes.parse_note(path)
    assert (note.provenance, note.agent) == ("agent", "claude")


def test_naming_an_agent_is_agent_provenance(tmp_path):
    note = notes.parse_note(_add(_project(tmp_path), "Wallet cache warms on boot", agent="devin"))
    assert note.provenance == "agent"


def test_an_unknown_provenance_is_refused(tmp_path):
    with pytest.raises(ValueError, match="provenance"):
        _add(_project(tmp_path), "Wallet cache warms on boot", provenance="robot")


def test_a_person_with_an_agent_name_is_refused(tmp_path):
    with pytest.raises(ValueError, match="agent"):
        _add(_project(tmp_path), "Wallet cache warms on boot", provenance="human", agent="claude")


def test_an_old_note_has_no_provenance_and_reads_as_before(tmp_path):
    project = _project(tmp_path)
    _raw(project, "a.md", "Wallet line reports what was paid")
    note = notes.load_notes(project)[0]
    assert (note.provenance, note.agent, note.valid_until) == (None, None, None)
    assert [n.path.name for n in notes.search_notes(project, "wallet")] == ["a.md"]


def test_valid_until_is_a_date_not_in_the_past(tmp_path):
    project = _project(tmp_path)
    today = datetime.date.today()
    path = _add(project, "Wallet cache warms on boot", valid_until=today.isoformat())
    assert notes.parse_note(path).valid_until == today.isoformat()
    with pytest.raises(ValueError, match="valid-until"):
        _add(project, "Refund reverses the line", valid_until="next week")
    with pytest.raises(ValueError, match="past"):
        _add(project, "Refund reverses the line",
             valid_until=(today - datetime.timedelta(days=1)).isoformat())


def test_an_expired_note_is_left_out_of_search(tmp_path):
    project = _project(tmp_path)
    _raw(project, "old.md", "Wallet balance endpoint", "valid_until: 2020-01-01\n")
    _raw(project, "new.md", "Wallet balance endpoint moved", "valid_until: 2999-01-01\n")
    assert [n.path.name for n in notes.search_notes(project, "wallet balance endpoint")] == ["new.md"]


def test_a_note_valid_until_today_still_holds_today(tmp_path):
    project = _project(tmp_path)
    _raw(project, "today.md", "Wallet balance endpoint",
         f"valid_until: {datetime.date.today().isoformat()}\n")
    assert [n.path.name for n in notes.search_notes(project, "wallet balance")] == ["today.md"]


def test_a_mangled_valid_until_never_hides_a_note(tmp_path):
    project = _project(tmp_path)
    _raw(project, "m.md", "Wallet balance endpoint", "valid_until: soon\n")
    assert [n.path.name for n in notes.search_notes(project, "wallet balance")] == ["m.md"]


def test_consolidate_lists_the_expired_notes(tmp_path):
    project = _project(tmp_path)
    _raw(project, "old.md", "Wallet balance endpoint", "valid_until: 2020-01-01\n")
    _raw(project, "new.md", "Refund reverses the line")
    data = consolidate.report(project)
    assert data["expired_notes"] == ["Wallet balance endpoint (2020-01-01)"]
    assert "EXPIRED NOTES (1)" in consolidate.render(data)


# --- review RVa ---------------------------------------------------------------------------


def test_an_amend_keeps_provenance_agent_and_valid_until(tmp_path):
    project = _project(tmp_path)
    until = (datetime.date.today() + datetime.timedelta(days=30)).isoformat()
    path = _add(project, "Wallet cache warms on boot", provenance="agent", agent="claude",
               valid_until=until)
    notes.amend_note(path, project, reaffirm="checked again today")
    note = notes.parse_note(path)
    assert (note.provenance, note.agent, note.valid_until) == ("agent", "claude", until)


def test_the_cli_writes_provenance_and_validity(tmp_path):
    project = tmp_path / "demo"
    project.mkdir()
    run = lambda *a: subprocess.run([sys.executable, str(REPO / "core" / "eos.py"), *a],  # noqa: E731
                                    capture_output=True, text=True, cwd=REPO)
    assert run("init", str(project)).returncode == 0
    until = (datetime.date.today() + datetime.timedelta(days=30)).isoformat()
    result = run("note", "add", str(project), "--kind", "finding", "--title", "Cache warms on boot",
                 "--body", "The first request pays for it otherwise.",
                 "--provenance", "human", "--valid-until", until)
    assert result.returncode == 0, result.stderr
    note = notes.load_notes(project)[0]
    assert (note.provenance, note.valid_until) == ("human", until)
    assert note.path.name in result.stdout


# --- N8 ---------------------------------------------------------------------------------


def test_amend_can_set_provenance_agent_and_valid_until_with_no_body(tmp_path):
    project = _project(tmp_path)
    path = _add(project, "Wallet cache warms on boot")
    until = (datetime.date.today() + datetime.timedelta(days=30)).isoformat()
    # No --body, --reaffirm or --scope: a metadata-only amend was refused before N8.
    notes.amend_note(path, project, provenance="agent", agent="claude", valid_until=until)
    note = notes.parse_note(path)
    assert (note.provenance, note.agent, note.valid_until) == ("agent", "claude", until)


def test_amend_setting_only_agent_implies_agent_provenance(tmp_path):
    project = _project(tmp_path)
    path = _add(project, "Wallet cache warms on boot")
    notes.amend_note(path, project, agent="devin")
    note = notes.parse_note(path)
    assert (note.provenance, note.agent) == ("agent", "devin")


def test_amend_keeps_the_fields_a_flag_did_not_touch(tmp_path):
    project = _project(tmp_path)
    until = (datetime.date.today() + datetime.timedelta(days=10)).isoformat()
    path = _add(project, "Wallet cache warms on boot", provenance="human", valid_until=until)
    notes.amend_note(path, project, reaffirm="checked again")
    note = notes.parse_note(path)
    assert (note.provenance, note.agent, note.valid_until) == ("human", None, until)


def test_amend_validates_provenance_the_same_as_add(tmp_path):
    project = _project(tmp_path)
    path = _add(project, "Wallet cache warms on boot")
    with pytest.raises(ValueError, match="provenance"):
        notes.amend_note(path, project, provenance="robot")


def test_amend_refuses_an_agent_name_against_the_notes_human_provenance(tmp_path):
    project = _project(tmp_path)
    path = _add(project, "Wallet cache warms on boot", provenance="human")
    with pytest.raises(ValueError, match="agent"):
        notes.amend_note(path, project, agent="claude")


def test_amend_validates_valid_until_the_same_as_add(tmp_path):
    project = _project(tmp_path)
    path = _add(project, "Wallet cache warms on boot")
    with pytest.raises(ValueError, match="past"):
        notes.amend_note(path, project,
                         valid_until=(datetime.date.today() - datetime.timedelta(days=1)).isoformat())


def test_the_cli_amends_provenance_and_validity_alone(tmp_path):
    project = tmp_path / "demo"
    project.mkdir()
    run = lambda *a: subprocess.run([sys.executable, str(REPO / "core" / "eos.py"), *a],  # noqa: E731
                                    capture_output=True, text=True, cwd=REPO)
    assert run("init", str(project)).returncode == 0
    added = run("note", "add", str(project), "--kind", "finding", "--title", "Cache warms on boot",
               "--body", "The first request pays for it otherwise.")
    assert added.returncode == 0, added.stderr
    note_path = notes.load_notes(project)[0].path
    until = (datetime.date.today() + datetime.timedelta(days=30)).isoformat()
    result = run("note", "amend", str(note_path), str(project), "--agent", "devin", "--valid-until", until)
    assert result.returncode == 0, result.stderr
    note = notes.load_notes(project)[0]
    assert (note.provenance, note.agent, note.valid_until) == ("agent", "devin", until)


# --- review RVc (N8): a metadata-only amend must not touch scope at all -------------


def test_a_metadata_only_amend_does_not_re_hash_or_refuse_on_a_changed_scope_file(tmp_path):
    # Before this fix, a metadata-only amend fell into the same scope re-hash
    # path a --reaffirm-free --scope amend uses, and refused whenever the
    # scoped file had changed for reasons this amend never claims about --
    # even though the docstring says provenance/agent/valid-until "changes
    # nothing the duplicate or stale-flag guards above care about".
    project = _project(tmp_path)
    scoped = project / "scoped.py"
    scoped.write_text("v1\n")
    path = _add(project, "Wallet cache warms on boot", scope=[str(scoped)])
    original_hash = notes.parse_note(path).scope_hashes

    scoped.write_text("v2\n")
    until = (datetime.date.today() + datetime.timedelta(days=30)).isoformat()
    notes.amend_note(path, project, valid_until=until)

    note = notes.parse_note(path)
    assert note.valid_until == until
    # Scope is left exactly as recorded -- not re-hashed to the new content --
    # so a later `stale_notes` can still flag the real change.
    assert note.scope_hashes == original_hash
