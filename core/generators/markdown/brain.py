"""Generate the Markdown brain documents from a KnowledgeGraph.

The brain is a small, fixed set of documents (inspector.BRAIN_FILES), not an
Obsidian vault: every consumer -- the CLI, the MCP tools, eos-ui -- reads
those documents or graph.json, and nothing walks per-component notes. Node
detail therefore lives in graph.json and in the source itself, reachable via
find_symbol, and is referenced here by path rather than by wikilink.
"""
import shutil
from pathlib import Path
from typing import List

from ...knowledge.model import KnowledgeGraph, KnowledgeNode
from ...links import PARENT_PREFIX


class BrainGenerator:
    """Render a project's knowledge documents as Markdown."""

    def __init__(self, graph: KnowledgeGraph):
        self.graph = graph

    def generate(self, output_dir: Path) -> None:
        output_dir.mkdir(parents=True, exist_ok=True)

        # Older builds wrote one note per component here. This generator owns
        # the directory, so it clears the leftovers rather than leaving them to
        # rot: they are dead the moment a newer build runs, and nothing prunes
        # a directory it no longer writes (13,447 such files were sitting
        # across this workspace's services after the change that stopped
        # generating them).
        stale_vault = output_dir / "Components"
        if stale_vault.is_dir():
            shutil.rmtree(stale_vault)

        # Root index
        index_path = output_dir / "_index.md"
        index_path.write_text(self._render_index(), encoding="utf-8")

        # Tech stack summary
        tech_path = output_dir / "TechStack.md"
        tech_path.write_text(self._render_tech_stack(), encoding="utf-8")

        # Entry points
        entry_path = output_dir / "EntryPoints.md"
        entry_path.write_text(self._render_entry_points(), encoding="utf-8")

        # Architecture synthesis (hub/leaf analysis, folder structure).
        arch_path = output_dir / "Architecture.md"
        arch_path.write_text(self._render_architecture(), encoding="utf-8")

    def _render_index(self) -> str:
        own = self._own_nodes()
        lines: List[str] = [
            "---",
            f"languages: {self.graph.languages}",
            f"tech_stack: {self.graph.tech_stack}",
            f"components: {len(own)}",
            f"edges: {len(self.graph.edges)}",
            "---",
            "",
            "# Project Index",
            "",
            "## Tech Stack",
            "",
            "- " + "\n- ".join(self.graph.tech_stack) if self.graph.tech_stack else "_No tech stack detected._",
            "",
            "## Entry Points",
            "",
        ]
        for ep in self._own_entry_points():
            node = self.graph.get_node(ep)
            lines.append(f"- `{self._ref(node)}`" if node else f"- `{ep}`")
        if not self._own_entry_points():
            lines.append("_No entry points identified._")
        lines.append("")
        lines.append("## Components")
        lines.append("")
        for node in own:
            lines.append(f"- `{self._ref(node)}` — `{node.type}`")
        lines.append("")
        return "\n".join(lines) + "\n"

    def _render_tech_stack(self) -> str:
        lines = ["# Tech Stack", ""]
        for item in self.graph.tech_stack:
            lines.append(f"- {item}")
        lines.append("")
        lines.append("## Languages")
        lines.append("")
        for lang in self.graph.languages:
            lines.append(f"- {lang}")
        lines.append("")
        return "\n".join(lines) + "\n"

    def _render_entry_points(self) -> str:
        lines = ["# Entry Points", ""]
        for ep in self._own_entry_points():
            node = self.graph.get_node(ep)
            if not node:
                continue
            lines.append(f"- `{self._ref(node)}` — {node.label}")
            # The routes each entry point actually serves. The parser has
            # carried them in node.doc for a long time and no generator ever
            # rendered one, so a file listing 20 controllers named zero of
            # their endpoints -- which is the thing a reader opened it for.
            lines.extend(f"  - {route}" for route in self._routes(node))
        lines.append("")
        return "\n".join(lines) + "\n"

    @staticmethod
    def _routes(node) -> List[str]:
        return [line.lstrip("- ").strip()
                for line in (node.doc or "").splitlines()
                if line.startswith("- **")]

    def _own_entry_points(self) -> List[str]:
        """Entry points belonging to this project -- a linked parent's are not.

        _own_nodes() already excluded parent files from every listing, but the
        entry-point list was read raw in six places, so a thin overlay reported
        "Files: 4" and "Entry points: 14" in the same block, all fourteen of
        them the platform's. Before roles existed no Java file was ever an entry
        point, so the mis-attribution only became possible when they did.
        """
        own = []
        for ep in self.graph.entry_points:
            node = self.graph.get_node(ep)
            if not ((node.path if node else ep) or "").startswith(PARENT_PREFIX):
                own.append(ep)
        return own

    def _own_nodes(self) -> List[KnowledgeNode]:
        """The project's own nodes -- everything a linked parent contributed is left out.

        A parent tree is an order of magnitude larger than the FM overlay in
        front of it (4,545 of acme-catalog's 4,751 nodes), so listing
        it flat buries the project's own files and says nothing useful about
        either. Parent nodes stay in graph.json, where get_graph and
        impact_analysis need the inheritance edges, and their contents stay
        reachable one lookup at a time through find_symbol and
        get_parent_implementation.
        """
        return [n for n in self.graph.nodes if not (n.path or "").startswith(PARENT_PREFIX)]

    @staticmethod
    def _ref(node: KnowledgeNode) -> str:
        """How a node is named in a listing: its path, so a reader can open it."""
        base = node.path or node.id
        if base.startswith("file:"):
            base = base[5:]
        return base or node.label

    def _render_architecture(self) -> str:
        """Synthesize an architecture overview from the knowledge graph.

        Surfaces: file/folder/node counts, tech stack, entry points, the
        folder tree, and hub/leaf analysis (most-depended-on and
        most-depending nodes) — describing the project's own code, since a
        linked parent's tree is not this project's architecture.
        """
        own = self._own_nodes()
        file_nodes = [n for n in own if n.type != "folder"]
        folder_nodes = [n for n in own if n.type == "folder"]
        parent_files = len([n for n in self.graph.nodes if n.type != "folder"]) - len(file_nodes)
        import_edges = [e for e in self.graph.edges if e.kind == "import"]

        incoming: dict = {}
        outgoing: dict = {}
        for e in import_edges:
            incoming[e.target_id] = incoming.get(e.target_id, 0) + 1
            outgoing[e.source_id] = outgoing.get(e.source_id, 0) + 1

        hubs = sorted(
            [n for n in file_nodes if incoming.get(n.id, 0) > 0],
            key=lambda n: incoming.get(n.id, 0),
            reverse=True,
        )[:10]
        leaves = sorted(
            [n for n in file_nodes if outgoing.get(n.id, 0) == 0 and incoming.get(n.id, 0) == 0],
            key=lambda n: n.label,
        )[:15]

        folders_sorted = sorted(folder_nodes, key=lambda n: (n.path or ""))

        lines: List[str] = [
            "---",
            "type: architecture",
            f"files: {len(file_nodes)}",
            f"folders: {len(folder_nodes)}",
            f"import_edges: {len(import_edges)}",
            f"tech_stack: {self.graph.tech_stack}",
            "---",
            "",
            "# Architecture",
            "",
            "## Overview",
            "",
            f"- **Files:** {len(file_nodes)}",
            f"- **Folders:** {len(folder_nodes)}",
            f"- **Import edges:** {len(import_edges)}",
            f"- **Entry points:** {len(self._own_entry_points())}",
            f"- **Languages:** {', '.join(self.graph.languages) or 'none'}",
        ]
        if parent_files:
            lines.append(
                f"- **Linked parent files:** {parent_files} "
                "(indexed for lookup, not listed here — use find_symbol / get_parent_implementation)"
            )
        lines.extend(["", "## Tech Stack", ""])
        if self.graph.tech_stack:
            lines.append("- " + "\n- ".join(self.graph.tech_stack))
        else:
            lines.append("_No tech stack detected._")
        lines.append("")

        if self._own_entry_points():
            lines.append("## Entry Points")
            lines.append("")
            for ep in self._own_entry_points():
                node = self.graph.get_node(ep)
                lines.append(f"- `{self._ref(node)}`" if node else f"- `{ep}`")
            lines.append("")

        if folders_sorted:
            lines.append("## Folder Structure")
            lines.append("")
            lines.append("```text")
            for f in folders_sorted:
                depth = len(Path(f.path or "").parts) - 1 if f.path else 0
                indent = "  " * max(depth, 0)
                lines.append(f"{indent}{f.label}/")
            lines.append("```")
            lines.append("")

        if hubs:
            lines.append("## Hub Components (most depended on)")
            lines.append("")
            for n in hubs:
                lines.append(f"- `{self._ref(n)}` — {incoming.get(n.id, 0)} inbound")
            lines.append("")

        if leaves:
            lines.append("## Leaf Components (no inbound or outbound imports)")
            lines.append("")
            for n in leaves:
                lines.append(f"- `{self._ref(n)}`")
            lines.append("")

        return "\n".join(lines) + "\n"
