"""Knowledge model — the single source of truth for all artifacts.

Derived from ProjectSemantic, this layer lifts language-specific symbols into
project-level concepts: Components, Services, EntryPoints, Dependencies.
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class Dependency:
    """A directed dependency between two knowledge entities."""
    source_id: str
    target_id: str
    kind: str  # import | folder-hierarchy | monorepo-parent | shared-dep | manual-link
    weight: int = 1
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class KnowledgeNode:
    """Generic node in the knowledge graph."""
    id: str
    type: str  # component | service | entry-point | util | config | test | external-dep
    label: str
    path: Optional[str] = None
    language: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    doc: Optional[str] = None


@dataclass
class KnowledgeGraph:
    """The central knowledge representation."""
    nodes: List[KnowledgeNode] = field(default_factory=list)
    edges: List[Dependency] = field(default_factory=list)
    languages: List[str] = field(default_factory=list)
    tech_stack: List[str] = field(default_factory=list)
    entry_points: List[str] = field(default_factory=list)

    def add_node(self, node: KnowledgeNode) -> None:
        self.nodes.append(node)

    def add_edge(self, edge: Dependency) -> None:
        self.edges.append(edge)

    def get_node(self, node_id: str) -> Optional[KnowledgeNode]:
        for node in self.nodes:
            if node.id == node_id:
                return node
        return None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "languages": self.languages,
            "tech_stack": self.tech_stack,
            "entry_points": self.entry_points,
            "nodes": [
                {
                    "id": n.id,
                    "type": n.type,
                    "label": n.label,
                    "path": n.path,
                    "language": n.language,
                    "tags": n.tags,
                    "metadata": n.metadata,
                    "doc": n.doc,
                }
                for n in self.nodes
            ],
            "edges": [
                {
                    "source": e.source_id,
                    "target": e.target_id,
                    "kind": e.kind,
                    "weight": e.weight,
                    "metadata": e.metadata,
                }
                for e in self.edges
            ],
        }
