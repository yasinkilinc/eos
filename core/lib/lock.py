"""One writer at a time for a file that is read, changed and written back (2.x roadmap F2).

`<file>.lock` is created with O_EXCL and holds the owner's pid and start time.
A lock whose owner is gone, or that is older than STALE_SECONDS, is taken over:
a crashed writer must not stop every later one. Waiting longer than the timeout
raises LockTimeout -- the caller decides whether that fails the command or skips
the write (a statistic skips; a counter fails).
"""
from __future__ import annotations

import contextlib
import os
import time
from pathlib import Path

STALE_SECONDS = 10.0
TIMEOUT_SECONDS = 15.0
POLL_SECONDS = 0.02


class LockTimeout(TimeoutError):
    pass


def lock_path(path: str | Path) -> Path:
    target = Path(path)
    return target.with_name(target.name + ".lock")


def _alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _stale(marker: Path, stale: float) -> bool:
    try:
        text = marker.read_text(encoding="utf-8").split()
        age = time.time() - marker.stat().st_mtime
    except (OSError, ValueError):
        return False  # gone already, or being written: try again
    if age > stale:
        return True
    # A lock just created is empty until its owner writes the pid: an empty or
    # half-written one is young and owned, and only its age can make it stale.
    if not text or not text[0].isdigit():
        return False
    return not _alive(int(text[0]))


@contextlib.contextmanager
def locked(path: str | Path, timeout: float = TIMEOUT_SECONDS, stale: float = STALE_SECONDS):
    marker = lock_path(path)
    marker.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    while True:
        try:
            descriptor = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            if _stale(marker, stale):
                with contextlib.suppress(OSError):
                    marker.unlink()
                continue
            if time.monotonic() >= deadline:
                raise LockTimeout(f"{marker} is held by another writer")
            time.sleep(POLL_SECONDS)
            continue
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(f"{os.getpid()} {time.time():.0f}\n")
        break
    try:
        yield marker
    finally:
        with contextlib.suppress(OSError):
            marker.unlink()
