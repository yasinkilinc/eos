"""One writer at a time for a file that is read, changed and written back (2.x roadmap F2).

On POSIX the lock is the kernel's (`fcntl.flock`) on a lock file kept under the
user's state directory, one per locked path (beside the file when that directory
cannot be written): a holder that dies -- a hook killed
by its timeout -- releases it with its process, so there is no stale lock to take
over and no window in which two writers both take one over (branch review
finding 3). The lock files are never deleted: deleting one while a writer waits
on it is the same race again. Where `fcntl` does not exist the lock is an
`O_EXCL` marker with the owner's pid, taken over when its owner is dead or it is
older than STALE_SECONDS.

Waiting longer than the timeout raises LockTimeout -- the caller decides whether
that fails the command or skips the write (a statistic skips; a counter fails).
"""
from __future__ import annotations

import contextlib
import hashlib
import os
import time
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None

STALE_SECONDS = 10.0
TIMEOUT_SECONDS = 15.0
POLL_SECONDS = 0.02


class LockTimeout(TimeoutError):
    pass


def _state_dir() -> Path:
    configured = os.environ.get("EOS_STATE_DIR")
    return Path(configured).expanduser() if configured else Path.home() / ".local" / "state" / "eos"


def lock_path(path: str | Path) -> Path:
    """The lock file for `path`: under the state directory, keyed by the resolved path."""
    key = hashlib.sha256(str(Path(path).expanduser().resolve()).encode("utf-8")).hexdigest()[:32]
    return _state_dir() / "locks" / f"{key}.lock"


def _marker(path: str | Path) -> Path:
    """`lock_path`, or a hidden lock file beside the target when the state
    directory cannot be created (read-only home, a sandbox): every writer of the
    same file falls back the same way, so they still share one lock."""
    marker = lock_path(path)
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        if os.access(marker.parent, os.W_OK):
            return marker
    except OSError:
        pass
    target = Path(path).expanduser().resolve()
    return target.parent / f".{target.name}.eos-lock"


@contextlib.contextmanager
def locked(path: str | Path, timeout: float = TIMEOUT_SECONDS, stale: float = STALE_SECONDS):
    marker = _marker(path)
    if fcntl is None:
        yield from _exclusive(marker, timeout, stale)
        return
    descriptor = os.open(marker, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise LockTimeout(f"{path} is held by another writer") from None
                time.sleep(POLL_SECONDS)
        try:
            yield marker
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


# --- the O_EXCL fallback, for a platform without fcntl ----------------------------------


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
        return False
    if age > stale:
        return True
    # A lock just created is empty until its owner writes the pid: only its age can make it stale.
    if not text or not text[0].isdigit():
        return False
    return not _alive(int(text[0]))


def _exclusive(marker: Path, timeout: float, stale: float):
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
                raise LockTimeout(f"{marker} is held by another writer") from None
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
