"""Drafting a test for a refusal nothing asserts.

Deliberately narrow, because the valuable gap is narrow. Measured at 134 of
403 codes on one real service: a test already reaches the class that raises a
refusal and nothing checks that branch. The fixture exists, the mocks exist,
the file exists -- what is missing is one method.

A draft that does not look like its neighbours is not reviewed, it is deleted,
so the style is read off the file the method would join rather than taken from
a table of defaults. And every symbol it names is grounded against the source
first: a plausible guess in generated code is worse than a refusal, because it
survives review by looking right.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from core import testgen

REPO = Path(__file__).resolve().parents[1]
EOS = [sys.executable, str(REPO / "core" / "eos.py")]
FIXTURE = Path(__file__).parent / "fixtures" / "java_structure" / "svc"


def _run(args, **kw):
    return subprocess.run(EOS + args, capture_output=True, text=True, encoding="utf-8", **kw)


def _scanned(tmp_path: Path) -> Path:
    root = tmp_path / "svc"
    shutil.copytree(FIXTURE, root)
    assert _run(["init", str(root), "--no-ai"]).returncode == 0
    done = _run(["scan", str(root), "--full"])
    assert done.returncode == 0, done.stderr
    return root


def test_a_draft_joins_the_test_that_already_reaches_the_class(tmp_path):
    """AGE_IMPLAUSIBLE is the reachable case: the test holds the class as a
    field and never asserts this refusal."""
    draft = testgen.draft_for(_scanned(tmp_path), "AGE_IMPLAUSIBLE")

    assert draft.status == testgen.STATUS_DRAFT, draft.refused
    assert draft.test_exists is True, draft
    assert draft.test_path.endswith("ValidateAgeCommandTest.java"), draft.test_path
    assert "class ValidateAgeCommand is declared" in " ".join(draft.grounded), draft.grounded


def test_a_draft_asserts_the_code_it_was_asked_about(tmp_path):
    draft = testgen.draft_for(_scanned(tmp_path), "AGE_IMPLAUSIBLE")

    assert '"AGE_IMPLAUSIBLE"' in draft.body, draft.body
    assert "raisesAgeImplausible" in draft.body, draft.body


def test_a_draft_says_it_is_a_draft_inside_the_code(tmp_path):
    """The one thing it cannot know is the arrangement that reaches the branch.
    Saying so in the method is what keeps it from being pasted and trusted."""
    draft = testgen.draft_for(_scanned(tmp_path), "AGE_IMPLAUSIBLE")

    assert "DRAFT" in draft.body, draft.body
    assert "arrange" in draft.body.lower(), draft.body


def test_the_style_is_read_from_the_file_the_method_would_join(tmp_path):
    root = _scanned(tmp_path)
    existing = root / "src" / "test" / "java" / "com" / "example" / "orders" / "ValidateAgeCommandTest.java"
    existing.write_text(existing.read_text(encoding="utf-8")
        .replace("import static org.assertj.core.api.Assertions.assertThatThrownBy;",
                 "import static org.junit.jupiter.api.Assertions.assertThrows;")
        .replace("assertThatThrownBy(() -> command.execute(new OrderContext(17)))\n"
                 '                .hasMessageContaining("AGE_LIMIT");',
                 'assertThrows(RuntimeException.class, () -> command.execute(new OrderContext(17)));'),
        encoding="utf-8")
    assert _run(["scan", str(root), "--full"]).returncode == 0

    draft = testgen.draft_for(root, "AGE_IMPLAUSIBLE")

    assert draft.style["assertions"] == "junit", draft.style
    assert "assertThrows" in draft.body, draft.body
    assert "assertThatThrownBy" not in draft.body, draft.body


def test_a_code_thrown_only_in_a_linked_parent_is_refused(tmp_path):
    """A draft for a class in a tree this project does not own would be written
    somewhere it must not be written."""
    root = _scanned(tmp_path)
    # Simulate the parent-only shape the real services have.
    import sqlite3
    from core import index
    conn = sqlite3.connect(index.db_path(root))
    try:
        conn.execute("UPDATE node SET path = '@parent:' || path WHERE path LIKE 'src/main/%'")
        conn.commit()
    finally:
        conn.close()

    draft = testgen.draft_for(root, "AGE_IMPLAUSIBLE")

    assert draft.status == "refused", draft
    assert "linked parent" in draft.refused, draft.refused


def test_an_unknown_code_is_refused_by_name(tmp_path):
    draft = testgen.draft_for(_scanned(tmp_path), "NO_SUCH_CODE")

    assert draft.status == "refused"
    assert "NO_SUCH_CODE" in draft.refused


def test_a_draft_is_written_beside_the_index_never_into_the_source_tree(tmp_path):
    root = _scanned(tmp_path)
    draft = testgen.draft_for(root, "AGE_IMPLAUSIBLE")

    target = testgen.write_draft(root, draft)

    assert target.parent == root / ".eos" / "data" / "candidates", target
    assert "src" not in target.relative_to(root).parts[:1], target
    assert "DRAFT" in target.read_text(encoding="utf-8")


def test_the_cli_refuses_with_an_exit_code(tmp_path):
    root = _scanned(tmp_path)

    done = _run(["draft-test", str(root), "NO_SUCH_CODE"])

    assert done.returncode == 1
    assert "refused" in done.stderr


def test_the_cli_emits_json(tmp_path):
    root = _scanned(tmp_path)

    done = _run(["draft-test", str(root), "AGE_IMPLAUSIBLE", "--format", "json"])

    assert done.returncode == 0, done.stderr
    payload = json.loads(done.stdout)
    assert payload["status"] == "draft"
    assert payload["grounded"], payload


def test_without_an_index_it_says_how_to_build_one(tmp_path):
    root = tmp_path / "bare"
    (root / ".eos").mkdir(parents=True)

    with pytest.raises(ValueError, match="eos index"):
        testgen.draft_for(root, "ANY_CODE")


def test_a_code_passed_as_an_argument_is_not_asserted_through_the_message(tmp_path):
    """`ValidationException.of("CODE", "human text")` puts the code in an
    argument and never in the message, so `hasMessageContaining(code)` fails
    for a reason that has nothing to do with the behaviour under test.

    Measured on a real service: the draft asserted the message, and the
    message read "Invalid numeric value for 'topUpAmount'".
    """
    draft = testgen.draft_for(_scanned(tmp_path), "AGE_IMPLAUSIBLE")

    assert draft.status == testgen.STATUS_DRAFT, draft.refused
    assert 'hasMessageContaining("AGE_IMPLAUSIBLE")' not in draft.body, draft.body
    assert "ValidationException" in draft.body, draft.body
    assert ".code()" in draft.body, "the accessor is declared in the fixture and must be used"
    assert '.isEqualTo("AGE_IMPLAUSIBLE")' in draft.body, draft.body
    assert any("code()" in line for line in draft.grounded), draft.grounded


def test_a_private_method_is_driven_through_a_public_entry(tmp_path):
    """A draft that names a private method does not compile, and a reviewer
    told "draft" finds that out only after wiring it up.

    Measured: the draft called `fmTopUpValidationCommand.parseBigDecimal(...)`
    on a private method, while the file's existing tests drive the same throw
    through the public `execute`.
    """
    root = _scanned(tmp_path)
    pkg = root / "src" / "main" / "java" / "com" / "example" / "orders"
    (pkg / "CheckLimitCommand.java").write_text(
        "package com.example.orders;\n"
        "public class CheckLimitCommand {\n"
        "    public CommandResult execute(OrderContext context) {\n"
        "        parseAmount(context);\n"
        "        return new CommandResult(true, null);\n"
        "    }\n"
        "    private void parseAmount(OrderContext context) {\n"
        '        throw ValidationException.of("LIMIT_UNPARSEABLE", "not a number");\n'
        "    }\n"
        "}\n", encoding="utf-8")
    assert _run(["scan", str(root), "--full"]).returncode == 0

    draft = testgen.draft_for(root, "LIMIT_UNPARSEABLE")

    assert draft.status == testgen.STATUS_DRAFT, draft.refused
    assert "parseAmount(" not in draft.body, (
        "the draft calls a private method; it cannot compile\n" + draft.body)
    assert ".execute(" in draft.body, draft.body
    assert "parseAmount is private" in draft.body, "it must say why it went in another way"


def test_a_class_with_no_way_in_is_refused_rather_than_drafted(tmp_path):
    """Refusing is the documented behaviour when a symbol cannot be grounded,
    and "there is no callable entry point" is that case, not a special one."""
    root = _scanned(tmp_path)
    pkg = root / "src" / "main" / "java" / "com" / "example" / "orders"
    (pkg / "SealedCommand.java").write_text(
        "package com.example.orders;\n"
        "public class SealedCommand {\n"
        "    private void onlyWayIn() {\n"
        '        throw ValidationException.of("SEALED_OFF", "no");\n'
        "    }\n"
        "}\n", encoding="utf-8")
    assert _run(["scan", str(root), "--full"]).returncode == 0

    draft = testgen.draft_for(root, "SEALED_OFF")

    assert draft.status == "refused", draft.body
    assert "no way in from a test" in draft.refused, draft.refused
