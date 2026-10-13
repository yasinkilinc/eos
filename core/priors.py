"""Priors from outcomes (2.x roadmap M6, ADR-036).

A note that verified runs used is, a little, more likely to answer the next
question than one no run ever used. That is the whole claim, and everything
here exists to keep it small:

- **Derived, never stored.** A prior is a fold of the execution ledger
  (ADR-020/022), recomputed on every read. There is no counter to drift, lose
  a write or be hand-edited; `eos consolidate` shows what the fold says.
- **Only a verified outcome moves it.** `ok` with `outcome_source = verified`
  (ADR-028: the checks the run's changes needed passed after its last change).
  A `claimed` ok is the session's word, and proxy evidence never promotes; a
  failed run is not evidence against what it read -- reading notes is what a
  session in trouble does.
- **An integer budget of one per run**, split across the notes the run
  referenced: the procedure it followed (`run start --procedure`) and the note
  files it read (`read` events, ref `note:<file name>`). The shares are summed
  over all runs and the total -- the number of runs that contributed -- is
  handed out in integers by largest remainder, so the points add up to the
  runs exactly and a tie in one run is not settled the same way in every run.
  A note written in the run's own session earns nothing from it: reading back
  what you just wrote is not using it. A prior stops at `CAP`.
- **Applied scaled, after the threshold.** The scorer adds
  `alpha * prior / 100` to a note that already matched; it reorders matches and
  never makes one. `alpha` is `[notes] prior_alpha`, clamped to `ALPHA_MAX`;
  0 turns priors off and skips the ledger read.
"""
from __future__ import annotations

import dataclasses
import json
from fractions import Fraction
from pathlib import Path

CAP = 100
# The report's bound (§17 risks: "integer budget and alpha <= 0.2"). A full
# prior is then worth at most a fifth of the 0-1 relevance scale.
ALPHA_MAX = 0.2
# Off unless a project turns it on: measured on the host's golden sets it moved
# nothing (ADR-036), so there is no evidence yet that it earns a place.
DEFAULT_ALPHA = 0.0
NOTE_REF = "note:"


def note_ref(filename: str) -> str:
    """The `ref` a `read` event carries for a note file."""
    return f"{NOTE_REF}{filename}"


@dataclasses.dataclass(frozen=True)
class Run:
    """What a prior needs of one execution."""
    session: str | None
    procedure: str | None
    outcome: str | None
    outcome_source: str | None
    reads: tuple[str, ...]


@dataclasses.dataclass
class Fold:
    runs: int                       # verified runs that referenced at least one note
    credit: dict[str, Fraction]     # each note's exact share, summed over those runs
    values: dict[str, int]          # the integer priors, 1..CAP; a note at 0 is absent


def moves(run: Run) -> bool:
    return run.outcome == "ok" and run.outcome_source == "verified"


def referenced(run: Run, procedure_files: dict[str, str], written_by: dict[str, str]) -> list[str]:
    """The note files `run` referenced, each once, minus those its own session wrote."""
    names = []
    if run.procedure and run.procedure in procedure_files:
        names.append(procedure_files[run.procedure])
    names += list(run.reads)
    return [name for name in dict.fromkeys(names)
            if not (run.session and written_by.get(name) == run.session)]


def largest_remainder(shares: dict[str, Fraction], total: int) -> dict[str, int]:
    """`total` integer points across `shares` (which sum to it): each its floor,
    then one more to the largest remainders, an equal remainder by name."""
    points = {name: share.numerator // share.denominator for name, share in shares.items()}
    left = total - sum(points.values())
    for name in sorted(shares, key=lambda name: (-(shares[name] - points[name]), name))[:max(0, left)]:
        points[name] += 1
    return points


def fold(runs, corpus) -> Fold:
    procedure_files = {note.procedure: note.path.name for note in corpus
                       if note.kind == "procedure" and note.procedure}
    written_by = {note.path.name: note.session for note in corpus if note.session}
    credit: dict[str, Fraction] = {}
    count = 0
    for run in runs:
        if not moves(run):
            continue
        names = referenced(run, procedure_files, written_by)
        if not names:
            continue
        count += 1
        share = Fraction(1, len(names))
        for name in names:
            credit[name] = credit.get(name, Fraction(0)) + share
    values = {name: min(CAP, points) for name, points in largest_remainder(credit, count).items() if points}
    return Fold(runs=count, credit=credit, values=values)


def _reads(events) -> tuple[str, ...]:
    return tuple(e.ref[len(NOTE_REF):] for e in events
                 if e.kind == "read" and isinstance(e.ref, str) and e.ref.startswith(NOTE_REF))


def fold_records(records, corpus) -> Fold:
    """The fold over `executions.Record`s a caller already loaded (consolidate)."""
    return fold((Run(r.session, r.procedure, r.outcome, r.outcome_source, _reads(r.events))
                 for r in records), corpus)


def _runs_in(ledger: Path) -> list[Run]:
    """The ledger's runs, reading only the lines a prior needs: starts, finishes
    and note reads. The full fold decodes every event (0.26 s on a 10 MB host
    ledger); this skips them unread (0.02 s), which matters on every search."""
    from core import executions

    parts = [executions.rotated(ledger, index) for index in range(executions.KEEP_ROTATED, 0, -1)] + [ledger]
    starts: dict[str, dict] = {}
    finishes: dict[str, dict] = {}
    reads: dict[str, list[str]] = {}
    for text in executions._read_consistently(parts):
        for line in text.splitlines():
            if '"start"' not in line and '"finish"' not in line and NOTE_REF not in line:
                continue
            try:
                data = json.loads(line)
            except ValueError:
                continue
            if not isinstance(data, dict):
                continue
            kind = data.get("type")
            if kind == executions.LINE_START and data.get("id"):
                starts.setdefault(data["id"], {}).update({k: v for k, v in data.items() if v is not None})
            elif kind == executions.LINE_FINISH and data.get("id"):
                finishes.setdefault(data["id"], data)  # the first finish holds, as in the full fold
            elif (kind == executions.LINE_EVENT and data.get("kind") == "read" and data.get("execution")
                  and isinstance(data.get("ref"), str) and data["ref"].startswith(NOTE_REF)):
                reads.setdefault(data["execution"], []).append(data["ref"][len(NOTE_REF):])
    runs = []
    for rid in dict.fromkeys([*starts, *finishes, *reads]):
        start, finish = starts.get(rid, {}), finishes.get(rid, {})
        runs.append(Run(start.get("session"), start.get("procedure"), finish.get("outcome"),
                        finish.get("outcome_source"), tuple(reads.get(rid, ()))))
    return runs


def fold_project(project_root: str | Path, corpus) -> Fold:
    """The fold of one project's ledger, procedures resolved against `corpus`."""
    from core import executions

    return fold(_runs_in(executions.path_for(project_root)), corpus)


def for_corpus(project_root: str | Path, corpus) -> dict[str, int]:
    """Note file name -> prior, from `project_root`'s ledger."""
    return fold_project(project_root, corpus).values


def for_search(project_root: str | Path, corpus) -> tuple[dict[str, int] | None, float]:
    """`(priors, alpha)` for `notes.rank` -- the ledger is read only when the
    project's weight is above 0. A host ranking several stores passes the
    project whose ledger and config speak for them (ADR-036)."""
    weight = alpha(project_root)
    return (for_corpus(project_root, corpus) if weight else None), weight


def note_files(project_root: str | Path, paths, cwd: str | Path | None = None) -> list[str]:
    """The note file names among `paths`: an existing `.md` file directly in the
    notes directory of `project_root` or of a workspace project it lists."""
    from core import notes, workspace

    candidates = [str(path) for path in paths if str(path).endswith(".md")]
    if not candidates:
        return []
    root = Path(project_root).expanduser().resolve()
    stores = {notes.notes_dir(root)}
    for project in workspace.load_quiet(root):
        try:
            stores.add(notes.notes_dir(project.root))
        except Exception:  # noqa: BLE001 - one project's broken config costs its notes, not the call
            continue
    found = []
    for raw in candidates:
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = Path(cwd or root) / path
        try:
            path = path.resolve()
        except (OSError, RuntimeError):
            continue
        if path.parent in stores and path.is_file():
            found.append(path.name)
    return list(dict.fromkeys(found))


def paths_in_command(command: str) -> list[str]:
    """The words of a shell command that name a Markdown file (`cat <note>`,
    `sed -n 1,40p <note>`): what the hook checks against the notes directories."""
    import shlex

    try:
        words = shlex.split(command or "")
    except ValueError:
        words = (command or "").split()
    return [word.rstrip(";|&)") for word in words if word.rstrip(";|&)").endswith(".md")]


def alpha(project_root: str | Path) -> float:
    """`[notes] prior_alpha`, clamped to [0, ALPHA_MAX]; anything not a number is the default."""
    from core.lib.config_io import ConfigIO

    config_path = Path(project_root).expanduser().resolve() / ".eos" / "config.toml"
    value = None
    if config_path.is_file():
        section = ConfigIO.read_toml(config_path).get("notes")
        value = section.get("prior_alpha") if isinstance(section, dict) else None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return DEFAULT_ALPHA
    return min(ALPHA_MAX, max(0.0, float(value)))
