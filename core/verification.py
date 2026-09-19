"""Record what was actually run, and what happened.

EOS does not run anything. Executing a test means a build tool, an
environment, credentials and minutes -- and `core/` is stdlib-only by design
(ADR-010), so making it a build client would trade that property for work
something else already does well. The workspace this was measured in already
has the right contract: a wrapper that keeps the full log on disk and prints a
fixed-shape digest.

So the division is: an adapter executes, and this records the evidence.
Command, exit code, a digest of the output, where the log is, which commit was
in the tree, when. All of it observed, none of it inferred.

The verdict is deliberately not EOS's to make. A failing test can mean the
rule is not enforced, or that the test is wrong, or that the environment is;
nothing in an exit code separates those, and a system that guessed would
produce exactly the confident wrong answer the rest of this codebase is built
to avoid. `outcome` is what the run did. `verdict` is optional, written by a
person, and never filled in automatically.

Records are append-only and live in the knowledge directory beside the notes,
because they are observations rather than derivations: `eos scan` rewrites
everything under `.eos/data`, and an execution cannot be recomputed from a
file tree.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

from core import notes

FILENAME = "verifications.jsonl"

PASSED = "passed"
FAILED = "failed"
ERRORED = "errored"
OUTCOMES = (PASSED, FAILED, ERRORED)

# What a person may conclude, once they have looked. EOS writes none of these
# on its own; they exist so a conclusion has somewhere to live next to the
# evidence for it rather than in a chat log.
VERDICTS = (
    "expected-behaviour",   # the code is right; the test asserted the wrong thing
    "test-defect",          # the test is wrong
    "environment-failure",  # neither; the run could not be trusted
    "potential-defect",     # the behaviour looks wrong and needs more evidence
    "confirmed-defect",     # reproduced, understood, and wrong
)


@dataclasses.dataclass(frozen=True)
class Record:
    code: str
    outcome: str
    command: str
    exit_code: int | None
    recorded_at: str
    commit: str | None = None
    log: str | None = None
    output_sha256: str | None = None
    verdict: str | None = None
    note: str | None = None

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


def path_for(project_root: str | Path) -> Path:
    return notes.notes_dir(project_root) / FILENAME


def record(project_root: str | Path, code: str, outcome: str, command: str,
           exit_code: int | None = None, log: str | None = None,
           output: str | None = None, verdict: str | None = None,
           note: str | None = None) -> Record:
    """Append one execution record. Nothing here interprets the result."""
    from core.knowledge.evidence import utc_now

    if outcome not in OUTCOMES:
        raise ValueError(f"outcome must be one of {', '.join(OUTCOMES)}; got {outcome!r}")
    if verdict is not None and verdict not in VERDICTS:
        raise ValueError(f"verdict must be one of {', '.join(VERDICTS)}; got {verdict!r}")
    if not command.strip():
        raise ValueError("a record without the command that produced it is not evidence")

    entry = Record(
        code=code, outcome=outcome, command=command.strip(), exit_code=exit_code,
        recorded_at=utc_now(), commit=_head(project_root), log=log,
        output_sha256=hashlib.sha256(output.encode("utf-8")).hexdigest() if output else None,
        verdict=verdict, note=note,
    )
    target = path_for(project_root)
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry.to_dict(), ensure_ascii=False) + "\n")
    return entry


def load(project_root: str | Path) -> list[Record]:
    """Every recorded run, oldest first. An unreadable line is skipped, not fatal."""
    target = path_for(project_root)
    if not target.is_file():
        return []
    found: list[Record] = []
    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
            found.append(Record(**{key: data.get(key) for key in
                                   (field.name for field in dataclasses.fields(Record))}))
        except (ValueError, TypeError):
            continue
    return found


def latest_by_code(project_root: str | Path) -> dict[str, Record]:
    """The most recent run per behaviour code."""
    latest: dict[str, Record] = {}
    for entry in load(project_root):
        latest[entry.code] = entry
    return latest


def _head(project_root: str | Path) -> str | None:
    """The commit the tree was on, so a record can be placed in history."""
    import shutil

    git = shutil.which("git")
    if git is None:
        return None
    try:
        done = subprocess.run([git, "-C", str(project_root), "rev-parse", "--verify", "--quiet", "HEAD"],
                              capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if done.returncode != 0:
        return None
    return done.stdout.decode("utf-8", errors="replace").strip() or None


def summary(project_root: str | Path) -> dict[str, Any]:
    latest = latest_by_code(project_root)
    tally: dict[str, int] = {}
    for entry in latest.values():
        tally[entry.outcome] = tally.get(entry.outcome, 0) + 1
    verdicts: dict[str, int] = {}
    for entry in latest.values():
        if entry.verdict:
            verdicts[entry.verdict] = verdicts.get(entry.verdict, 0) + 1
    return {"codes": len(latest), "outcomes": tally, "verdicts": verdicts,
            "runs": len(load(project_root))}
