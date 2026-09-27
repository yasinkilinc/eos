"""Whole-file writes a reader never sees half of (2.x roadmap F2).

Write a temp file beside the target, flush it to disk, rename it over the
target, then flush the directory so the rename itself survives a crash. A
reader sees the old content or the new, never a prefix.
"""
from __future__ import annotations

import os
import secrets
from pathlib import Path


def write_text(path: str | Path, text: str, encoding: str = "utf-8") -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, name = _temp_beside(target)
    try:
        with os.fdopen(handle, "w", encoding=encoding, newline="") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        if target.exists():
            os.chmod(name, target.stat().st_mode & 0o7777)
        os.replace(name, target)
    except BaseException:
        try:
            os.unlink(name)
        except OSError:
            pass
        raise
    _fsync_directory(target.parent)


def _temp_beside(target: Path) -> tuple[int, str]:
    """A new temp file created 0666 minus the umask, as open() would make the
    target -- not mkstemp's 0600, and without reading the process umask, which
    is not thread-safe."""
    while True:
        name = str(target.parent / f".{target.name}.{secrets.token_hex(6)}.tmp")
        try:
            return os.open(name, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o666), name
        except FileExistsError:
            continue


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
