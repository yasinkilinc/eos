"""core.verify: which changes are verified (ADR-028)."""
import os

from core import verify

TOML = r'''
[[scope]]
name = "svc"
paths = ["../services/{service}/src/**"]
passes = ['tools/mvn\.sh\s+(test|verify)\s+{service}\b']
run = "tools/mvn.sh test {service}"

[[scope]]
name = "scripts"
paths = ["scripts/**/*.sh"]
passes = ['scripts/tests/test-[\w-]+\.sh']
run = "the matching scripts/tests/test-*.sh"
'''


def _project(tmp_path, text=TOML):
    root = tmp_path / "ws" / "hub"
    (root / ".eos" / "knowledge").mkdir(parents=True)
    (root / ".eos" / "config.toml").write_text('[knowledge]\ndir = ".eos/knowledge"\n', encoding="utf-8")
    (root / ".eos" / "knowledge" / verify.FILENAME).write_text(text, encoding="utf-8")
    return root


def test_the_same_file_spelled_three_ways_is_one_instance(tmp_path):
    root = _project(tmp_path)
    scopes = verify.load(root)
    source = tmp_path / "ws" / "services" / "billing" / "src" / "main" / "A.java"
    source.parent.mkdir(parents=True)
    source.write_text("class A {}", encoding="utf-8")
    link = tmp_path / "link"
    os.symlink(tmp_path / "ws", link)
    spellings = ["../services/billing/src/main/A.java", str(source), str(link / "services/billing/src/main/A.java")]
    assert {verify.instance(scopes, root, s) for s in spellings} == {"svc:billing"}
    assert verify.instance(scopes, root, "docs/readme.md") is None
    assert verify.instance(scopes, root, "scripts/a/b.sh") == "scripts"


def test_only_a_command_that_runs_the_check_clears_its_instance(tmp_path):
    scopes = verify.load(_project(tmp_path))
    keys = ["svc:billing", "scripts"]
    assert verify.cleared(scopes, "cd hub && tools/mvn.sh test billing", keys) == ["svc:billing"]
    assert verify.cleared(scopes, "tools/mvn.sh test orders", keys) == []
    assert verify.cleared(scopes, "grep 'tools/mvn.sh test billing' notes.md", keys) == []
    assert verify.cleared(scopes, "echo tools/mvn.sh test billing", keys) == []
    assert verify.cleared(scopes, "tools/mvn.sh test billing | tail -5", keys) == []
    assert verify.cleared(scopes, "set -o pipefail && tools/mvn.sh test billing | tail -5", keys) == ["svc:billing"]
    assert verify.cleared(scopes, "bash scripts/tests/test-x.sh", keys) == ["scripts"]


def test_dirty_follows_the_order_of_changes_and_passes(tmp_path):
    root = _project(tmp_path)
    scopes = verify.load(root)
    a = "../services/billing/src/A.java"
    events = [("changed", a), ("passed", "svc:billing"), ("changed", "docs/x.md")]
    assert verify.dirty(scopes, root, events) == []
    events.append(("changed", a))
    assert verify.dirty(scopes, root, events) == [("svc:billing", 3)]
    assert verify.dirty(scopes, root, events + [("passed", "svc:orders")]) == [("svc:billing", 3)]


def test_a_missing_or_broken_file_means_no_scopes(tmp_path):
    assert verify.load(_project(tmp_path, "[[scope]\nname = 'x'")) == []
    root = _project(tmp_path / "other")
    (root / ".eos" / "knowledge" / verify.FILENAME).unlink()
    assert verify.load(root) == []
    assert verify.load(_project(tmp_path / "bad", "[[scope]]\nname='x'\npaths=['a/**']\npasses=['(']\nrun='r'\n")) == []


def test_the_reason_names_three_instances_and_stays_short(tmp_path):
    scopes = verify.load(_project(tmp_path))
    found = [(f"svc:s{i}", i) for i in range(6)]
    text = verify.reason(scopes, found)
    assert "svc:s0" in text and "tools/mvn.sh test s0" in text and "svc:s3" not in text
    assert "(+3 more)" in text and len(text) <= verify.MAX_REASON
    assert verify.signature(found) == verify.signature(list(reversed(found)))


# --- review findings (branch review, 2026-09-27) ---------------------------------------


def test_a_check_whose_exit_code_is_not_the_commands_does_not_clear(tmp_path):
    scopes = verify.load(_project(tmp_path))
    keys = ["svc:billing"]
    for command in ("tools/mvn.sh test billing || true", "tools/mvn.sh test billing; echo done",
                    "tools/mvn.sh test billing &", "tools/mvn.sh test billing && sed -i s/a/b/ x.java",
                    "git commit -F - <<'EOF'\ntools/mvn.sh test billing\nEOF",
                    "echo start\ntools/mvn.sh test billing"):
        assert verify.cleared(scopes, command, keys) == [], command
    assert verify.cleared(scopes, "cd hub && tools/mvn.sh test billing", keys) == keys


def test_a_value_is_matched_whole(tmp_path):
    scopes = verify.load(_project(tmp_path))
    assert verify.cleared(scopes, "tools/mvn.sh test billing-batch", ["svc:billing"]) == []


def test_each_path_pattern_names_its_own_placeholders(tmp_path):
    text = ("[[scope]]\nname = \"two\"\npaths = [\"a/{x}/**\", \"b/{y}/**\"]\n"
            "passes = ['make\\s+test-{x}', 'make\\s+check-{y}']\nrun = \"make check-{y}\"\n")
    root = _project(tmp_path, text)
    scopes = verify.load(root)
    key = verify.instance(scopes, root, "b/two/file.txt")
    assert verify.cleared(scopes, "make check-two", [key]) == [key]
    assert verify.cleared(scopes, "make test-", [key]) == []
    assert verify.run_hint(scopes, key) == "make check-two"


def test_a_repeated_placeholder_in_a_path_is_one_value(tmp_path):
    text = "[[scope]]\nname = \"rep\"\npaths = [\"svc/{s}/{s}/**\"]\npasses = ['make\\s+{s}']\nrun = \"make {s}\"\n"
    root = _project(tmp_path, text)
    scopes = verify.load(root)
    assert verify.instance(scopes, root, "svc/a/a/x.py") == "rep:a"
    assert verify.instance(scopes, root, "svc/a/b/x.py") is None
