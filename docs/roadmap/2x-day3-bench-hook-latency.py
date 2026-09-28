"""One-off p50 measurement of the Bash tool's hook latency, before/after E1's
working-tree capture (2.x roadmap Day 3, item D1): a new PreToolUse:Bash hook
now fires on every Bash call (cheap text parse; a git subprocess only for a
mutating git verb), and PostToolUse does a second git subprocess (a
working-tree snapshot) for a mutating git call whose PreToolUse snapshot
exists. Measured against a real clone of this repo (real git history), with
an open run so the capture code path actually executes (without one,
`_post_tool` returns before ever reaching it -- both before and after).

"before" imports the pre-change core/hooks.py from `git show <ref>:...`
(default: the parent of HEAD's own change) into its own module, so the
comparison needs no stash/checkout of the working tree. 60 samples per
command per hook path, after 5 warm-up calls, median (p50).

Not part of the test suite -- a manual benchmark, kept for reproducing the
numbers in docs/roadmap/2x-progress.md's D1 row. Usage:
    python3 docs/roadmap/2x-day3-bench-hook-latency.py [--before-ref REF]
"""
import argparse
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)


def load_before_hooks(ref: str):
    text = subprocess.run(["git", "-C", REPO, "show", f"{ref}:core/hooks.py"],
                          capture_output=True, text=True, check=True).stdout
    path = os.path.join(tempfile.gettempdir(), "eos-bench-hooks-before.py")
    with open(path, "w") as handle:
        handle.write(text)
    spec = importlib.util.spec_from_file_location("hooks_before", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["hooks_before"] = module
    spec.loader.exec_module(module)
    return module


def measure(hooks_module, events, payload, n=60):
    times = []
    for i in range(n + 5):
        for event in events:
            sys.stdin = io.StringIO(json.dumps(payload))
            started = time.perf_counter()
            hooks_module.main([event])
            elapsed = (time.perf_counter() - started) * 1000
            if i >= 5:
                times.append(elapsed)
    times.sort()
    return times[len(times) // 2]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--before-ref", default="HEAD",
                        help="git ref holding the pre-D1 core/hooks.py (default: HEAD)")
    args = parser.parse_args()

    bench_repo = tempfile.mkdtemp(prefix="eos-bench-repo-")
    subprocess.run(["git", "clone", "-q", REPO, bench_repo], check=True)
    subprocess.run([sys.executable, os.path.join(REPO, "core", "eos.py"), "init", bench_repo, "--no-ai"],
                   check=True, capture_output=True)

    os.environ["EOS_STATE_DIR"] = tempfile.mkdtemp(prefix="eos-bench-state-")
    os.environ["CLAUDE_CODE_SESSION_ID"] = "bench"
    from core import executions, hooks as hooks_after

    hooks_before = load_before_hooks(args.before_ref)
    executions.start(bench_repo, "bench", session="bench")

    commands = {
        "read-only (ls -la)": "ls -la",
        "git read verb (git status)": "git status",
        "git mutating verb, no diff (git tag, forced)": "git tag -f eos-bench-mark",
    }
    try:
        for label, command in commands.items():
            payload = {"session_id": "bench", "cwd": bench_repo, "tool_name": "Bash",
                      "tool_input": {"command": command}, "tool_use_id": "b1"}
            before_p50 = measure(hooks_before, ["post-tool"], payload)
            after_post_only_p50 = measure(hooks_after, ["post-tool"], payload)
            after_full_p50 = measure(hooks_after, ["pre-tool", "post-tool"], payload)
            print(f"{label}:")
            print(f"  before (PostToolUse only -- no PreToolUse:Bash existed): {before_p50:.3f} ms")
            print(f"  after  (PostToolUse only, no PreToolUse snapshot):       {after_post_only_p50:.3f} ms")
            print(f"  after  (PreToolUse:Bash + PostToolUse, the real path):   {after_full_p50:.3f} ms")
    finally:
        shutil.rmtree(bench_repo, ignore_errors=True)


if __name__ == "__main__":
    main()
