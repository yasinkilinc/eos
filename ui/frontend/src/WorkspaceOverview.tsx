import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import type {
  Instance,
  WorkspaceDependencies,
  WorkspaceEdge,
  WorkspaceOverviewResponse,
  WorkspacePath,
  WorkspaceProject,
  WorkspaceRelations,
} from "./types";

const STATUS_CLASS: Record<string, string> = {
  ready: "ok",
  stale: "warn",
  failed: "err",
};

/** Human-readable reasons for the `skipped` entries the projections emit. */
const SKIP_REASON: Record<string, string> = {
  not_ready: "artifact not ready",
  unreadable: "artifact could not be read",
};

function formatBytes(bytes: number): string {
  if (bytes <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${unit === 0 ? Math.round(value) : value.toFixed(1)} ${units[unit]}`;
}

function formatTimestamp(value: string | null | undefined): string {
  if (!value) return "never";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString();
}

function trimTrailingSlash(path: string): string {
  return path.replace(/\/+$/, "");
}

/**
 * Resolve a workspace project row back to an EOS instance id. The manifest's
 * project_path is the reliable join key; project_id is only a fallback because
 * the Graphify coordinator may mint its own ids.
 */
function instanceIdFor(project: WorkspaceProject, instances: Instance[]): string | null {
  const projectPath = trimTrailingSlash(project.project_path ?? "");
  if (projectPath) {
    const byPath = instances.find((i) => trimTrailingSlash(i.path) === projectPath);
    if (byPath) return byPath.id;
  }
  if (project.project_id) {
    const byId = instances.find((i) => i.id === project.project_id);
    if (byId) return byId.id;
  }
  return null;
}

function HopEvidence({ hop }: { hop: WorkspaceEdge }) {
  return (
    <div className="hop">
      <div className="hop-head">
        <code>{hop.source}</code>
        <span className="arrow">→</span>
        <code>{hop.target}</code>
        <span className="weight">{hop.weight} references</span>
      </div>
      {hop.examples.length > 0 && (
        <ul className="evidence">
          {hop.examples.map((example) => (
            <li key={`${example.node_id}:${example.matched}`}>
              <strong>{example.label}</strong>
              <span className="muted-text"> matched {example.matched} by {example.kind}</span>
              {example.source_file && <span className="path-text"> — {example.source_file}</span>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function WorkspaceOverview({
  instances,
  onOpenInstance,
}: {
  instances: Instance[];
  onOpenInstance: (instanceId: string) => void;
}) {
  const [includeParent, setIncludeParent] = useState(false);
  const [overview, setOverview] = useState<WorkspaceOverviewResponse | null>(null);
  const [relations, setRelations] = useState<WorkspaceRelations | null>(null);
  const [dependencies, setDependencies] = useState<WorkspaceDependencies | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Same ticket guard as InstanceGraph: a response whose ticket has been
  // superseded (include_parent toggled, manual refresh) never reaches setState.
  const loadIdRef = useRef(0);

  const [pathSource, setPathSource] = useState("");
  const [pathTarget, setPathTarget] = useState("");
  const [pathResult, setPathResult] = useState<WorkspacePath | null>(null);
  const [pathError, setPathError] = useState<string | null>(null);
  const [pathBusy, setPathBusy] = useState(false);
  const pathIdRef = useRef(0);

  const load = () => {
    const requestId = ++loadIdRef.current;
    const isCurrent = () => loadIdRef.current === requestId;

    setError(null);
    setOverview(null);
    setRelations(null);
    setDependencies(null);

    api
      .workspaceOverview({ includeParent })
      .then((value) => {
        if (isCurrent()) setOverview(value);
      })
      .catch((e) => {
        if (isCurrent()) setError(String(e));
      });
    api
      .workspaceRelations({ includeParent })
      .then((value) => {
        if (isCurrent()) setRelations(value);
      })
      .catch((e) => {
        if (isCurrent()) setError(String(e));
      });
    api
      .workspaceDependencies({ includeParent })
      .then((value) => {
        if (isCurrent()) setDependencies(value);
      })
      .catch((e) => {
        if (isCurrent()) setError(String(e));
      });
  };

  useEffect(load, [includeParent]);

  // Keep the path selectors pointing at keys that still exist after a reload.
  useEffect(() => {
    if (!overview) return;
    const keys = overview.projects.map((project) => project.project_key);
    setPathSource((prev) => (keys.includes(prev) ? prev : keys[0] ?? ""));
    setPathTarget((prev) => (keys.includes(prev) ? prev : keys[1] ?? keys[0] ?? ""));
  }, [overview]);

  const findPath = () => {
    if (!pathSource || !pathTarget) return;
    const requestId = ++pathIdRef.current;
    const isCurrent = () => pathIdRef.current === requestId;

    setPathBusy(true);
    setPathError(null);
    setPathResult(null);
    api
      .workspacePath(pathSource, pathTarget, { includeParent })
      .then((value) => {
        if (isCurrent()) setPathResult(value);
      })
      .catch((e) => {
        if (isCurrent()) setPathError(String(e));
      })
      .finally(() => {
        if (isCurrent()) setPathBusy(false);
      });
  };

  const projectKeys = overview ? overview.projects.map((project) => project.project_key) : [];
  const summary = overview?.summary;

  return (
    <div className="workspace" data-testid="workspace">
      <div className="workspace-toolbar">
        <label className="toggle">
          <input
            type="checkbox"
            checked={includeParent}
            onChange={(event) => setIncludeParent(event.target.checked)}
          />
          Include parent aggregate
        </label>
        <button type="button" onClick={load}>
          Refresh
        </button>
      </div>

      {error && <div className="notice">{error}</div>}

      <section className="workspace-section">
        <h2>Aggregate</h2>
        {summary ? (
          <div className="summary-grid" data-testid="workspace-summary">
            <div className="stat">
              <span className="stat-value">{summary.project_count}</span>
              <span className="stat-label">projects</span>
            </div>
            <div className="stat">
              <span className="stat-value">{summary.ready_count}</span>
              <span className="stat-label">ready</span>
            </div>
            <div className="stat">
              <span className="stat-value">{summary.problem_count}</span>
              <span className="stat-label">problems</span>
            </div>
            <div className="stat">
              <span className="stat-value">{summary.total_nodes}</span>
              <span className="stat-label">nodes</span>
            </div>
            <div className="stat">
              <span className="stat-value">{summary.total_edges}</span>
              <span className="stat-label">edges</span>
            </div>
            <div className="stat">
              <span className="stat-value">{summary.total_communities}</span>
              <span className="stat-label">communities</span>
            </div>
            <div className="stat">
              <span className="stat-value">{formatBytes(summary.total_bytes)}</span>
              <span className="stat-label">artifacts</span>
            </div>
          </div>
        ) : (
          <p>Loading…</p>
        )}
      </section>

      <section className="workspace-section">
        <h2>Projects</h2>
        {overview === null ? (
          <p>Loading…</p>
        ) : overview.projects.length === 0 ? (
          <p>No Graphify artifacts discovered. Generate them with ensure-graphify-projects.sh.</p>
        ) : (
          <table className="instances workspace-table" data-testid="workspace-projects">
            <thead>
              <tr>
                <th>Project</th>
                <th>Status</th>
                <th>Nodes</th>
                <th>Edges</th>
                <th>Communities</th>
                <th>Size</th>
                <th>Generated</th>
              </tr>
            </thead>
            <tbody>
              {overview.projects.map((project) => {
                const instanceId = instanceIdFor(project, instances);
                return (
                  <tr
                    key={project.project_key}
                    className={instanceId ? "row" : ""}
                    data-testid="workspace-project-row"
                    title={instanceId ? "Open this project's graph" : "No registered EOS instance for this artifact"}
                    onClick={() => {
                      if (instanceId) onOpenInstance(instanceId);
                    }}
                  >
                    <td>
                      {project.project_key}
                      {project.is_parent && <span className="badge muted-badge">parent</span>}
                    </td>
                    <td>
                      <span className={`badge ${STATUS_CLASS[project.status] || ""}`}>
                        {project.status}
                      </span>
                      {project.error && <div className="muted-text">{project.error}</div>}
                    </td>
                    <td>{project.node_count}</td>
                    <td>{project.edge_count}</td>
                    <td>{project.community_count}</td>
                    <td>{formatBytes(project.artifact_bytes)}</td>
                    <td>{formatTimestamp(project.generated_at)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
        {overview?.truncated && (
          <p className="muted-text">Result truncated; raise the limit to see every project.</p>
        )}
      </section>

      <section className="workspace-section">
        <h2>Cross-project path</h2>
        <div className="path-finder">
          <select
            value={pathSource}
            onChange={(event) => setPathSource(event.target.value)}
            aria-label="Path source project"
            data-testid="path-source"
          >
            {projectKeys.map((key) => (
              <option key={key} value={key}>
                {key}
              </option>
            ))}
          </select>
          <span className="arrow">→</span>
          <select
            value={pathTarget}
            onChange={(event) => setPathTarget(event.target.value)}
            aria-label="Path target project"
            data-testid="path-target"
          >
            {projectKeys.map((key) => (
              <option key={key} value={key}>
                {key}
              </option>
            ))}
          </select>
          <button
            type="button"
            onClick={findPath}
            disabled={pathBusy || !pathSource || !pathTarget}
            data-testid="path-find"
          >
            {pathBusy ? "Finding…" : "Find path"}
          </button>
        </div>

        {pathError && <div className="notice">{pathError}</div>}
        {pathResult && (
          <div className="path-result" data-testid="path-result">
            {pathResult.found ? (
              <>
                <div className="path-chain">
                  {pathResult.path.map((key, index) => (
                    <span key={key}>
                      {index > 0 && <span className="arrow">→</span>}
                      <code>{key}</code>
                    </span>
                  ))}
                </div>
                {pathResult.hops.map((hop) => (
                  <HopEvidence key={`${hop.source}->${hop.target}`} hop={hop} />
                ))}
              </>
            ) : (
              <p className="muted-text">
                {pathResult.reason === "max_hops_exceeded"
                  ? `No path within the hop budget between ${pathResult.source} and ${pathResult.target}.`
                  : `No path found between ${pathResult.source} and ${pathResult.target}.`}
              </p>
            )}
          </div>
        )}
      </section>

      <section className="workspace-section">
        <h2>Shared dependencies</h2>
        {dependencies === null ? (
          <p>Loading…</p>
        ) : dependencies.dependencies.length === 0 ? (
          <p>
            No dependency is shared by at least {dependencies.min_projects} projects.
          </p>
        ) : (
          <table className="instances workspace-table" data-testid="workspace-dependencies">
            <thead>
              <tr>
                <th>Dependency</th>
                <th>Projects</th>
                <th>References</th>
                <th>Used by</th>
                <th>Examples</th>
              </tr>
            </thead>
            <tbody>
              {dependencies.dependencies.map((dependency) => (
                <tr key={dependency.dependency}>
                  <td>
                    <code>{dependency.dependency}</code>
                  </td>
                  <td>{dependency.project_count}</td>
                  <td>{dependency.reference_count}</td>
                  <td className="path">{dependency.projects.join(", ")}</td>
                  <td className="path">{dependency.examples.join(", ") || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {dependencies && dependencies.skipped.length > 0 && (
          <p className="muted-text">
            Skipped:{" "}
            {dependencies.skipped
              .map((s) => `${s.project_key} (${SKIP_REASON[s.reason] || s.reason})`)
              .join(", ")}
          </p>
        )}
      </section>

      <section className="workspace-section">
        <h2>Service relations</h2>
        {relations === null ? (
          <p>Loading…</p>
        ) : relations.edges.length === 0 ? (
          <p>No service-to-service references detected across the discovered artifacts.</p>
        ) : (
          <div className="relation-list" data-testid="workspace-relations">
            {relations.edges.map((edge) => (
              <HopEvidence key={`${edge.source}->${edge.target}`} hop={edge} />
            ))}
          </div>
        )}
        {relations && relations.skipped.length > 0 && (
          <p className="muted-text">
            Skipped:{" "}
            {relations.skipped
              .map((s) => `${s.project_key} (${SKIP_REASON[s.reason] || s.reason})`)
              .join(", ")}
          </p>
        )}
        {relations?.truncated && (
          <p className="muted-text">Relation list truncated at the server limit.</p>
        )}
      </section>
    </div>
  );
}
