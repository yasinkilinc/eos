#!/usr/bin/env python3
"""Put EOS's session brief in front of the agent, with nothing to decide.

Written by `eos init` / `eos ai update` (engine {{VERSION}}). Edit the brief
itself with `eos brief`, not this file: a regenerated hook overwrites it.

A SessionStart hook's stdout becomes context the session starts with, so this
is the one surface where EOS costs nothing to *reach for* -- there is no tool
to choose and no call to remember. Measured on a real workspace, EOS offered
as twelve MCP tools was reached for in 4 of 25 sessions; a hook is reached in
all of them, because reaching is not a decision anyone makes.

Two rules, both about not being in the way:

It always exits 0 and never writes to stderr on a failure path. A session-start
hook that fails noisily -- no `eos` on PATH, an unscanned project, a slow
filesystem -- teaches people to delete the hook, and the whole loop goes with
it.

It prints nothing when there is nothing to say. An empty ledger and no matching
notes is the common state of a fresh project, and a block that says so every
morning is a block that stops being read.

Silence is not the same as absence, though, and this file is the one place
that distinction could have been lost. `eos cost` counts sessions by the calls
they made; a hook that failed quietly would remove its whole session from the
denominator, so a project where nothing worked at all would report the best
adoption number it has ever had. When every way of reaching EOS fails, this
records that it tried -- and only into a telemetry log that already exists,
because a log that does not is a project that has not turned telemetry on.
"""
import datetime
import json
import os
import shutil
import subprocess
import sys

TIMEOUT_SECONDS = 20


def _interpreter():
    """A Python this engine actually runs on (3.11+), preferring this one.

    A hook invoked by the system `python3` can easily be running 3.9, and the
    engine's own annotations are 3.10+ syntax -- so handing it `sys.executable`
    produced a non-zero exit and, by the rule above, silence. Silence that
    looks like "nothing in flight" is the worst failure this file can have.
    """
    if sys.version_info >= (3, 11):
        yield sys.executable
    for name in ("python3.14", "python3.13", "python3.12", "python3.11", "python3"):
        found = shutil.which(name)
        if found:
            yield found


def _runners(root):
    """Every way to reach EOS here, best first.

    Both are tried rather than the first that exists: an `eos` on PATH older
    than this project's runtime copy is the ordinary state of a workspace a
    week after an upgrade, and it fails on exactly the new command this hook
    depends on. Falling through to the project's own runtime costs one failed
    process and keeps the loop working.
    """
    found = shutil.which("eos")
    if found:
        yield [found]
    runtime = os.path.join(root, ".eos", "runtime", "eos.py")
    if os.path.isfile(runtime):
        for python in _interpreter():
            yield [python, runtime]


def main() -> int:
    payload = {}
    try:
        raw = sys.stdin.read() if not sys.stdin.isatty() else ""
        if raw.strip():
            payload = json.loads(raw)
    except (ValueError, OSError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}

    root = payload.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    session = str(payload.get("session_id") or "")

    # EOS_SESSION is exported as well as passed, so every *other* EOS call this
    # session makes is attributed to it too. Without that, `eos cost` can count
    # calls and not sessions, which is the number worth having.
    env = dict(os.environ)
    if session:
        env["EOS_SESSION"] = session

    arguments = ["brief", root, "--agent", "claude"]
    if session:
        arguments += ["--session", session]

    for runner in _runners(root):
        try:
            done = subprocess.run(runner + arguments, capture_output=True, text=True,
                                  timeout=TIMEOUT_SECONDS, env=env)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if done.returncode == 0:
            sys.stdout.write(done.stdout)
            return 0

    _record_failure(root, session)
    return 0


def _record_failure(root, session):
    """Leave a line saying the brief was attempted and could not be produced.

    Written directly rather than through EOS, because by this point EOS is
    what could not be run. It goes only into a telemetry log that already
    exists: the writer creates that file only where a project turned telemetry
    on, so its absence is consent nobody gave, and this respects it the same
    way the engine does.
    """
    log = os.path.join(root, ".eos", "data", "telemetry.jsonl")
    if not os.path.isfile(log):
        return
    entry = {
        "at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "command": "brief",
        "flags": [],
        "session": (session or "")[:64] or None,
        "ms": 0.0,
        "chars": 0,
        "tokens": 0,
        "rebuilt": False,
        "ok": False,
    }
    try:
        with open(log, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        return


if __name__ == "__main__":
    raise SystemExit(main())
