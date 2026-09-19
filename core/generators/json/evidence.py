"""Write evidence.jsonl -- the provenance sidecar for graph.json.

A sibling of GraphIndexGenerator, deliberately a separate artifact rather than
extra keys inside graph.json: that file is 25,466,819 bytes for 6,040 nodes and
72,139 edges on a real service, MCP `get_graph` returns it whole, and per-edge
provenance would add ~10.6 MB raw.

JSONL, not JSON. Structural extraction will produce tens of thousands of facts
on a project this size, and the index must be able to stream them one line at a
time instead of materialising the whole list.

Layout: one header line, then one fact per line, then one coverage line per
detector/predicate pair. The header carries the counts so a reader can tell a
complete file from one a killed scan left half-written -- the same guard
_load_brain already applies to graph.json.
"""
import json
from pathlib import Path

from ...knowledge.evidence import utc_now
from ...knowledge.model import KnowledgeGraph
from ...knowledge.semantic import ScanReport

FORMAT = 1


class EvidenceGenerator:
    """Write the provenance sidecar next to graph.json."""

    def __init__(self, graph: KnowledgeGraph, report: ScanReport, engine_version: str = ""):
        self.graph = graph
        self.report = report
        self.engine_version = engine_version

    def _exclusions(self) -> list[tuple]:
        return [
            (kind, rule, files)
            for kind, rules in (("ignore", self.report.skipped_by_ignore),
                                ("unsupported", self.report.unsupported_extensions),
                                ("unresolved", self.report.unresolved_imports))
            for rule, files in rules.items()
        ]

    def counts(self) -> dict:
        """What the header will claim, so the caller can record it too.

        Every row kind is counted, not just the interesting ones: a scan killed
        while writing loses whatever was at the tail, and a guard that only
        checks facts and coverage would pass a file missing its exclusions.
        """
        return {
            "facts": len(self.graph.facts),
            "coverage": len(self.report.coverage),
            "exclusions": len(self._exclusions()),
        }

    def generate(self, output_path: Path) -> dict:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        counts = self.counts()
        header = {
            "type": "header",
            "format": FORMAT,
            "engine_version": self.engine_version,
            "generated_at": utc_now(),
            **counts,
        }
        with open(output_path, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(header, ensure_ascii=False) + "\n")
            for fact in self.graph.facts:
                handle.write(json.dumps({"type": "fact", **fact.to_dict()}, ensure_ascii=False) + "\n")
            for entry in self.report.coverage.values():
                handle.write(json.dumps({"type": "coverage", **entry.to_dict()}, ensure_ascii=False) + "\n")
            for kind, rule, files in self._exclusions():
                handle.write(json.dumps(
                    {"type": "exclusion", "kind": kind, "rule": rule, "files": files},
                    ensure_ascii=False) + "\n")
        return counts
