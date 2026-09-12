# ADR-010: Three Dependency Tiers, Not One Requirements File

**Status:** Accepted
**Date:** 2026-09-12
**Deciders:** yasinkilinc

## Context

EOS is dropped into arbitrary project folders, some of which have no Python
package manager set up and no appetite for one. A single `pip install -r
requirements.txt` gate would either force every user to accept third-party
dependencies just to run `eos scan`, or force the project to bundle those
dependencies into `core/` and lose the "pure stdlib runtime" property that
ADR-001 relies on. At the same time, the optional UI genuinely needs FastAPI,
uvicorn, pydantic and watchdog, and pretending otherwise would just produce a
worse error message at import time instead of a good one before it.

## Decision

Treat dependencies as three tiers, each with a different failure mode,
implemented in `core/preflight.py`:

- **T1 — cannot be installed by EOS.** Python itself. EOS cannot bootstrap
  its own interpreter, so `eos` prints the exact platform-specific command
  to run (`brew install python@3.12`, `apt install python3.12`, `pyenv
  install`) instead of failing with a traceback.
- **T2 — installed by EOS, after asking, into a venv EOS owns.** The UI's
  FastAPI/uvicorn/pydantic/watchdog stack. `eos ui` never touches the system
  interpreter or the project's own virtual environment; it manages its own at
  `~/.local/share/eos/venv` (or equivalent) and asks before the first
  install unless `--yes` is given.
- **T3 — optional.** Absent means one feature is unavailable, not an error.
  Graphify integration (`EOS_GRAPHIFY_ROOT`) is the current example: unset,
  the feature is off; set, it works; there is no broken middle state.

## Consequences

- `core/` under CLI use stays pure stdlib for every command except `ui`,
  matching ADR-001.
- A missing T1 dependency produces an actionable message, not a stack trace.
- A missing T2 dependency is fixed by consent (`eos ui --yes` or an
  interactive prompt), never by a silent background install.
- Adding a new optional integration means adding a T3 check, not renegotiating
  the whole dependency story.
