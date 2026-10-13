"""`eos consolidate` and a project's routing corpus (2.x roadmap L1, ADR-025 addendum).

`[model_routing] corpus` names the labelled TSV `route --eval` reads; with it,
consolidate prints the policy's accuracy on the development rows, advised vs
used, and keyword proposals a person may accept -- and counts the ones accepted.
Without it, consolidate prints nothing new.
"""
import datetime
import json
import subprocess
import sys
from pathlib import Path

import pytest

import core.routing as routing
from core import consolidate, executions
from core.routing import config, corpus, evaluate, usage

EOS = [sys.executable, str(Path(__file__).resolve().parents[1] / "core" / "eos.py")]
HEADER = "prompt\ttype\tlevel\tmodel\teffort\tsplit\n"
# No built-in table holds "zorblat": both dev rows fall to the no-signal type.
# No two rows share any other word, so "zorblat" is the only candidate two rows back.
DEV = [("zorblat nightly pricing numbers", "investigation", "dev"),
       ("zorblat queue contents today", "investigation", "dev"),
       ("refactor auth module", "refactoring", "dev")]


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    (root / ".eos").mkdir(parents=True)
    monkeypatch.setenv("EOS_STATE_DIR", str(tmp_path / "state"))
    for name in ("EOS_EXECUTION", "EOS_EXECUTION_LEDGER", routing.MODEL_ENV, routing.EFFORT_ENV):
        monkeypatch.delenv(name, raising=False)
    return root


def _corpus(root, rows, name="routing.tsv"):
    path = root / "evals" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(HEADER + "".join(f"{prompt}\t{kind}\t\t\t\t{split}\n" for prompt, kind, split in rows),
                    encoding="utf-8")
    return f"evals/{name}"


def _config(root, text):
    (root / ".eos" / "config.toml").write_text(text, encoding="utf-8")


# --- the config key ----------------------------------------------------------------


def test_corpus_is_one_path_or_a_list_of_project_relative_paths():
    assert config.parse({"corpus": "evals/a.tsv"}).corpus == ("evals/a.tsv",)
    assert config.parse({"corpus": ["evals/a.tsv", "evals/b.tsv"]}).corpus == ("evals/a.tsv", "evals/b.tsv")
    assert config.RoutingConfig().corpus == ()
    for bad in (3, "", [""], ["a.tsv", 2], "/abs/routing.tsv", "../outside.tsv"):
        with pytest.raises(ValueError, match="corpus"):
            config.parse({"corpus": bad})


def test_naming_a_corpus_alone_does_not_switch_routing_on(project):
    """A table holding only `corpus` says where the labels are, not that the brief
    should print a ROUTE line; and the corpus never changes a decision, so the
    receipt hash (L5) does not move with it."""
    before = evaluate.config_hash(project)
    _config(project, '[model_routing]\ncorpus = "evals/routing.tsv"\n')
    assert config.load(project).configured is False
    assert evaluate.config_hash(project) == before
    _config(project, '[model_routing]\nbrief = "always"\ncorpus = "evals/routing.tsv"\n')
    assert config.load(project).configured is True


# --- consolidate without a corpus ---------------------------------------------------


def test_without_a_corpus_consolidate_prints_nothing_new(project):
    _corpus(project, DEV)
    _config(project, '[model_routing]\nbrief = "always"\n')
    data = consolidate.report(project)
    assert data["routing"] is None
    text = consolidate.render(data)
    for word in ("ROUTING", "ADVISED", "KEYWORD", "ACCEPTED"):
        assert word not in text


# --- consolidate with a corpus ------------------------------------------------------


def test_accuracy_and_a_proposal_come_from_the_dev_rows(project):
    name = _corpus(project, DEV)
    _config(project, f'[model_routing]\ncorpus = "{name}"\n')
    data = consolidate.report(project)
    found = data["routing"]
    assert found["corpora"] == [{"path": name, "prompts": 3, "error": None,
                                 "accuracy": {"type": pytest.approx(1 / 3, abs=0.001), "level": None,
                                              "model": None, "effort": None}}]
    assert [(p["type"], p["word"], p["fixes"]) for p in found["proposals"]] == [("investigation", "zorblat", 2)]
    text = consolidate.render(data)
    assert f"{name}  3 dev prompts  type 33.3%" in text
    assert "KEYWORD PROPOSALS (1)" in text
    assert f'{found["proposals"][0]["id"]}  investigation += "zorblat"' in text


def test_the_test_split_is_never_read(project, monkeypatch):
    """L5: tuning reads the dev split only. The test rows here would make "zorblat"
    a proposal on their own, and would add to the prompts measured."""
    rows = [("zorblat nightly pricing numbers", "investigation", "test"),
            ("zorblat queue contents today", "investigation", "test"),
            ("refactor auth module", "refactoring", "dev")]
    name = _corpus(project, rows)
    _config(project, f'[model_routing]\ncorpus = "{name}"\n')
    seen = []
    real = evaluate.run
    monkeypatch.setattr(evaluate, "run", lambda root, chosen, split=None: seen.extend(chosen) or real(root, chosen, split))
    found = consolidate.report(project)["routing"]
    assert found["corpora"][0]["prompts"] == 1 and found["proposals"] == []
    assert seen and all(row.get("split") != "test" for row in seen)


def test_a_dev_prompt_another_corpus_holds_for_test_is_not_read(project):
    dev = _corpus(project, DEV, "a.tsv")
    test = _corpus(project, [("zorblat nightly pricing numbers", "investigation", "test")], "b.tsv")
    _config(project, f'[model_routing]\ncorpus = ["{dev}", "{test}"]\n')
    found = consolidate.report(project)["routing"]
    assert [c["prompts"] for c in found["corpora"]] == [2, 0]
    assert found["proposals"] == [], "one dev row is left: below the two a proposal needs"


def test_a_word_that_breaks_a_correctly_typed_row_is_not_proposed(project):
    """The third row is right today only through the no-signal default; "zorblat"
    as an investigation word would take it there."""
    rows = DEV + [("zorblat release notes", "normal_implementation", "dev")]
    name = _corpus(project, rows)
    _config(project, f'[model_routing]\ncorpus = "{name}"\n')
    assert consolidate.report(project)["routing"]["proposals"] == []


def test_a_word_most_of_whose_rows_carry_another_label_is_not_proposed(project):
    rows = DEV + [(f"zorblat sprint{n}", "planning", "dev") for n in range(3)]
    name = _corpus(project, rows)
    _config(project, f'[model_routing]\ncorpus = "{name}"\n')
    pairs = [(p["type"], p["word"]) for p in consolidate.report(project)["routing"]["proposals"]]
    assert ("investigation", "zorblat") not in pairs and ("planning", "zorblat") in pairs


def test_a_word_a_keyword_table_already_holds_is_not_proposed(project):
    name = _corpus(project, DEV)
    _config(project, f'[model_routing]\ncorpus = "{name}"\n[model_routing.keywords]\nplanning = ["zorblat"]\n')
    words = [p["word"] for p in consolidate.report(project)["routing"]["proposals"]]
    assert "zorblat" not in words


def test_a_proposal_keeps_its_id_from_one_run_to_the_next(project):
    name = _corpus(project, DEV)
    _config(project, f'[model_routing]\ncorpus = "{name}"\n')
    first = consolidate.report(project)["routing"]["proposals"][0]["id"]
    assert first == consolidate.report(project)["routing"]["proposals"][0]["id"]
    assert first == corpus.proposal_id("investigation", "zorblat") and first.startswith("kw-")


def test_an_unreadable_corpus_is_a_line_not_a_failure(project):
    _config(project, '[model_routing]\ncorpus = "evals/missing.tsv"\n')
    data = consolidate.report(project)
    assert data["routing"]["corpora"][0]["error"]
    assert "evals/missing.tsv  cannot read" in consolidate.render(data)
    done = subprocess.run(EOS + ["consolidate", str(project)], capture_output=True, text=True)
    assert done.returncode == 0 and "Traceback" not in done.stderr


def test_advised_vs_used_is_counted_from_the_routed_runs(project):
    name = _corpus(project, DEV)
    _config(project, f'[model_routing]\ncorpus = "{name}"\n')
    run = executions.start(project, "fix the typo", session="s1")
    routing.route(project, "fix the typo", session="s1")
    path = usage.path_for(project)
    path.write_text(json.dumps({"at": "2026-09-27T00:00:00+00:00", "session": "s1", "models": {},
                                "subagent_models": {"opus": {"messages": 3}}}) + "\n", encoding="utf-8")
    executions.finish(project, run.id, outcome="ok")
    data = consolidate.report(project)
    assert data["routing"]["advised_vs_used"] == {"same": 0, "different": 1, "unknown": 0}
    assert "ADVISED VS USED  1 routed run(s): 0 on the advised model, 1 on another, 0 unknown" \
        in consolidate.render(data)


# --- accepting a proposal ------------------------------------------------------------


def test_accepting_a_proposal_is_recorded_and_counted_and_changes_no_config(project):
    name = _corpus(project, DEV)
    _config(project, f'[model_routing]\ncorpus = "{name}"\n')
    cfg_before = (project / ".eos" / "config.toml").read_bytes()
    pid = consolidate.report(project)["routing"]["proposals"][0]["id"]

    done = subprocess.run(EOS + ["consolidate", str(project), "--accept", pid], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    assert 'investigation += "zorblat"' in done.stdout
    assert (project / ".eos" / "config.toml").read_bytes() == cfg_before
    lines = (project / ".eos" / "data" / consolidate.ACCEPTED).read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["id"] for line in lines] == [pid]

    data = consolidate.report(project)
    assert data["accepted"] == {"last_30_days": 1, "total": 1}
    assert "ACCEPTED PROPOSALS  1 in the last 30 days (1 in all)" in consolidate.render(data)

    again = subprocess.run(EOS + ["consolidate", str(project), "--accept", pid], capture_output=True, text=True)
    assert again.returncode == 2 and "already accepted" in again.stderr


def test_an_id_no_current_proposal_has_is_refused(project):
    name = _corpus(project, DEV)
    _config(project, f'[model_routing]\ncorpus = "{name}"\n')
    done = subprocess.run(EOS + ["consolidate", str(project), "--accept", "kw-00000000"],
                          capture_output=True, text=True)
    assert done.returncode == 2 and "no current proposal" in done.stderr
    assert not (project / ".eos" / "data" / consolidate.ACCEPTED).exists()


def test_only_the_last_30_days_count_as_this_month(project):
    path = project / ".eos" / "data" / consolidate.ACCEPTED
    path.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.datetime.now(datetime.timezone.utc)
    old = (now - datetime.timedelta(days=45)).isoformat(timespec="seconds")
    new = (now - datetime.timedelta(days=3)).isoformat(timespec="seconds")
    path.write_text(json.dumps({"at": old, "id": "kw-1"}) + "\n" + "not json\n"
                    + json.dumps({"at": new, "id": "kw-2"}) + "\n", encoding="utf-8")
    data = consolidate.report(project)
    assert data["accepted"] == {"last_30_days": 1, "total": 2}
    assert "ACCEPTED PROPOSALS  1 in the last 30 days (2 in all)" in consolidate.render(data)


def test_counting_the_groups_reads_no_verification(project, monkeypatch):
    """On the host the verification read behind `outcome_source` was ~35 ms per ok
    run, 8 s in all; the counts consolidate prints never need it."""
    from core.routing import trace

    run = executions.start(project, "fix the typo", session="s1")
    routing.route(project, "fix the typo", session="s1")
    executions.finish(project, run.id, outcome="ok")

    def no_read(*_args, **_kwargs):
        raise AssertionError("outcome_source was read")

    monkeypatch.setattr(executions, "outcome_source", no_read)
    records = executions.load(project)
    groups = trace.advised_vs_used_outcome_stats(project, records=records, sources=False)
    assert [len(groups[key]) for key in ("same", "different", "unknown")] == [0, 0, 1]
    assert groups["unknown"][0]["outcome_source"] is None
