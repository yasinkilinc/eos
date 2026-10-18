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
    """The two streams clipped to about `limit` chars together, the pointer
    line last on stdout; None when together they fit. A stream that fits in
    half of `limit` stays whole (a short error beside a huge log is what the
    agent needs), the other gets the rest; otherwise each gets half."""
    if limit <= 0 or len(stdout) + len(stderr) <= limit:
        return None
    half = limit // 2
    if len(stderr) <= half:
        share = limit - len(stderr)
    elif len(stdout) <= half:
        share = len(stdout)
    else:
        share = half
    out = clip_text(stdout, share)
    err = clip_text(stderr, limit - share)
    return f"{out}\n[clipped: full output in {path}]", err
