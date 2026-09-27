"""Typed procedure steps (2.x roadmap E2, ADR-031).

A step may carry marks after its text, each optional:

  (tool: X)        the wrapper or program that does it
  (in: a, b)       what it reads: an earlier step's output or a prerequisite
  (out: c)         what it writes, for a later step to read
  (on_failure: N)  where to go when it fails: a step number, or words (`stop`)

The engine reads the marks and never interprets the text. From them: `lint`
names an input nothing provides, an on_failure naming no step and on_failure
jumps that go round in a circle; `attribute` places a run's events on the steps
by tool, in order, so a failed run says which step failed.
"""
from __future__ import annotations

import dataclasses
import re

_MARK = re.compile(r"\((?P<name>tool|in|out|on_failure):\s*(?P<value>[^)]*?)\s*\)", re.IGNORECASE)
_STEP_REF = re.compile(r"(?:step\s*)?(\d+)", re.IGNORECASE)


@dataclasses.dataclass(frozen=True)
class Step:
    number: int
    raw: str
    text: str
    tool: str | None = None
    inputs: tuple[str, ...] = ()
    outputs: tuple[str, ...] = ()
    on_failure: str | None = None

    def jump(self) -> int | None:
        """The step number on_failure names, None when it names none."""
        found = _STEP_REF.fullmatch(self.on_failure or "")
        return int(found.group(1)) if found else None


def _names(value: str) -> tuple[str, ...]:
    return tuple(name.strip() for name in value.split(",") if name.strip())


def parse(body: str) -> list[Step]:
    from core import notes

    parsed = []
    for number, raw in enumerate(notes.steps_in(body), start=1):
        marks: dict[str, str] = {}
        for found in _MARK.finditer(raw):
            marks.setdefault(found.group("name").lower(), found.group("value"))
        parsed.append(Step(number=number, raw=raw, text=re.sub(r"\s+", " ", _MARK.sub("", raw)).strip(),
                           tool=marks.get("tool") or None, inputs=_names(marks.get("in", "")),
                           outputs=_names(marks.get("out", "")), on_failure=marks.get("on_failure") or None))
    return parsed


def _cycles(parsed: list[Step]) -> list[list[int]]:
    jumps = {s.number: s.jump() for s in parsed if s.jump() is not None}
    found, seen = [], set()
    for start in sorted(jumps):
        path, at = [], start
        while at in jumps and at not in path and at not in seen:
            path.append(at)
            at = jumps[at]
        if at in path:
            loop = path[path.index(at):]
            found.append(loop + [at])
        seen.update(path)
    return found


def lint(parsed: list[Step], body: str) -> list[str]:
    from core import notes

    prerequisites = (notes.section_in(body, "Prerequisites") or "").casefold()
    problems, written = [], set()
    for step in parsed:
        for name in step.inputs:
            if name.casefold() not in written and name.casefold() not in prerequisites:
                problems.append(f"step {step.number} reads `{name}`, which no earlier step writes (out:) "
                                "and no prerequisite names")
        written |= {name.casefold() for name in step.outputs}
        jump = step.jump()
        if jump is not None and not 1 <= jump <= len(parsed):
            problems.append(f"step {step.number}: on_failure names step {jump}, and there are {len(parsed)} steps")
    for loop in _cycles(parsed):
        problems.append(f"steps {' -> '.join(map(str, loop))}: on_failure goes round in a circle, "
                        "a failure there never ends")
    return problems


def _failed(event) -> bool:
    if event.status == "failed":
        return True
    return event.exit_code not in (None, 0)


def _placed(parsed: list[Step], events) -> list[tuple[int, object]]:
    """(step number, event) for each event whose tool a step names: the first
    such step from the current one on, else the last one before it (a retry)."""
    from core.executions import normalize_tool

    tools = [(s.number, normalize_tool(s.tool)) for s in parsed if s.tool]
    placed, at = [], 1
    for event in events:
        tool = normalize_tool(event.tool)
        if tool is None:
            continue
        ahead = [n for n, t in tools if t == tool and n >= at]
        behind = [n for n, t in tools if t == tool and n < at]
        number = ahead[0] if ahead else behind[-1] if behind else None
        if number is not None:
            placed.append((number, event))
            at = number
    return placed


def attribute(parsed: list[Step], events) -> list[dict]:
    """One row per step: `ok`, `failed` or `not seen` by its last placed event."""
    last: dict[int, object] = {}
    counts: dict[int, int] = {}
    for number, event in _placed(parsed, events):
        last[number] = event
        counts[number] = counts.get(number, 0) + 1
    rows = []
    for step in parsed:
        event = last.get(step.number)
        status = "not seen" if event is None else "failed" if _failed(event) else "ok"
        rows.append({"step": step.number, "status": status, "events": counts.get(step.number, 0),
                     "exit_code": getattr(event, "exit_code", None)})
    return rows


def failed_step(parsed: list[Step], events) -> int | None:
    """The step of the last placed event that failed, None when none did."""
    failed = [number for number, event in _placed(parsed, events) if _failed(event)]
    return failed[-1] if failed else None
