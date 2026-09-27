"""Generate machine-readable graph_index.json from KnowledgeGraph."""
import json
from pathlib import Path

from ...knowledge.model import KnowledgeGraph


class GraphIndexGenerator:
    """Write graph.json for eos-ui and external tools."""

    def __init__(self, graph: KnowledgeGraph):
        self.graph = graph

    def generate(self, output_path: Path) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            # Compact (2.x roadmap E6): read by machines; 23.3 MB -> 18.9 MB on a real service.
            json.dump(self.graph.to_dict(), f, ensure_ascii=False, separators=(",", ":"))
