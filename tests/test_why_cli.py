"""`eos why` -- the command that keeps the provenance schema from staying empty.

This project has paid for the lesson once: a predecessor defined full schemas
for lessons, patterns, history and relationships, and four of them held nothing
across five snapshots. The one directory with data was the one with a CLI
command. A provenance column nobody can read is that failure repeated.

The line that matters most is the coverage line, because it answers "did you
not find it, or did you not look" at the moment the question is asked.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
EOS = [sys.executable, str(REPO / "core" / "eos.py")]
FIXTURE = Path(__file__).parent / "fixtures" / "polyglot_project"


def _run(args, **kw):
    return subprocess.run(EOS + args, capture_output=True, text=True, encoding="utf-8", **kw)


def _scanned(tmp_path: Path) -> Path:
    root = tmp_path / "polyglot"
    shutil.copytree(FIXTURE, root)
    assert _run(["init", str(root), "--no-ai"]).returncode == 0
    done = _run(["scan", str(root), "--full"])
    assert done.returncode == 0, done.stderr
    return root


def test_why_reports_the_detector_that_produced_an_edge(tmp_path):
    root = _scanned(tmp_path)

    done = _run(["why", str(root), "web/src/index.ts"])

    assert done.returncode == 0, done.stderr
    assert "import-edge" in done.stdout, done.stdout
    assert "imports@1" in done.stdout, done.stdout
    assert "extracted" in done.stdout, done.stdout
    assert "web/src/index.ts:1" in done.stdout, (
        f"the answer does not say which line it was read from:\n{done.stdout}"
    )


def test_why_separates_an_inbound_reference_from_an_outbound_one(tmp_path):
    root = _scanned(tmp_path)

    done = _run(["why", str(root), "web/src/components/Button.tsx"])

    assert done.returncode == 0, done.stderr
    assert "<- import-edge" in done.stdout, (
        f"a fact about who imports this file must not read as one about what it imports:\n{done.stdout}"
    )


def test_why_names_the_detector_that_looked_and_found_nothing(tmp_path):
    root = _scanned(tmp_path)

    done = _run(["why", str(root)])

    assert done.returncode == 0, done.stderr
    assert "coverage:" in done.stdout, done.stdout
    assert "imports@1 looked at" in done.stdout, (
        f"coverage must name the detector and the population it examined:\n{done.stdout}"
    )


def test_why_emits_machine_readable_json(tmp_path):
    root = _scanned(tmp_path)

    done = _run(["why", str(root), "web/src/index.ts", "--format", "json"])

    assert done.returncode == 0, done.stderr
    answer = json.loads(done.stdout)
    assert answer["facts"], answer
    assert {"origin", "confidence", "detector", "source_ref", "observed_at"} <= set(answer["facts"][0])
    assert answer["coverage"], "the json answer dropped coverage"


def test_why_filters_by_predicate(tmp_path):
    root = _scanned(tmp_path)

    done = _run(["why", str(root), "web/src/index.ts", "--predicate", "nothing-produces-this",
                 "--format", "json"])

    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout)["facts"] == []


def test_why_refuses_a_path_that_is_not_indexed(tmp_path):
    root = _scanned(tmp_path)

    done = _run(["why", str(root), "does/not/exist.ts"])

    assert done.returncode == 1
    assert len(done.stderr.strip().splitlines()) == 1, done.stderr
    assert "No indexed node" in done.stderr


def test_why_builds_a_missing_index_rather_than_refusing(tmp_path):
    """Same contract as `eos query`: a reader checks its sources before it
    trusts them, and an index nobody built yet is just the first such check."""
    root = tmp_path / "bare"
    (root / ".eos").mkdir(parents=True)

    done = _run(["why", str(root)])

    assert done.returncode == 0, done.stderr
    assert (root / ".eos" / "data" / "eos.db").is_file(), "why did not build the index"


def test_why_reports_a_path_that_is_not_in_a_freshly_built_index(tmp_path):
    root = tmp_path / "bare"
    (root / ".eos").mkdir(parents=True)

    done = _run(["why", str(root), "anything.py"])

    assert done.returncode == 1
    assert "No indexed node" in done.stderr, done.stderr


def test_the_coverage_line_answers_for_the_file_that_was_asked_about(tmp_path):
    """A project-wide tally is not an answer about one file.

    The promise is that `why` separates "found nothing here" from "never
    looked here". An eval session was told a file reached nothing, came here
    for the reason, and got eleven global counters -- "looked at 377 file(s)"
    -- which left it exactly where it started. It went back to reading source.
    """
    root = _scanned(tmp_path)

    done = _run(["why", str(root), "pkg/helper.py"])

    assert done.returncode == 0, done.stderr
    lines = [line for line in done.stdout.splitlines() if "coverage:" in line]
    assert lines, done.stdout
    for line in lines:
        assert any(mark in line for mark in
                   ("here", "does not read files like this one",
                    "records structure", "not derivable")), line


def test_a_detector_that_does_not_read_this_language_says_so(tmp_path):
    """"Found nothing" and "does not read this kind of file" are different
    facts, and only one of them is about the file."""
    root = _scanned(tmp_path)

    done = _run(["why", str(root), "pkg/helper.py", "--format", "json"])

    assert done.returncode == 0, done.stderr
    answer = json.loads(done.stdout)
    verdicts = {entry["detector"]: entry["applies_here"] for entry in answer["coverage"]}
    assert verdicts, answer
    assert set(verdicts.values()) <= {"yes", "no", "structural", "unknown"}, verdicts
    for entry in answer["coverage"]:
        assert "hits_here" in entry, entry


def test_coverage_carries_no_per_file_verdict_without_a_subject(tmp_path):
    """Asked about the project rather than a file, the honest answer is the
    tally alone -- there is no file for a verdict to be about."""
    root = _scanned(tmp_path)

    answer = json.loads(_run(["why", str(root), "--format", "json"]).stdout)

    assert answer["coverage"], answer
    assert all("applies_here" not in entry for entry in answer["coverage"]), answer["coverage"]
