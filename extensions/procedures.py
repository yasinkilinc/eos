"""Index a host's own step catalogue: procedures it already keeps, and how they last went.

An index extension (see core/extensions.py; ADR-013, ADR-023). Enable it per
project with:

    [index]
    extensions = ["<path to>/extensions/procedures.py"]

    [procedures]
    catalogue = "relative/or/absolute/path/to/catalogue.json"

Some hosts already keep procedures in a machine-readable form with recorded
state -- a scenario engine whose steps are code and whose last run is a state
file, a runbook directory. Rewriting those as notes would fork them; this
indexes them where they are, through one normalised file the host exports.
The engine never learns the host's format: the host's exporter is the adapter.

The catalogue is a JSON list, one object per procedure:

    {
      "name": "topup",                       # required, unique
      "summary": "Top up a prepaid line",    # optional
      "source": "catalog/topup.js",          # optional: where it is defined
      "steps": [{"name": "line", "tool": "api"}, {"name": "submit"}],
      "state": [                             # optional: last recorded run(s)
        {"env": "staging", "status": "COMPLETED", "updated_at": "2026-09-21T11:11:34Z",
         "failed_step": null}
      ]
    }

Counts are deliberately absent. The engine counts only executions it saw
finish (ADR-023); a host's own last state is shown as the host recorded it,
and never merged into a procedure note's counters.
"""
from __future__ import annotations

import json
from pathlib import Path

from core import index
from core.lib.config_io import ConfigIO

SCHEMA = """
CREATE TABLE catalogue_procedure (
    name TEXT PRIMARY KEY,
    summary TEXT,
    source TEXT,
    steps INTEGER NOT NULL
);
CREATE TABLE procedure_step (
    procedure TEXT NOT NULL REFERENCES catalogue_procedure(name),
    ord INTEGER NOT NULL,
    name TEXT NOT NULL,
    tool TEXT,
    PRIMARY KEY (procedure, ord)
) WITHOUT ROWID;
CREATE TABLE procedure_state (
    procedure TEXT NOT NULL REFERENCES catalogue_procedure(name),
    env TEXT NOT NULL,
    status TEXT,
    updated_at TEXT,
    failed_step TEXT,
    PRIMARY KEY (procedure, env)
) WITHOUT ROWID;
"""

COUNTS = {
    "catalogue procedures": "SELECT COUNT(*) FROM catalogue_procedure",
}

QUESTIONS = {
    "procedures": {
        "help": "Every catalogued procedure, its step count and its last recorded state per environment.",
        "sql": """
            SELECT p.name, p.steps, COALESCE(s.env, '-') AS env,
                   COALESCE(s.status, '(no state recorded)') AS status,
                   s.updated_at, s.failed_step, p.summary
              FROM catalogue_procedure p
              LEFT JOIN procedure_state s ON s.procedure = p.name
             ORDER BY p.name, s.env
        """,
    },
    "procedure-steps": {
        "help": "The steps of one catalogued procedure, in order. Takes a procedure name.",
        "sql": """
            SELECT ord + 1 AS step, name, COALESCE(tool, '-') AS tool
              FROM procedure_step
             WHERE procedure = ?
             ORDER BY ord
        """,
    },
    "procedure-failing": {
        "help": "Catalogued procedures whose last recorded state is not a success, and the step it stopped at.",
        "sql": """
            SELECT p.name, s.env, s.status, s.failed_step, s.updated_at
              FROM catalogue_procedure p
              JOIN procedure_state s ON s.procedure = p.name
             WHERE UPPER(COALESCE(s.status, '')) NOT IN ('COMPLETED', 'OK', 'PASSED', 'SUCCESS', 'DONE')
             ORDER BY s.updated_at DESC
        """,
    },
}

_KEYS = frozenset({"catalogue"})


def _catalogue(root: Path) -> Path | None:
    config = root / ".eos" / "config.toml"
    section = ConfigIO.read_toml(config).get("procedures", {}) if config.is_file() else {}
    unknown = sorted(set(section) - _KEYS)
    if unknown:
        raise ValueError(f"[procedures] in {config}: unknown key(s) {', '.join(unknown)}")
    value = section.get("catalogue")
    if not value:
        return None
    if not isinstance(value, str):
        raise ValueError(f"[procedures] catalogue in {config} must be a string")
    path = Path(value).expanduser()
    return path if path.is_absolute() else (root / path).resolve()


def sources(root: Path, notes_dir: Path):
    path = _catalogue(root)
    if path is None:
        return
    yield ("catalogue", str(path), index.file_digest(path) if path.is_file() else "absent")


def load(build) -> None:
    path = _catalogue(build.root)
    if path is None:
        return
    build.meta["procedure_catalogue"] = str(path)
    if not path.is_file():
        build.issue("procedures", str(path), "configured catalogue does not exist")
        return
    try:
        entries = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        build.issue("procedures", str(path), f"catalogue unreadable ({exc})")
        return
    if not isinstance(entries, list):
        build.issue("procedures", str(path), "catalogue must be a JSON list")
        return
    for position, entry in enumerate(entries):
        name = entry.get("name") if isinstance(entry, dict) else None
        if not isinstance(name, str) or not name.strip():
            build.issue("procedures", f"{path}#{position}", "entry has no name; skipped")
            continue
        steps = [step for step in entry.get("steps") or [] if isinstance(step, dict) and step.get("name")]
        build.conn.execute(
            "INSERT OR IGNORE INTO catalogue_procedure(name, summary, source, steps) VALUES (?, ?, ?, ?)",
            (name, entry.get("summary"), entry.get("source"), len(steps)))
        build.conn.executemany(
            "INSERT OR IGNORE INTO procedure_step(procedure, ord, name, tool) VALUES (?, ?, ?, ?)",
            [(name, ord_, str(step["name"]), step.get("tool")) for ord_, step in enumerate(steps)])
        states = [s for s in entry.get("state") or [] if isinstance(s, dict) and s.get("env")]
        build.conn.executemany(
            "INSERT OR IGNORE INTO procedure_state(procedure, env, status, updated_at, failed_step) "
            "VALUES (?, ?, ?, ?, ?)",
            [(name, str(s["env"]), s.get("status"), s.get("updated_at"), s.get("failed_step"))
             for s in states])
        # Findable beside notes and executions: a session searching "top up"
        # should meet the catalogued procedure next to the note about it.
        body = " ".join([entry.get("summary") or ""] + [str(s["name"]) for s in steps]
                        + [s.get("tool") or "" for s in steps])
        build.search("procedure", name, entry.get("summary") or name, body)
