"""The generated/hand-written split, and the stale-note report."""
import io
import shlex
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import notes


def _write(directory: Path, name: str, source: str | None, scope: str, body: str) -> Path:
    front = ["---", "kind: finding", f'title: "{name}"', "created: 2026-01-01"]
    if source:
        front.append(f"source: {source}")
    front += ["scope:", f"  - {scope}", "---", "", body, ""]
    path = directory / f"20260101-{name}.md"
    path.write_text("\n".join(front), encoding="utf-8")
    return path


def test_generator_sources_are_classified_as_generated(tmp_path):
    directory = tmp_path / ".eos" / "knowledge"
    directory.mkdir(parents=True)
    for source in ("api-inventory", "journey-map", "repo-topology"):
        _write(directory, source, source, "pom.xml", "Bulk table.")

    for note in notes.load_notes(tmp_path):
        assert notes.is_generated(note), f"{note.source} must count as generated"


def test_an_unfamiliar_source_falls_to_hand_written(tmp_path):
    """The safe direction: an unfamiliar source counts as hand-written. The
    other way round would be silently wrong -- a new bulk generator that is
    never added to this list would have its notes read as hand-written, and
    the gate would block on them."""
    directory = tmp_path / ".eos" / "knowledge"
    directory.mkdir(parents=True)
    _write(directory, "ticket", "PROJ-123", "src/main/java/A.java", "Hand written.")
    _write(directory, "nosource", None, "src/main/java/B.java", "Also hand written.")
    # A hand-analyzed derivative, not the raw table -- one exists in the real
    # corpus. This value is a SUPERSTRING of the generator name "api-inventory",
    # so a substring implementation (`any(g in source for g in
    # GENERATOR_SOURCES)`) misclassifies it as generated and passes without
    # this case. It is what makes the allowlist claim (exact match, not a
    # keyword search) testable rather than decorative.
    _write(directory, "derivative", "api-inventory-finding", "src/main/java/C.java", "Also hand written.")

    for note in notes.load_notes(tmp_path):
        assert not notes.is_generated(note)


def test_stale_notes_reports_whether_the_note_was_generated(tmp_path):
    """The gate reads this field to decide whether to block. Having the caller
    re-open the file and parse `source` itself would mean two
    classifications in two places."""
    directory = tmp_path / ".eos" / "knowledge"
    directory.mkdir(parents=True)
    watched = tmp_path / "pom.xml"
    watched.write_text("<project>one</project>", encoding="utf-8")

    notes.add_note(tmp_path, kind="finding", title="Generated one",
                   body="Bulk table.", scope=["pom.xml"], source="api-inventory")
    notes.add_note(tmp_path, kind="finding", title="Hand written one",
                   body="A real trap.", scope=["pom.xml"], source="PROJ-123")

    watched.write_text("<project>two</project>", encoding="utf-8")
    report = {item["note"]: item for item in notes.stale_notes(tmp_path)}

    assert len(report) == 2, "both must go stale"
    generated = [i for i in report.values() if i["generated"]]
    handwritten = [i for i in report.values() if not i["generated"]]
    assert len(generated) == 1 and generated[0]["source"] == "api-inventory"
    assert len(handwritten) == 1 and handwritten[0]["source"] == "PROJ-123"
    assert all(i["issues"][0]["reason"] == "changed" for i in report.values())


def test_audit_separates_hand_written_from_generated(tmp_path, capsys):
    from core import eos as eos_cli

    watched = tmp_path / "pom.xml"
    watched.write_text("<project>one</project>", encoding="utf-8")
    notes.add_note(tmp_path, kind="finding", title="Bulk api map", body="18 endpoints.",
                   scope=["pom.xml"], source="api-inventory")
    notes.add_note(tmp_path, kind="finding", title="A real trap", body="Watch out.",
                   scope=["pom.xml"], source="PROJ-123")
    watched.write_text("<project>two</project>", encoding="utf-8")

    code = eos_cli.main(["note", "audit", "--path", str(tmp_path)])
    out = capsys.readouterr().out

    assert code == 0, "audit reports; it is not a gate"
    assert "A real trap" in out
    assert "Bulk api map" in out
    assert "regenerate" in out, "the generated one must be told its remedy"
    assert "eos note amend" in out, "the hand-written one must be told its remedy"


# --- final round, CRITICAL 1: the removed case has its own remedy ----------
#
# `stale_notes` separates `removed` from `changed`, and the report printed the
# `changed` remedies for both. For a scope entry whose file is gone, `--body`
# and `--reaffirm` both re-hash the old path and both refuse; only `--scope`,
# which no surface printed, can clear it.

def test_audit_prints_the_scope_remedy_for_a_file_that_is_gone(tmp_path, capsys):
    from core import eos as eos_cli

    watched = tmp_path / "src" / "OldName.java"
    watched.parent.mkdir(parents=True)
    watched.write_text("class OldName {}", encoding="utf-8")
    notes.add_note(tmp_path, kind="finding", title="OldName is reflective",
                   body="The parent instantiates it by name.",
                   scope=["src/OldName.java"], source="PROJ-123")
    watched.rename(watched.with_name("NewName.java"))

    code = eos_cli.main(["note", "audit", "--path", str(tmp_path)])
    out = capsys.readouterr().out

    assert code == 0
    assert "--scope" in out, "the only remedy that can work must be the one printed"
    assert "no longer exists" in out, "and it must say why --body cannot work"
    amends = [line for line in out.splitlines() if "eos note amend" in line]
    assert amends and all("--scope" in line for line in amends), amends
    assert all("--body" not in line for line in amends), (
        f"nothing on this note requires a body edit: {amends}"
    )


def test_audit_still_prints_both_remedies_for_a_changed_file(tmp_path, capsys):
    from core import eos as eos_cli

    watched = tmp_path / "src" / "A.java"
    watched.parent.mkdir(parents=True)
    watched.write_text("class A { int v = 1; }", encoding="utf-8")
    notes.add_note(tmp_path, kind="finding", title="A holds one",
                   body="The field starts at one.", scope=["src/A.java"],
                   source="PROJ-123")
    watched.write_text("class A { int v = 2; }", encoding="utf-8")

    code = eos_cli.main(["note", "audit", "--path", str(tmp_path)])
    out = capsys.readouterr().out

    assert code == 0
    assert "--body" in out and "--reaffirm" in out
    assert "no longer exists" not in out


def test_audit_names_the_sections_a_defect_body_must_keep(tmp_path, capsys):
    """Same trap the Stop hook's message had: `--body '<real prose>'` on a
    defect note is refused unless the new body keeps Root cause, Solution and
    Metric. The report prints that command first."""
    from core import eos as eos_cli

    watched = tmp_path / "src" / "A.java"
    watched.parent.mkdir(parents=True)
    watched.write_text("class A { int v = 1; }", encoding="utf-8")
    notes.add_note(tmp_path, kind="defect", title="A returned one too many",
                   cause="An off-by-one in the loop bound.",
                   solution="Compare with < rather than <=.",
                   metric="12 failing rows before, 0 after.",
                   scope=["src/A.java"], source="PROJ-123")
    watched.write_text("class A { int v = 2; }", encoding="utf-8")

    code = eos_cli.main(["note", "audit", "--path", str(tmp_path)])
    out = capsys.readouterr().out

    assert code == 0
    assert "Root cause" in out and "Solution" in out and "Metric" in out, out


# --- parked IMPORTANT 1: the mixed note's remedy, on this surface too ------
#
# The Stop hook classifies a stale note by the whole note, because `amend_note`
# validates every scope entry: one `removed` entry refuses `--body` and
# `--reaffirm`, and one `changed` entry refuses a scope-only amend, so a note
# carrying both has a third remedy -- `--scope` and `--body` in one command.
# This report classified per note as well but only two ways, and so printed
# `--scope` alone for the mixed note: a command that exits 1.

def _mixed_note(tmp_path):
    """A hand-written note scoping two files, one changed and one deleted."""
    changed = tmp_path / "src" / "A.java"
    removed = tmp_path / "src" / "B.java"
    changed.parent.mkdir(parents=True)
    changed.write_text("class A { int v = 1; }", encoding="utf-8")
    removed.write_text("class B {}", encoding="utf-8")
    note_path = notes.add_note(
        tmp_path, kind="finding", title="A and B together",
        body="A holds one and B is empty.",
        scope=["src/A.java", "src/B.java"], source="PROJ-123")
    changed.write_text("class A { int v = 2; }", encoding="utf-8")
    removed.unlink()
    return note_path


def _run_printed(argv):
    """Run a command line taken from the report, the way an agent's Bash call
    runs it: no stdin, exit code and stderr captured."""
    from core import eos as eos_cli
    stdin, stderr = sys.stdin, sys.stderr
    sys.stdin = io.StringIO("")
    sys.stderr = io.StringIO()
    try:
        return eos_cli.main(argv), sys.stderr.getvalue()
    finally:
        sys.stdin, sys.stderr = stdin, stderr


def _amend_lines(out):
    return [line for line in out.splitlines() if "eos note amend" in line]


def test_audit_prints_scope_and_body_together_for_a_mixed_note(tmp_path, capsys):
    from core import eos as eos_cli

    _mixed_note(tmp_path)

    code = eos_cli.main(["note", "audit", "--path", str(tmp_path)])
    out = capsys.readouterr().out

    assert code == 0
    amends = _amend_lines(out)
    assert amends, "there must still be an amend command"
    assert all("--scope" in line and "--body" in line for line in amends), (
        "one removed entry refuses --body alone and one changed entry refuses "
        f"--scope alone, so the only working remedy carries both: {amends}"
    )
    assert "src/B.java" in out, "the removed entry blocking the amend must be named"


def test_the_printed_mixed_remedy_from_the_report_clears_the_note(tmp_path, capsys):
    """The rule every printed command is held to: substituted, it must run and
    the note must stop being stale."""
    from core import eos as eos_cli

    _mixed_note(tmp_path)
    eos_cli.main(["note", "audit", "--path", str(tmp_path)])
    line = next(l for l in _amend_lines(capsys.readouterr().out) if "--scope" in l)

    argv = shlex.split(line[line.index("eos note amend"):])
    values = {"--scope": "src/A.java", "--body": "A now holds two, and B was deleted."}
    for index, token in enumerate(argv):
        if token.startswith("<") and token.endswith(">"):
            argv[index] = values[argv[index - 1]]
    rc, err = _run_printed(argv[1:])

    assert rc == 0, f"substituted, the printed command must run: {err!r}"
    assert notes.stale_notes(tmp_path) == [], "and must clear the staleness"


def test_the_printed_mixed_remedy_refuses_rather_than_running_the_placeholder(
        tmp_path, capsys):
    """Run verbatim it must refuse, say why, and leave the note alone."""
    from core import eos as eos_cli

    note_path = _mixed_note(tmp_path)
    eos_cli.main(["note", "audit", "--path", str(tmp_path)])
    before = notes.parse_note(note_path).scope

    for line in _amend_lines(capsys.readouterr().out):
        argv = shlex.split(line[line.index("eos note amend"):])
        rc, err = _run_printed(argv[1:])
        assert rc != 0, f"running this verbatim must not succeed: {line}"
        assert err.strip(), "and must say why"
    assert notes.parse_note(note_path).scope == before, "the note must be untouched"
