"""Integration: what get_context assembles -- orientation plus accumulated notes.

Writing a note is a dead end unless it comes back, and orientation is a dead
weight unless it stays small. Both are exercised against the real init -> scan
-> context pipeline (a real brain/, not a fabricated one) so they are proven
against what `eos context` actually produces.
"""
import subprocess
import sys
from pathlib import Path

import pytest

from core import inspector, notes

REPO = Path(__file__).resolve().parents[1]


def _scanned_project(tmp_path) -> Path:
    proj = tmp_path / "demo"
    proj.mkdir()
    (proj / "main.py").write_text("import utils\nutils.hi()\n", encoding="utf-8")
    (proj / "utils.py").write_text("def hi():\n    print('hi')\n", encoding="utf-8")
    for args in (["init", str(proj)], ["scan", str(proj), "--full"]):
        r = subprocess.run(
            [sys.executable, str(REPO / "core" / "eos.py"), *args],
            capture_output=True, text=True,
        )
        assert r.returncode == 0, r.stderr
    return proj


def test_build_context_injects_matching_notes(tmp_path):
    proj = _scanned_project(tmp_path)
    notes.add_note(
        proj, kind="finding", title="Connection pool exhausted",
        body="The pool ran out of connections during deposits.",
        tags=["pool", "database"],
    )
    notes.add_note(
        proj, kind="finding", title="Cache warms on boot",
        body="Unrelated startup behaviour.", tags=["cache"],
    )

    context = inspector.build_context(proj, task="database connection pool")

    assert "## Accumulated Knowledge" in context
    assert "Connection pool exhausted" in context


def test_build_context_notes_section_is_capped_near_15_percent_of_budget(tmp_path):
    proj = _scanned_project(tmp_path)
    # Titles and bodies vary per note: normalized_title_key masks digits to a
    # single token, so "Finding number 0" and "Finding number 1" collide;
    # identical bodies trip the body-duplicate check too (core/notes.py).
    topics = [
        "auth", "cache", "queue", "retry", "timeout", "socket", "thread", "buffer",
        "cursor", "index", "schema", "token", "header", "payload", "handler", "router",
        "worker", "client", "server", "session", "cookie", "config", "logger", "metric",
        "signal", "kernel", "driver", "adapter", "mapper", "filter",
    ]
    for i, topic in enumerate(topics):
        notes.add_note(proj, kind="finding", title=f"Finding about {topic}", body=f"Short body {i}.")

    budget = 500  # max_chars = budget * 4 = 2000; notes get ~300 of that
    context = inspector.build_context(proj, budget=budget)

    start = context.index("## Accumulated Knowledge")
    notes_section = context[start:]
    assert len(notes_section) <= 0.20 * (budget * 4), (
        "notes must not crowd out the code context they are supposed to complement"
    )
    assert "omitted" in notes_section.lower()


@pytest.fixture(scope="module")
def large_project(tmp_path_factory) -> Path:
    """A project whose brain is bigger than the context caps used below.

    200 modules is enough for the flat "## Components" listing alone to blow
    past a 6,000-char cap -- the shape of every real FM service, where the
    old build_context filled to budget*4 and threw the rest away.
    """
    proj = tmp_path_factory.mktemp("large") / "svc"
    (proj / "src").mkdir(parents=True)
    (proj / "main.py").write_text(
        '"""Order service entry point."""\nimport src.mod_0\n', encoding="utf-8"
    )
    for i in range(200):
        (proj / "src" / f"mod_{i}.py").write_text(
            f'"""Module {i}."""\nimport src.mod_0\n\n\ndef f{i}():\n    pass\n', encoding="utf-8"
        )
    for args in (["init", str(proj)], ["scan", str(proj), "--full"]):
        r = subprocess.run(
            [sys.executable, str(REPO / "core" / "eos.py"), *args],
            capture_output=True, text=True,
        )
        assert r.returncode == 0, r.stderr
    return proj


def test_context_is_orientation_only_and_sized_by_content_not_by_budget(large_project):
    brain = large_project / ".eos" / "data" / "brain"
    raw_brain = sum(len((brain / n).read_text(encoding="utf-8")) for n in inspector.BRAIN_FILES)
    assert raw_brain > 6000, "premise: the brain must exceed the smaller cap below"

    small = inspector.build_context(large_project, budget=1500)   # cap 6,000 chars
    large = inspector.build_context(large_project, budget=3000)   # cap 12,000 chars

    # The old build_context returned exactly budget*4 for both.
    assert small == large, "context must be sized by its content, not filled to the budget"
    assert len(small) < 3000, f"orientation should stay ~1-2KB, got {len(small)}"

    for enumeration in ("## Components", "## Module Structure", "## Folder Structure"):
        assert enumeration not in small, f"{enumeration} is an inventory, not orientation"

    # Orientation itself must survive the diet.
    assert "## Scale" in small
    assert "## Tech Stack" in small
    assert "## Entry Points" in small
    assert "## Hub Components" in small
    # ...and the reader must be told how to go deeper than orientation.
    assert "find_symbol" in small


def test_context_drops_whole_sections_instead_of_cutting_mid_line(large_project):
    full_lines = set(inspector.build_context(large_project, budget=100000).splitlines())

    squeezed = inspector.build_context(large_project, budget=250)  # cap 1,000 chars

    stray = [
        line for line in squeezed.splitlines()
        if line and "omitted" not in line and line not in full_lines
    ]
    assert not stray, f"context was cut mid-line: {stray}"
    assert "omitted" in squeezed, "a trimmed context must say so, not look complete"


def test_target_file_truncation_is_disclosed_not_silent(tmp_path):
    """compose() embeds the target file; an agent must never be handed a
    silently half-read file and reason about it as if it were whole."""
    proj = tmp_path / "big"
    proj.mkdir()
    (proj / "big.py").write_text(
        "import os\n" + "".join(f"x{i} = 'padding-padding'\n" for i in range(4000)),
        encoding="utf-8",
    )
    for args in (["init", str(proj)], ["scan", str(proj), "--full"]):
        r = subprocess.run(
            [sys.executable, str(REPO / "core" / "eos.py"), *args],
            capture_output=True, text=True,
        )
        assert r.returncode == 0, r.stderr

    # Budget high enough that the section-fit loop never fires: any truncation
    # here comes from read_file's own max_chars, which was being discarded.
    context = inspector.build_context(proj, budget=100000, task="understand", target="big.py")

    assert "truncated" in context.lower(), "a partially embedded target file must say so"


def test_a_huge_note_cannot_blow_past_the_context_budget(tmp_path):
    """The notes module allows one oversized note to overflow its own cap;
    build_context must not let that become an unbounded context."""
    proj = tmp_path / "noted"
    proj.mkdir()
    (proj / "a.py").write_text("def a():\n    pass\n", encoding="utf-8")
    for args in (["init", str(proj)], ["scan", str(proj), "--full"]):
        r = subprocess.run(
            [sys.executable, str(REPO / "core" / "eos.py"), *args],
            capture_output=True, text=True,
        )
        assert r.returncode == 0, r.stderr

    notes.add_note(proj, kind="finding", title="huge", body="B" * 200_000)

    budget = 12000
    context = inspector.build_context(proj, budget=budget)

    assert len(context) <= budget * 4, (
        f"context must respect its ceiling; got {len(context)} for a cap of {budget * 4}"
    )


def test_a_journey_note_is_not_sorted_with_the_endpoint_tables(tmp_path):
    """"Generated" answers the wrong question for ranking.

    `api-inventory` is an extracted table; `journey-map` is prose a person
    wrote in a journey document, moved into a note by a script. Both are
    generated and only one is filler. Measured on a 20-question golden set:
    treating the whole source set as filler dropped recall@1 from 0.95 to
    0.75, because five of the twenty answers were journey notes.
    """
    project = tmp_path / "proj"
    directory = notes.notes_dir(project)
    directory.mkdir(parents=True)

    def write(name, title, source):
        (directory / name).write_text(
            f"---\nkind: finding\ntitle: {title}\ncreated: 2026-09-01\n"
            f"source: {source}\n---\n\nBody of {title}.\n", encoding="utf-8")

    # Newest last, so recency alone would put the inventory first.
    write("20260901-journey.md", "Command chain is data, not Java", "journey-map")
    write("20260902-inventory.md", "REST endpoints of the service", "api-inventory")

    section = notes.render_context_section(project, query=None, max_chars=10_000)

    assert section.index("Command chain is data") < section.index("REST endpoints"), \
        "the journey note carries what someone learned; the table does not"
    assert notes.is_generated(notes.parse_note(directory / "20260901-journey.md")), \
        "it is still generated: hand-editing it is still the wrong repair"
