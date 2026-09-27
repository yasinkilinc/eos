"""No unlocked read-modify-write in the modules that hold shared state (2.x roadmap F2).

These files are written by concurrent sessions: a counter, a note section, a
trimmed log, a run pointer. Every rewrite of an existing file goes through
core.lib.atomic under core.lib.lock. A bare `.write_text(` here is the shape of
the lost update, so each file may keep only the ones listed, which create new
files (a new note has a unique name and no reader of a previous version).
"""
import re
from pathlib import Path

CORE = Path(__file__).resolve().parents[1] / "core"
ALLOWED = {
    "notes/write.py": 2,      # add_note and the lesson writer create new files
    "notes/store.py": 0,
    "notes/procedures.py": 0,
    "telemetry.py": 0,
    "hooks.py": 0,
    "executions.py": 0,
    "routing/trace.py": 0,
}


def test_shared_state_is_rewritten_only_atomically():
    found = {name: len(re.findall(r"(?<!atomic)\.write_text\(", (CORE / name).read_text(encoding="utf-8")))
             for name in ALLOWED}
    assert found == ALLOWED, found
