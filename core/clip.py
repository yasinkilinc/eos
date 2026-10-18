"""Print a saved Bash output, clipped: `python clip.py <file> <chars>`.

Used by the `[hooks] bash_clip_chars` rewrite of a Bash call (core/hooks.py).
Standalone on purpose -- it runs inside the user's shell after the command and
must never fail: whatever it cannot read it says so and exits 0.
"""
import sys


def main(argv: list[str]) -> int:
    path = argv[0] if argv else ""
    try:
        limit = int(argv[1]) if len(argv) > 1 else 0
    except ValueError:
        limit = 0
    try:
        with open(path, encoding="utf-8", errors="replace", newline="") as handle:
            text = handle.read()
    except Exception as error:  # noqa: BLE001 - the shell this runs in must go on
        _write(f"[clip: cannot read {path}: {error.__class__.__name__}]\n")
        return 0
    if limit <= 0 or len(text) <= limit:
        _write(text)
        return 0
    half = limit // 2
    _write(f"{text[:half]}\n[clipped: full output in {path}]\n{text[-half:]}")
    return 0


def _write(text: str) -> None:
    try:
        sys.stdout.write(text)
        sys.stdout.flush()
    except Exception:  # noqa: BLE001 - a closed pipe is not worth a traceback
        pass


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
