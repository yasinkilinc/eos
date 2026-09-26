"""A killed index build leaves no permanent litter (2.x roadmap F1).

A build writes eos.db.<random>.tmp and removes it on any error it sees; a build
that is killed (a hook's timeout) never sees one, and the host had one such file
left. The next build removes temp files older than STALE_TEMP_SECONDS; a newer
one may be a concurrent build's and stays.
"""
import os
import subprocess
import sys
import time
from pathlib import Path

from core import index

EOS = [sys.executable, str(Path(__file__).resolve().parents[1] / "core" / "eos.py")]


def test_a_build_removes_old_orphaned_temp_files_only(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    assert subprocess.run(EOS + ["init", str(root), "--no-ai"], capture_output=True).returncode == 0
    data = index.db_path(root).parent
    data.mkdir(parents=True, exist_ok=True)
    old, fresh = data / "eos.db.killed.tmp", data / "eos.db.running.tmp"
    for path in (old, fresh):
        path.write_bytes(b"partial")
    hour_ago = time.time() - 3600
    os.utime(old, (hour_ago, hour_ago))

    index.build(root)

    assert not old.exists() and fresh.exists()
