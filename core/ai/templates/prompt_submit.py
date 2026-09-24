#!/usr/bin/env python3
"""Put what EOS knows about *this task* in front of the agent, when there is something.

Written by `eos init` / `eos ai update` (engine {{VERSION}}). Edit the brief
itself with `eos brief --task`, not this file: a regenerated hook overwrites it.

The SessionStart hook runs before anyone has said what the session is for, so
it can only rank by branch name. This one runs when the task is typed: the
prompt goes to `eos brief --task`, which answers with the procedure recorded
for it, the last runs of it and what the failed ones taught -- the two things
a fresh session otherwise rediscovers at its own cost, or improvises.

It fires on every prompt, so three rules keep it from becoming the cost it
exists to remove:

  - Nothing is printed when nothing matched. `--task-only` returns an empty
    string when no procedure, run or note answers the prompt; "continue" and
    "ok" cost nothing.
  - The same brief is not printed twice in one session. A digest of each
    delivered block is kept per session; a follow-up prompt about the same
    task gets nothing new, and a changed block (a run finished since) gets
    delivered again.
  - The prompt is a query and is never stored (ADR-019): it is passed to one
    process and discarded. Only digests of what EOS *printed* are kept.

Like the SessionStart hook it always exits 0 and never writes to stderr: a
prompt hook that fails noisily blocks the one thing the user is trying to do.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys

TIMEOUT_SECONDS = 10
# Enough of the prompt to match on; a pasted log does not make the match better.
MAX_TASK_CHARS = 2000


def _interpreter():
    if sys.version_info >= (3, 11):
        yield sys.executable
    for name in ("python3.14", "python3.13", "python3.12", "python3.11", "python3"):
        found = shutil.which(name)
        if found:
            yield found


def _runners(root):
    found = shutil.which("eos")
    if found:
        yield [found]
    runtime = os.path.join(root, ".eos", "runtime", "eos.py")
    if os.path.isfile(runtime):
        for python in _interpreter():
            yield [python, runtime]


def _seen_file(session):
    base = os.environ.get("EOS_STATE_DIR") or os.path.join(os.path.expanduser("~"), ".local", "state", "eos")
    safe = "".join(ch for ch in session if ch.isalnum() or ch in "-_.")[:64] or "unnamed"
    return os.path.join(base, "prompted", safe)


def _already_delivered(session, digest):
    if not session:
        return False
    try:
        with open(_seen_file(session), encoding="utf-8") as handle:
            return digest in {line.strip() for line in handle}
    except OSError:
        return False


def _remember(session, digest):
    if not session:
        return
    path = _seen_file(session)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(digest + "\n")
    except OSError:
        return


def main() -> int:
    try:
        raw = sys.stdin.read() if not sys.stdin.isatty() else ""
        payload = json.loads(raw) if raw.strip() else {}
    except (ValueError, OSError):
        payload = {}
    if not isinstance(payload, dict):
        return 0

    prompt = str(payload.get("prompt") or "").strip()[:MAX_TASK_CHARS]
    if not prompt:
        return 0
    root = payload.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    session = str(payload.get("session_id") or "")

    env = dict(os.environ)
    if session:
        env["EOS_SESSION"] = session
    arguments = ["brief", root, "--task", prompt, "--task-only", "--agent", "claude"]
    if session:
        arguments += ["--session", session]

    for runner in _runners(root):
        try:
            done = subprocess.run(runner + arguments, capture_output=True, text=True,
                                  timeout=TIMEOUT_SECONDS, env=env)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if done.returncode != 0:
            continue
        text = done.stdout.strip()
        if not text:
            return 0
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if _already_delivered(session, digest):
            return 0
        sys.stdout.write(text + "\n")
        _remember(session, digest)
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
