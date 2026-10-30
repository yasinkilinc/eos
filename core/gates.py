"""Gates: ask the ledger whether an action is backed by evidence.

The questions live here; the host (a hook) decides what an action is and what a
refusal does. `check` reads decisions and events only, never the repository.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from core import decisions, executions

MODES = ("off", "warn", "block")
ACTIONS = ("edit-test", "edit-main", "edit-other", "confirm", "env-write", "commit", "push")
TOOL = "gate"

_LOOKED_AT = ("evidenced", "resolved")
_RED_TOOLS = ("mvn", "apitest")
_REF_CUT = 120
_EXEMPT_TOOL = "product-upgrade"
_EXEMPT_PROCEDURE = "upgrade"
_FIX_WORDS = re.compile(r"\b(bug|fix|hata|duzelt\w*)\b")
_ROOT_CAUSE = ("root cause:", "kok neden:")
_UNSAFE_TARGET = re.compile(r"[\s='\"]")
_VERIFY_KINDS = ("ran", "verified")
_VERIFY_SOURCES = ("wrapper", "hook")

_CODE = re.compile(r"```.*?(?:```|\Z)|`[^`\n]*`", re.S)
_CLAIM = re.compile(
    r"(?<!\w)(?P<neg>(?:not|\w*n['\u2019]t)\s+)?"
    r"(?:bitti|tamamlandi|duzeldi|gecti|calisiyor|done|fixed|passing|passes|works)(?!\w)")

_NOT_YET = re.compile(r"(?<!\w)(?:not\s+yet|henuz|daha)\s+(?:\w+\s+)?$")

_FIX_RUN = 'eos run start . --title "…" --work <id>'
_FIX_LOOK = 'eos decide --claim "…" --evidence <e-id>'
_FIX_RED = ("automation/mvn.sh test <service> or automation/apitest.sh run env1 <spec> "
            "showing the failure")
_FIX_ROOT = 'eos decide --claim "kök neden: …" --evidence <e-id>'


@dataclass(frozen=True)
class Refusal:
    rule: str
    detail: str
    fix: str
    ids: tuple[str, ...]


def _gates_table(project_root: str | Path, name: str = "gates") -> dict | None:
    from core.lib.config_io import ConfigIO

    try:
        table = ConfigIO.read_toml(Path(project_root) / ".eos" / "config.toml").get(name)
    except (OSError, ValueError):
        return None
    return table if isinstance(table, dict) else None


def mode(project_root: str | Path) -> str:
    """`[gates] mode` of the project's `.eos/config.toml`; a project with no `[gates]` table
    takes its workspace's, so a session in a service has the workspace's switch. Anything
    unreadable or unknown is `off`."""
    from core import workspace

    table = _gates_table(project_root)
    if table is None:
        parent = workspace.of(project_root)
        table = _gates_table(parent) if parent is not None else None
    value = table.get("mode") if table is not None else None
    return value if value in MODES else "off"


def resume_auto(project_root: str | Path) -> bool:
    """`[resume] auto` of `.eos/config.toml` (default True); a project with no `[resume]`
    table takes its workspace's, as `mode` does. Anything but `false` is True."""
    from core import workspace

    table = _gates_table(project_root, "resume")
    if table is None:
        parent = workspace.of(project_root)
        table = _gates_table(parent, "resume") if parent is not None else None
    return table.get("auto") is not False if table is not None else True


def _folded(text: str) -> str:
    return text.replace("İ", "i").replace("I", "ı").lower().translate(decisions._FOLD)


def _scope_events(records: list[executions.Record], run: executions.Record):
    return [e for r in records if decisions._in_scope(r, run.work_item, run.id) for e in r.events]


def _rules(action: str) -> tuple[str, ...]:
    if action == "edit-test":
        return ("nothing-looked-at",)
    if action == "edit-main":
        return ("open-assumption", "nothing-looked-at", "red-first")
    if action == "env-write":
        return ("open-assumption", "nothing-looked-at", "root-cause")
    if action in ("confirm", "commit", "push"):
        return ("open-assumption", "nothing-looked-at")
    return ()


def _red_exempt(run: executions.Record, tool: str | None) -> bool:
    return tool == _EXEMPT_TOOL or (run.procedure or "").startswith(_EXEMPT_PROCEDURE)


def _looked_at(scope: list[decisions.Decision], records: list[executions.Record],
               run: executions.Record, now: str | None) -> list[decisions.Decision]:
    """The scope's evidenced or resolved decisions whose evidence, checked again now,
    still passes: live-state evidence older than 24 h no longer does."""
    index = decisions.event_index(records)
    return [d for d in scope if d.state in _LOOKED_AT
            and not decisions._reasons(records, run, list(d.evidence), now, index=index)]


def check(project_root: str | Path, *, session: str | None, action: str, path: str | None = None,
          tool: str | None = None, records: list[executions.Record] | None = None,
          now: str | None = None) -> list[Refusal]:
    """The refusals for `action`, empty when the ledger backs it. `path` is the host's
    context, no rule reads it yet; `now` dates the evidence freshness check."""
    if action not in ACTIONS:
        raise ValueError(f"action must be one of {', '.join(ACTIONS)}; got {action!r}")
    no_run = [Refusal("no-open-run", "no execution is open for this session", _FIX_RUN, ())]
    found = executions.current(project_root, session)
    if found is None:
        return no_run
    if records is None:
        records = decisions.all_records(project_root, session)
    run = next((r for r in records if r.id == found[0]), None)
    if run is None:
        return no_run
    scope = decisions._fold(records, run.work_item, run.id)
    looked_at = _looked_at(scope, records, run, now)
    wanted = _rules(action)
    refusals: list[Refusal] = []
    pending = [d for d in scope if d.state == "assumption"]
    if "open-assumption" in wanted and pending:
        first = pending[0].id
        # A prompt approves only on a run its session opened: a session-less run has none.
        fix = (f"eos decide --resolve {first} --evidence <e-id> or the user writes `{first} onay`"
               if run.session else
               f"resolve it with evidence: eos decide --resolve {first} --evidence <e-id>")
        refusals.append(Refusal(
            "open-assumption",
            "; ".join(f"{d.id} unverified, check: {d.check or '-'}" for d in pending),
            fix, tuple(d.id for d in pending)))
    if "nothing-looked-at" in wanted and not looked_at:
        refusals.append(Refusal("nothing-looked-at",
                                "no decision in this scope rests on evidence", _FIX_LOOK, ()))
    if "red-first" in wanted and not _red_exempt(run, tool):
        services = _services(path)
        if not any(e.kind == "ran" and e.tool in _RED_TOOLS and e.exit_code not in (None, 0)
                   and e.source in _VERIFY_SOURCES
                   and (e.tool != "mvn" or services is None or _mvn_service(e) in (None, *services))
                   for e in _scope_events(records, run)):
            refusals.append(Refusal("red-first", "no failing apitest run, or mvn run of the "
                                    "edited service, before this edit", _FIX_RED, ()))
    if "root-cause" in wanted and _FIX_WORDS.search(_folded(run.title)) and not any(
            _folded(d.claim).lstrip().startswith(_ROOT_CAUSE) for d in looked_at):
        refusals.append(Refusal("root-cause", "a fix run writes to an environment with no root "
                                "cause decision", _FIX_ROOT, ()))
    return refusals


def source_change(entry: executions.Event, project_root: str | Path, *,
                  ledger: Path | None = None) -> bool:
    """A `changed` event the done gate owes a check for: a source file (the note gate's
    suffixes) outside the knowledge dir of the project whose `ledger` recorded it
    (`project_root` when there is no ledger or it belongs to no project); a relative
    ref is that project's. A note EOS wrote never is one."""
    from core import note_gate, notes

    if entry.kind != "changed" or executions.is_memory_write(entry) or not entry.ref:
        return False
    if not entry.ref.endswith(note_gate.SOURCE_SUFFIXES):
        return False
    owner = executions._root_of(ledger) if ledger is not None else None
    root = Path(owner or project_root).expanduser().resolve()
    target = Path(entry.ref).expanduser()
    target = (target if target.is_absolute() else root / target).resolve()
    knowledge = notes.notes_dir(root)
    return target != knowledge and knowledge not in target.parents


def fresh_verification(records: list[executions.Record], run: executions.Record, *,
                       after: executions.Event | None) -> executions.Event | None:
    """The latest event of `run` a tool recorded as a passing check (`ran` or `verified`
    with exit 0; a `called` lookup is no check), later in the run's ledger order than
    `after` (the last source change; None: anywhere). Order is the event's position,
    never its whole-second stamp, so `records` should be the run's own ledger. A
    subagent's report, a cli-written event and a non-zero exit never count."""
    best: executions.Event | None = None
    for record in records:
        if record.id != run.id:
            continue
        for entry in record.events:
            if (entry.kind not in _VERIFY_KINDS or entry.source not in _VERIFY_SOURCES
                    or entry.tool == "subagent" or entry.exit_code != 0):
                continue
            if after is not None and (entry.ord is None or after.ord is None
                                      or entry.ord <= after.ord):
                continue
            if best is None or entry.ord > best.ord:
                best = entry
    return best


def done_claim(text: str) -> bool:
    """Whether `text` says something is finished or working. A word inside a code span or
    fence is quoted, not claimed; an English word right after `not ` / `n't ` is denied, and so is one with
    `not yet` / `henüz` / `daha` within the two words before it."""
    if not text:
        return False
    plain = _folded(_CODE.sub(" ", text))
    return any(not found.group("neg") and not _NOT_YET.search(plain[:found.start()])
               for found in _CLAIM.finditer(plain))


def _mvn_service(entry: executions.Event) -> str | None:
    """The service a wrapper `mvn` event ran for: the operand after the subcommand in its
    `ref` (`test svc-a`, `test ../x/svc-a/`), as a basename; None when it has none or the
    wrapper cut the ref (`_REF_CUT` characters), which leaves a meaningless prefix."""
    ref = entry.ref or ""
    words = ref.split()
    if len(ref) >= _REF_CUT or len(words) < 2:
        return None
    return Path(words[1].rstrip("/")).name


def _services(path: str | None) -> set[str] | None:
    """The service names a wrapper may record for the repo holding `path`: its checkout's
    name and, for a linked worktree, the main checkout's. None when no repo is found."""
    if not path or not Path(path).is_absolute():
        return None
    where = Path(path).parent
    try:
        top, common = subprocess.run(
            ["git", "-C", str(where), "rev-parse", "--show-toplevel", "--git-common-dir"],
            capture_output=True, text=True, check=True).stdout.splitlines()
    except (OSError, ValueError, subprocess.CalledProcessError):
        return None
    names = {Path(top).name}
    main = (where / common).resolve() if not Path(common).is_absolute() else Path(common)
    if main.name == ".git":
        names.add(main.parent.name)
    return names


def _repo_relative(target: str) -> str:
    path = Path(target)
    if not path.is_absolute():
        if ".." in path.parts or _UNSAFE_TARGET.search(target):
            return path.name
        return target
    try:
        top = subprocess.run(["git", "-C", str(path.parent), "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, check=True).stdout.strip()
        return path.resolve().relative_to(Path(top).resolve()).as_posix()
    except (OSError, ValueError, subprocess.CalledProcessError):
        return path.name


def _rule_action(body: str | None) -> tuple[str, str]:
    rule, _, rest = (body or "").partition(": ")
    return rule, rest.split(" ")[0]


def record_refusals(project_root: str | Path, refusals: list[Refusal], *, session: str | None,
                    action: str, target: str | None,
                    records: list[executions.Record] | None = None) -> None:
    """One `noted` event, tool `gate`, per (rule, action, target) per run; a repeat writes
    nothing. The body is the rule, the action and the decision ids; the target is
    repo-relative. `records`, when the caller already read them, hold the run; else
    the run alone is read from its ledger."""
    found = executions.current(project_root, session)
    if found is None or not refusals:
        return
    execution, ledger = found
    if records is None:
        records = executions.load_path(ledger, only=execution)
    relative = _repo_relative(target) if target else None
    seen = {_rule_action(e.body) for r in records if r.id == execution
            for e in r.events if e.kind == "noted" and e.tool == TOOL and e.ref == relative}
    for refusal in refusals:
        if (refusal.rule, action) in seen:
            continue
        seen.add((refusal.rule, action))
        body = f"{refusal.rule}: {action}" + (f" {' '.join(refusal.ids)}" if refusal.ids else "")
        executions.event(project_root, execution, ledger=ledger, kind="noted", tool=TOOL,
                         ref=relative, body=body, source="hook", session=session)
