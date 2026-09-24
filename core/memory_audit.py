"""The operational-memory acceptance harness — M0 of docs/plans/operational-memory.md.

Twenty-one checks, one per row of the audit's capability matrix, each
answering "does this project have this capability, and has it been used"
without writing anything. Two consumers share them:

  eos doctor --memory             the matrix for a real project, so a session
                                  learns where the plan stands by running a
                                  command rather than by asking
  tests/test_operational_memory   the same checks after exercising the
                                  planned interfaces on a fixture; the gate

Every check is read-only. A check that needs a record to exist reports
PARTIAL when the capability is present and nothing has used it yet, because
the audit that opened this plan found two stores built for this purpose that
had never been written to, and a matrix that could not tell "built" from
"used" would have called them done.

Statuses, and what a row must be for the plan to close:

  IMPLEMENTED   capability present and exercised in this project
  PARTIAL       capability present, or half of it; nothing has used it
  MISSING       nothing meaningful exists
  DECIDED       out of scope by a recorded decision; counts as satisfied

Interfaces this file probes are the ones the plan names. Where the plan
names a command, the probe looks for its handler; where it names a store,
the probe reads it. A later milestone implements to these names, and a name
that turns out wrong is changed here and in the plan in the same commit.
"""
from __future__ import annotations

import importlib
import inspect
import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from core import notes

IMPLEMENTED = "IMPLEMENTED"
PARTIAL = "PARTIAL"
MISSING = "MISSING"
DECIDED = "DECIDED"

SATISFIED = frozenset({IMPLEMENTED, DECIDED})

PLAN = "docs/plans/operational-memory.md"

# The brief a session starts with may not cost more than this; the plan's
# whole argument is that the alternative costs a hundred times as much.
BRIEF_TOKEN_BUDGET = 1500
CHARS_PER_TOKEN = 3.5

# Third-party vector machinery that core/ must not import (ADR-001, ADR-010).
_VECTOR_IMPORTS = re.compile(
    r"^\s*(?:import|from)\s+(?:numpy|faiss|sentence_transformers|torch|"
    r"openai|chromadb|annoy|hnswlib)\b", re.M)


@dataclass(frozen=True)
class Result:
    id: str
    capability: str
    milestone: str
    status: str
    evidence: str


def _module(name: str):
    try:
        return importlib.import_module(name)
    except ImportError:
        return None


def _has(module, *names: str) -> list[str]:
    """The names a module lacks, so an evidence line can say which."""
    return [name for name in names if module is None or not hasattr(module, name)]


def _accepts(fn, *params: str) -> list[str]:
    try:
        signature = inspect.signature(fn)
    except (TypeError, ValueError):
        return list(params)
    return [p for p in params if p not in signature.parameters]


def _db(root: Path) -> Path | None:
    from core import index
    path = index.db_path(root)
    return path if path.exists() else None


def _tables(db: Path) -> set[str]:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'")}
    finally:
        conn.close()


def _count(db: Path, sql: str) -> int:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return int(conn.execute(sql).fetchone()[0])
    finally:
        conn.close()


def _notes_of(root: Path, kind: str) -> list:
    return [n for n in notes.load_notes(root) if n.kind == kind]


def _executions(root: Path):
    """(module, folded records) or (None, []) before M1."""
    module = _module("core.executions")
    if module is None or _has(module, "load"):
        return module, []
    try:
        return module, list(module.load(root))
    except Exception as exc:  # noqa: BLE001 - a broken ledger is evidence, not a crash
        return module, [f"unreadable: {exc}"]


# --- the checks ------------------------------------------------------------------


def check_c01(root: Path) -> Result:
    db = _db(root)
    if db is None:
        return _r("C-01", PARTIAL, "no index yet; `eos index` builds it from the files")
    have = _tables(db)
    need = {"note", "search", "fact", "git_commit"}
    missing = sorted(need - have)
    if missing:
        return _r("C-01", PARTIAL, f"index present, tables missing: {', '.join(missing)}")
    return _r("C-01", IMPLEMENTED, f"{db.name}: {len(have)} tables, rebuilt from files")


def check_c02(root: Path) -> Result:
    recorded = notes.load_notes(root)
    if not recorded:
        return _r("C-02", PARTIAL, "notes store exists; no note written here yet")
    probe = recorded[-1]
    words = " ".join(list(notes._words(probe.title))[:4])
    found = notes.search_notes(root, words, limit=5) if words else []
    if not any(n.path == probe.path for n in found):
        return _r("C-02", PARTIAL, f"{len(recorded)} notes; search did not return one by its own title")
    return _r("C-02", IMPLEMENTED, f"{len(recorded)} notes, searchable")


def check_c03(root: Path) -> Result:
    if "procedure" not in notes.KINDS:
        return _r("C-03", MISSING, "no `procedure` note kind (M2)")
    lacking = _has(notes, "procedure_steps")
    if lacking:
        return _r("C-03", PARTIAL, f"kind exists; notes.{lacking[0]} missing")
    procedures = _notes_of(root, "procedure")
    with_steps = [p for p in procedures if notes.procedure_steps(p)]
    if not with_steps:
        return _r("C-03", PARTIAL, f"{len(procedures)} procedure notes, none with steps")
    return _r("C-03", IMPLEMENTED, f"{len(with_steps)} procedures with ordered steps")


def check_c04(root: Path) -> Result:
    module, records = _executions(root)
    if module is None:
        return _r("C-04", MISSING, "core.executions does not exist (M1)")
    lacking = _has(module, "start", "event", "finish", "load")
    if lacking:
        return _r("C-04", PARTIAL, f"core.executions lacks {', '.join(lacking)}")
    if records and isinstance(records[0], str):
        return _r("C-04", PARTIAL, records[0])
    with_events = [x for x in records if getattr(x, "events", None)]
    if not with_events:
        return _r("C-04", PARTIAL, f"ledger present, {len(records)} executions, none with an event")
    return _r("C-04", IMPLEMENTED, f"{len(records)} executions, {len(with_events)} with events")


def check_c05(root: Path) -> Result:
    module, records = _executions(root)
    if module is None:
        return _r("C-05", MISSING, "no execution ledger (M1)")
    events = [e for x in records if not isinstance(x, str) for e in getattr(x, "events", [])]
    if not events:
        return _r("C-05", PARTIAL, "no events to order")
    lacking = _has(events[0], "at", "session", "tool", "target", "kind")
    if lacking:
        return _r("C-05", PARTIAL, f"event lacks {', '.join(lacking)}")
    for x in records:
        stamps = [e.at for e in getattr(x, "events", [])]
        if stamps != sorted(stamps):
            return _r("C-05", PARTIAL, f"execution {x.id}: events not in `at` order")
    return _r("C-05", IMPLEMENTED, f"{len(events)} events, ordered, with session/tool/target")


def check_c06(root: Path) -> Result:
    cli = _module("core.eos")
    lacking = _has(cli, "cmd_run_event")
    if lacking:
        return _r("C-06", MISSING, "no `eos run event` handler (M1)")
    module, records = _executions(root)
    events = sum(len(getattr(x, "events", [])) for x in records if not isinstance(x, str))
    if not events:
        return _r("C-06", PARTIAL, "`eos run event` exists; nothing has appended through it here")
    return _r("C-06", IMPLEMENTED, f"`eos run event` present; {events} events appended")


def check_c07(root: Path) -> Result:
    from core import brief
    lacking = _accepts(brief.build, "task")
    if lacking:
        return _r("C-07", MISSING, "brief.build takes no `task` (M3)")
    if "procedure" not in notes.KINDS:
        return _r("C-07", PARTIAL, "task accepted; nothing procedural to match against yet")
    return _r("C-07", IMPLEMENTED, "brief.build(task=…) ranks procedures against the task")


def check_c08(root: Path) -> Result:
    module, records = _executions(root)
    if module is None:
        return _r("C-08", MISSING, "no execution ledger (M1)")
    lacking = _has(module, "ranked")
    if lacking:
        return _r("C-08", PARTIAL, "core.executions.ranked missing (M3)")
    if not [x for x in records if not isinstance(x, str)]:
        return _r("C-08", PARTIAL, "ranking exists; no executions to rank")
    return _r("C-08", IMPLEMENTED, f"executions.ranked over {len(records)} records")


def check_c09(root: Path) -> Result:
    from core import index
    floors = [name for name in ("index", "notes") if not hasattr(
        {"index": index, "notes": notes}[name], "SCORE_FLOOR")]
    if floors:
        return _r("C-09", PARTIAL, f"FTS5/IDF search exists; no SCORE_FLOOR in {', '.join(floors)} (M3)")
    db = _db(root)
    if db is None:
        return _r("C-09", PARTIAL, "floors exist; no index to search")
    reachable = _count(db, "SELECT COUNT(*) FROM search WHERE source IN ('execution', 'procedure')")
    if not reachable:
        return _r("C-09", PARTIAL, "floors exist; no execution or procedure rows in `search`")
    return _r("C-09", IMPLEMENTED, f"score floors set; {reachable} execution/procedure rows searchable")


def check_c10(root: Path) -> Result:
    core_dir = Path(notes.__file__).resolve().parent
    offenders = [p.name for p in core_dir.rglob("*.py")
                 if _VECTOR_IMPORTS.search(p.read_text(encoding="utf-8", errors="ignore"))]
    if offenders:
        return _r("C-10", MISSING, f"vector imports in core/: {', '.join(offenders)} — ADR-001/010 broken")
    return _r("C-10", DECIDED, "no vector retrieval by ADR-001/ADR-010; core/ imports none")


def check_c11(root: Path) -> Result:
    from core import brief
    lacking = _accepts(brief.build, "task", "budget")
    if lacking:
        return _r("C-11", MISSING, f"brief.build lacks {', '.join(lacking)} (M3)")
    procedures = _notes_of(root, "procedure") if "procedure" in notes.KINDS else []
    if not procedures:
        return _r("C-11", PARTIAL, "budgeted brief exists; no procedure to brief about")
    text = brief.build(root, task=procedures[0].title)
    tokens = int(len(text) / CHARS_PER_TOKEN)
    if tokens > BRIEF_TOKEN_BUDGET:
        return _r("C-11", PARTIAL, f"brief --task is ~{tokens} tokens, over {BRIEF_TOKEN_BUDGET}")
    return _r("C-11", IMPLEMENTED, f"brief --task ~{tokens} tokens (budget {BRIEF_TOKEN_BUDGET})")


def check_c12(root: Path) -> Result:
    module, records = _executions(root)
    if module is None:
        return _r("C-12", MISSING, "no execution ledger (M1)")
    if _has(module, "tools"):
        return _r("C-12", PARTIAL, "core.executions.tools missing (M5)")
    used = list(module.tools(root))
    if not used:
        return _r("C-12", PARTIAL, "tools() exists; no tool use recorded")
    return _r("C-12", IMPLEMENTED, f"{len(used)} tools with counts and last outcome")


def check_c13(root: Path) -> Result:
    module, records = _executions(root)
    if module is None:
        return _r("C-13", MISSING, "no execution ledger (M1)")
    if _has(module, "diff"):
        return _r("C-13", PARTIAL, "core.executions.diff missing (M5)")
    real = [x for x in records if not isinstance(x, str)]
    if not real:
        return _r("C-13", PARTIAL, "diff() exists; no executions")
    lacking = _has(real[0], "commit_start", "commit_end")
    if lacking:
        return _r("C-13", PARTIAL, f"execution record lacks {', '.join(lacking)}")
    with_change = [x for x in real if any(getattr(e, "kind", "") == "changed" for e in x.events)]
    if not with_change:
        return _r("C-13", PARTIAL, "no execution carries a `changed` event yet")
    return _r("C-13", IMPLEMENTED, f"{len(with_change)} executions with changed paths and a commit range")


def check_c14(root: Path) -> Result:
    if "decision" not in notes.KINDS:
        return _r("C-14", MISSING, "no `decision` note kind (M4)")
    decisions = _notes_of(root, "decision")
    if not decisions:
        return _r("C-14", PARTIAL, "kind exists; no decision recorded here")
    return _r("C-14", IMPLEMENTED, f"{len(decisions)} decisions")


def check_c15(root: Path) -> Result:
    if "lesson" not in notes.KINDS:
        return _r("C-15", MISSING, "no `lesson` note kind (M4)")
    lessons = _notes_of(root, "lesson")
    linked = [n for n in lessons if getattr(n, "execution", None)]
    if not lessons:
        return _r("C-15", PARTIAL, "kind exists; no lesson recorded here")
    if not linked:
        return _r("C-15", PARTIAL, f"{len(lessons)} lessons, none linked to an execution")
    return _r("C-15", IMPLEMENTED, f"{len(linked)} lessons linked to executions")


def check_c16(root: Path) -> Result:
    from core import verification
    fields = {f.name for f in verification.Record.__dataclass_fields__.values()}
    lacking = sorted({"session", "execution"} - fields)
    if lacking:
        return _r("C-16", PARTIAL, f"verification records lack {', '.join(lacking)} (M4)")
    records = verification.load(root) if hasattr(verification, "load") else []
    linked = [x for x in records if getattr(x, "execution", None)]
    if not linked:
        return _r("C-16", PARTIAL, f"{len(records)} verifications, none tied to an execution")
    return _r("C-16", IMPLEMENTED, f"{len(linked)} verifications tied to executions")


def check_c17(root: Path) -> Result:
    if "procedure" not in notes.KINDS:
        return _r("C-17", MISSING, "no procedures to learn into (M2)")
    module, _ = _executions(root)
    if module is None or _has(module, "finish"):
        return _r("C-17", PARTIAL, "no executions.finish to move counters (M1/M2)")
    procedures = _notes_of(root, "procedure")
    if procedures and _has(procedures[0], "runs_ok", "runs_failed", "last_verified"):
        return _r("C-17", PARTIAL, "procedure notes carry no counters")
    moved = [p for p in procedures if (p.runs_ok or 0) + (p.runs_failed or 0) > 0]
    if not moved:
        return _r("C-17", PARTIAL, f"{len(procedures)} procedures, no counter has moved")
    return _r("C-17", IMPLEMENTED, f"{len(moved)} procedures with run counters and last_verified")


def check_c18(root: Path) -> Result:
    if "procedure" not in notes.KINDS:
        return _r("C-18", MISSING, "no procedures (M2)")
    if _has(notes, "procedure_confidence"):
        return _r("C-18", PARTIAL, "notes.procedure_confidence missing (M4)")
    stored = [p.path.name for p in _notes_of(root, "procedure")
              if re.search(r"^confidence:", p.path.read_text(encoding="utf-8"), re.M)]
    if stored:
        return _r("C-18", MISSING, f"confidence STORED in {stored[0]} — must be derived at read time")
    return _r("C-18", IMPLEMENTED, "confidence derived from counters and age, never stored")


def check_c19(root: Path) -> Result:
    module, records = _executions(root)
    if module is None:
        return _r("C-19", MISSING, "no execution ledger (M1)")
    if _has(module, "by_session"):
        return _r("C-19", PARTIAL, "executions.by_session missing")
    real = [x for x in records if not isinstance(x, str)]
    with_session = [x for x in real if getattr(x, "session", None)]
    if not with_session:
        return _r("C-19", PARTIAL, f"{len(real)} executions, none carries a session id")
    return _r("C-19", IMPLEMENTED, f"{len(with_session)} executions joinable to work and notes by session")


def check_c20(root: Path) -> Result:
    from core import brief
    lacking = _accepts(brief.build, "task", "budget")
    if lacking:
        return _r("C-20", MISSING, f"brief.build lacks {', '.join(lacking)} (M3)")
    module, records = _executions(root)
    real = [x for x in records if not isinstance(x, str)]
    if len(real) < 10:
        return _r("C-20", PARTIAL, f"{len(real)} executions; the budget is proven at volume (test fixture: 1,000)")
    procedures = _notes_of(root, "procedure") if "procedure" in notes.KINDS else []
    task = procedures[0].title if procedures else real[-1].title
    tokens = int(len(brief.build(root, task=task)) / CHARS_PER_TOKEN)
    if tokens > BRIEF_TOKEN_BUDGET:
        return _r("C-20", PARTIAL, f"~{tokens} tokens with {len(real)} executions")
    return _r("C-20", IMPLEMENTED, f"~{tokens} tokens with {len(real)} executions on record")


def check_c21(root: Path) -> Result:
    templates = Path(notes.__file__).resolve().parent / "ai" / "templates"
    if not (templates / "prompt_submit.py").exists():
        return _r("C-21", MISSING, "no UserPromptSubmit hook template (M3)")
    settings = root / ".claude" / "settings.json"
    if not settings.exists():
        return _r("C-21", PARTIAL, "template exists; this project has no .claude/settings.json")
    try:
        hooks = json.loads(settings.read_text(encoding="utf-8")).get("hooks", {})
    except (OSError, ValueError):
        return _r("C-21", PARTIAL, "template exists; settings.json unreadable")
    registered = {event for event in ("SessionStart", "UserPromptSubmit") if hooks.get(event)}
    if registered != {"SessionStart", "UserPromptSubmit"}:
        return _r("C-21", PARTIAL, f"template exists; registered here: {', '.join(sorted(registered)) or 'nothing'}")
    return _r("C-21", IMPLEMENTED, "SessionStart and UserPromptSubmit hooks registered")


# --- the matrix -------------------------------------------------------------------

CHECKS = (
    ("C-01", "SQLite persistence", "M0", check_c01),
    ("C-02", "Project knowledge", "M0", check_c02),
    ("C-03", "Procedural memory / HOW", "M2", check_c03),
    ("C-04", "Episodic memory / WHAT", "M1", check_c04),
    ("C-05", "Execution timeline", "M1", check_c05),
    ("C-06", "Execution events", "M1", check_c06),
    ("C-07", "Task matching", "M3", check_c07),
    ("C-08", "Historical execution retrieval", "M3", check_c08),
    ("C-09", "SQL/FTS retrieval", "M3", check_c09),
    ("C-10", "Semantic retrieval", "—", check_c10),
    ("C-11", "Context packing", "M3", check_c11),
    ("C-12", "Tool/application memory", "M5", check_c12),
    ("C-13", "File/change tracking", "M5", check_c13),
    ("C-14", "Decision memory", "M4", check_c14),
    ("C-15", "Lesson memory", "M4", check_c15),
    ("C-16", "Evidence", "M4", check_c16),
    ("C-17", "Success/failure learning", "M2", check_c17),
    ("C-18", "Confidence", "M4", check_c18),
    ("C-19", "Cross-session persistence", "M1", check_c19),
    ("C-20", "Minimal-token retrieval", "M3", check_c20),
    ("C-21", "AI/MCP integration", "M3", check_c21),
)

_BY_ID = {check_id: (capability, milestone, fn) for check_id, capability, milestone, fn in CHECKS}


def _r(check_id: str, status: str, evidence: str) -> Result:
    capability, milestone, _ = _BY_ID[check_id]
    return Result(check_id, capability, milestone, status, evidence)


def check(check_id: str, root: str | Path) -> Result:
    """One check by id, for a test that just exercised its interface."""
    return _BY_ID[check_id][2](Path(root).expanduser().resolve())


def run(root: str | Path) -> list[Result]:
    """All twenty-one, in matrix order. A check that raises is a MISSING row
    that says so, not a crash: the matrix must always render in full, or a
    session cannot tell a red row from a harness that stopped early."""
    project = Path(root).expanduser().resolve()
    results = []
    for check_id, capability, milestone, fn in CHECKS:
        try:
            results.append(fn(project))
        except Exception as exc:  # noqa: BLE001 - see docstring
            results.append(Result(check_id, capability, milestone, MISSING,
                                  f"check raised {type(exc).__name__}: {exc}"))
    return results


def complete(results: list[Result]) -> bool:
    return all(r.status in SATISFIED for r in results)


def render(results: list[Result]) -> str:
    satisfied = sum(1 for r in results if r.status in SATISFIED)
    width = max(len(r.capability) for r in results)
    lines = [f"Operational memory — {satisfied} of {len(results)} rows satisfied  ({PLAN})", ""]
    for r in results:
        lines.append(f"  {r.id}  {r.capability:<{width}}  {r.status:<11} {r.milestone:<3} {r.evidence}")
    open_rows = [r for r in results if r.status not in SATISFIED]
    if open_rows:
        first = min(open_rows, key=lambda r: (r.milestone, r.id))
        lines += ["", f"Next: {first.id} ({first.milestone}) — {first.evidence}"]
    else:
        lines += ["", "Every row satisfied. The plan's §8 protocol decides whether that is 1.0.0."]
    return "\n".join(lines)
