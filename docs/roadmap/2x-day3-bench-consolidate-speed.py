"""One-off timing of `consolidate.change_capture` on a synthetic 300-run
ledger, before/after batching its git subprocesses (2.x roadmap Day 3, item
D2). RVc measured 3.46s at n=300 with one `git diff` (touched_files) plus one
`git log` (_foreign_files) subprocess per finished run.

"before" imports the pre-D2 core/consolidate.py from `git show <ref>:...`
(default: HEAD) into its own module, so the comparison needs no stash or
checkout of the working tree; "after" is the current core/consolidate.py.
Both run the exact same synthetic ledger and must produce the same result
(checked here, not just timed).

Not part of the test suite -- a manual benchmark, kept for reproducing the
numbers in docs/roadmap/2x-progress.md's D2 row. Usage:
    python3 docs/roadmap/2x-day3-bench-consolidate-speed.py [--before-ref REF] [-n 300]
"""
import argparse
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

_GIT_ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}


def _git(root, *args):
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True, env=_GIT_ENV)


def build_ledger(root, executions_module, n):
    _git(root, "init", "-q")
    (root / "a.txt").write_text("1\n")
    _git(root, "add", "a.txt")
    _git(root, "commit", "-q", "-m", "init")
    for i in range(n):
        run = executions_module.start(root, f"Run {i}")
        (root / f"f{i}.txt").write_text("1\n")
        _git(root, "add", f"f{i}.txt")
        _git(root, "commit", "-q", "-m", f"run {i}")
        executions_module.event(root, run.id, kind="changed", ref=f"f{i}.txt", source="hook")
        executions_module.finish(root, run.id, outcome="ok")


def load_module(ref: str, name: str, relpath: str):
    text = subprocess.run(["git", "-C", REPO, "show", f"{ref}:{relpath}"],
                          capture_output=True, text=True, check=True).stdout
    path = os.path.join(tempfile.gettempdir(), f"eos-bench-{name}.py")
    with open(path, "w") as handle:
        handle.write(text)
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--before-ref", default="HEAD", help="git ref holding the pre-D2 core/consolidate.py")
    parser.add_argument("-n", type=int, default=300)
    args = parser.parse_args()

    from pathlib import Path

    from core import executions

    consolidate_before = load_module(args.before_ref, "consolidate_before", "core/consolidate.py")
    from core import consolidate as consolidate_after

    root = Path(tempfile.mkdtemp(prefix="eos-bench-ledger-"))
    os.environ["EOS_STATE_DIR"] = tempfile.mkdtemp(prefix="eos-bench-state-")
    try:
        build_ledger(root, executions, args.n)
        records = executions.load(root)

        started = time.perf_counter()
        before_result = consolidate_before.change_capture(root, records=records)
        before_elapsed = time.perf_counter() - started

        started = time.perf_counter()
        after_result = consolidate_after.change_capture(root, records=records)
        after_elapsed = time.perf_counter() - started

        print(f"n={args.n} runs")
        print(f"before (one git diff + one git log subprocess per run): {before_elapsed:.3f}s -- {before_result}")
        print(f"after  (batched):                                       {after_elapsed:.3f}s -- {after_result}")
        print(f"same result: {before_result == after_result}")
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    main()
