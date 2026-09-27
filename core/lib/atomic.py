"""Whole-file writes a reader never sees half of (2.x roadmap F2).

Write a temp file beside the target, flush it to disk, rename it over the
target, then flush the directory so the rename itself survives a crash. A
reader sees the old content or the new, never a prefix.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path


def write_text(path: str | Path, text: str, encoding: str = "utf-8") -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(handle, "w", encoding=encoding, newline="") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        if target.exists():
            os.chmod(name, target.stat().st_mode & 0o7777)
        else:  # mkstemp makes it 0600; a new file gets what open() would give it
            current = os.umask(0)
            os.umask(current)
            os.chmod(name, 0o666 & ~current)
        os.replace(name, target)
    except BaseException:
        try:
            os.unlink(name)
        except OSError:
            pass
        raise
    _fsync_directory(target.parent)


def _fsync_directory(directory: Path) -> None:
    try:
        descriptor = os.open(directory, os.O_RDONLY)
    except OSError:
        return  # not every platform opens a directory; the rename is still atomic
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)
