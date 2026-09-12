import { useEffect, useRef, useState } from "react";
import cytoscape from "cytoscape";
import { api } from "./api";
import type {
  Graph,
  GraphProvider,
  GraphifyArtifact,
  GraphifyCommunities,
  GraphifyCommunity,
  GraphifyQueueEntry,
  GraphifySummary,
  SelectedNodeData,
} from "./types";

const TYPE_COLOR: Record<string, string> = {
  "entry-point": "#22c55e",
  service: "#3b82f6",
  component: "#64748b",
  util: "#06b6d4",
  config: "#f59e0b",
  test: "#a855f7",
  "external-dep": "#ef4444",
  folder: "#475569",
  code: "#38bdf8",
  community: "#f97316",
};

/**
 * Above this node count the raw graph is never fetched: the subgraph endpoint
 * caps out at 250 nodes, so anything larger would render an arbitrary fragment.
 * Community summary nodes are loaded instead and the user drills into one.
 */
const LARGE_GRAPH_NODES = 400;

/** Nodes pulled per community drill-down; well inside the endpoint's limit. */
const COMMUNITY_DRILL_LIMIT = 250;

const QUEUE_POLL_MS = 5000;

const QUEUE_CLASS: Record<string, string> = {
  ok: "ok",
  running: "warn",
  pending: "warn",
  failed: "err",
};

/** Cytoscape node id for a community summary node. */
function communityNodeId(community: GraphifyCommunity): string {
  return `community:${community.id}`;
}

/** Project the community summary into the Graph shape the canvas already renders. */
function communityGraph(data: GraphifyCommunities): Graph {
  return {
    languages: [],
    tech_stack: [],
    entry_points: [],
    nodes: data.communities.map((community) => ({
      id: communityNodeId(community),
      type: "community",
      label: `${community.name} (${community.node_count})`,
      path: null,
      language: null,
      tags: [],
      metadata: {
        community: community.id,
        community_name: community.name,
        top_symbols: community.members.map((member) => member.label),
      },
      doc: `${community.node_count} nodes and ${community.edge_count} internal edges. Drill in to load this community's bounded subgraph.`,
    })),
    // The communities endpoint reports intra-community edges only, so there is
    // no honest inter-community edge to draw here.
    edges: [],
    provider: "graphify",
    truncated: data.truncated,
  };
}

/** Narrow an untrusted metadata field down to the string[] the panel renders. */
function stringList(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

export function InstanceGraph({ id }: { id: string }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const cyRef = useRef<cytoscape.Core | null>(null);
  const layoutRef = useRef<cytoscape.Layouts | null>(null);
  const [graph, setGraph] = useState<Graph | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selectedNode, setSelectedNode] = useState<SelectedNodeData | null>(null);
  const [isRescanning, setIsRescanning] = useState(false);
  const [provider, setProvider] = useState<GraphProvider>("eos");
  const [graphifyArtifact, setGraphifyArtifact] = useState<GraphifyArtifact | null>(null);
  const [graphifySummary, setGraphifySummary] = useState<GraphifySummary | null>(null);
  const [communities, setCommunities] = useState<GraphifyCommunities | null>(null);
  const [activeCommunity, setActiveCommunity] = useState<GraphifyCommunity | null>(null);
  const [queueEntry, setQueueEntry] = useState<GraphifyQueueEntry | null>(null);
  const requestIdRef = useRef(0);
  const queueRequestIdRef = useRef(0);
  const [graphQuery, setGraphQuery] = useState("");
  const [submittedGraphQuery, setSubmittedGraphQuery] = useState("");

  /**
   * Tear the canvas down.
   *
   * The cose layout drives itself with requestAnimationFrame. Stopping it only
   * sets a flag: any frame already queued still runs, and its final pass writes
   * node positions through the core. Destroying the core synchronously
   * therefore leaves that frame dereferencing a null core ("Cannot read
   * properties of null (reading 'notify')"). Releasing the refs immediately but
   * deferring the actual destroy by one frame lets the queued layout frame
   * finish against a live core first; rAF callbacks run in registration order,
   * so the layout's frame is always ahead of this one.
   */
  const destroyCanvas = () => {
    const cy = cyRef.current;
    const layout = layoutRef.current;
    cyRef.current = null;
    layoutRef.current = null;
    if (!cy) return;
    layout?.stop();
    requestAnimationFrame(() => cy.destroy());
  };

  const loadGraph = () => {
    // Every load claims a ticket; a response whose ticket is no longer the
    // current one belongs to a superseded provider/query and is dropped.
    const requestId = ++requestIdRef.current;
    const isCurrent = () => requestIdRef.current === requestId;

    destroyCanvas();
    setError(null);
    setGraph(null);
    setSelectedNode(null);
    setGraphifySummary(null);

    if (provider === "eos") {
      setGraphifyArtifact(null);
      setCommunities(null);
      api.getGraph(id)
        .then((value) => {
          if (isCurrent()) setGraph(value);
        })
        .catch((e) => {
          if (isCurrent()) setError(String(e));
        });
      return;
    }

    api.getGraphifySummary(id)
      .then((value) => {
        if (isCurrent()) setGraphifySummary(value);
      })
      .catch(() => {
        // Summary is supplementary; the graph view stays usable without it.
      });

    // The artifact tells us how big the project is, so it decides whether a raw
    // graph may be requested at all.
    api.getGraphifyArtifact(id)
      .then((artifact) => {
        if (!isCurrent()) return;
        setGraphifyArtifact(artifact);
        if (artifact.status === "missing") {
          setCommunities(null);
          return;
        }

        const seed = submittedGraphQuery || activeCommunity?.members[0]?.id || "";
        if (artifact.node_count > LARGE_GRAPH_NODES && !seed) {
          api.getGraphifyCommunities(id)
            .then((value) => {
              if (!isCurrent()) return;
              setCommunities(value);
              setGraph(communityGraph(value));
            })
            .catch((e) => {
              if (isCurrent()) setError(String(e));
            });
          return;
        }

        api.getGraphifyGraph(id, seed || undefined, { limit: COMMUNITY_DRILL_LIMIT })
          .then((value) => {
            if (isCurrent()) setGraph(value);
          })
          .catch((e) => {
            if (isCurrent()) setError(String(e));
          });
      })
      .catch((e) => {
        if (isCurrent()) setError(String(e));
      });
  };

  useEffect(() => {
    loadGraph();
    return destroyCanvas;
  }, [id, provider, submittedGraphQuery, activeCommunity]);

  // Refresh-queue state for this instance. Advisory, so failures stay silent,
  // but it uses its own ticket so a late response never clobbers a newer one.
  useEffect(() => {
    const pollQueue = () => {
      const requestId = ++queueRequestIdRef.current;
      api.getGraphifyQueueEntry(id)
        .then((entry) => {
          if (queueRequestIdRef.current === requestId) setQueueEntry(entry);
        })
        .catch(() => {
          // Queue status is advisory; the graph view works without it.
        });
    };
    pollQueue();
    const timer = window.setInterval(pollQueue, QUEUE_POLL_MS);
    return () => {
      queueRequestIdRef.current += 1;
      window.clearInterval(timer);
    };
  }, [id]);

  const handleRescan = async () => {
    if (provider === "graphify") {
      loadGraph();
      return;
    }
    setIsRescanning(true);
    try {
      const res = await fetch(`/api/instances/${id}/rescan`, { method: "POST" });
      if (!res.ok) throw new Error("Rescan failed");
      loadGraph();
    } catch (e) {
      setError(String(e));
    } finally {
      setIsRescanning(false);
    }
  };

  // Build the Cytoscape graph once graph data is available.
  useEffect(() => {
    if (!graph || !containerRef.current || cyRef.current) return;

    const cy = cytoscape({
      container: containerRef.current,
      elements: [
        ...graph.nodes
          .filter((n) => n.type !== "folder")
          .map((n) => ({
            data: {
              id: n.id,
              label: n.label,
              type: n.type,
              language: n.language,
              path: n.path,
              metadata: n.metadata,
              doc: n.doc,
            },
          })),
        ...graph.edges
          .filter((e) => !e.source.startsWith("folder:") && !e.target.startsWith("folder:"))
          .map((e, i) => ({
            data: { id: `e${i}`, source: e.source, target: e.target, kind: e.kind },
          })),
      ],
      // Placeholder only; the real layout is run below so its handle can be
      // captured and stopped before the core is destroyed.
      layout: { name: "preset" },
      style: [
        {
          selector: "node",
          style: {
            label: "data(label)",
            "text-valign": "bottom",
            "text-halign": "center",
            "background-color": "#64748b",
            width: 26,
            height: 26,
            "font-size": 10,
            color: "#e2e8f0",
          },
        },
        {
          selector: "node[type='entry-point']",
          style: { "background-color": TYPE_COLOR["entry-point"], width: 34, height: 34 },
        },
        { selector: "node[type='service']", style: { "background-color": TYPE_COLOR.service, width: 30, height: 30 } },
        { selector: "node[type='util']", style: { "background-color": TYPE_COLOR.util } },
        { selector: "node[type='config']", style: { "background-color": TYPE_COLOR.config } },
        { selector: "node[type='test']", style: { "background-color": TYPE_COLOR.test } },
        {
          selector: "node[type='community']",
          style: {
            "background-color": TYPE_COLOR.community,
            shape: "round-rectangle",
            width: 46,
            height: 46,
            "font-size": 11,
          },
        },
        {
          selector: "node[type='folder']",
          style: {
            "background-color": TYPE_COLOR.folder,
            shape: "rectangle",
            width: 18,
            height: 18,
          },
        },
        {
          selector: "edge",
          style: {
            label: "data(kind)",
            "font-size": 8,
            "text-rotation": "autorotate",
            "text-background-opacity": 1,
            "text-background-color": "#0f172a",
            "text-background-padding": "2px",
            color: "#94a3b8",
            "curve-style": "bezier",
            "target-arrow-shape": "triangle",
            width: 1.5,
            "line-color": "#475569",
            "target-arrow-color": "#475569",
            "arrow-scale": 1.2,
          },
        },
        {
          selector: ".dimmed",
          style: {
            opacity: 0.2,
          },
        },
        {
          selector: "node.highlighted",
          style: {
            "border-width": 4,
            "border-color": "#38bdf8",
            "border-opacity": 1,
            "text-background-opacity": 1,
            "text-background-color": "#0f172a",
            "text-background-padding": "4px",
            "text-border-opacity": 1,
            "text-border-width": 1,
            "text-border-color": "#38bdf8"
          },
        },
        {
          selector: "edge.highlighted",
          style: {
            "line-color": "#38bdf8",
            "target-arrow-color": "#38bdf8",
            width: 3,
            "z-index": 10
          },
        }
      ],
    });

    cy.on("tap", "node", (evt: cytoscape.EventObject) => {
      const n = evt.target;
      setSelectedNode(n.data() as SelectedNodeData);

      // Haraket (Action) for the user:
      // 1. Zoom and center on the clicked node
      cy.animate({
        center: { eles: n },
        zoom: 1.5,
        duration: 500,
        easing: "ease-out-cubic"
      });

      // 2. Highlight neighbors, dim others
      cy.elements().removeClass("dimmed").removeClass("highlighted");
      const neighborhood = n.neighborhood().add(n);
      cy.elements().difference(neighborhood).addClass("dimmed");
      neighborhood.addClass("highlighted");
    });

    cy.on("tap", (evt: cytoscape.EventObject) => {
      if (evt.target === cy) {
        setSelectedNode(null);
        cy.elements().removeClass("dimmed").removeClass("highlighted");
        cy.animate({
          fit: { eles: cy.elements(), padding: 30 },
          duration: 500
        });
      }
    });

    const layout = cy.layout({
      name: "cose",
      padding: 30,
      nodeRepulsion: () => 400000,
      idealEdgeLength: () => 100,
      edgeElasticity: () => 100,
      gravity: 250,
      numIter: 1000,
      initialTemp: 200,
      coolingFactor: 0.95,
      minTemp: 1.0
    } as cytoscape.LayoutOptions);
    layoutRef.current = layout;
    layout.run();

    cyRef.current = cy;
  }, [graph]);

  // Counts come from the summary when it has loaded, otherwise from the artifact.
  const graphifyStats = graphifySummary ?? graphifyArtifact;
  const selectedCommunity =
    selectedNode && selectedNode.type === "community"
      ? communities?.communities.find((c) => communityNodeId(c) === selectedNode.id) ?? null
      : null;

  return (
    <div className="graph-page">
      <div className="graph-toolbar">
        <div>
          <h2>
            {graph
              ? `${provider === "graphify" ? "Graphify graph" : "EOS graph"} — ${graph.tech_stack.join(", ") || "unknown"}`
              : "Loading graph…"}
          </h2>
          <div className="provider-toggle">
            <button
              className={provider === "eos" ? "selected" : ""}
              data-testid="provider-eos"
              onClick={() => setProvider("eos")}
            >
              EOS graph
            </button>
            <button
              className={provider === "graphify" ? "selected" : ""}
              data-testid="provider-graphify"
              onClick={() => setProvider("graphify")}
            >
              Graphify graph
            </button>
            {queueEntry && (
              <span
                className={`badge ${QUEUE_CLASS[queueEntry.state] || ""}`}
                data-testid="queue-state"
                title={`Graphify refresh queue: ${queueEntry.state} (${queueEntry.attempts} attempts)`}
              >
                queue: {queueEntry.state}
              </span>
            )}
            {provider === "graphify" && graphifyArtifact?.has_html && (
              <a href={api.graphifyHtmlUrl(id)} target="_blank" rel="noreferrer">Open full HTML</a>
            )}
            {provider === "graphify" && graphifyArtifact?.has_report && (
              <a href={api.graphifyReportUrl(id)} target="_blank" rel="noreferrer">Open report</a>
            )}
          </div>
        </div>
        <div className="graph-actions">
          {provider === "graphify" && (
            <form onSubmit={(event) => { event.preventDefault(); setSubmittedGraphQuery(graphQuery.trim()); }}>
              <input
                value={graphQuery}
                onChange={(event) => setGraphQuery(event.target.value)}
                placeholder="Find node"
                aria-label="Find Graphify node"
              />
              <button type="submit">Focus</button>
            </form>
          )}
          <button onClick={handleRescan} disabled={isRescanning || (provider === "eos" && !graph)}>
            {provider === "graphify" ? "Refresh graph" : isRescanning ? "Scanning..." : "Clean & Rescan"}
          </button>
        </div>
      </div>

      {error && <div className="notice">{error}</div>}
      {queueEntry?.state === "failed" && queueEntry.last_error && (
        <div className="notice error" data-testid="queue-error">
          Last Graphify refresh failed: {queueEntry.last_error}
        </div>
      )}
      {provider === "graphify" && graphifyArtifact && graphifyArtifact.status !== "ready" && (
        <div className="notice">
          {graphifyArtifact.status === "missing"
            ? "Graphify artifact is not available for this project."
            : `Graphify artifact is ${graphifyArtifact.status}; regenerate it before trusting this view.`}
        </div>
      )}
      {provider === "graphify" && graphifyStats && (
        <div className="graphify-summary" data-testid="graphify-summary">
          <span className={`badge ${graphifyStats.status === "ready" ? "ok" : "warn"}`}>
            {graphifyStats.status}
          </span>
          <span>{graphifyStats.node_count} nodes</span>
          <span>{graphifyStats.edge_count} edges</span>
          <span>{graphifyStats.community_count} communities</span>
          {graphifyStats.generated_at && <span>generated {graphifyStats.generated_at}</span>}
          {graphifySummary && graphifySummary.communities.length > 0 && (
            <span className="community-chips">
              {graphifySummary.communities.slice(0, 8).map((community) => (
                <button
                  key={community.name}
                  type="button"
                  title={`Focus ${community.name}`}
                  onClick={() => {
                    setGraphQuery(community.name);
                    setSubmittedGraphQuery(community.name);
                  }}
                >
                  {community.name} ({community.node_count})
                </button>
              ))}
            </span>
          )}
        </div>
      )}

      {provider === "graphify" && communities && (
        <div className="community-bar" data-testid="community-bar">
          <span>
            Community-first view — {communities.community_count} communities over{" "}
            {communities.node_count} nodes
          </span>
          {activeCommunity && (
            <button type="button" onClick={() => setActiveCommunity(null)}>
              ← Back to communities
            </button>
          )}
          <span className="community-chips">
            {communities.communities.map((community) => (
              <button
                key={community.id}
                type="button"
                className={activeCommunity?.id === community.id ? "selected" : ""}
                title={`Load the bounded subgraph for ${community.name}`}
                onClick={() => setActiveCommunity(community)}
              >
                {community.name} ({community.node_count})
              </button>
            ))}
          </span>
        </div>
      )}

      <div className="graph-layout">
        <div className="graph-canvas-container">
          <div className="graph-canvas" ref={containerRef} />
          {graph && (
            <div className="legend">
              {Object.entries(TYPE_COLOR).map(([t, c]) => (
                <span key={t}>
                  <i style={{ background: c }} /> {t}
                </span>
              ))}
            </div>
          )}
        </div>

        {selectedNode && (
          <div className="inspector-panel">
            <div className="inspector-header">
              <h3>{selectedNode.label}</h3>
              <span className="node-badge" style={{ backgroundColor: TYPE_COLOR[selectedNode.type] || '#64748b' }}>
                {selectedNode.type}
              </span>
            </div>

            <div className="inspector-body">
              {selectedCommunity && (
                <div className="property-group">
                  <h4>Community</h4>
                  <div className="property-row">
                    <span className="prop-label">Nodes</span>
                    <span className="prop-value">{selectedCommunity.node_count}</span>
                  </div>
                  <div className="property-row">
                    <span className="prop-label">Internal edges</span>
                    <span className="prop-value">{selectedCommunity.edge_count}</span>
                  </div>
                  <button
                    className="copy-context-btn"
                    data-testid="community-drill"
                    onClick={() => setActiveCommunity(selectedCommunity)}
                  >
                    Drill into this community
                  </button>
                </div>
              )}

              <div className="property-group">
                <h4>Properties</h4>
                <div className="property-row">
                  <span className="prop-label">Language</span>
                  <span className="prop-value">{selectedNode.language || 'N/A'}</span>
                </div>
                <div className="property-row">
                  <span className="prop-label">Path</span>
                  <span className="prop-value path-text">{selectedNode.path || 'N/A'}</span>
                </div>
              </div>

              {stringList(selectedNode.metadata?.exports).length > 0 && (
                <div className="property-group">
                  <h4>Exports / Classes</h4>
                  <ul className="prop-value" style={{ margin: '5px 0', paddingLeft: '20px' }}>
                    {stringList(selectedNode.metadata?.exports).map((e, i) => (
                      <li key={i}>{e}</li>
                    ))}
                  </ul>
                </div>
              )}

              {stringList(selectedNode.metadata?.top_symbols).length > 0 && (
                <div className="property-group">
                  <h4>Symbols / Methods</h4>
                  <ul className="prop-value" style={{ margin: '5px 0', paddingLeft: '20px' }}>
                    {stringList(selectedNode.metadata?.top_symbols).map((s, i) => (
                      <li key={i}>{s}</li>
                    ))}
                  </ul>
                </div>
              )}

              <div className="property-group">
                <h4>Documentation & Architecture</h4>
                <div className="markdown-preview" style={{ whiteSpace: "pre-wrap" }}>
                  {selectedNode.doc ? selectedNode.doc : <span style={{ color: "var(--muted)", fontStyle: "italic" }}>No Spring Boot semantics (REST/DB/Kafka) or Javadocs found for this file.</span>}
                </div>
              </div>

              <div className="property-group">
                <h4>AI Context</h4>
                <button className="copy-context-btn" onClick={() => navigator.clipboard.writeText(JSON.stringify(selectedNode, null, 2))}>
                  Copy JSON Context
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
