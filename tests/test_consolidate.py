"""`eos consolidate` (2.x roadmap L1): a fold-only report of what needs attention."""
import subprocess
import sys
from pathlib import Path

from core import consolidate, notes

EOS = [sys.executable, str(Path(__file__).resolve().parents[1] / "core" / "eos.py")]
BASE = ("the nightly job reconciles pending orders with the payment provider and moves every order "
        "that has no settlement after two days into the failed state releasing its reserved number "
        "for the next activation that asks for one in the same pool")


def test_the_report_names_what_needs_attention_and_changes_nothing(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    assert subprocess.run(EOS + ["init", str(root), "--no-ai"], capture_output=True).returncode == 0
    made = subprocess.run(EOS + ["procedure", "new", str(root), "--title", "Release the service", "--steps", "-"],
                          input="Tag it (tool: nothing-here)\n", capture_output=True, text=True)
    assert made.returncode == 0, made.stderr
    alike = BASE.replace("two days", "three days").replace("same pool", "same number pool")
    assert 0.6 <= notes.trigram_jaccard(BASE, alike) < notes.PARAPHRASE_JACCARD
    notes.add_note(root, "finding", "Pending orders are failed by the nightly job", BASE)
    notes.add_note(root, "finding", "Reconciliation releases reserved numbers", alike)
    before = sorted(p.read_text() for p in (root / ".eos" / "knowledge").glob("*.md"))

    data = consolidate.report(root)
    assert data["procedures"]["never_run"] and data["procedures"]["lint"]
    assert data["near_duplicates"] and data["near_duplicates"][0][0] >= 0.6
    text = consolidate.render(data)
    assert "PROCEDURES NEVER RUN (1)" in text and "NOTES THAT READ ALIKE (1)" in text
    assert sorted(p.read_text() for p in (root / ".eos" / "knowledge").glob("*.md")) == before

    done = subprocess.run(EOS + ["consolidate", str(root)], capture_output=True, text=True)
    assert done.returncode == 0 and "Nothing here was changed" in done.stdout


def test_a_step_tool_no_run_ever_recorded_is_named(tmp_path, monkeypatch):
    """Seen on the host: three steps' scripts recorded nothing, so no run could say
    whether they passed -- and nothing said so."""
    monkeypatch.setenv("EOS_STATE_DIR", str(tmp_path / "state"))
    root = tmp_path / "proj"
    root.mkdir()
    assert subprocess.run(EOS + ["init", str(root), "--no-ai"], capture_output=True).returncode == 0
    made = subprocess.run(EOS + ["procedure", "new", str(root), "--title", "Ship it", "--steps", "-",
                                 "--success", "it runs"],
                          input="Build (tool: mvn)\nCheck grants (tool: grants)\n", capture_output=True, text=True)
    slug = made.stdout.split("\t")[0]
    run = subprocess.run(EOS + ["run", "start", str(root), "--title", "Ship once", "--procedure", slug,
                                "--session", "s1"], capture_output=True, text=True).stdout.split()[0]
    subprocess.run(EOS + ["run", "event", str(root), run, "--kind", "ran", "--tool", "automation/mvn.sh",
                          "--exit", "0"], capture_output=True)
    subprocess.run(EOS + ["run", "finish", str(root), run, "--outcome", "ok"], capture_output=True)
    data = consolidate.report(root)
    assert data["procedures"]["silent_steps"] == {slug: ["step 2 (tool: grants)"]}
    assert f"STEPS NO RUN RECORDED (1)" in consolidate.render(data)


def test_a_broken_config_is_an_error_not_a_traceback(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    assert subprocess.run(EOS + ["init", str(root), "--no-ai"], capture_output=True).returncode == 0
    (root / ".eos" / "config.toml").write_text("not valid toml [[[", encoding="utf-8")
    done = subprocess.run(EOS + ["consolidate", str(root)], capture_output=True, text=True)
    assert done.returncode == 1 and "Traceback" not in done.stderr and "error:" in done.stderr


def test_an_invalid_ticket_pattern_in_config_costs_only_the_stale_index_line(tmp_path):
    """RVg (review of D3-D4): `index.is_stale` (fed6e4a) shared `_sources_digest`
    with `index.refresh()`, and an invalid `[index] ticket_pattern`/
    `merge_branch_pattern` there makes `_ticket_pattern`/`_merge_branch_pattern`
    raise `index.IndexBuildError` -- right for `build()`/`refresh()`, which must
    refuse to write from a config nobody can act on, but originally left
    uncaught by `is_stale()` itself, so it reached `cmd_consolidate` (which only
    ever expected `(OSError, ValueError)`, the malformed-TOML-syntax case the
    sibling test above covers) as a raw traceback. Fixed at the root: `is_stale`
    catches `IndexBuildError` and answers `None` ("cannot tell"), the same
    signal an unbuilt index already gives -- so a config field irrelevant to
    every other section of the report costs only the one line about it,
    matching how `cmd_query`/`cmd_impact` already treat the identical error
    from `index.refresh()` (a warning, still answering) rather than aborting."""
    from core import index, notes

    root = tmp_path / "proj"
    root.mkdir()
    assert subprocess.run(EOS + ["init", str(root), "--no-ai"], capture_output=True).returncode == 0
    index.build(root)
    notes.add_note(root, "lesson", "Registry was down",
                   "## What went wrong\nRegistry down.\n\n## What was learned\ny\n\n## Next time\nz\n")
    config = root / ".eos" / "config.toml"
    config.write_text(config.read_text(encoding="utf-8") + '\n[index]\nticket_pattern = "["\n', encoding="utf-8")

    done = subprocess.run(EOS + ["consolidate", str(root)], capture_output=True, text=True)
    assert done.returncode == 0 and "Traceback" not in done.stderr
    assert "STALE" not in done.stdout and "LESSONS WITHOUT EVIDENCE (1)" in done.stdout


def test_a_config_of_the_wrong_shape_is_read_as_no_setting(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    assert subprocess.run(EOS + ["init", str(root), "--no-ai"], capture_output=True).returncode == 0
    (root / ".eos" / "config.toml").write_text('knowledge = "oops"\n', encoding="utf-8")
    done = subprocess.run(EOS + ["consolidate", str(root)], capture_output=True, text=True)
    assert done.returncode == 0 and "Traceback" not in done.stderr


def test_lessons_without_evidence_are_counted(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    assert subprocess.run(EOS + ["init", str(root), "--no-ai"], capture_output=True).returncode == 0
    notes.add_note(root, "lesson", "Registry was down",
                   "## What went wrong\nRegistry down.\n\n## What was learned\ny\n\n## Next time\nz\n")
    notes.add_note(root, "lesson", "Build broke on main",
                   "## What went wrong\nBuild broke.\n\n## What was learned\ny\n\n## Next time\nz\n",
                   evidence="automation/jenkins.sh log build-42")

    data = consolidate.report(root)

    assert data["lessons_without_evidence"] == ["Registry was down"]
    assert "LESSONS WITHOUT EVIDENCE (1)" in consolidate.render(data)


def test_a_note_citing_a_replaced_note_is_named(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    assert subprocess.run(EOS + ["init", str(root), "--no-ai"], capture_output=True).returncode == 0
    old = notes.add_note(root, "finding", "Retries use a fixed delay", "The gateway retry waits two seconds.")
    notes.add_note(root, "finding", "Cancel releases the number", f"Follows from note:{old.stem}.")
    notes.add_note(root, "finding", "Retries back off", "The gateway retry now doubles its wait.",
                   supersedes=old.name)
    data = consolidate.report(root)
    assert data["cites_replaced"] == [("Cancel releases the number", "Retries use a fixed delay")]
    assert "NOTES CITING A REPLACED NOTE (1)" in consolidate.render(data)


def test_no_index_built_yet_is_not_reported_as_stale(tmp_path):
    """L1 remainder, item (a): a project that never ran `eos index`/`eos scan`
    has nothing to compare a fresh digest against -- "not built" is not a claim
    that the (nonexistent) index is stale."""
    root = tmp_path / "proj"
    root.mkdir()
    assert subprocess.run(EOS + ["init", str(root), "--no-ai"], capture_output=True).returncode == 0
    data = consolidate.report(root)
    assert data["stale_index"] is None
    assert "STALE" not in consolidate.render(data)


def test_report_does_not_crash_when_the_sources_digest_cannot_be_computed(tmp_path):
    """`index.is_stale` is documented as a pure read that only ever answers
    None/True/False -- the same "cannot measure, never an exception" contract
    `report()`'s own `touched_files`/`change_capture` follow. But it shares
    `_sources_digest` with `index.refresh()`, and `_sources_digest` calls
    `_ticket_pattern()`, which *deliberately* raises `IndexBuildError` for a
    project whose `.eos/config.toml` `[index] ticket_pattern` is not a valid
    regex (by design for a mutating build/refresh -- see `_ticket_pattern`'s
    own docstring). `eos query`'s `cmd_query` already knows this and wraps
    `index.refresh()` in `try/except (index.IndexBuildError, OSError,
    ValueError, sqlite3.Error)`, degrading to a stderr warning and still
    answering the query. `consolidate.report()` wraps nothing around
    `index.is_stale(root)`, so the exact same broken-regex config that `eos
    query` tolerates takes down the whole `eos consolidate` report -- not just
    the stale-index line, every other section (procedures, near-duplicates,
    lessons without evidence, ...) that `report()` would otherwise still be
    able to answer."""
    root = tmp_path / "proj"
    root.mkdir()
    assert subprocess.run(EOS + ["init", str(root), "--no-ai"], capture_output=True).returncode == 0
    notes.add_note(root, "lesson", "Registry was down",
                   "## What went wrong\nRegistry down.\n\n## What was learned\ny\n\n## Next time\nz\n")
    from core import index

    index.build(root)
    config = root / ".eos" / "config.toml"
    config.write_text(config.read_text(encoding="utf-8") + '\n[index]\nticket_pattern = "["\n', encoding="utf-8")

    data = consolidate.report(root)  # must degrade (e.g. stale_index=None), never raise

    assert data["lessons_without_evidence"] == ["Registry was down"]


def test_stale_index_is_named_after_a_source_changes_and_clears_after_a_rebuild(tmp_path):
    """L1 remainder, item (a): the index's own declared freshness key
    (`inputs_sha256` in its `meta` table vs. a freshly computed sources digest)
    is the one generated EOS artifact under `.eos/` with a deterministic
    staleness check -- consolidate reports it without rebuilding."""
    from core import index

    root = tmp_path / "proj"
    root.mkdir()
    assert subprocess.run(EOS + ["init", str(root), "--no-ai"], capture_output=True).returncode == 0
    index.build(root)
    assert consolidate.report(root)["stale_index"] is False

    notes.add_note(root, "finding", "A new finding", BASE)
    data = consolidate.report(root)
    assert data["stale_index"] is True
    assert "STALE" in consolidate.render(data)

    index.build(root)
    assert consolidate.report(root)["stale_index"] is False
