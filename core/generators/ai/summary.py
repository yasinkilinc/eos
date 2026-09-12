"""AI-optimized project summary generator.

Produces a single ``AI_SUMMARY.md`` designed to be pasted into an AI
assistant's context so it can contribute to a project without reading
every source file. Token-economical: plain prose, no wikilink noise,
compact structure. Derived from the KnowledgeGraph (no LLM calls —
stays zero-dependency so it runs inside the deployed runtime).

The summary covers:

- One-line project description (from the entry-point docstring).
- Tech stack and language distribution.
- Entry points and their role.
- Module structure (folder-level grouping).
- Hub components (most depended on).
- Quick-start pointer for an AI/developer.
"""
from collections import Counter
from pathlib import Path
from typing import Dict, List

from ...knowledge.model import KnowledgeGraph
from ...links import PARENT_PREFIX


class AISummaryGenerator:
    """Render a compact, AI-ready project summary from the graph."""

    def __init__(self, graph: KnowledgeGraph):
        self.graph = graph

    def generate(self, output_path: Path) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(self._render(), encoding="utf-8")

    @staticmethod
    def _is_linked(node) -> bool:
        """Whether a node comes from a linked parent rather than this project.

        A linked parent is indexed under an "@parent:" storage key so lookups
        can reach it, but it is not the project. Counting it as the project is
        how a 15-file service described itself as "1891 files ... 1891 java",
        with every hub component belonging to the upstream platform -- the
        exact inversion of the parent-safe rule the overlay workflow depends on.
        """
        return str(getattr(node, "path", "") or "").startswith(PARENT_PREFIX)

    def _render(self) -> str:
        own_nodes = [n for n in self.graph.nodes if not self._is_linked(n)]
        linked_nodes = [n for n in self.graph.nodes if self._is_linked(n)]
        file_nodes = [n for n in own_nodes if n.type != "folder"]
        folder_nodes = [n for n in own_nodes if n.type == "folder"]
        linked_file_nodes = [n for n in linked_nodes if n.type != "folder"]
        own_ids = {n.id for n in own_nodes}
        import_edges = [
            e
            for e in self.graph.edges
            if e.kind == "import" and e.source_id in own_ids and e.target_id in own_ids
        ]

        lang_counts = Counter(n.language for n in file_nodes if n.language)
        type_counts = Counter(n.type for n in file_nodes)

        incoming: Dict[str, int] = {}
        for e in import_edges:
            incoming[e.target_id] = incoming.get(e.target_id, 0) + 1
        hubs = sorted(
            [n for n in file_nodes if incoming.get(n.id, 0) > 0],
            key=lambda n: incoming.get(n.id, 0),
            reverse=True,
        )[:5]

        entry_nodes = [
            self.graph.get_node(ep) for ep in self.graph.entry_points
        ]
        entry_nodes = [n for n in entry_nodes if n]

        # One-line description: prefer this project's own entry point, and only
        # fall back to a linked parent's when the overlay has none of its own.
        description = next(
            (n.doc for n in entry_nodes if n.doc and not self._is_linked(n)),
            None,
        ) or next((n.doc for n in entry_nodes if n.doc), None)

        lines: List[str] = []

        # Header — compact, scannable.
        lines.append("# Project Summary")
        lines.append("")
        if description:
            lines.append(f"> {description.strip().splitlines()[0]}")
            lines.append("")

        # Stats block — numbers up front so the AI can gauge scale.
        lines.append("## Scale")
        lines.append("")
        lines.append(
            f"{len(file_nodes)} files across {len(folder_nodes)} folders, "
            f"{len(import_edges)} import edges, {sum(type_counts.values())} components."
        )
        if lang_counts:
            lang_parts = [f"{c} {l}" for l, c in lang_counts.most_common()]
            lines.append(f"Languages: {', '.join(lang_parts)}.")
        if linked_file_nodes:
            lines.append(
                f"Plus {len(linked_file_nodes)} file(s) indexed from linked parent "
                "project(s), searchable but not part of this project."
            )
        lines.append("")

        # Tech stack.
        if self.graph.tech_stack:
            lines.append("## Tech Stack")
            lines.append("")
            lines.append(", ".join(self.graph.tech_stack))
            lines.append("")

        # Component breakdown by type.
        if type_counts:
            lines.append("## Component Types")
            lines.append("")
            for t, c in type_counts.most_common():
                lines.append(f"- {t}: {c}")
            lines.append("")

        # Entry points — where execution / understanding starts.
        #
        # An overlay's own entry points and its parent's are both worth
        # listing, but never in one undifferentiated list: for a thin overlay
        # every entry point belongs to the platform it extends, and reading
        # them as the project's own is how parent architecture gets attributed
        # to the overlay.
        own_entries = [n for n in entry_nodes if not self._is_linked(n)]
        linked_entries = [n for n in entry_nodes if self._is_linked(n)]

        def _entry_line(node) -> str:
            desc = f" — {node.doc.strip().splitlines()[0]}" if node.doc else ""
            return f"- `{node.path}` ({node.language}){desc}"

        if own_entries:
            lines.append("## Entry Points")
            lines.append("")
            lines.extend(_entry_line(n) for n in own_entries)
            lines.append("")

        if linked_entries:
            lines.append("## Entry Points (linked parent, not this project)")
            lines.append("")
            lines.extend(_entry_line(n) for n in linked_entries)
            lines.append("")

        # Module structure — folder-level map.
        if folder_nodes:
            lines.append("## Module Structure")
            lines.append("")
            folders_sorted = sorted(folder_nodes, key=lambda n: (n.path or ""))
            for f in folders_sorted:
                depth = len(Path(f.path or "").parts) - 1 if f.path else 0
                indent = "  " * max(depth, 0)
                # Count files directly under this folder.
                direct = [
                    n for n in file_nodes
                    if n.path and str(Path(n.path).parent).replace("\\", "/") == (f.path or "")
                ]
                child = f"{f.label}/ ({len(direct)} files)" if direct else f"{f.label}/"
                lines.append(f"{indent}{child}")
            lines.append("")

        # Hub components — the load-bearing modules.
        if hubs:
            lines.append("## Hub Components (highest inbound dependencies)")
            lines.append("")
            for n in hubs:
                lines.append(
                    f"- `{n.path}` — {n.label} "
                    f"({incoming.get(n.id, 0)} dependents, {n.language})"
                )
            lines.append("")

        # AI guidance — how to use this summary.
        lines.append("## How to Use This Summary")
        lines.append("")
        lines.append(
            "Read the Entry Points first, then the Hub Components — they carry "
            "the most integration surface. For deeper detail on any component, "
            "read its source file at the listed path. The full dependency graph "
            "is in graph.json alongside this file."
        )
        lines.append("")

        return "\n".join(lines) + "\n"
