"""The composed context: no section said twice, and the target section answers.

Two measurements drove this, both on one real service.

The brain is five documents and four of them list the entry points, so
concatenating them repeated the same content: "Entry Points" headed four
separate sections of the composed context and one controller's name appeared
four times. 9,350 of 20,804 characters -- 45% of an agent's budget -- were
spent saying the same thing again. Deduplicating by heading brought the same
context to 11,345 characters with nothing lost.

The target section was `json.dumps(impact(...))`: two lists of paths an agent
has to read in full to learn anything from. It now answers instead -- what
reaches the file, what it reaches, what it can refuse with, and what the tests
do about each refusal.
"""
import shutil
import subprocess
import sys
from pathlib import Path

from core import inspector

REPO = Path(__file__).resolve().parents[1]
EOS = [sys.executable, str(REPO / "core" / "eos.py")]
FIXTURE = Path(__file__).parent / "fixtures" / "java_structure" / "svc"

COMMAND = "src/main/java/com/example/orders/ValidateAgeCommand.java"


def _run(args, **kw):
    return subprocess.run(EOS + args, capture_output=True, text=True, encoding="utf-8", **kw)


def _scanned(tmp_path: Path) -> Path:
    root = tmp_path / "svc"
    shutil.copytree(FIXTURE, root)
    assert _run(["init", str(root), "--no-ai"]).returncode == 0
    done = _run(["scan", str(root), "--full"])
    assert done.returncode == 0, done.stderr
    return root


def _headings(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.startswith("#")]


def test_no_heading_is_covered_twice(tmp_path):
    context = inspector.build_context(_scanned(tmp_path))

    keys = [inspector._heading_key(heading) for heading in _headings(context)]
    repeated = {key for key in keys if keys.count(key) > 1}
    assert not repeated, f"the same topic is covered more than once: {sorted(repeated)}"


def test_a_heading_that_differs_only_by_an_aside_is_the_same_topic():
    """'## Entry Points (linked parent, not this project)' is the third
    document's version of a list the first already gave."""
    assert inspector._heading_key("## Entry Points (linked parent, not this project)") \
        == inspector._heading_key("# Entry Points")


def test_the_first_document_to_cover_a_topic_keeps_its_content(tmp_path):
    """Deduplicating must drop repeats, not the subject."""
    context = inspector.build_context(_scanned(tmp_path))

    assert "OrderController" in context, context[:600]
    assert context.count("OrderController.java") <= 2, (
        f"the entry point is still listed by several documents: {context.count('OrderController.java')}"
    )


def test_the_target_section_says_what_reaches_the_file(tmp_path):
    root = _scanned(tmp_path)

    context = inspector.build_context(root, target=COMMAND)

    assert "## What Is Known About The Target" in context, context[:800]
    assert "ValidateAgeCommandTest.java" in context, (
        "the test that reaches the target is the first thing worth knowing about it"
    )


def test_the_target_section_grades_the_refusals_the_file_can_raise(tmp_path):
    root = _scanned(tmp_path)

    context = inspector.build_context(root, target=COMMAND)

    assert "AGE_LIMIT` — asserted" in context, context
    assert "AGE_IMPLAUSIBLE` — reachable" in context, (
        "the actionable case -- the class is under test and this branch is not "
        f"asserted -- must reach the agent:\n{context}"
    )


def test_a_target_with_nothing_known_gets_no_empty_heading(tmp_path):
    """A heading with nothing under it costs budget and teaches the reader that
    the heading is not worth reading."""
    root = _scanned(tmp_path)

    context = inspector.build_context(root, target="src/main/java/com/example/orders/CommandResult.java")

    if "## What Is Known About The Target" in context:
        body = context.split("## What Is Known About The Target", 1)[1].split("\n#", 1)[0]
        assert body.strip(), "the section was emitted with an empty body"


def test_the_target_section_replaced_the_json_dump(tmp_path):
    root = _scanned(tmp_path)

    context = inspector.build_context(root, target=COMMAND)

    assert "## Target Impact" not in context, (
        "the raw impact JSON is back; it costs budget and answers nothing"
    )


def test_a_task_still_reaches_the_note_ranking(tmp_path):
    root = _scanned(tmp_path)
    assert _run(["note", "add", str(root), "--kind", "finding",
                 "--title", "Age limit rejects boundary values",
                 "--body", "The limit is inclusive, which surprised two reviewers."]).returncode == 0

    context = inspector.build_context(root, task="age limit boundary")

    assert "## Task" in context
    assert "Age limit rejects boundary values" in context, context[-1500:]
