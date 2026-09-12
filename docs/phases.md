# EOS Phases

| Phase | Scope | Status |
|-------|-------|--------|
| 0 | CLI-only core, Python + JS/TS support, brain + graph generation | ✅ Done |
| 1 | Multi-language plugins, incremental scan, manifest versioning, `runtime/` deploy on init | ✅ Done |
| 2 | eos-ui: SQLite, scan-root management, reconcile trigger, list view | 🚧 In progress |
| 3 | Graph visualization (Cytoscape.js) | ✅ Done (instance list + drill-down graph) |
| 4 | File watcher (watchdog), Tauri packaging, monorepo/nested support | 🚧 File watcher done; Tauri/nested planned |

## Phase 2 — eos-ui (current)

Goals:

- FastAPI server bound to `127.0.0.1` (replaces the stdlib `http.server` stub).
- Scan-root CRUD: add / list / remove roots.
- Reconcile trigger: walk roots, upsert instances by ID (ADR-003), mark missing.
- Instance listing + detail (read `data/brain/graph.json` for the drill-down view).
- Minimal list view (no graph yet — that is Phase 3).

Out of scope for Phase 2: Cytoscape graph, file watcher, desktop packaging,
nested/monorepo `.eos`.

## Phase 3 — Graph visualization (done)

- `ui/frontend/` — Vite + React + TypeScript + Cytoscape.js.
- Instance list (table) with status badges; click a row → drill-down.
- Component-level graph via Cytoscape `breadthfirst` layout, nodes colored by
  type (entry-point / service / component / util / config / test).
- Dev: `npm run dev` on :5173 proxies `/api` to the FastAPI server on :8000.
- Prod: `npm run build` emits to `ui/static/`; FastAPI serves it at `/static`
  and `/` so a single server hosts UI + API.

## Phase 4 — Planned

File watcher (incremental re-scan on change), Tauri desktop packaging,
nested/monorepo `.eos` support, additional language plugins.

## Phase 4 (in progress) — File watcher

- `ui/watcher.py` — ``WatcherService`` uses watchdog to observe scan-roots,
  debounces bursts (default 1.5s), maps each changed file to its owning
  project (the nearest ancestor with a ``.eos`` dir), runs an incremental
  ``eos scan`` on it, and reconciles SQLite metadata.
- Noise filter skips ``.git``, ``node_modules``, ``.venv``, ``.eos``, caches,
  ``.pyc``, ``.DS_Store`` so VCS/dependency churn does not trigger rescans.
- CLI: ``eos-ui watch [--delay N]`` runs the watcher as a foreground process.
- Optional dependency: ``watchdog`` (in requirements-ui.txt). The API server
  does not import the watcher, so it stays runnable without it.
- Remaining Phase 4: Tauri desktop packaging, nested/monorepo ``.eos``.
