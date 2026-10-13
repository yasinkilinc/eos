"""Priors from outcomes (2.x roadmap M6, ADR-036).

A note a verified run used is a little more likely to answer the next question
than one no run ever used. The prior is folded from the execution ledger: each
finished run whose outcome was verified gives one integer point, split by
largest remainder across the notes it referenced (the procedure it followed,
the note files it read). The scorer adds it scaled, after the match threshold,
so it reorders matches and never makes one.
"""
import json
import os
import subprocess
import sys
from fractions import Fraction
from pathlib import Path

import pytest

from core import executions, notes, priors

EOS = [sys.executable, str(Path(__file__).resolve().parents[1] / "core" / "eos.py")]


def _note(project, name, title, body="Body.", kind="finding", **front):
    directory = notes.notes_dir(project)
    directory.mkdir(parents=True, exist_ok=True)
    extra = "".join(f"{key}: {value}\n" for key, value in front.items())
    path = directory / name
    path.write_text(f"---\nkind: {kind}\ntitle: {title}\ncreated: 2026-09-01\n{extra}---\n\n{body}\n",
                    encoding="utf-8")
    return path


def _project(tmp_path, config=None):
    project = tmp_path / "proj"
    (project / ".eos").mkdir(parents=True)
    if config is not None:
        (project / ".eos" / "config.toml").write_text(config, encoding="utf-8")
    return project


def _run(project, rid, *, outcome="ok", source="verified", procedure=None, reads=(), session="s1"):
    """One run as the ledger holds it: start, a `read` event per note, finish."""
    ledger = executions.path_for(project)
    ledger.parent.mkdir(parents=True, exist_ok=True)
    lines = [{"v": 1, "type": "start", "id": rid, "title": rid, "procedure": procedure, "session": session}]
    lines += [{"v": 1, "type": "event", "execution": rid, "ord": None, "at": "2026-10-01T00:00:00+00:00",
               "kind": "read", "tool": "read", "ref": priors.note_ref(name), "session": session}
              for name in reads]
    if outcome:
        finish = {"v": 1, "type": "finish", "id": rid, "at": "2026-10-01T01:00:00+00:00", "outcome": outcome}
        if source:
            finish["outcome_source"] = source
        lines.append(finish)
    with open(ledger, "a", encoding="utf-8") as handle:
        for line in lines:
            handle.write(json.dumps(line, ensure_ascii=False) + "\n")


# --- the split ---------------------------------------------------------------------


def test_largest_remainder_hands_out_exactly_the_budget_in_integers():
    shares = {"a.md": Fraction(5, 4), "b.md": Fraction(1, 2), "c.md": Fraction(1, 4)}
    assert priors.largest_remainder(shares, 2) == {"a.md": 1, "b.md": 1, "c.md": 0}


def test_an_equal_remainder_goes_to_the_name_that_sorts_first():
    assert priors.largest_remainder({"b.md": Fraction(1, 2), "a.md": Fraction(1, 2)}, 1) == {"a.md": 1, "b.md": 0}


def test_one_verified_run_gives_one_point_split_across_what_it_read(tmp_path):
    project = _project(tmp_path)
    _run(project, "x-1", reads=["a.md", "b.md", "c.md"])
    _run(project, "x-2", reads=["a.md"])
    fold = priors.fold_project(project, [])
    assert fold.runs == 2
    assert fold.credit == {"a.md": Fraction(4, 3), "b.md": Fraction(1, 3), "c.md": Fraction(1, 3)}
    assert fold.values == {"a.md": 2}


def test_reading_a_note_twice_in_one_run_is_still_one_share(tmp_path):
    project = _project(tmp_path)
    _run(project, "x-1", reads=["a.md", "a.md", "b.md"])
    assert priors.fold_project(project, []).credit == {"a.md": Fraction(1, 2), "b.md": Fraction(1, 2)}


@pytest.mark.parametrize("outcome,source", [("ok", "claimed"), ("ok", None), ("failed", None),
                                            ("abandoned", None), (None, None)])
def test_only_a_verified_ok_moves_a_prior(tmp_path, outcome, source):
    project = _project(tmp_path)
    _run(project, "x-1", outcome=outcome, source=source, reads=["a.md"])
    fold = priors.fold_project(project, [])
    assert (fold.runs, fold.values) == (0, {})


def test_the_procedure_a_run_followed_is_a_note_it_referenced(tmp_path):
    project = _project(tmp_path)
    proc = _note(project, "deploy.md", "Deploy the service", kind="procedure", procedure="deploy")
    corpus = notes.load_notes(project)
    _run(project, "x-1", procedure="deploy", reads=["deploy.md", "other.md"])
    fold = priors.fold_project(project, corpus)
    assert fold.credit == {proc.name: Fraction(1, 2), "other.md": Fraction(1, 2)}


def test_a_note_written_in_the_runs_own_session_earns_nothing_from_it(tmp_path):
    project = _project(tmp_path)
    _note(project, "mine.md", "What this session found", session="s1")
    _note(project, "theirs.md", "What another session found", session="s0")
    _run(project, "x-1", reads=["mine.md", "theirs.md"], session="s1")
    assert priors.fold_project(project, notes.load_notes(project)).credit == {"theirs.md": Fraction(1)}


def test_a_prior_stops_at_one_hundred(tmp_path):
    project = _project(tmp_path)
    for index in range(103):
        _run(project, f"x-{index}", reads=["a.md"])
    assert priors.fold_project(project, []).values == {"a.md": priors.CAP}


def test_the_fast_fold_agrees_with_the_full_ledger_fold(tmp_path):
    project = _project(tmp_path)
    _note(project, "deploy.md", "Deploy the service", kind="procedure", procedure="deploy")
    _run(project, "x-1", procedure="deploy", reads=["a.md"])
    _run(project, "x-2", reads=["a.md", "b.md"], source="claimed")
    _run(project, "x-3", reads=["b.md"])
    corpus = notes.load_notes(project)
    full = priors.fold_records(executions.load(project), corpus)
    assert priors.fold_project(project, corpus) == full


def test_rotated_ledger_files_are_read_too(tmp_path):
    project = _project(tmp_path)
    _run(project, "x-1", reads=["a.md"])
    ledger = executions.path_for(project)
    ledger.rename(executions.rotated(ledger, 1))
    _run(project, "x-2", reads=["b.md"])
    assert priors.fold_project(project, []).runs == 2


def test_no_ledger_is_no_prior(tmp_path):
    fold = priors.fold_project(_project(tmp_path), [])
    assert (fold.runs, fold.values) == (0, {})


# --- the knob ----------------------------------------------------------------------


@pytest.mark.parametrize("config,expected", [
    (None, priors.DEFAULT_ALPHA),
    ("[notes]\nprior_alpha = 0\n", 0.0),
    ("[notes]\nprior_alpha = 0.05\n", 0.05),
    ("[notes]\nprior_alpha = 0.9\n", priors.ALPHA_MAX),
    ("[notes]\nprior_alpha = -1\n", 0.0),
    ("[notes]\nprior_alpha = \"high\"\n", priors.DEFAULT_ALPHA),
    ("[notes]\nprior_alpha = true\n", priors.DEFAULT_ALPHA),
    ("notes = 3\n", priors.DEFAULT_ALPHA),
])
def test_the_weight_is_a_project_setting_clamped_to_the_allowed_range(tmp_path, config, expected):
    assert priors.alpha(_project(tmp_path, config)) == expected


def test_the_allowed_range_is_the_reports_bound():
    assert priors.ALPHA_MAX == 0.2 and 0.0 <= priors.DEFAULT_ALPHA <= priors.ALPHA_MAX


# --- the scorer --------------------------------------------------------------------


def _two_equal(tmp_path, config):
    project = _project(tmp_path, config)
    _note(project, "a-first.md", "Rotate the signing key")
    _note(project, "b-second.md", "Rotate the signing key")
    _note(project, "c-other.md", "Unrelated payment retry rules")
    return project


def test_a_prior_breaks_a_tie_the_name_used_to_break(tmp_path):
    project = _two_equal(tmp_path, "[notes]\nprior_alpha = 0.1\n")
    corpus = notes.load_notes(project)
    assert [n.path.name for n in notes.rank(corpus, "signing key")] == ["a-first.md", "b-second.md"]
    ranked = notes.rank(corpus, "signing key", priors={"b-second.md": 5}, alpha=0.1)
    assert [n.path.name for n in ranked] == ["b-second.md", "a-first.md"]


def test_a_prior_never_makes_a_match_out_of_a_note_the_query_does_not_reach(tmp_path):
    project = _two_equal(tmp_path, None)
    corpus = notes.load_notes(project)
    ranked = notes.rank(corpus, "signing key", priors={"c-other.md": 100}, alpha=0.2)
    assert "c-other.md" not in [n.path.name for n in ranked]


def test_a_prior_is_scaled_so_it_cannot_outvote_a_clearly_better_match(tmp_path):
    project = _project(tmp_path)
    _note(project, "a-exact.md", "Rotate the signing key for the gateway")
    _note(project, "b-partial.md", "Gateway timeouts", body="The signing step is slow.")
    corpus = notes.load_notes(project)
    ranked = notes.rank(corpus, "rotate signing key gateway", priors={"b-partial.md": 100}, alpha=0.2)
    assert ranked[0].path.name == "a-exact.md"


def test_without_a_weight_priors_change_nothing(tmp_path):
    project = _two_equal(tmp_path, None)
    corpus = notes.load_notes(project)
    assert notes.rank(corpus, "signing key", priors={"b-second.md": 100}, alpha=0.0) == \
        notes.rank(corpus, "signing key")


def test_search_applies_the_projects_priors_with_its_weight(tmp_path):
    project = _two_equal(tmp_path, "[notes]\nprior_alpha = 0.1\n")
    _run(project, "x-1", reads=["b-second.md"])
    assert [n.path.name for n in notes.search_notes(project, "signing key")][0] == "b-second.md"


def test_search_with_the_knob_off_ignores_the_ledger(tmp_path):
    project = _two_equal(tmp_path, "[notes]\nprior_alpha = 0\n")
    _run(project, "x-1", reads=["b-second.md"])
    assert [n.path.name for n in notes.search_notes(project, "signing key")][0] == "a-first.md"


def test_for_search_reads_no_ledger_when_the_weight_is_zero(tmp_path, monkeypatch):
    project = _two_equal(tmp_path, "[notes]\nprior_alpha = 0\n")
    monkeypatch.setattr(priors, "for_corpus", lambda *a: pytest.fail("the ledger was read"))
    assert priors.for_search(project, []) == (None, 0.0)


# --- what `eos note show` records ---------------------------------------------------


def test_note_show_inside_an_open_run_records_the_read(tmp_path):
    project = _project(tmp_path)
    note = _note(project, "20260901-key.md", "Rotate the signing key")
    env = {**os.environ, "EOS_STATE_DIR": str(tmp_path / "state"), "EOS_SESSION": "s-show"}
    for name in ("EOS_EXECUTION", "EOS_EXECUTION_LEDGER"):
        env.pop(name, None)
    assert subprocess.run(EOS + ["run", "start", str(project), "--title", "t"], env=env,
                          capture_output=True).returncode == 0
    shown = subprocess.run(EOS + ["note", "show", str(project), "signing key"], env=env,
                           capture_output=True, text=True)
    assert shown.returncode == 0 and "Rotate the signing key" in shown.stdout
    [record] = executions.load(project)
    assert [(e.kind, e.ref, e.source) for e in record.events] == [("read", f"note:{note.name}", "cli")]


def test_note_show_without_an_open_run_records_nothing_and_still_shows(tmp_path):
    project = _project(tmp_path)
    _note(project, "20260901-key.md", "Rotate the signing key")
    env = {**os.environ, "EOS_STATE_DIR": str(tmp_path / "state"), "EOS_SESSION": "s-none"}
    for name in ("EOS_EXECUTION", "EOS_EXECUTION_LEDGER"):
        env.pop(name, None)
    shown = subprocess.run(EOS + ["note", "show", str(project), "signing key"], env=env,
                           capture_output=True, text=True)
    assert shown.returncode == 0 and "Rotate the signing key" in shown.stdout
    assert executions.load(project) == []
