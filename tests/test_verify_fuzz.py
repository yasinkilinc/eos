"""The verify check rule against real bash (ADR-028): a sample of the
differential fuzzer in tools/verify_fuzz.py -- the check passing and failing,
and no command counted whose exit status does not depend on it."""
import importlib.util
import os
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parents[1] / "tools" / "verify_fuzz.py"


@pytest.mark.skipif(not os.access("/bin/bash", os.X_OK), reason="needs /bin/bash")
def test_no_generated_command_is_a_false_clear():
    spec = importlib.util.spec_from_file_location("verify_fuzz", TOOL)
    fuzz = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fuzz)
    summary = fuzz.run(400)
    assert summary["crashes"] == []
    assert [r["command"] for r in summary["false_clears"]] == []
    assert summary["runs_true"] > 100          # the sample does exercise commands that count
