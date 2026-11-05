"""A hook runs with the cyclic garbage collector paused.

A hook is a process of a second or two that builds a ledger of 113k events
and a tokenised corpus; collection passes over those cost 90 ms of an 825 ms
task brief (2026-11-05) and free nothing the process does not drop at exit.
The collector's state is restored on return, for a caller that is not a
short process.
"""
import gc
import io
import json
import sys

import pytest

from core import hooks


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "proj"
    (root / ".eos").mkdir(parents=True)
    return root


def _run(monkeypatch, root, seen):
    def handler(project_root, hook, cfg, agent):
        seen.append(gc.isenabled())
        return ""

    monkeypatch.setitem(hooks.HANDLERS, "probe-gc", handler)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"session_id": "s1", "cwd": str(root)})))
    return hooks.main(["probe-gc"])


def test_the_collector_is_paused_while_a_hook_runs_and_back_after(project, monkeypatch):
    seen = []
    assert gc.isenabled()

    assert _run(monkeypatch, project, seen) == 0

    assert seen == [False]
    assert gc.isenabled()


def test_a_caller_that_had_paused_it_finds_it_paused(project, monkeypatch):
    seen = []
    gc.disable()
    try:
        _run(monkeypatch, project, seen)
        assert not gc.isenabled()
    finally:
        gc.enable()
