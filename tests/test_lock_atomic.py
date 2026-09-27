"""Locks and atomic writes (2.x roadmap F2).

Measured on a comparable tool (Ruflo #2878): 11 of 12 concurrent writes lost
before an O_EXCL lock. EOS read-modify-writes notes' counters and trims its
logs; two sessions finishing runs at once lost a count the same way.
"""
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from core.lib import atomic, lock

REPO = Path(__file__).resolve().parents[1]


def test_an_atomic_write_replaces_the_file_and_leaves_no_temp(tmp_path):
    target = tmp_path / "state.txt"
    target.write_text("old", encoding="utf-8")
    atomic.write_text(target, "new")
    assert target.read_text(encoding="utf-8") == "new"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["state.txt"]


INCREMENT = r'''
import sys
sys.path.insert(0, sys.argv[1])
from pathlib import Path
from core.lib import atomic, lock
path = Path(sys.argv[2])
for _ in range(int(sys.argv[3])):
    with lock.locked(path):
        value = int(path.read_text() or "0")
        atomic.write_text(path, str(value + 1))
'''


def test_concurrent_read_modify_writes_lose_nothing_under_the_lock(tmp_path):
    counter = tmp_path / "counter"
    counter.write_text("0", encoding="utf-8")
    workers = [subprocess.Popen([sys.executable, "-c", INCREMENT, str(REPO), str(counter), "25"])
               for _ in range(4)]
    assert all(worker.wait(timeout=60) == 0 for worker in workers)
    assert counter.read_text(encoding="utf-8") == "100"
    assert lock.lock_path(counter).parent != counter.parent     # no lock file beside the data


def test_a_lock_whose_holder_died_is_free(tmp_path):
    """The holder is killed while holding it (a hook's timeout): the next writer
    gets it at once -- no stale-lock takeover, so no two writers."""
    target = tmp_path / "note.md"
    dead = subprocess.run([sys.executable, "-c",
                           "import os, sys; sys.path.insert(0, sys.argv[1]);"
                           "from pathlib import Path; from core.lib import lock\n"
                           "with lock.locked(Path(sys.argv[2])): os._exit(0)",
                           str(REPO), str(target)], timeout=30)
    assert dead.returncode == 0
    started = time.monotonic()
    with lock.locked(target, timeout=2):
        pass
    assert time.monotonic() - started < 1


def test_a_lock_held_by_a_live_process_times_out(tmp_path):
    target = tmp_path / "note.md"
    with lock.locked(target):
        holder = subprocess.run([sys.executable, "-c",
                                 "import sys; sys.path.insert(0, sys.argv[1]);"
                                 "from pathlib import Path; from core.lib import lock;"
                                 "import time\n"
                                 "try:\n"
                                 "    with lock.locked(Path(sys.argv[2]), timeout=0.5): print('got')\n"
                                 "except lock.LockTimeout: print('timeout')",
                                 str(REPO), str(target)], capture_output=True, text=True, timeout=30)
    assert holder.stdout.strip() == "timeout"


FINISH = r'''
import os, sys, time
sys.path.insert(0, sys.argv[1])
from core import executions
while not os.path.exists(sys.argv[4]):   # a barrier: every worker finishes at once
    time.sleep(0.001)
executions.finish(sys.argv[2], sys.argv[3], outcome="ok")
'''


def test_runs_of_one_procedure_finished_at_once_are_all_counted(tmp_path, monkeypatch):
    from core import executions, notes

    monkeypatch.setenv("EOS_STATE_DIR", str(tmp_path / "state"))
    root = tmp_path / "proj"
    root.mkdir()
    eos = [sys.executable, str(REPO / "core" / "eos.py")]
    assert subprocess.run(eos + ["init", str(root), "--no-ai"], capture_output=True).returncode == 0
    made = subprocess.run(eos + ["procedure", "new", str(root), "--title", "Deploy to staging", "--steps", "-",
                                 "--success", "health answers 200"],
                          input="Build (tool: build)\n", capture_output=True, text=True)
    assert made.returncode == 0, made.stderr
    slug = made.stdout.split("\t")[0]
    runs = [executions.start(root, f"deploy {n}", procedure=slug, session=f"s{n}").id for n in range(16)]
    go = tmp_path / "go"
    workers = [subprocess.Popen([sys.executable, "-c", FINISH, str(REPO), str(root), run, str(go)],
                                env={**os.environ, "EOS_STATE_DIR": str(tmp_path / "state")}) for run in runs]
    time.sleep(2)
    go.write_text("go", encoding="utf-8")
    assert all(worker.wait(timeout=120) == 0 for worker in workers)
    assert notes.find_procedure(root, slug).runs_ok == 16
