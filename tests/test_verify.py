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
    assert "no `| tail`" in text           # measured live: a piped check did not count and nobody knew why
    assert verify.signature(found) == verify.signature(list(reversed(found)))


# --- review findings (branch review, 2026-09-27) ---------------------------------------


def test_a_check_whose_exit_code_is_not_the_commands_does_not_clear(tmp_path):
    scopes = verify.load(_project(tmp_path))
    keys = ["svc:billing"]
    for command in ("tools/mvn.sh test billing || true", "tools/mvn.sh test billing; echo done",
                    "tools/mvn.sh test billing &", "tools/mvn.sh test billing && sed -i s/a/b/ x.java",
                    "git commit -F - <<'EOF'\ntools/mvn.sh test billing\nEOF"):
        assert verify.cleared(scopes, command, keys) == [], command
    assert verify.cleared(scopes, "cd hub && tools/mvn.sh test billing", keys) == keys
    # The last line's status is the command's (second review: the first rule was too strict).
    assert verify.cleared(scopes, "echo start\ntools/mvn.sh test billing", keys) == keys


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


# --- second review findings --------------------------------------------------------------

SECOND = r'''
[[scope]]
name = "unit"
paths = ["src/{name}.py"]
passes = ['scripts/test-{name}\.sh', 'pytest\s+tests/test_{name}', 'make\s+test\b']
run = "pytest tests/test_{name}.py"

[[scope]]
name = "mixed"
paths = ["lib/{part}/**", "lib/shared.py"]
passes = ['make\s+check-{part}']
run = "make check-{part}"
'''


def _cleared(tmp_path, command, key="unit:billing"):
    return verify.cleared(verify.load(_project(tmp_path, SECOND)), command, [key]) == [key]


def test_text_after_a_placeholder_bounds_the_value_itself(tmp_path):
    assert _cleared(tmp_path, "scripts/test-billing.sh")
    assert _cleared(tmp_path / "b", "pytest tests/test_billing.py")
    assert not _cleared(tmp_path / "c", "scripts/test-billingx.sh")


def test_commands_a_person_would_expect_to_count_do(tmp_path):
    for n, command in enumerate(["make test && echo PASS", "cd x; make test", "cd x\nmake test",
                                 "# run the tests\nmake test", "set -o pipefail; make test | tail -5",
                                 "make test 2>&1", "make test &> out.log", "VAR=1 make test",
                                 "(cd x && make test)", "time make test", "nice make test",
                                 "timeout 600 make test", "make test \\\n  --verbose"]):
        assert _cleared(tmp_path / str(n), command), command


def test_commands_whose_status_is_not_the_checks_still_do_not(tmp_path):
    for n, command in enumerate(["make test <<< y || true", "make test <<EOF || true\nx\nEOF",
                                 "make test <<EOF\nx\nEOF\necho done", "make test | tail # pipefail",
                                 "make test; echo done", "make test || true", "make test &",
                                 "echo 'make test; ok'"]):
        assert not _cleared(tmp_path / str(n), command), command


def test_a_quoted_separator_is_not_a_separator(tmp_path):
    assert _cleared(tmp_path, "make test ARGS='a;b'")


def test_a_file_matched_without_a_placeholder_can_still_clear(tmp_path):
    root = _project(tmp_path, SECOND)
    scopes = verify.load(root)
    key = verify.instance(scopes, root, "lib/shared.py")
    assert key == "mixed" and verify.cleared(scopes, "make check-core", [key]) == [key]
    assert verify.cleared(scopes, "make check-", [key]) == []


# --- third review findings ---------------------------------------------------------------


def test_a_check_inside_a_command_substitution_does_not_count(tmp_path):
    for n, command in enumerate(["echo start; echo $(false; make test)", "echo `false; make test`",
                                 "echo $(true && make test)", "echo \"$(make test)\" | cat"]):
        assert not _cleared(tmp_path / str(n), command), command


def test_a_check_run_through_a_shell_dash_c_counts(tmp_path):
    for n, command in enumerate(['bash -c "make test"', "sh -c 'make test'", 'bash -lc "cd x && make test"']):
        assert _cleared(tmp_path / str(n), command), command
    assert not _cleared(tmp_path / "x", 'bash -c "make test || true"')


# --- measured on the host's sessions: 24 of 47 unchecked instances had run the check -----


def test_a_shell_named_by_its_path_passes_the_check_through(tmp_path):
    for n, command in enumerate(["/bin/bash make test", "/usr/bin/env make test", "/usr/bin/time make test"]):
        assert _cleared(tmp_path / str(n), command), command


def test_under_errexit_a_check_ending_its_own_command_counts_wherever_it_is(tmp_path):
    for n, command in enumerate(["set -e\nmake test\necho done", "set -euo pipefail; cd x; make test; git status",
                                 "set -o errexit -o pipefail\nmake test 2>&1 | tail -3\necho done",
                                 "set -e; cd x && make test; echo done"]):
        assert _cleared(tmp_path / str(n), command), command
    for n, command in enumerate(["set -e; make test && echo ok; echo done",      # a failing && head does not exit
                                 "set -e; make test || true; echo done",
                                 "set -e; make test | tail -3; echo done",      # no pipefail: tail's status
                                 "set -e; set +e; make test; echo done",
                                 "make test; set -e; echo done"]):
        assert not _cleared(tmp_path / f"x{n}", command), command


def test_a_check_run_so_it_cannot_count_is_told_apart_from_no_check(tmp_path):
    scopes = verify.load(_project(tmp_path, SECOND))
    keys = ["unit:billing"]
    assert verify.attempted(scopes, "make test 2>&1 | tail -4; git status", keys) == keys
    assert verify.attempted(scopes, "cd x && make test; echo done", keys) == keys
    assert verify.attempted(scopes, "make test", keys) == []                 # it counted
    assert verify.attempted(scopes, "git add Makefile && echo make test", keys) == []


# --- fifth review findings ---------------------------------------------------------------


def test_errexit_does_not_reach_conditions_function_bodies_or_subshells(tmp_path):
    for n, command in enumerate(["set -e\nif echo lint && make test; then echo ok; fi\necho after",
                                 "set -e\nwhile make test; do break; done\necho after",
                                 "set -e\nf() { true && make test; }\necho after",
                                 "set -e; set +o errexit; true && make test; echo after",
                                 "(set -e; make test); echo after",
                                 "set -eo pipefail; set +o pipefail; make test | tail -3"]):
        assert not _cleared(tmp_path / str(n), command), command
    assert _cleared(tmp_path / "ok", "set -e; (cd x && make test); echo after")


def test_a_quoted_check_is_not_an_attempt(tmp_path):
    scopes = verify.load(_project(tmp_path, SECOND))
    assert verify.attempted(scopes, 'echo "(make test)"; true', ["unit:billing"]) == []
