"""Read-only project inspection helpers shared by the CLI and MCP server."""
from __future__ import annotations

import datetime
import fnmatch
import json
import os
import sqlite3
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


def impact(root: str | Path, relative_path: str, depth: int = 1,
           kinds: tuple[str, ...] | None = None) -> dict[str, Any]:
    """What this file reaches, and what reaches it, out to `depth` hops.

    Answered from the SQLite index when there is one: the graph.json path
    re-parses a file that is 25 MB on a real service and then linear-scans
    6,040 nodes, which measured 0.14 s per call against 0.0013 s indexed.
    Without an index it falls back to that path, so the answer degrades rather
    than disappearing before the first `eos index`.

    The caller is told which it got, and whether the index is older than the
    scan output it was built from. Staleness is one stat() rather than
    index.refresh(): a rebuild re-hashes the graph and shells out to git, which
    is right for `eos query` and wrong inside an MCP call.
    """
    from core import index as _index

    normalized = str(relative_path).replace("\\", "/").removeprefix("./")
    conn = _index.open_for_read(root)
    if conn is not None:
        try:
            answer = _index.impact_rows(conn, normalized, depth=depth,
                                        kinds=kinds or _index.IMPACT_KINDS)
            built_at = conn.execute("SELECT value FROM meta WHERE key = 'built_at'").fetchone()
        finally:
            conn.close()
        if answer["file"] is not None:
            answer["source"] = "index"
            answer["depth"] = max(1, min(int(depth), _index.MAX_IMPACT_DEPTH))
            answer["index_built_at"] = built_at[0] if built_at else None
            answer["stale"] = _index_is_behind_scan(root, answer["index_built_at"])
            return answer
        # Indexed, but this path is not in it. The graph may still know the
        # file -- the index drops folder nodes -- so fall through rather than
        # refusing, and let the graph path produce the error if it does not.

    return _impact_from_graph(root, normalized)


def _impact_from_graph(root: str | Path, normalized: str) -> dict[str, Any]:
    graph = load_graph(root)
    node = _node_for_path(graph, normalized)
    if node is None:
        raise ValueError(f"No graph node found for file: {normalized}")
    node_id = node.get("id")
    dependencies: list[dict[str, Any]] = []
    dependents: list[dict[str, Any]] = []
    nodes = {item.get("id"): item for item in graph.get("nodes", [])}
    for edge in graph.get("edges", []):
        if edge.get("kind") != "import":
            continue
        if edge.get("source") == node_id:
            target = nodes.get(edge.get("target"), {})
            dependencies.append({"path": target.get("path"), "depth": 1, "kind": edge.get("kind")})
        elif edge.get("target") == node_id:
            source = nodes.get(edge.get("source"), {})
            dependents.append({"path": source.get("path"), "depth": 1, "kind": edge.get("kind")})
    return {"file": node.get("path"), "dependencies": dependencies, "dependents": dependents,
            "source": "graph.json", "depth": 1, "truncated": False}


_BUILT_AT_PRECISION = datetime.timedelta(seconds=1)


def _index_is_behind_scan(root: str | Path, built_at: str | None) -> bool:
    """Whether scan output is newer than the index built from it.

    Compares against the mtime of graph.json, which `eos scan` writes on every
    run. Cheap enough to do on every call, which is the point: a reader that
    cannot afford to check will not check.
    """
    if not built_at:
        return False
    graph_path = Path(root).expanduser().resolve() / ".eos" / "data" / "brain" / "graph.json"
    try:
        written = datetime.datetime.fromtimestamp(graph_path.stat().st_mtime, datetime.timezone.utc)
        # built_at is recorded to whole seconds, and a scan writes graph.json
        # milliseconds before the index it then builds -- so an exact
        # comparison calls every fresh scan stale. One second of slack is the
        # truncation window, not a fudge factor.
        return written > datetime.datetime.fromisoformat(built_at) + _BUILT_AT_PRECISION
    except (OSError, ValueError):
        return False


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
    # The brain is five documents and four of them list the entry points, so a
    # plain concatenation repeats itself. Measured on one service: the phrase
    # "Entry Points" headed four separate sections of the composed context and
    # one controller's name appeared four times, for 9,350 of 20,804 characters
    # -- 45% of an agent's budget spent saying the same thing again.
    #
    # Keyed on the heading text, so the *first* document to cover a topic wins
    # and the rest are dropped whole. First, not best, deliberately: the brain
    # is generated in a fixed order, so which one wins is stable, and a
    # "pick the longest" rule would silently change what an agent reads
    # whenever a file grew.
    seen_headings: set[str] = set()
    for line in _read_brain(root).splitlines():
        if line.startswith("#"):
            heading = line.strip()
            normalized = _heading_key(heading)
            skipping = heading in DROPPED_BRAIN_SECTIONS or normalized in seen_headings
            if not skipping:
                seen_headings.add(normalized)
        if not skipping:
            kept.append(line)
    return "\n".join(kept).strip()


def _heading_key(heading: str) -> str:
    """A heading's identity, independent of level and of a parenthesised aside.

    "## Entry Points", "# Entry Points" and "## Entry Points (linked parent,
    not this project)" are the same topic covered by three documents.
    """
    text = heading.lstrip("#").strip().lower()
    return text.split("(")[0].strip()


def _target_facts(project: Path, target: str) -> str:
    """What is known about the target file, in prose rather than as a JSON dump.

    The old section was `json.dumps(impact(...))` -- two lists of paths, which
    an agent has to read in full to learn anything from. Everything the index
    has gained since then answers a question instead: what this file serves,
    what reaches it, what it can refuse with, and what the tests do about
    those refusals.

    Where the file throws nothing and reaches nothing, the section is omitted
    rather than printed empty: a heading with nothing under it costs budget and
    teaches the reader that the heading is not worth reading.
    """
    lines: list[str] = []
    try:
        reach = impact(project, target, depth=2)
    except ValueError:
        return ""

    dependents = reach.get("dependents") or []
    dependencies = reach.get("dependencies") or []
    if dependents:
        tests = [entry["path"] for entry in dependents if is_test_path(entry["path"])]
        lines.append(f"- Reached by {len(dependents)} file(s)"
                     + (f", {len(tests)} of them tests" if tests else ", none of them tests"))
        for entry in dependents[:5]:
            lines.append(f"  - {entry['path']}")
    if dependencies:
        lines.append(f"- Reaches {len(dependencies)} file(s) within 2 hops")

    try:
        codes = rules(project)
    except ValueError:
        codes = {"codes": []}
    mine = [entry for entry in codes["codes"]
            if any(str(reference).startswith(f"{target}:") for reference in entry["thrown_at"])]
    if mine:
        lines.append(f"- Can refuse with {len(mine)} behaviour code(s):")
        for entry in mine[:8]:
            lines.append(f"  - `{entry['code']}` — {entry['coverage']}"
                         + (f" ({entry['where'][0]})" if entry["where"] else ""))
        if len(mine) > 8:
            lines.append(f"  - …and {len(mine) - 8} more, see `eos rules`")

    if not lines:
        return ""
    return "## What Is Known About The Target\n\n" + "\n".join(lines)


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
        target_section = _target_facts(project, target)
        if target_section:
            sections.append(target_section)
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
        # An index built before provenance existed has no fact table. Say so in
        # a sentence that names the fix, rather than letting a raw
        # "no such table" reach an agent through an MCP tool result.
        present = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name IN ('fact', 'coverage')")}
        if not {"fact", "coverage"} <= present:
            raise ValueError(
                f"{database} predates provenance (schema {_index.SCHEMA_VERSION} expected). "
                "Run 'eos index' to rebuild it.")
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


def file_history(project_root: str | Path, relative_path: str, limit: int = 20) -> list[dict[str, Any]]:
    """Commits that touched one file, newest first, from the index.

    Returns an empty list when there is no index rather than raising: it is
    reached through an MCP tool that has to answer before the first scan.
    """
    from core import index as _index

    conn = _index.open_for_read(project_root)
    if conn is None:
        return []
    normalized = str(relative_path).replace("\\", "/").removeprefix("./")
    try:
        rows = conn.execute(
            "SELECT c.sha, c.authored_at, c.author_name, c.subject "
            "FROM git_commit_file f JOIN git_commit c ON c.sha = f.sha "
            "WHERE f.path = ? ORDER BY c.ord LIMIT ?", (normalized, max(1, int(limit)))).fetchall()
    except sqlite3.Error:
        return []
    finally:
        conn.close()
    return [dict(zip(("sha", "authored_at", "author", "subject"), row)) for row in rows]


def rules(project_root: str | Path, untested_only: bool = False) -> dict[str, Any]:
    """The behaviour identifiers this project throws, and whether a test names one.

    A refusal identified by a constant is the closest thing to a business rule
    that can be read out of source rather than inferred from it: the code is
    what the API returns, what a test asserts, and what a ticket quotes. So the
    question "which behaviours are not covered at all" has a deterministic
    answer -- is this code named anywhere under a test path -- with no
    judgement about what any test means.

    It is a floor, not coverage. A test naming the code proves the code is
    known to the suite, not that the path reaching it is exercised.
    """
    from core import index as _index

    conn = _index.open_for_read(project_root)
    if conn is None:
        raise ValueError(f"No index at {_index.db_path(project_root)}. Run 'eos index' first.")
    try:
        if not _index.has_tables(conn, ("fact",)):
            raise ValueError(f"{_index.db_path(project_root)} predates provenance. "
                             "Run 'eos index' to rebuild it.")
        # Whether the detector ever ran here, before reading what it produced.
        # A brain written by an older engine has no code facts and no coverage
        # row for them, and the two are different statements -- an empty answer
        # that does not say which is the failure this whole design exists to
        # prevent. Measured: a fresh session hit exactly this, was told nothing
        # actionable, and guessed at a rescan.
        looked = conn.execute(
            "SELECT 1 FROM coverage WHERE detector LIKE 'codes@%' LIMIT 1").fetchone()
        # No coverage row and no Java either means the detector does not apply
        # here, which is a different answer from a scan too old to have run it.
        applicable = conn.execute(
            "SELECT 1 FROM node WHERE language = 'java' LIMIT 1").fetchone()
        thrown = conn.execute(
            "SELECT f.object, n.path, f.source_ref FROM fact f JOIN node n ON n.id = f.subject "
            "WHERE f.predicate = 'throws-code' ORDER BY f.object, n.path").fetchall()
        detail = {}
        for row in conn.execute(
                "SELECT object FROM fact WHERE predicate = 'throws-code-at'").fetchall():
            code, _, rest = row[0].partition("|")
            where, _, exception = rest.partition("|")
            detail.setdefault(code, set()).add((where, exception))
        named = conn.execute(
            "SELECT DISTINCT f.object, n.path FROM fact f JOIN node n ON n.id = f.subject "
            "WHERE f.predicate IN ('names-code', 'throws-code')").fetchall()
        # Which test files have an edge into a throwing class. Naming a code
        # proves the suite knows the string; reaching the class that throws it
        # proves the suite runs that code at all, which is a different and
        # stronger statement.
        edges = conn.execute(
            "SELECT DISTINCT d.path, s.path FROM edge e "
            "JOIN node s ON s.nid = e.src JOIN node d ON d.nid = e.dst "
            "WHERE e.kind IN ('calls', 'new', 'field', 'import')").fetchall()
    finally:
        conn.close()

    reaching: dict[str, set[str]] = {}
    for target, source in edges:
        if is_test_path(source):
            reaching.setdefault(target, set()).add(source)

    # The top of the ladder is the only rung that is not static analysis: a
    # run someone recorded, with the command and the exit code behind it.
    from core import verification

    runs = verification.latest_by_code(project_root)

    mentions: dict[str, set[str]] = {}
    for code, path in named:
        if is_test_path(path):
            mentions.setdefault(code, set()).add(path)

    found: dict[str, dict[str, Any]] = {}
    for code, path, source_ref in thrown:
        entry = found.setdefault(code, {"code": code, "thrown_at": [], "where": [], "tests": [],
                                        "reached_by": []})
        entry["thrown_at"].append(source_ref or path)
        entry.setdefault("_paths", set()).add(path)
    for code, entry in found.items():
        entry["where"] = sorted(where for where, _ in detail.get(code, ()))
        entry["exceptions"] = sorted({ex for _, ex in detail.get(code, ()) if ex})
        entry["tests"] = sorted(mentions.get(code, ()))
        entry["reached_by"] = sorted({test for path in entry.pop("_paths", ())
                                      for test in reaching.get(path, ())})
        run = runs.get(code)
        entry["verification"] = run.to_dict() if run else None
        entry["coverage"] = _coverage_of(bool(entry["reached_by"]), bool(entry["tests"]),
                                         run.outcome if run else None)
    ordered = sorted(found.values(), key=lambda entry: (_COVERAGE_ORDER[entry["coverage"]],
                                                        entry["code"]))
    if untested_only:
        ordered = [entry for entry in ordered if entry["coverage"] == "none"]
    tally = {state: 0 for state in _COVERAGE_ORDER}  # noqa: E501
    for entry in found.values():
        tally[entry["coverage"]] += 1
    return {
        "codes": ordered,
        "total": len(found),
        "untested": tally["none"],
        "coverage": tally,
        "detector_ran": bool(looked),
        "detector_applies": bool(applicable),
    }


# How much a test suite is known to have to do with one refusal. Ordered worst
# first, because the list exists for the gap.
#
# Measured on one service with its parent, over 403 codes: 209 none, 134
# reachable, 9 named, 51 asserted. "reachable" is the interesting middle -- the
# class is exercised, and this particular branch is not checked -- and a binary
# tested/untested answer hides all 134 of them on one side or the other.
_COVERAGE_ORDER = {"none": 0, "named": 1, "reachable": 2, "asserted": 3, "verified": 4}


def _coverage_of(reached: bool, named: bool, outcome: str | None) -> str:
    # A recorded passing run outranks every static signal, because it is the
    # only one that observed the behaviour rather than inferring it from
    # structure -- but only where a test actually names the code.
    #
    # Without that condition a green run of a neighbouring test promotes a
    # refusal nobody asserted. It is not hypothetical: the first real run
    # tried here was a 16-test class that passes and never mentions the code
    # it was picked for, which is precisely the `reachable` state. Making the
    # rung conditional is safe by construction; a warning would have relied on
    # somebody reading it.
    #
    # A failing run does not demote the static reading: the test existing and
    # the test passing are different facts, and `eos findings` is where the
    # failure is read.
    if outcome == "passed" and reached and named:
        return "verified"
    if reached and named:
        return "asserted"
    if reached:
        return "reachable"
    if named:
        return "named"
    return "none"


def trace(project_root: str | Path, target: str, depth: int = 4) -> dict[str, Any]:
    """What one entry point reaches, and what the call graph cannot see from it.

    The second half is not a caveat, it is the finding. Measured on a real
    service: 235 files throw a behaviour code and only 6 of them are reachable
    from any of its 39 entry points, because 120 of the rest are components a
    runtime container looks up by name from configuration. On that shape of
    system the call graph is not the program, and a trace that reported only
    what it could follow would be a confident, incomplete answer.

    So every trace reports the runtime-wired population alongside it: classes
    registered under a name with no inbound call edge anywhere in the project.
    Those are where the chain continues.
    """
    from core import index as _index

    conn = _index.open_for_read(project_root)
    if conn is None:
        raise ValueError(f"No index at {_index.db_path(project_root)}. Run 'eos index' first.")
    try:
        if not _index.has_tables(conn, ("fact",)):
            raise ValueError(f"{_index.db_path(project_root)} predates provenance. "
                             "Run 'eos index' to rebuild it.")
        normalized = str(target).replace("\\", "/").removeprefix("./")
        reached = _index.impact_rows(conn, normalized, depth=depth)
        if reached["file"] is None:
            raise ValueError(f"No indexed node for: {target}")

        paths = [entry["path"] for entry in reached["dependencies"]]
        endpoints = _routes_for(conn, [normalized])
        codes = _codes_for(conn, [normalized, *paths])
        unreachable = _runtime_wired(conn)
    finally:
        conn.close()

    return {
        "file": reached["file"],
        "depth": reached.get("depth", depth),
        "reaches": reached["dependencies"],
        "endpoints": endpoints,
        "codes": codes,
        "runtime_wired": unreachable,
        "truncated": reached.get("truncated", False),
    }


def _routes_for(conn, paths: list[str]) -> list[str]:
    if not paths:
        return []
    placeholders = ",".join("?" * len(paths))
    rows = conn.execute(
        f"SELECT doc FROM node WHERE path IN ({placeholders}) AND doc IS NOT NULL", paths).fetchall()
    routes = []
    for (doc,) in rows:
        routes.extend(line.lstrip("- ").strip() for line in doc.splitlines() if line.startswith("- **"))
    return sorted(set(routes))


def _codes_for(conn, paths: list[str]) -> list[dict[str, Any]]:
    if not paths:
        return []
    placeholders = ",".join("?" * len(paths))
    rows = conn.execute(
        "SELECT DISTINCT f.object, n.path FROM fact f JOIN node n ON n.id = f.subject "
        f"WHERE f.predicate = 'throws-code' AND n.path IN ({placeholders}) "
        "ORDER BY f.object", paths).fetchall()
    return [{"code": code, "path": path} for code, path in rows]


def _runtime_wired(conn) -> dict[str, Any]:
    """Classes registered under a name that nothing in this project calls.

    Registered and uncalled is the signature of a component a container
    resolves at run time -- from a configuration row, an annotation scan, a
    service loader. The count is what tells a reader how much of the system a
    call-graph answer could not have covered.
    """
    named = {row[0]: row[1] for row in conn.execute(
        "SELECT n.path, f.object FROM fact f JOIN node n ON n.id = f.subject "
        "WHERE f.predicate = 'bean-name'").fetchall()}
    if not named:
        return {"total": 0, "uncalled": 0, "examples": []}
    called = {row[0] for row in conn.execute(
        "SELECT DISTINCT d.path FROM edge e JOIN node d ON d.nid = e.dst "
        "WHERE e.kind IN ('calls', 'new', 'field')").fetchall()}
    uncalled = sorted(path for path in named if path not in called)
    return {
        "total": len(named),
        "uncalled": len(uncalled),
        "examples": [{"path": path, "bean": named[path]} for path in uncalled[:5]],
    }


def questions(project_root: str | Path) -> dict[str, dict[str, Any]]:
    """Named questions this project's index extensions provide."""
    from core import extensions as _extensions
    from core import index as _index

    root = _index._project(project_root)
    found: dict[str, dict[str, Any]] = {}
    for extension in _extensions.load_all(root):
        for name, entry in extension.questions.items():
            found[name] = {"help": entry.get("help", ""), "sql": entry["sql"],
                           "extension": extension.name}
    return found


def ask(project_root: str | Path, name: str, argument: str | None = None) -> tuple[list[str], list[tuple]]:
    """Run one extension-provided question against the index."""
    from core import index as _index

    available = questions(project_root)
    entry = available.get(name)
    if entry is None:
        raise ValueError(
            f"No question named {name!r}. Available: {', '.join(sorted(available)) or 'none'}")
    sql = entry["sql"]
    wanted = sql.count("?")
    if wanted and argument is None:
        raise ValueError(f"Question {name!r} needs an argument: {entry['help'] or sql}")
    database = _index.db_path(project_root)
    if not database.is_file():
        raise ValueError(f"No index at {database}. Run 'eos index' first.")
    conn = _index.connect_read_only(database)
    try:
        cursor = conn.execute(sql, tuple([argument] * wanted))
        return [column[0] for column in cursor.description], cursor.fetchall()
    finally:
        conn.close()
