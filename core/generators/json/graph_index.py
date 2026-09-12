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
            json.dump(self.graph.to_dict(), f, indent=2, ensure_ascii=False)
