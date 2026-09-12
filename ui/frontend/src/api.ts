import type {
  Graph,
  GraphifyArtifact,
  GraphifyCommunities,
  GraphifyQueueEntry,
  GraphifyQueueStatus,
  GraphifySummary,
  Instance,
  ScanRoot,
  WorkspaceDependencies,
  WorkspaceOverviewResponse,
  WorkspacePath,
  WorkspaceRelations,
} from "./types";

/** HTTP error carrying FastAPI's `detail` so callers can show the real reason. */
export class ApiError extends Error {
  readonly status: number;
  readonly detail: string;

  constructor(status: number, detail: string) {
    super(detail);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

/** Pull `detail` out of an error body without trusting its shape. */
function errorDetail(body: unknown): string | null {
  if (typeof body !== "object" || body === null || !("detail" in body)) return null;
  const detail = (body as { detail: unknown }).detail;
  return typeof detail === "string" ? detail : null;
}

type QueryValue = string | number | boolean | undefined;

function queryString(params: Record<string, QueryValue>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined) search.set(key, String(value));
  }
  const encoded = search.toString();
  return encoded ? `?${encoded}` : "";
}

async function getJSON<T>(url: string): Promise<T> {
  const r = await fetch(url);
  if (!r.ok) {
    let detail: string | null = null;
    try {
      detail = errorDetail(await r.json());
    } catch {
      // Non-JSON error body; fall back to the status line below.
    }
    throw new ApiError(r.status, detail ?? `${r.status} ${url}`);
  }
  return (await r.json()) as T;
}

/** Bounds mirror the FastAPI Query() limits; out-of-range values are rejected with 422. */
export interface WorkspaceQuery {
  includeParent?: boolean;
  limit?: number;
}

export const api = {
  health: () => getJSON<{ status: string }>("/api/health"),
  listInstances: () => getJSON<Instance[]>("/api/instances"),
  getInstance: (id: string) => getJSON<Instance>(`/api/instances/${id}`),
  getGraph: (id: string) => getJSON<Graph>(`/api/instances/${id}/graph?t=${Date.now()}`),
  getGraphifyArtifact: (id: string) => getJSON<GraphifyArtifact>(`/api/instances/${id}/graphify`),
  getGraphifySummary: (id: string) => getJSON<GraphifySummary>(`/api/instances/${id}/graphify/summary`),
  getGraphifyGraph: (id: string, node?: string, opts: { depth?: number; limit?: number } = {}) => {
    const query = queryString({
      node,
      depth: opts.depth ?? 2,
      limit: opts.limit ?? 250,
    });
    return getJSON<Graph>(`/api/instances/${id}/graphify/graph${query}`);
  },
  /** Community summary nodes; the bounded alternative to pulling a large raw graph. */
  getGraphifyCommunities: (
    id: string,
    opts: WorkspaceQuery & { members?: number } = {}
  ) =>
    getJSON<GraphifyCommunities>(
      `/api/instances/${id}/graphify/communities${queryString({
        include_parent: opts.includeParent,
        limit: opts.limit,
        members: opts.members,
      })}`
    ),
  getGraphifyQueue: () => getJSON<GraphifyQueueStatus>("/api/graphify/queue"),
  getGraphifyQueueEntry: (id: string) =>
    getJSON<GraphifyQueueEntry>(`/api/graphify/queue/${id}`),
  graphifyHtmlUrl: (id: string) => `/api/instances/${id}/graphify/html`,
  graphifyReportUrl: (id: string) => `/api/instances/${id}/graphify/report`,
  workspaceOverview: (opts: WorkspaceQuery = {}) =>
    getJSON<WorkspaceOverviewResponse>(
      `/api/workspace/overview${queryString({
        include_parent: opts.includeParent,
        limit: opts.limit,
      })}`
    ),
  workspaceRelations: (opts: WorkspaceQuery = {}) =>
    getJSON<WorkspaceRelations>(
      `/api/workspace/relations${queryString({
        include_parent: opts.includeParent,
        limit: opts.limit,
      })}`
    ),
  /** 404 with detail "unknown_source"/"unknown_target"; unreachable is a 200 with found=false. */
  workspacePath: (
    from: string,
    to: string,
    opts: { includeParent?: boolean; maxHops?: number } = {}
  ) =>
    getJSON<WorkspacePath>(
      `/api/workspace/path${queryString({
        from,
        to,
        include_parent: opts.includeParent,
        max_hops: opts.maxHops,
      })}`
    ),
  workspaceDependencies: (opts: WorkspaceQuery & { minProjects?: number } = {}) =>
    getJSON<WorkspaceDependencies>(
      `/api/workspace/dependencies${queryString({
        include_parent: opts.includeParent,
        limit: opts.limit,
        min_projects: opts.minProjects,
      })}`
    ),
  listRoots: () => getJSON<ScanRoot[]>("/api/roots"),
  addRoots: async (paths: string[]) => {
    const r = await fetch("/api/roots", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ paths }),
    });
    if (!r.ok) throw new Error(`${r.status}`);
    return (await r.json()) as ScanRoot[];
  },
  removeRoot: async (id: number) => {
    const r = await fetch(`/api/roots/${id}`, { method: "DELETE" });
    if (!r.ok) throw new Error(`${r.status}`);
    return (await r.json()) as ScanRoot[];
  },
  reconcile: () =>
    getJSON<{
      added: string[];
      updated: string[];
      missing_marked: number;
      unmanaged_marked: number;
      roots_scanned: string[];
    }>("/api/reconcile"),
  initScanProject: async (path: string) => {
    const r = await fetch("/api/instances/init_scan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ paths: [path] }),
    });
    if (!r.ok) throw new Error(`${r.status}`);
    return (await r.json()) as {
      added: string[];
      updated: string[];
      missing_marked: number;
      unmanaged_marked: number;
      roots_scanned: string[];
    };
  },
};
