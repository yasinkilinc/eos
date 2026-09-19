"""Read-only project inspection helpers shared by the CLI and MCP server."""
from __future__ import annotations

import fnmatch
import json
import os
from pathlib import Path
from typing import Any

from core.lib.paths import is_test_path


BRAIN_FILES = ("AI_SUMMARY.md", "Architecture.md", "EntryPoints.md", "TechStack.md", "_index.md")

# Brain sections that enumerate the project file by file. They are the bulk of
# the brain -- on acme-orders the flat component list alone was 6,687 of the
# 12,000 tokens get_context returned -- and they grow with the project while saying
# nothing an agent can act on: a name it has not asked about is not orientation.
# What they enumerate stays reachable through find_symbol, get_structure and
# graph.json, on demand and only for what the agent is actually looking at.
DROPPED_BRAIN_SECTIONS = ("## Components", "## Module Structure", "## Folder Structure")

# Costs ~60 tokens, and saves the far larger request for a full inventory that
# an agent makes when it cannot see how else to find a name.
GOING_DEEPER = """## Going Deeper

This is orientation, not an inventory. From here: `find_symbol` locates a symbol,
`get_file` reads any path, `compose` builds context for a task plus target file,
`get_parent_implementation` shows behaviour inherited from a linked parent, and
`search_notes` recalls what was already learned about this project."""
SENSITIVE_PATTERNS = (".env", ".env.*", "*.key", "*.pem", "*.p12", "*.jks", "*secret*", "*credential*")

IGNORED_DIRS = {
    ".eos",
    ".git",
    ".gradle",
    ".mvn",
    ".next",
    ".pytest_cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "target",
    "venv",
}


def resolve_root(path: str | Path) -> Path:
    return Path(path).expanduser().resolve()


def require_project(root: str | Path) -> Path:
    project = resolve_root(root)
    eos_dir = project / ".eos"
    if not eos_dir.is_dir():
        raise ValueError(f"No .eos directory found at {project}. Run 'eos init' first.")
    return project


def _is_sensitive(relative_path: str) -> bool:
    name = Path(relative_path).name.casefold()
    return any(fnmatch.fnmatch(name, pattern) for pattern in SENSITIVE_PATTERNS)


def _read_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def load_graph(root: str | Path) -> dict[str, Any]:
    project = require_project(root)
    graph = _read_json(project / ".eos" / "data" / "brain" / "graph.json", None)
    if not isinstance(graph, dict) or not isinstance(graph.get("nodes"), list) or not isinstance(graph.get("edges"), list):
        raise ValueError(f"Graph is missing at {project}. Run 'eos scan' first.")
    return graph


def load_metadata(root: str | Path) -> dict[str, Any]:
    project = require_project(root)
    return _read_json(project / ".eos" / "data" / "last_scan.json", {})


def project_summary(root: str | Path) -> dict[str, Any]:
    project = require_project(root)
    eos_dir = project / ".eos"
    version_path = eos_dir / "runtime" / "VERSION"
    version = version_path.read_text(encoding="utf-8").strip() if version_path.is_file() else "unknown"
    instance_id = (eos_dir / "id.txt").read_text(encoding="utf-8").strip() if (eos_dir / "id.txt").is_file() else ""
    metadata = load_metadata(project)
    return {
        "project": str(project),
        "name": project.name,
        "instance_id": instance_id,
        "engine_version": version,
        "last_scan": metadata,
        "runtime": str(eos_dir / "runtime"),
    }


def structure(root: str | Path, max_entries: int = 500) -> list[str]:
    project = require_project(root)
    entries: list[str] = []
    for dirpath, dirnames, filenames in os.walk(project, topdown=True, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if d not in IGNORED_DIRS)
        relative_dir = Path(dirpath).relative_to(project)
        for filename in sorted(filenames):
            relative = (relative_dir / filename).as_posix()
            if _is_sensitive(relative):
                continue
            entries.append(relative)
            if len(entries) >= max_entries:
                return entries
    return entries


def read_file(root: str | Path, relative_path: str, max_chars: int = 20000) -> dict[str, Any]:
    project = require_project(root)
    if _is_sensitive(relative_path):
        raise ValueError("Sensitive files are not exposed through EOS inspection")
    candidate = (project / relative_path).resolve()
    try:
        candidate.relative_to(project)
    except ValueError as exc:
        raise ValueError("Requested file is outside the project root") from exc
    if not candidate.is_file():
        raise ValueError(f"File not found: {relative_path}")
    content = candidate.read_text(encoding="utf-8", errors="replace")
    truncated = len(content) > max_chars
    if truncated:
        content = content[:max_chars]
    return {
        "path": candidate.relative_to(project).as_posix(),
        "content": content,
        "truncated": truncated,
    }


def find_symbols(root: str | Path, query: str, max_results: int = 100) -> list[dict[str, Any]]:
    project = require_project(root)
    cache = _read_json(project / ".eos" / "data" / "cache" / "file_cache.json", {})
    matches: list[dict[str, Any]] = []
    needle = query.casefold()
    for path, entry in cache.items() if isinstance(cache, dict) else []:
        semantic = entry.get("semantic", {}) if isinstance(entry, dict) else {}
        for symbol in semantic.get("symbols", []) if isinstance(semantic, dict) else []:
            name = str(symbol.get("name", ""))
            if needle not in name.casefold():
                continue
            matches.append({"path": path, **symbol})
            if len(matches) >= max_results:
                return matches
    return matches


def get_parent_implementation(root: str | Path, symbol: str, max_results: int = 10) -> dict[str, Any]:
    """Search only the parent-tagged slice of the symbol index and inline
    the real source around each hit, read live off the resolved parent path
    -- never from the cache, so this always reflects what is on disk now."""
    from core import links

    project = require_project(root)
    cache = _read_json(project / ".eos" / "data" / "cache" / "file_cache.json", {})
    configured = links.read_links(project)

    needle = symbol.casefold()
    matches: list[dict[str, Any]] = []
    for path, entry in cache.items() if isinstance(cache, dict) else []:
        if not path.startswith(links.PARENT_PREFIX):
            continue
        semantic = entry.get("semantic", {}) if isinstance(entry, dict) else {}
        for sym in semantic.get("symbols", []) if isinstance(semantic, dict) else []:
            name = str(sym.get("name", ""))
            if needle not in name.casefold():
                continue

            matches.append({
                "path": path,
                "name": name,
                "kind": sym.get("kind"),
                "line": sym.get("line"),
            })

    # Rank before truncating. Iterating the cache in dict order and returning at
    # max_results meant the cut happened before any ordering existed, so the
    # answer was whichever files the last scan happened to write first. Measured
    # over 85 real Fm* override lookups that put a parent *test* class at rank 1
    # in 9 of them -- including this tool's own documented example, where
    # `parent acme-billing ActivityService` answered with ActivityServiceTest
    # and 30 lines of @Mock declarations. Sorting first moves rank-1 accuracy
    # from 84.7% to 94.1% and test noise to zero, with no rescan and no index
    # change: the same cache on disk simply gets read in a sensible order.
    matches.sort(key=lambda m: (
        is_test_path(str(m["path"])),
        str(m["name"]).casefold() != needle,
        str(m["path"]),
    ))
    matches = matches[:max_results]

    # Source is read only for the survivors. Reading it inside the search loop
    # would open every file a broad symbol touches -- `Service` matches hundreds
    # -- to then discard all but max_results of them.
    for match in matches:
        label, _, rel_within = str(match["path"])[len(links.PARENT_PREFIX):].partition("/")
        match["source"] = None
        link = configured.get(label)
        if link is None:
            continue
        src_path = links.resolve_link_path(project, link) / rel_within
        if not src_path.is_file():
            continue
        lines = src_path.read_text(encoding="utf-8", errors="replace").splitlines()
        start = max(0, int(match.get("line") or 1) - 1)
        match["source"] = "\n".join(lines[start:start + 30])

    return {"symbol": symbol, "matches": matches}


def _node_for_path(graph: dict[str, Any], relative_path: str) -> dict[str, Any] | None:
    # removeprefix, not lstrip: lstrip strips *characters*, so a path that
    # legitimately begins with a dot lost it -- ".worktrees/x/Foo.java" became
    # "worktrees/x/Foo.java", matched no node, and impact() raised
    # "No graph node found for file". Every dot-prefixed path was affected.
    normalized = relative_path.replace("\\", "/").removeprefix("./")
    for node in graph.get("nodes", []):
        if node.get("path") == normalized:
            return node
    return None


def impact(root: str | Path, relative_path: str) -> dict[str, Any]:
    graph = load_graph(root)
    node = _node_for_path(graph, relative_path)
    if node is None:
        raise ValueError(f"No graph node found for file: {relative_path}")
    node_id = node.get("id")
    dependencies: list[dict[str, Any]] = []
    dependents: list[dict[str, Any]] = []
    nodes = {item.get("id"): item for item in graph.get("nodes", [])}
    for edge in graph.get("edges", []):
        if edge.get("kind") != "import":
            continue
        if edge.get("source") == node_id:
            target = nodes.get(edge.get("target"), {})
            dependencies.append({"path": target.get("path"), "kind": edge.get("kind")})
        elif edge.get("target") == node_id:
            source = nodes.get(edge.get("source"), {})
            dependents.append({"path": source.get("path"), "kind": edge.get("kind")})
    return {"file": node.get("path"), "dependencies": dependencies, "dependents": dependents}


def history(root: str | Path) -> dict[str, Any]:
    project = require_project(root)
    runtime = project / ".eos" / "runtime"
    return {
        "engine_version": (runtime / "VERSION").read_text(encoding="utf-8").strip() if (runtime / "VERSION").is_file() else "unknown",
        "last_scan": load_metadata(project),
    }


def _read_brain(root: Path) -> str:
    brain = root / ".eos" / "data" / "brain"
    sections: list[str] = []
    for name in BRAIN_FILES:
        path = brain / name
        if path.is_file():
            sections.append(path.read_text(encoding="utf-8", errors="replace"))
    if not sections:
        raise ValueError(f"Context is missing at {brain}. Run 'eos scan' first.")
    return "\n\n".join(sections)


def _brain_orientation(root: Path) -> str:
    """The brain with its file-by-file enumerations removed.

    Section-aware rather than character-based: a heading in
    DROPPED_BRAIN_SECTIONS suppresses everything up to the next heading, so
    what survives is whole sections of prose -- scale, stack, entry points,
    hubs -- and never half of a list.
    """
    kept: list[str] = []
    skipping = False
    for line in _read_brain(root).splitlines():
        if line.startswith("#"):
            skipping = line.strip() in DROPPED_BRAIN_SECTIONS
        if not skipping:
            kept.append(line)
    return "\n".join(kept).strip()


def _fit_sections(sections: list[str], max_chars: int) -> str:
    """Join sections, dropping whole trailing ones once the budget is spent.

    Same shape as notes.render_context_section: sections go in whole or not at
    all, the first one is the allowed overage so something is always returned,
    and the number left out is reported -- a trimmed context that looks
    complete is worse than a short one.
    """
    kept = sections[:1]
    for section in sections[1:]:
        if len("\n\n".join(kept + [section])) > max_chars:
            break
        kept.append(section)
    dropped = len(sections) - len(kept)
    if dropped:
        kept.append(f"_{dropped} section(s) omitted to stay within the context budget._")
    return "\n\n".join(kept)


def build_context(root: str | Path, budget: int = 12000, task: str | None = None, target: str | None = None) -> str:
    """Assemble bounded orientation for an agent starting work on this project.

    The output is sized by what is worth saying, not by the budget: the budget
    is a ceiling that drops whole sections when hit, not a quota to fill.
    """
    project = require_project(root)
    summary = project_summary(project)
    max_chars = max(1000, budget * 4)

    sections = [
        "\n".join([
            "# EOS Project Context",
            "",
            f"- Project: `{summary['name']}`",
            f"- Path: `{summary['project']}`",
            f"- Engine version: `{summary['engine_version']}`",
        ]),
    ]
    if task:
        sections.append(f"## Task\n\n{task}")
    if target:
        file_data = read_file(project, target, max_chars=12000)
        # read_file reports whether it cut the file short; dropping that flag
        # hands the agent a half-read file that looks whole, and it will reason
        # about the missing half as if it were absent from the source.
        cut = (
            "\n\n_File truncated at 12,000 characters — use `get_file` with a "
            "larger `max_chars` for the rest._"
            if file_data["truncated"] else ""
        )
        sections.append(
            f"## Target File\n\n`{file_data['path']}`\n\n```\n{file_data['content']}\n```{cut}"
        )
        try:
            file_impact = impact(project, target)
            sections.append(f"## Target Impact\n\n```json\n{json.dumps(file_impact, indent=2)}\n```")
        except ValueError:
            pass
    sections.append(_brain_orientation(project))

    from core import notes

    # Capped at 15% of the budget: notes complement the code context, and
    # must not be able to crowd it out. Scored against `task` when the caller
    # gave one (compose always does); a plain `eos context` has no task, so
    # recency stands in for relevance there.
    notes_section = notes.render_context_section(project, query=task, max_chars=int(max_chars * 0.15))

    # render_context_section deliberately lets a single oversized note exceed
    # its own cap rather than cut a note in half. That is right for the notes
    # file, and wrong to propagate here: one 200 KB note produced a 200 KB
    # context, 4x the ceiling this function promises, which is exactly the
    # runaway an agent's context window cannot absorb. Past its allotment the
    # whole block is dropped and said so -- search_notes still reaches it.
    notes_allotment = int(max_chars * 0.15)
    if notes_section and len(notes_section) > notes_allotment:
        notes_section = (
            "## Accumulated Knowledge\n\n"
            "_Notes omitted here: they exceed this context's share of the budget. "
            "Use `search_notes` to read them._"
        )

    # The pointer and the notes are held out of the fit loop and their room
    # reserved up front: both are now bounded, and both are worth more per
    # character than the tail of an orientation section.
    reserved = len(GOING_DEEPER) + 2 + (len(notes_section) + 2 if notes_section else 0)
    parts = [_fit_sections(sections, max(0, max_chars - reserved)), GOING_DEEPER]
    if notes_section:
        parts.append(notes_section)
    return "\n\n".join(parts) + "\n"


def compose(root: str | Path, task: str, target: str | None = None, budget: int = 12000) -> str:
    return build_context(root, budget=budget, task=task, target=target)


def why(project_root: str | Path, subject: str | None = None,
        predicate: str | None = None, min_confidence: float | None = None) -> dict[str, Any]:
    """What is known about one file, how it came to be known, and what was not looked for.

    The last part is the point. A reader who asks "does this service publish
    events?" and gets nothing back cannot tell a service that publishes none
    from a detector that never ran -- so every answer carries the coverage of
    the detectors that were asked, including the ones that found nothing.

    `subject` is a project-relative path. Omitted, the answer is the coverage
    summary alone: what this project's scan looked at, and what it produced.
    """
    from core import index as _index

    database = _index.db_path(project_root)
    if not database.exists():
        raise ValueError(f"No index at {database}. Run 'eos index' first.")

    node_id = None
    facts: list[dict[str, Any]] = []
    inbound: list[dict[str, Any]] = []
    conn = _index.connect_read_only(database)
    try:
        if subject:
            normalized = subject.replace("\\", "/").removeprefix("./")
            row = conn.execute("SELECT id, path FROM node WHERE path = ?", (normalized,)).fetchone()
            if row is None:
                raise ValueError(f"No indexed node for: {subject}")
            node_id, normalized = row[0], row[1]

            clauses = ["(subject = ? OR subject LIKE ? OR subject LIKE ?)"]
            params: list[Any] = [node_id, f"{node_id}|%", f"%|{node_id}|%"]
            if predicate:
                clauses.append("predicate = ?")
                params.append(predicate)
            if min_confidence is not None:
                clauses.append("confidence >= ?")
                params.append(min_confidence)
            rows = conn.execute(
                "SELECT subject_kind, subject, predicate, object, origin, confidence, detector, "
                f"source_ref, observed_at FROM fact WHERE {' AND '.join(clauses)} "
                "ORDER BY predicate, object", params).fetchall()
            columns = ("subject_kind", "subject", "predicate", "object", "origin",
                       "confidence", "detector", "source_ref", "observed_at")
            for values in rows:
                entry = dict(zip(columns, values))
                # An edge subject is "src|dst|kind|imported"; the fact is about
                # this file either way, but the direction changes what it means.
                if entry["subject_kind"] == "edge" and not entry["subject"].startswith(f"{node_id}|"):
                    inbound.append(entry)
                else:
                    facts.append(entry)

        coverage = [
            dict(zip(("detector", "predicate", "files_eligible", "files_with_hits", "hits"), values))
            for values in conn.execute(
                "SELECT detector, predicate, files_eligible, files_with_hits, hits "
                "FROM coverage ORDER BY detector, predicate").fetchall()
        ]
        built_at = conn.execute("SELECT value FROM meta WHERE key = 'built_at'").fetchone()
    finally:
        conn.close()

    return {
        "subject": subject,
        "node_id": node_id,
        "facts": facts,
        "inbound": inbound,
        "coverage": coverage,
        "index_built_at": built_at[0] if built_at else None,
    }
