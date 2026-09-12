import { useEffect, useState } from "react";
import { api } from "./api";
import type { Instance } from "./types";
import { InstanceList } from "./InstanceList";
import { InstanceGraph } from "./InstanceGraph";
import { WorkspaceOverview } from "./WorkspaceOverview";

type View = "instances" | "workspace";

export function App() {
  const [instances, setInstances] = useState<Instance[] | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [view, setView] = useState<View>("instances");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = () => {
    setError(null);
    api
      .listInstances()
      .then(setInstances)
      .catch((e) => setError(String(e)));
  };

  useEffect(refresh, []);

  const [newPath, setNewPath] = useState("");

  const reconcile = () => {
    setBusy(true);
    setError(null);
    api
      .reconcile()
      .then((r) => {
        refresh();
        const msg = `added ${r.added.length}, updated ${r.updated.length}, missing ${r.missing_marked}, unmanaged ${r.unmanaged_marked}`;
        setError(msg);
      })
      .catch((e) => setError(String(e)))
      .finally(() => setBusy(false));
  };

  const initAndScan = () => {
    if (!newPath) return;
    setBusy(true);
    setError(null);
    api
      .initScanProject(newPath)
      .then((r) => {
        refresh();
        const msg = `Initialized and scanned project. Added ${r.added.length} instances.`;
        setError(msg);
        setNewPath("");
      })
      .catch((e) => setError(String(e)))
      .finally(() => setBusy(false));
  };

  if (selected) {
    return (
      <div className="page">
        <button className="back" data-testid="back" onClick={() => setSelected(null)}>
          ← Back to {view === "workspace" ? "workspace" : "instances"}
        </button>
        {/* Keyed so switching projects remounts with a clean graph/community state. */}
        <InstanceGraph key={selected} id={selected} />
      </div>
    );
  }

  if (view === "workspace") {
    return (
      <div className="page">
        <header>
          <h1>Workspace overview</h1>
          <div className="actions">
            <button data-testid="nav-instances" onClick={() => setView("instances")}>
              Instances
            </button>
          </div>
        </header>
        <WorkspaceOverview
          instances={instances ?? []}
          onOpenInstance={(id) => setSelected(id)}
        />
      </div>
    );
  }

  return (
    <div className="page">
      <header>
        <h1>EOS UI</h1>
        <div className="actions">
          <input
            type="text"
            placeholder="/absolute/path/to/project"
            value={newPath}
            onChange={(e) => setNewPath(e.target.value)}
            disabled={busy}
            className="path-input"
          />
          <button onClick={initAndScan} disabled={busy || !newPath}>
            {busy ? "Scanning..." : "Add & Scan Project"}
          </button>
          <button onClick={reconcile} disabled={busy}>
            {busy ? "Reconciling…" : "Reconcile"}
          </button>
          <button onClick={refresh} disabled={busy}>Refresh</button>
          <button data-testid="nav-workspace" onClick={() => setView("workspace")}>
            Workspace overview
          </button>
        </div>
      </header>
      {error && <div className="notice">{error}</div>}
      {instances === null ? (
        <p>Loading…</p>
      ) : instances.length === 0 ? (
        <p>No instances registered. Add a scan-root above to initialize a project.</p>
      ) : (
        <InstanceList instances={instances} onSelect={(id) => setSelected(id)} />
      )}
    </div>
  );
}
