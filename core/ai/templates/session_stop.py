#!/usr/bin/env python3
"""Ask a session, once, what happened to the work it claimed.

Written by `eos init` / `eos ai update` (engine {{VERSION}}). Edit what it
checks with `eos work`, not this file: a regenerated hook overwrites it.

The other half of the loop the brief opens. A session that claims an item and
ends without closing it leaves the next session a claim it cannot act on: it
reads as an agent still working, so nobody takes it, and it goes stale a day
later. The claim costing more than it saves is the failure mode, and it is
entirely avoidable in the one moment the session still knows the answer.

It is narrow on purpose, in three ways.

**It asks only about items this session claimed.** Open work nobody took, and
work another session holds, are not this session's to close, and a hook that
raised them would be asking for a decision the agent cannot make.

**It asks once.** Claude Code sets `stop_hook_active` when a session is
already continuing because of a stop hook; seeing it, this exits immediately.
Nothing here can loop a session.

**Every answer is accepted.** `eos work done` closes it, `eos work block`
parks it with a reason, `eos work drop` abandons it with one. The escape is
the point: a gate with no way through gets deleted, and a deleted gate records
nothing at all.

Any failure exits 0. A session that cannot end because a hook broke is worse
than an unclosed claim.

It records that it ran, both ways. "How many sessions tried to end with work
still open" is the number that should fall if any of this is working, and it
cannot be read from a hook that only leaves a trace when it fires: sessions
that ended cleanly would be missing from the denominator, and the ratio would
look worst exactly when things improved.
"""
import datetime
import json
import os
import shutil
import subprocess
import sys

TIMEOUT_SECONDS = 20


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


def _held(root, session):
    """Items this session claimed and has not closed, or None if unknown.

    None and [] are different answers and are kept apart: nothing to close is
    a session that may end, while "EOS could not be asked" is a session this
    hook has no business stopping.
    """
    arguments = ["work", "list", root, "--session", session, "--format", "json"]
    for runner in _runners(root):
        try:
            done = subprocess.run(runner + arguments, capture_output=True, text=True,
                                  timeout=TIMEOUT_SECONDS)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if done.returncode != 0:
            continue
        try:
            items = json.loads(done.stdout or "[]")
        except ValueError:
            return None
        if isinstance(items, list):
            return items
    return None


def _record(root, session, clean):
    """One line per session this hook asked about, whichever way it went.

    Written directly rather than through EOS because the answer being recorded
    is this hook's, not a command's -- and only into a telemetry log that
    already exists, since the engine creates that file only where telemetry
    was turned on. `ok` is whether the session was leaving nothing behind.

    This is a near-copy of the same function in eos-brief.py. The two are
    standalone files deployed into a project and neither may import the
    other, and a third shared file would be one more thing to keep in sync
    across every project EOS has ever written to.
    """
    log = os.path.join(root, ".eos", "data", "telemetry.jsonl")
    if not os.path.isfile(log):
        return
    entry = {
        "at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "command": "close",
        "flags": [],
        "session": (session or "")[:64] or None,
        "ms": 0.0,
        "chars": 0,
        "tokens": 0,
        "rebuilt": False,
        "ok": bool(clean),
    }
    try:
        with open(log, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        return


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

    if payload.get("stop_hook_active"):
        return 0

    session = str(payload.get("session_id") or "")
    if not session:
        # With no session id there is no way to tell this session's claims from
        # anyone else's, and stopping a session over another agent's work would
        # be worse than saying nothing.
        return 0

    root = payload.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    held = _held(root, session)
    if held is None:
        return 0
    _record(root, session, clean=not held)
    if not held:
        return 0

    lines = [f"This session still holds {len(held)} work item(s). "
             "The next session reads them as work in progress:"]
    for item in held[:5]:
        lines.append(f"  {item.get('id')}  {item.get('title')}  [{item.get('status')}]")
    if len(held) > 5:
        lines.append(f"  ...and {len(held) - 5} more")
    lines.append("")
    lines.append("Close each one, in whichever way is true:")
    lines.append(f'  eos work done {root} <id> --note "what was done"')
    lines.append(f'  eos work block {root} <id> --reason "what it is waiting on"')
    lines.append(f'  eos work drop {root} <id> --reason "why it will not be done"')
    sys.stderr.write("\n".join(lines) + "\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
