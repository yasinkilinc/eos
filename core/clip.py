"""Clip a Bash call's output to its head and tail (`[hooks] bash_clip_chars`).

Pure functions, no shell and no subprocess: the PostToolUse hook in
core/hooks.py hands them the call's stdout and stderr and sends the result back
as `updatedToolOutput`.
"""


def clip_text(text: str, limit: int) -> str:
    """`text` as it is when it fits `limit` chars, else its first and last
    half of `limit` with a line saying what was left out."""
    if limit < 0 or len(text) <= limit:
        return text
    half = limit // 2
    head, tail = text[:half], text[len(text) - half:] if half else ""
    return f"{head}\n[... {len(text) - len(head) - len(tail)} chars omitted ...]\n{tail}"


def clip_streams(stdout: str, stderr: str, limit: int, path: str) -> tuple[str, str] | None:
    """The two streams clipped to `limit` chars together (each gets a share
    in proportion to its length), the pointer line last on stdout; None when
    together they fit."""
    total = len(stdout) + len(stderr)
    if limit <= 0 or total <= limit:
        return None
    share = limit * len(stdout) // total
    out = clip_text(stdout, share)
    err = clip_text(stderr, limit - share)
    return f"{out}\n[clipped: full output in {path}]", err
