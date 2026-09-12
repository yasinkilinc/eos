export interface ScanRoot {
  id: number;
  path: string;
  added_at?: string | null;
  last_scan_at?: string | null;
}

export interface Instance {
  id: string;
  path: string;
  name?: string | null;
  engine_version: string;
  tech_stack: string[];
  status: string;
  created_at?: string | null;
  last_scanned_at?: string | null;
  last_updated_at?: string | null;
  metadata?: unknown;
}

export interface GraphNodeMetadata {
  exports?: string[];
  top_symbols?: string[];
  community?: number | string | null;
  community_name?: string | null;
  [key: string]: unknown;
}

export interface GraphNode {
  id: string;
  type: string;
  label: string;
  path?: string | null;
  language?: string | null;
  tags?: string[];
  metadata?: GraphNodeMetadata | null;
  doc?: string | null;
}

/** Shape of the Cytoscape node payload rendered by the inspector panel. */
export interface SelectedNodeData {
  id: string;
  label: string;
  type: string;
  language?: string | null;
  path?: string | null;
  metadata?: GraphNodeMetadata | null;
  doc?: string | null;
}

export interface GraphEdge {
  source: string;
  target: string;
  kind: string;
  weight?: number;
  metadata?: unknown;
}

export type GraphProvider = "eos" | "graphify";

export interface GraphifyArtifact {
  provider: "graphify";
  status: string;
  project_id?: string | null;
  project_key?: string | null;
  generated_at?: string | null;
  source_revision?: string | null;
  node_count: number;
  edge_count: number;
  community_count: number;
  has_html: boolean;
  has_report: boolean;
}

export interface GraphifySummary extends GraphifyArtifact {
  communities: Array<{ name: string; node_count: number }>;
}

export interface Graph {
  languages: string[];
  tech_stack: string[];
  entry_points: string[];
  nodes: GraphNode[];
  edges: GraphEdge[];
  provider?: GraphProvider;
  truncated?: boolean;
}

// --------------------------------------------------------------------------- //
// Workspace projections (/api/workspace/*)
// --------------------------------------------------------------------------- //

export interface WorkspaceProject {
  project_key: string;
  project_id?: string | null;
  project_path?: string | null;
  status: string;
  node_count: number;
  edge_count: number;
  community_count: number;
  generated_at?: string | null;
  source_revision?: string | null;
  graphify_version?: string | null;
  error?: string | null;
  has_graph: boolean;
  has_html: boolean;
  has_report: boolean;
  artifact_dir: string;
  artifact_bytes: number;
  is_parent: boolean;
}

export interface WorkspaceSummary {
  project_count: number;
  returned_count: number;
  ready_count: number;
  problem_count: number;
  total_nodes: number;
  total_edges: number;
  total_communities: number;
  total_bytes: number;
}

export interface WorkspaceOverviewResponse {
  projects: WorkspaceProject[];
  summary: WorkspaceSummary;
  truncated: boolean;
  include_parent: boolean;
}

export interface WorkspaceNode {
  project_key: string;
  project_id?: string | null;
  project_path?: string | null;
  status: string;
  node_count: number;
  edge_count: number;
  community_count: number;
  generated_at?: string | null;
  is_parent: boolean;
}

export interface WorkspaceEdgeExample {
  node_id: string;
  label: string;
  source_file?: string | null;
  matched: string;
  kind: string;
}

export interface WorkspaceEdge {
  source: string;
  target: string;
  weight: number;
  examples: WorkspaceEdgeExample[];
}

export interface WorkspaceSkipped {
  project_key: string;
  status: string;
  reason: string;
  detail?: string | null;
}

export interface WorkspaceRelations {
  nodes: WorkspaceNode[];
  edges: WorkspaceEdge[];
  edge_count: number;
  skipped: WorkspaceSkipped[];
  truncated: boolean;
  include_parent: boolean;
}

export interface WorkspacePath {
  source: string;
  target: string;
  found: boolean;
  path: string[];
  /** Same shape as a relation edge, one entry per consecutive pair in `path`. */
  hops: WorkspaceEdge[];
  reason?: string | null;
  truncated: boolean;
  include_parent: boolean;
}

export interface WorkspaceDependency {
  dependency: string;
  project_count: number;
  reference_count: number;
  projects: string[];
  examples: string[];
}

export interface WorkspaceDependencies {
  dependencies: WorkspaceDependency[];
  dependency_count: number;
  skipped: WorkspaceSkipped[];
  min_projects: number;
  truncated: boolean;
  include_parent: boolean;
}

// --------------------------------------------------------------------------- //
// Per-project community projection and the refresh queue
// --------------------------------------------------------------------------- //

export interface GraphifyCommunityMember {
  id: string;
  label: string;
  source_file?: string | null;
  degree: number;
}

export interface GraphifyCommunity {
  id: string;
  name: string;
  node_count: number;
  edge_count: number;
  members: GraphifyCommunityMember[];
}

export interface GraphifyCommunities {
  project_key: string;
  status: string;
  generated_at?: string | null;
  node_count: number;
  edge_count: number;
  community_count: number;
  communities: GraphifyCommunity[];
  truncated: boolean;
  include_parent: boolean;
}

/** States produced by ui/app/graphify_queue.py; "idle" is also the synthetic no-watcher entry. */
export type GraphifyQueueState = "idle" | "pending" | "running" | "ok" | "failed";

export interface GraphifyQueueEntry {
  project_path: string;
  project_key: string;
  state: GraphifyQueueState;
  attempts: number;
  last_error?: string | null;
  last_started_at?: string | null;
  last_finished_at?: string | null;
  queued_at?: string | null;
}

export interface GraphifyQueueStatus {
  enabled: boolean;
  entries: GraphifyQueueEntry[];
}
