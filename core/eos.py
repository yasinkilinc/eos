#!/usr/bin/env python3
"""EOS CLI — local, stdlib-only project intelligence engine.

Commands:
  init      Create .eos/ in current directory.
  scan      Parse source files and regenerate brain/graph artifacts.
  update    Update the eos-core engine from the canonical runtime.
  doctor    Validate .eos/ integrity and report issues.
  info      Print summary about the local .eos instance.
  status    Print project status as JSON.
  graph     Export the generated project graph.
  context   Generate an AI-oriented project context.
  compose   Compose focused context for a task and target file.
  impact    Analyze direct import dependencies and dependents.
  mcp       Start the read-only stdio MCP server.
  bench     Measure EOS tools against plain alternatives on this project.
  ui        Start the multi-project dashboard (installs its own deps on request).
  index     Rebuild the queryable per-project database (.eos/data/eos.db).
  query     Run read-only SQL, or --search, against that database.
  clean     Remove generated artifacts (cache/brain/graph/index).
"""
import argparse
import dataclasses
import json
import shutil
import sqlite3
import subprocess
import sys
import uuid
from pathlib import Path

# Bootstrap so the same eos.py runs both from the dev repo (<repo>/core/eos.py)
# and from a deployed flat runtime (.eos/runtime/eos.py) without the dev repo.
# In deployed mode (parent dir holds id.txt) we alias the `core` package to
# this directory, so `from core.x import y` resolves to runtime siblings.
_HERE = Path(__file__).resolve().parent
if (_HERE.parent / "id.txt").exists():
    sys.path.insert(0, str(_HERE))
    import types as _types

    _core = _types.ModuleType("core")
    _core.__path__ = [str(_HERE)]
    sys.modules["core"] = _core
    _CORE_ROOT = _HERE.parent
else:
    if str(_HERE.parent) not in sys.path:
        sys.path.insert(0, str(_HERE.parent))
    _CORE_ROOT = _HERE.parent

# A harness hook runs on every tool call of every session, so `eos hook` is
# dispatched before the scanner, the generators and the index are imported --
# none of which it uses. Measured: the full import is most of `eos`'s startup.
if __name__ == "__main__" and sys.argv[1:2] == ["hook"]:
    from core import hooks as _hooks

    raise SystemExit(_hooks.main(sys.argv[2:]))

from core.lib.cache_store import CacheStore
from core.lib.config_io import ConfigIO
from core.knowledge.builder import KnowledgeBuilder
from core.scanner import Scanner
from core.generators.markdown.brain import BrainGenerator
from core.generators.json.evidence import EvidenceGenerator
from core.generators.json.graph_index import GraphIndexGenerator
from core.generators.ai.summary import AISummaryGenerator
from core import index
from core import inspector
from core import links
from core import notes
from core.plugins.registry import PluginRegistry


VERSION = (_HERE / "VERSION").read_text(encoding="utf-8").strip()
EOS_DIR = ".eos"


def _eos_dir(path: Path) -> Path:
    return path / EOS_DIR


def cmd_init(args: argparse.Namespace) -> int:
    root = Path(args.path).resolve()
    eos = _eos_dir(root)

    # A pre-existing .eos is not proof of a valid instance. An unrelated CLI
    # that also answers to "eos" writes its own .eos layout (context/, graphs/,
    # knowledge/, eos.db) with none of our markers, and returning 0 here left
    # such trees permanently unrepairable: scan/context/graph all succeed, but
    # id.txt is absent so the instance reports an empty instance_id and
    # engine_version "unknown", and `eos info` crashes outright.
    #
    # Repair what is missing instead, and never touch what is already there:
    # id.txt is the stable identity and is only ever created, never rewritten,
    # and unrelated content in .eos (developer notes, collections) is left
    # untouched.
    existed = eos.exists()

    runtime = eos / "runtime"
    for directory in (eos, runtime, eos / "data", eos / "data" / "cache", eos / "data" / "brain"):
        directory.mkdir(parents=True, exist_ok=True)

    id_file = eos / "id.txt"
    if id_file.is_file() and id_file.read_text(encoding="utf-8").strip():
        instance_id = id_file.read_text(encoding="utf-8").strip()
    else:
        instance_id = str(uuid.uuid4())
        id_file.write_text(instance_id + "\n", encoding="utf-8")

    if not (eos / "config.toml").is_file():
        config = {
            "project": {"name": root.name},
            "scan": {"max_depth": 15, "ignore": []},
        }
        ConfigIO.write_toml(eos / "config.toml", config)

    # Deploy the canonical runtime (core/) into .eos/runtime/ so the instance
    # is self-contained and `eos` commands work from the project folder
    # without the development repository on sys.path.
    from core.lib.updater import Updater

    Updater(canonical_core=_CORE_ROOT / "core", runtime_dir=runtime).update()

    verb = "Repaired" if existed else "Initialized"
    print(f"{verb} EOS instance {instance_id[:8]} at {eos}")

    if args.link_parent:
        # Store what the user typed. links.resolve_link_path already resolves a
        # relative path against the project root, and keeping it relative is
        # what lets .eos/config.toml be committed: an absolute path forces
        # every developer on a team to re-run init with their own layout.
        stored_path = Path(args.link_parent).expanduser()
        probe = (
            stored_path
            if stored_path.is_absolute()
            else (root / stored_path)
        ).resolve()
        if not probe.exists():
            print(f"  Warning: parent path does not exist yet: {probe}")
            print("  Link saved anyway -- fetch the parent repo first.")
        parent_path = stored_path
        label = args.link_label or "parent"
        # A plugin-supplied version marker only identifies the default
        # parent's baseline. A shared-library link has no such marker and
        # records no ref rather than an inherited one.
        ref = _parent_ref_for(root) if label == "parent" else None
        links.write_link(root, label, str(parent_path), "parent", ref=ref)
        print(f"  Linked [{label}]: {parent_path}")
        if ref:
            print(f"    product.version: {ref}")
        print("  Run `eos scan --with-parents` to index including linked projects.")

    if not getattr(args, "no_ai", False):
        from core.ai import writer

        surface = getattr(args, "surface", "cli")
        _store_surface(root, surface)
        claude = getattr(args, "claude", "files")
        _store_ai_key(root, "claude", claude)
        written = writer.write_all(root, VERSION, surface=surface,
                                   route_hook=_route_hook_wanted(root), claude=claude)
        print(f"  AI integration ({surface} surface):")
        for path in written:
            print(f"    {path.relative_to(root)}")
        _warn_stale_mcp(root, surface)
    return 0


def _store_surface(root: Path, surface: str) -> None:
    """Remember the choice, so `eos ai update` does not silently change it.

    Without this, an upgrade run without the flag would re-add an MCP
    registration a project deliberately removed -- and the cost of that is
    paid on every request of every session afterwards, which is exactly the
    failure mode nobody notices.
    """
    config_path = root / ".eos" / "config.toml"
    config = ConfigIO.read_toml(config_path) if config_path.is_file() else {}
    if config.get("ai", {}).get("surface") == surface:
        return
    config.setdefault("ai", {})["surface"] = surface
    ConfigIO.write_toml(config_path, config)


def _read_surface(root: Path) -> str:
    config_path = root / ".eos" / "config.toml"
    if not config_path.is_file():
        return "cli"
    try:
        configured = ConfigIO.read_toml(config_path).get("ai", {}).get("surface")
    except (OSError, ValueError):
        return "cli"
    from core.ai import writer

    return configured if configured in writer.SURFACES else "cli"


def _warn_stale_mcp(root: Path, surface: str) -> None:
    from core.ai import writer

    if surface == "cli" and writer.mcp_registered(root):
        # Never deleted on the project's behalf: .mcp.json is the user's file
        # and another tool may be reading it. Said out loud, because a roster
        # nobody uses is invisible and is charged for every session.
        print("  note: .mcp.json still registers `eos`. On the cli surface nothing "
              "reads it, and its tool roster is charged to every session -- remove "
              "mcpServers.eos to stop paying for it.")


def _parent_ref_for(root: Path) -> str | None:
    """Ask the detected language plugins for a parent baseline marker."""
    for plugin in PluginRegistry.detect_languages(root):
        ref = plugin.parent_ref(root)
        if ref:
            return ref
    return None


def cmd_scan(args: argparse.Namespace) -> int:
    root = Path(args.path).resolve()
    eos = _eos_dir(root)
    if not eos.exists():
        print(f"No .eos directory found at {root}. Run 'eos init' first.")
        return 1

    cache = CacheStore(eos / "data" / "cache")
    scanner = Scanner(root, cache)
    if args.with_parents:
        configured = links.read_links(root)
        parent_roots = {
            label: links.resolve_link_path(root, link)
            for label, link in configured.items()
            if link.role == "parent"
        }
        project = scanner.scan_with_links(parent_roots, full=args.full)
    else:
        project = scanner.scan(full=args.full)

    graph = KnowledgeBuilder().build(project, root=root)

    brain_dir = eos / "data" / "brain"
    BrainGenerator(graph).generate(brain_dir)
    GraphIndexGenerator(graph).generate(brain_dir / "graph.json")
    AISummaryGenerator(graph).generate(brain_dir / "AI_SUMMARY.md")
    # Written before last_scan.json, and its counts recorded there: that is what
    # lets the index refuse a sidecar a killed scan left half-written, instead
    # of confidently reporting provenance for edges from a previous graph.
    evidence_counts = EvidenceGenerator(graph, project.report, VERSION).generate(
        brain_dir / "evidence.jsonl")

    # Update metadata
    metadata = {
        "languages": project.detected_languages,
        "files_parsed": len(project.files),
        "nodes": len(graph.nodes),
        "edges": len(graph.edges),
        "facts": evidence_counts["facts"],
        "coverage": evidence_counts["coverage"],
    }
    (eos / "data" / "last_scan.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")

    # Built from what was just written. The index is derived, so a failure is
    # reported in one line and does not fail a scan whose brain is on disk:
    # ensure-eos-mcp.sh and the watcher read a nonzero exit as "no brain".
    indexed = None
    try:
        indexed = _describe_index(index.build(root))
    except Exception as exc:
        print(f"Index not rebuilt: {type(exc).__name__}: {exc}", file=sys.stderr)

    print(f"Scanned {len(project.files)} files across {project.detected_languages}")

    # What was left out, and why. Without these two lines a scan that dropped
    # 730 real source modules under build/ printed the same thing as a scan
    # that had nothing to drop.
    report = project.report
    if report.skipped_by_ignore:
        ranked = sorted(report.skipped_by_ignore.items(), key=lambda kv: (-kv[1], kv[0]))
        shown = ", ".join(f"{name} ({count})" for name, count in ranked[:5])
        total = sum(report.skipped_by_ignore.values())
        more = "" if len(ranked) <= 5 else f", +{len(ranked) - 5} more rule(s)"
        print(f"Skipped {total} file(s) by ignore rules: {shown}{more}")
        print("  Un-ignore a default with [scan] unignore in .eos/config.toml")
    if report.unresolved_imports:
        ranked = sorted(report.unresolved_imports.items(), key=lambda kv: (-kv[1], kv[0]))
        shown = ", ".join(f"{kind} ({count})" for kind, count in ranked)
        total = sum(report.unresolved_imports.values())
        print(f"{total} import(s) named something not found in the project: {shown}")
    if report.unsupported_extensions:
        ranked = sorted(report.unsupported_extensions.items(), key=lambda kv: (-kv[1], kv[0]))
        shown = ", ".join(f"{ext} ({count})" for ext, count in ranked[:5])
        total = sum(report.unsupported_extensions.values())
        print(f"{total} file(s) in languages EOS does not index: {shown}")

    print(f"Generated {len(graph.nodes)} nodes, {len(graph.edges)} edges")
    print(f"Brain written to {brain_dir}")
    if indexed:
        print(indexed)
    return 0


def cmd_update(args: argparse.Namespace) -> int:
    from core.lib.updater import Updater

    root = Path(args.path).resolve()
    eos = _eos_dir(root)
    if not eos.exists():
        print(f"No .eos directory found at {root}. Run 'eos init' first.")
        return 1

    canonical_core = _CORE_ROOT / "core"
    runtime_dir = eos / "runtime"
    updater = Updater(canonical_core=canonical_core, runtime_dir=runtime_dir)

    if args.dry_run:
        result = updater.update(dry_run=True)
        print(f"Dry run: {len(result.get('would_copy', []))} to copy, {len(result.get('would_delete', []))} to delete")
        if result.get("would_copy"):
            print("  + / ~  (add/change)")
            for f in result["would_copy"]:
                print(f"    {f}")
        if result.get("would_delete"):
            print("  - (remove)")
            for f in result["would_delete"]:
                print(f"    {f}")
        return 0

    result = updater.update()
    version = updater._read_version()
    print(f"Engine updated to {version}")
    added = len(result["added"])
    changed = len(result["changed"])
    removed = len(result["removed"])
    unchanged = len(result["unchanged"])
    print(f"  +{added} added  ~{changed} changed  -{removed} removed  ({unchanged} unchanged)")
    if not (added or changed or removed):
        print("  Runtime already up to date.")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    from core import preflight

    if getattr(args, "memory", False):
        # Where the operational-memory plan stands, for this project, as the
        # matrix the plan is judged by. Read-only. Exit 1 while any row is
        # open, so a script can ask the same question a session does.
        from core import memory_audit

        results = memory_audit.run(args.path)
        if args.format == "json":
            print(json.dumps([r.__dict__ for r in results], indent=2))
        else:
            print(memory_audit.render(results))
        return 0 if memory_audit.complete(results) else 1

    print("Dependencies:")
    blocking = []
    for req in preflight.report():
        mark = "ok  " if req.ok else ("FAIL" if req.tier == 1 else "warn")
        print(f"  {mark}  {req.name:8} {req.found or 'not found'}")
        if not req.ok and req.tier == 1:
            blocking.append(req)
    if blocking:
        print()
        for req in blocking:
            print(f"eos: {req.name} is required but unusable ({req.found or 'not found'})")
            print(req.hint)
        return 1
    print()

    root = Path(args.path).resolve()
    eos = _eos_dir(root)
    issues = []

    if not eos.exists():
        issues.append(".eos directory missing")
    else:
        if not (eos / "id.txt").exists():
            issues.append("id.txt missing")
        if not (eos / "runtime" / "VERSION").exists():
            issues.append("runtime/VERSION missing")
        if not (eos / "runtime" / "manifest.json").exists():
            issues.append("runtime/manifest.json missing")
        if not (eos / "config.toml").exists():
            issues.append("config.toml missing")

    if issues:
        print("Issues found:")
        for issue in issues:
            print(f"  - {issue}")
        return 1

    instance_id = (eos / "id.txt").read_text(encoding="utf-8").strip()
    version = (eos / "runtime" / "VERSION").read_text(encoding="utf-8").strip()
    print(f"EOS instance {instance_id[:8]} is healthy (engine {version})")

    # Not fatal: a fresh project's .eos/ is gitignored by default, so this is
    # the expected state until someone deliberately shares notes. It is worth
    # a warning because that default falls out of two independent choices
    # (knowledge_dir defaults inside .eos/, and .eos/ is commonly gitignored
    # wholesale) that nobody chose together, and it stays silent forever
    # unless something says so.
    if notes.is_notes_dir_gitignored(root):
        print(
            f"Warning: notes directory ({notes.notes_dir(root)}) is gitignored; "
            "notes recorded there will never be shared. Set [knowledge] dir in "
            "config.toml to a tracked path if notes should be committed."
        )

    # Also not fatal: a stale scope entry means the note's claim may no
    # longer hold, not that the instance is broken -- separate from `issues`
    # on purpose, printed alongside with the exit code left exactly as it
    # was. One block, not two: hand-written entries are listed in detail
    # (they are what an agent has to read and fix), generated ones are
    # named only by count with their own remedy (regenerate; do not edit by
    # hand) -- one pom.xml bump goes stale on six of them at once, and
    # listing each would bury the hand-written ones that actually need
    # reading.
    stale = notes.stale_notes(root)
    if stale:
        handwritten = [entry for entry in stale if not entry["generated"]]
        generated = [entry for entry in stale if entry["generated"]]
        print("Warning: notes with a stale scope (recorded code has changed):")
        for entry in handwritten:
            for issue in entry["issues"]:
                print(f"  - {entry['note']}: {issue['scope']} ({issue['reason']})")
        if generated:
            print(f"  - {len(generated)} generated note(s) also stale; regenerate them, do not edit by hand")
        print("  Run `eos note audit` for the full list.")

    _report_ledger(root)
    return 0


def _report_ledger(root: Path) -> None:
    """What the work ledger says about itself, in a doctor that is not fatal.

    Three states go wrong quietly and each has a different fix. A ledger
    nothing else can read answers "is anyone on this" with a confident no. A
    contested item is two agents spending two sessions on one piece of work. A
    stale claim is neither: it is a session that may simply be gone, and the
    only safe move is to say so to whoever reads it next.
    """
    import datetime

    from core import work

    state = work.sync_state(root)
    if state["state"] in ("ignored", "untracked", "uncommitted", "unpushed"):
        print(f"Warning: {work.sync_sentence(state)} ({state['path']})")

    in_flight = work.items(root)
    if not in_flight:
        return
    now = datetime.datetime.now(datetime.timezone.utc)
    contested = [item for item in in_flight if item.contested]
    stale = [item for item in in_flight if item.is_stale(now)]
    if contested:
        print("Warning: work items held by more than one session at once:")
        for item in contested:
            print(f"  - {item.id}: {item.title} ({' and '.join(item.holder_labels)})")
    if stale:
        print(f"Warning: {len(stale)} claim(s) with no event for over "
              f"{work.STALE_AFTER_HOURS}h -- the session holding them may be gone:")
        for item in stale[:5]:
            print(f"  - {item.id}: {item.title} ({' and '.join(item.holder_labels) or 'unclaimed'})")
        if len(stale) > 5:
            print(f"  - ...and {len(stale) - 5} more; `eos work list {root}` lists them")


def cmd_info(args: argparse.Namespace) -> int:
    root = Path(args.path).resolve()
    eos = _eos_dir(root)
    if not eos.exists():
        print(f"No .eos directory at {root}")
        return 1

    instance_id = (eos / "id.txt").read_text(encoding="utf-8").strip()
    version = (eos / "runtime" / "VERSION").read_text(encoding="utf-8").strip()
    last_scan_path = eos / "data" / "last_scan.json"
    last_scan = json.loads(last_scan_path.read_text(encoding="utf-8")) if last_scan_path.exists() else {}

    print(f"EOS Instance: {instance_id}")
    print(f"Engine Version: {version}")
    print(f"Project Path: {root}")
    if last_scan:
        print(f"Last Scan: {last_scan.get('files_parsed', 0)} files, {last_scan.get('nodes', 0)} nodes, {last_scan.get('edges', 0)} edges")
    else:
        print("Last Scan: never")
    return 0


def cmd_clean(args: argparse.Namespace) -> int:
    root = Path(args.path).resolve()
    eos = _eos_dir(root)
    if not eos.exists():
        print(f"No .eos directory at {root}")
        return 1

    import shutil
    for subdir in ("data/cache", "data/brain"):
        target = eos / subdir
        if target.exists():
            shutil.rmtree(target)
            target.mkdir(parents=True)
            print(f"Cleaned {target}")

    last_scan = eos / "data" / "last_scan.json"
    if last_scan.exists():
        last_scan.unlink()

    database = index.db_path(root)
    if database.exists():
        database.unlink()
        print(f"Cleaned {database}")
    return 0


def _describe_index(result: index.BuildResult) -> str:
    counts = ", ".join(f"{count} {label}" for label, count in result.counts.items())
    search = "fts5" if result.fts5 else "LIKE, this SQLite has no FTS5"
    lines = [
        f"Index written to {result.path} ({counts}; search: {search}; "
        f"{result.size / 1_048_576:.1f} MB in {result.seconds:.2f}s)"
    ]
    if result.issues:
        lines.append(f"  {len(result.issues)} source(s) left out, see the build_issue table:")
        for source, ref, problem in result.issues[:5]:
            lines.append(f"    {source} {ref}: {problem}" if ref else f"    {source}: {problem}")
        if len(result.issues) > 5:
            lines.append(f"    ... and {len(result.issues) - 5} more")
    return "\n".join(lines)


def _escape_what_stdout_cannot_encode() -> None:
    # Commit subjects, notes, brain and journey docs carry emoji, arrows and
    # Turkish letters. Where stdout is not UTF-8 -- Windows when output is piped
    # or redirected, which is how an agent reads it -- print() raised
    # UnicodeEncodeError on the first of them.
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(errors="backslashreplace")


def cmd_index(args: argparse.Namespace) -> int:
    _escape_what_stdout_cannot_encode()
    try:
        result = index.build(args.path)
    except index.IndexBuildError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(_describe_index(result))
    return 0


def _query_cell(value) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bytes):
        return value.hex()
    return str(value).replace("\t", "\\t").replace("\r", "\\r").replace("\n", "\\n")


def cmd_query(args: argparse.Namespace) -> int:
    _escape_what_stdout_cannot_encode()
    database = index.db_path(args.path)
    if not database.is_file():
        root = Path(args.path).expanduser().resolve()
        print(f"error: no index at {database}; build it with `eos index {root}`", file=sys.stderr)
        return 1
    if (args.sql is None) == (args.search is None):
        print("error: give either one SQL statement or --search, not both", file=sys.stderr)
        return 1
    try:
        rebuilt = index.refresh(args.path)
    except (index.IndexBuildError, OSError, ValueError, sqlite3.Error) as exc:
        # ValueError: a .eos/config.toml that no longer parses.
        try:
            built_at = index.run_query(database, "SELECT value FROM meta WHERE key = 'built_at'")[1][0][0]
        except (sqlite3.Error, IndexError):
            built_at = "unknown"
        print(f"warning: answering from a possibly stale index (built {built_at}); "
              f"could not bring it up to date: {exc}", file=sys.stderr)
    else:
        if rebuilt is not None:
            print(f"Index rebuilt before answering: its sources changed since it was built ({rebuilt.seconds:.2f}s)",
                  file=sys.stderr)
    try:
        if args.search is not None:
            columns, rows = index.search(database, args.search, limit=args.limit)
        else:
            columns, rows = index.run_query(database, args.sql)
    except sqlite3.Error as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if columns:
        print("\t".join(columns))
    for row in rows:
        print("\t".join(_query_cell(value) for value in row))
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    summary = inspector.project_summary(args.path)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


def cmd_graph(args: argparse.Namespace) -> int:
    graph = inspector.load_graph(args.path)
    if args.type != "all":
        graph = {**graph, "edges": [edge for edge in graph["edges"] if edge.get("kind") == args.type]}
    output = args.output
    if output == "-":
        print(json.dumps(graph, indent=2, ensure_ascii=False))
        return 0
    if output:
        output_path = Path(output).expanduser().resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(graph, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"Graph written to {output_path}")
    else:
        print(f"Graph contains {len(graph['nodes'])} nodes and {len(graph['edges'])} {args.type} edges")
    return 0


def cmd_context(args: argparse.Namespace) -> int:
    context = inspector.build_context(args.path, budget=args.budget)
    root = inspector.require_project(args.path)
    if args.stdout:
        # A reader asking what the context says should not have to write a
        # file to find out. An eval session ran this under an instruction not
        # to modify anything and then had to go and check whether .eos/ was
        # ignored: the surprise was the problem, not the byte.
        from core import telemetry

        telemetry.declare_answer_size(len(context))
        print(context)
        return 0
    output_path = root / ".eos" / "data" / "brain" / "llm_context.md"
    output_path.write_text(context, encoding="utf-8")
    # The answer is the file, not the line about it. Without this the most
    # expensive call in the system is recorded as the cheapest.
    from core import telemetry

    telemetry.declare_answer_size(len(context))
    print(f"Context written to {output_path} ({len(context)} characters)")
    return 0


def cmd_compose(args: argparse.Namespace) -> int:
    print(inspector.compose(args.path, args.task, args.target, budget=args.budget))
    return 0


def cmd_impact(args: argparse.Namespace) -> int:
    if args.include:
        # Provenance lives in the index, so make sure it is not older than the
        # scan it describes. Plain `eos impact` deliberately does not: it is
        # also reached through an MCP call, where a rebuild is the wrong
        # amount of work to do inside a request.
        try:
            index.refresh(args.path)
        except (index.IndexBuildError, OSError, ValueError, sqlite3.Error) as exc:
            print(f"warning: index may be stale ({type(exc).__name__}: {exc})", file=sys.stderr)

    answer = inspector.impact(args.path, args.file, depth=args.depth)
    if args.include:
        try:
            detail = inspector.why(args.path, args.file)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        if "facts" in args.include:
            answer["facts"] = detail["facts"]
            answer["inbound_facts"] = detail["inbound"]
        if "coverage" in args.include:
            answer["coverage"] = detail["coverage"]
        if "history" in args.include:
            answer["history"] = inspector.file_history(args.path, args.file)
    print(json.dumps(answer, indent=2, ensure_ascii=False))
    return 0


def cmd_why(args: argparse.Namespace) -> int:
    """Print where a fact came from, and what nothing looked for."""
    try:
        rebuilt = index.refresh(args.path)
    except (index.IndexBuildError, OSError, ValueError, sqlite3.Error) as exc:
        print(f"warning: index may be stale ({type(exc).__name__}: {exc})", file=sys.stderr)
    else:
        if rebuilt is not None:
            print("note: index rebuilt from changed sources", file=sys.stderr)

    try:
        answer = inspector.why(args.path, args.subject, args.predicate, args.min_confidence)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.format == "json":
        print(json.dumps(answer, indent=2, ensure_ascii=False))
        return 0

    if answer["subject"]:
        print(answer["subject"])
        for entry in answer["facts"]:
            _print_fact(entry, "  ")
        for entry in answer["inbound"]:
            _print_fact(entry, "  <- ")
        if not answer["facts"] and not answer["inbound"]:
            print("  (no facts recorded)")
    # Always: the question "did you not find it, or did you not look" has to be
    # answerable at the moment it is asked, not by reading a separate table.
    for entry in answer["coverage"]:
        found = f"{entry['hits']} in {entry['files_with_hits']} file(s)" if entry["hits"] else "nothing"
        line = (f"  coverage: {entry['detector']} looked at {entry['files_eligible']} file(s) "
                f"for {entry['predicate']}, found {found}")
        # The whole question is about the one file that was asked about, and a
        # project-wide count does not answer it. Say what happened here.
        verdict = entry.get("applies_here")
        if verdict == "yes" and entry.get("hits_here"):
            line += f"; {entry['hits_here']} here"
        elif verdict == "yes":
            line += "; examined this file, found nothing here"
        elif verdict == "no":
            line += "; does not read files like this one"
        elif verdict == "structural":
            line += "; records structure, not file contents, so it has no per-file answer"
        elif verdict == "unknown":
            line += "; produced nothing anywhere, so whether it reads this file is not derivable"
        print(line)
    return 0


def cmd_rules(args: argparse.Namespace) -> int:
    """List the behaviour identifiers this project throws, and what names them."""
    try:
        index.refresh(args.path)
    except (index.IndexBuildError, OSError, ValueError, sqlite3.Error) as exc:
        print(f"warning: index may be stale ({type(exc).__name__}: {exc})", file=sys.stderr)
    try:
        answer = inspector.rules(args.path, untested_only=args.untested)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.format == "json":
        print(json.dumps(answer, indent=2, ensure_ascii=False))
        return 0

    if not answer["codes"]:
        if not answer.get("detector_ran") and not answer.get("detector_applies"):
            print("Nothing here raises a refusal identified by a constant: the detector "
                  "reads Java, and this project has none indexed.")
            return 0
        if not answer.get("detector_ran"):
            # Actionable, not a pointer. The scan output this index was built
            # from predates behaviour-code extraction, and rebuilding the index
            # cannot invent facts a scan never wrote.
            print("This project's scan output predates behaviour-code extraction, so "
                  "nothing looked for them. Run `eos scan` (not `eos index`: rebuilding "
                  "the index re-reads the same scan output).", file=sys.stderr)
            return 1
        if args.untested and answer.get("total"):
            # The filter emptied the list, not the detector -- and saying "found
            # no behaviour codes" when 70 are indexed is the same silence-versus-
            # absence failure one level up. A session hit exactly this: it read
            # the answer as the command being broken for this project, re-ran it
            # four ways, and only settled it by running the unflagged form and
            # finding the codes it had just been told did not exist.
            tally = answer.get("coverage", {})
            graded = ", ".join(f"{count} {grade}" for grade, count in tally.items() if count)
            print(f"All {answer['total']} behaviour code(s) here are named by at least "
                  f"one test, so --untested matches none of them ({graded}). "
                  "Drop --untested to see them, or read the grades: only `none` means "
                  "no test names the code at all.")
            return 0
        print("The detector ran and found no behaviour codes: nothing here raises a "
              "refusal identified by a constant. `eos why` shows what it examined.")
        return 0
    marks = {"none": "!", "named": "~", "reachable": "-", "asserted": " ", "verified": "+"}
    # Bounded by default, and the bound is not a style choice. Telemetry on its
    # first day measured the unbounded listing at 37,874 tokens on one service
    # -- for a command the skill tells agents to run before writing a test.
    # A habit that costs a third of a context window is not a habit anyone
    # keeps. The tally below is the part worth reading; the list is a sample of
    # the worst, and --format json is still complete for a machine.
    shown = answer["codes"] if args.limit <= 0 else answer["codes"][:args.limit]
    for entry in shown:
        where = ", ".join(entry["where"][:2]) or "?"
        print(f"{marks[entry['coverage']]} {entry['code']:<40} {entry['coverage']:<10} {where}")
        for reference in entry["thrown_at"][:3]:
            print(f"    thrown at {reference}")
        if entry["reached_by"]:
            print(f"    reached by {len(entry['reached_by'])} test file(s): "
                  f"{', '.join(Path(p).name for p in entry['reached_by'][:2])}")
        if entry["tests"]:
            print(f"    named by {len(entry['tests'])} test file(s): "
                  f"{', '.join(Path(p).name for p in entry['tests'][:2])}")
        run = entry.get("verification")
        if run:
            print(f"    last run {run['outcome']} (exit {run['exit_code']}) at {run['recorded_at']}"
                  + (f", verdict {run['verdict']}" if run.get("verdict") else ""))
        if not entry["reached_by"] and not entry["tests"] and not run:
            print("    no test reaches the class or names the code")

    hidden = len(answer["codes"]) - len(shown)
    if hidden:
        print(f"\n… and {hidden} more, least covered first. `--limit 0` for all of them, "
              "`--format json` for every field.")

    tally = answer["coverage"]
    print(f"\n{answer['total']} code(s):")
    print(f"  verified   {tally['verified']:>4}  a recorded run passed (the only rung that is not analysis)")
    print(f"  asserted   {tally['asserted']:>4}  a test reaches the class and names the code")
    print(f"  reachable  {tally['reachable']:>4}  the class is exercised, this refusal is not asserted")
    print(f"  named      {tally['named']:>4}  the code is named, nothing touches the class")
    print(f"  none       {tally['none']:>4}  no test reaches the class or names the code")
    print("\nStill a floor: reaching a class is not the same as exercising the branch "
          "that raises the code.")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    """Record what an adapter ran for one behaviour code, and what happened.

    EOS does not run the test. Executing one needs a build tool, an
    environment and minutes, and core/ is stdlib-only by design; the workspace
    already has a wrapper that keeps the log and prints a digest. This records
    the evidence that wrapper produced.
    """
    from core import verification

    output = None
    if args.output == "-":
        output = sys.stdin.read()
    elif args.output:
        try:
            output = Path(args.output).read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            print(f"error: cannot read {args.output} ({exc})", file=sys.stderr)
            return 1
    # ADR-024: inside an open run, the check belongs to it.
    from core import executions, telemetry
    session = getattr(args, "session", None) or telemetry.detect_session(args.path)[0]
    current = executions.current(args.path, session)
    execution = current[0] if current else None
    try:
        entry = verification.record(
            args.path, args.code, args.outcome, args.ran, exit_code=args.exit_code,
            log=args.log, output=output, verdict=args.verdict, note=args.note,
            session=session, execution=execution)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if execution:
        try:
            executions.event(args.path, execution, kind="verified", tool="eos verify",
                             ref=f"{entry.code}:{entry.outcome}", exit_code=entry.exit_code,
                             session=session)
        except (ValueError, OSError):
            pass  # the verification is recorded; the timeline line is a convenience

    print(f"recorded {entry.outcome} for {entry.code} at {entry.recorded_at}")
    print(f"  {entry.command}")
    if entry.commit:
        print(f"  commit {entry.commit[:12]}")
    if entry.verdict is None and entry.outcome != verification.PASSED:
        # Said once, here, rather than inferred anywhere: an exit code does not
        # separate "the rule is not enforced" from "the test is wrong" from
        # "the environment was".
        print("  no verdict recorded. A failing run does not say which of a wrong rule, "
              f"a wrong test or a bad environment it was -- pass --verdict when you know "
              f"({', '.join(verification.VERDICTS)}).", file=sys.stderr)
    return 0


def cmd_cost(args: argparse.Namespace) -> int:
    """What EOS has cost this project, per command."""
    from core import telemetry

    if not telemetry.enabled(args.path):
        print("Telemetry is off. Turn it on with [telemetry] enabled = true in "
              f"{Path(args.path) / '.eos' / 'config.toml'} — it records the command, "
              "the flag names, the milliseconds and the size of the answer, never "
              "what was asked.")
        return 0

    report = telemetry.summary(args.path)
    if args.format == "json":
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0
    if not report["calls"]:
        print("Telemetry is on, and nothing has been recorded yet.")
        return 0

    print(f"{report['calls']} call(s) since {report['since']}, "
          f"~{report['tokens']} token(s) returned in total (estimated at 4 chars each)")
    print(f"{'command':<16}{'calls':>7}{'median ms':>11}{'median tok':>12}{'rebuilds':>10}{'failed':>8}")
    for row in report["commands"]:
        print(f"{row['command']:<16}{row['calls']:>7}{row['median_ms']:>11}"
              f"{row['median_tokens']:>12}{row['rebuilt']:>10}{row['failed']:>8}")

    # Calls are what this file holds; sessions are what the question was
    # always about. A tool called twelve times by one session and never by
    # eleven others is not a tool anyone adopted, and the per-command table
    # above cannot tell that apart from steady use.
    sessions = report["sessions"]
    if sessions["sessions"]:
        share = round(100 * sessions["beyond_opening"] / sessions["sessions"])
        print(f"\n{sessions['sessions']} session(s) identified themselves; "
              f"{sessions['beyond_opening']} of them ({share}%) called EOS for something "
              f"beyond the opening brief.")
        print(f"Calls per session: median {sessions['median_calls']}, "
              f"most {sessions['max_calls']}.")
    if sessions["closed_sessions"]:
        # The outcome number, not a usage one: it should fall as sessions
        # learn to close what they took, and a rise is worth acting on.
        print(f"{sessions['closed_sessions']} session(s) were asked what happened to "
              f"their claims on the way out; {sessions['left_work_open']} of them still "
              "held work at that point.")
    if sessions["failed_openings"]:
        # The number that makes the one above trustworthy. From a percentage
        # alone, a hook that cannot run EOS at all is indistinguishable from a
        # project where everything is working.
        print(f"{sessions['failed_openings']} session(s) could not produce the opening "
              "brief: the hook ran and EOS could not be reached. Check that `eos` is on "
              f"PATH, and that `eos update {args.path}` has refreshed this project's "
              "runtime copy.")
    if sessions["attributed_by"]:
        # Which variable did the attributing, because "the harness told us"
        # and "somebody passed a flag by hand" are different levels of trust
        # in the same number.
        named = ", ".join(f"{name} ({count})" for name, count
                          in sorted(sessions["attributed_by"].items()))
        print(f"Sessions identified by: {named}.")
    if sessions["unattributed_calls"]:
        print(f"{sessions['unattributed_calls']} call(s) carried no session id and are "
              "counted above but belong to no session. Claude Code is read from "
              "$CLAUDE_CODE_SESSION_ID automatically; another harness exports "
              "$EOS_SESSION, or names its own variable in [telemetry] session_env.")
    return 0


def cmd_findings(args: argparse.Namespace) -> int:
    """Recorded runs, newest first, and what they were judged to be."""
    from core import verification

    records = verification.load(args.path)
    if args.failed_only:
        records = [entry for entry in records if entry.outcome != verification.PASSED]
    if args.format == "json":
        print(json.dumps([entry.to_dict() for entry in records], indent=2, ensure_ascii=False))
        return 0
    if not records:
        print("No run has been recorded here. `eos verify` records one; "
              "`eos draft-test` drafts a skeleton to start the test from.")
        return 0
    for entry in reversed(records):
        verdict = entry.verdict or ("-" if entry.outcome == verification.PASSED else "no verdict yet")
        print(f"{entry.recorded_at}  {entry.outcome:<8} {entry.code:<40} {verdict}")
        print(f"    {entry.command}")
        if entry.log:
            print(f"    log {entry.log}")
        if entry.note:
            print(f"    {entry.note}")
    summary = verification.summary(args.path)
    print(f"\n{summary['runs']} run(s) over {summary['codes']} code(s): "
          f"{summary['outcomes']}" + (f", verdicts {summary['verdicts']}" if summary["verdicts"] else ""))
    return 0


def cmd_draft_test(args: argparse.Namespace) -> int:
    """Draft a test for a refusal the suite does not assert."""
    from core import testgen

    try:
        index.refresh(args.path)
    except (index.IndexBuildError, OSError, ValueError, sqlite3.Error) as exc:
        print(f"warning: index may be stale ({type(exc).__name__}: {exc})", file=sys.stderr)
    try:
        draft = testgen.draft_for(args.path, args.code)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.format == "json":
        print(json.dumps(draft.to_dict(), indent=2, ensure_ascii=False))
        return 0 if draft.status == testgen.STATUS_DRAFT else 1

    if draft.status != testgen.STATUS_DRAFT:
        print(f"refused: {draft.refused}", file=sys.stderr)
        return 1

    where = "joins" if draft.test_exists else "would create"
    print(f"# draft for {draft.code}")
    print(f"# {where}: {draft.test_path}")
    print(f"# thrown by:  {draft.source_ref}")
    print(f"# style read from: {draft.style.get('source')}")
    for fact in draft.grounded:
        print(f"# grounded:  {fact}")
    print()
    print(draft.body)
    if args.write:
        target = testgen.write_draft(args.path, draft)
        print(f"\nWritten to {target}", file=sys.stderr)
    print("\n# Not compiled, not run, not reviewed. Make it fail for the right "
          "reason before making it pass.", file=sys.stderr)
    return 0


def cmd_ask(args: argparse.Namespace) -> int:
    """Run a question this project's index extensions provide."""
    try:
        available = inspector.questions(args.path)
    except Exception as exc:  # noqa: BLE001 - a misconfigured extension, reported in one line
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if not args.name:
        if not available:
            print("No extension provides a question here. `eos why` reports "
                  "which extensions ran at all.")
            return 0
        for name in sorted(available):
            entry = available[name]
            print(f"{name:<24} {entry['help'] or entry['sql'].split(chr(10))[0]}")
            print(f"{'':<24} from {entry['extension']}")
        return 0

    try:
        index.refresh(args.path)
    except (index.IndexBuildError, OSError, ValueError, sqlite3.Error) as exc:
        print(f"warning: index may be stale ({type(exc).__name__}: {exc})", file=sys.stderr)
    try:
        columns, rows = inspector.ask(args.path, args.name, args.argument)
    except (ValueError, sqlite3.Error) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.format == "json":
        print(json.dumps([dict(zip(columns, row)) for row in rows], indent=2, ensure_ascii=False))
        return 0
    print("\t".join(columns))
    for row in rows:
        print("\t".join("" if value is None else str(value) for value in row))
    return 0


def cmd_trace(args: argparse.Namespace) -> int:
    """What an entry point reaches, and what the call graph cannot see from it."""
    try:
        index.refresh(args.path)
    except (index.IndexBuildError, OSError, ValueError, sqlite3.Error) as exc:
        print(f"warning: index may be stale ({type(exc).__name__}: {exc})", file=sys.stderr)
    try:
        answer = inspector.trace(args.path, args.file, depth=args.depth)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.format == "json":
        print(json.dumps(answer, indent=2, ensure_ascii=False))
        return 0

    print(answer["file"])
    for route in answer["endpoints"]:
        print(f"  serves {route}")
    print(f"  reaches {len(answer['reaches'])} file(s) within {answer['depth']} hop(s)"
          + (" (truncated)" if answer["truncated"] else ""))
    for entry in answer["reaches"][:10]:
        print(f"    {entry['depth']}  {entry['path']}")
    if len(answer["reaches"]) > 10:
        print(f"    … and {len(answer['reaches']) - 10} more")

    if answer["codes"]:
        print(f"  can refuse with {len(answer['codes'])} behaviour code(s): "
              f"{', '.join(c['code'] for c in answer['codes'][:6])}")
    else:
        print("  no behaviour code is reachable by call edges from here")

    # Printed every time, not only when it is inconvenient. A trace that
    # reported only what it could follow would be a confident, incomplete
    # answer on any system whose components are looked up by name.
    wired = answer["runtime_wired"]
    if wired["uncalled"]:
        print(f"\n  {wired['uncalled']} of {wired['total']} named component(s) in this project "
              "have no caller at all: they are resolved at run time, so no call-graph "
              "answer -- including this one -- can follow the chain through them.")
        for example in wired["examples"][:3]:
            print(f"    {example['bean']}  {example['path']}")
    return 0


def _print_fact(entry: dict, prefix: str) -> None:
    value = f"{entry['predicate']}={entry['object']}" if entry["object"] else entry["predicate"]
    print(f"{prefix}{value:<44} {entry['origin']:<10} {entry['confidence']:.2f} "
          f"{entry['detector']:<16} {entry['source_ref'] or ''}")


def _split_csv(value: str | None) -> list[str] | None:
    if not value:
        return None
    return [item.strip() for item in value.split(",") if item.strip()]


def cmd_note_add(args: argparse.Namespace) -> int:
    # `--scope ,` (likewise `""`, `" "`, `",,"`) splits to nothing, and
    # passing that through as "no scope" wrote a note with neither `scope:`
    # nor `scope_hashes:` -- un-monitorable for the rest of its life, on
    # exit 0, from a typo, on the command that wrote every note in the
    # corpus. The same silent data loss `amend` refuses. Checked here rather
    # than in `add_note` because only the CLI can tell "the caller typed
    # --scope and it named nothing" from "there is no scope", which is the
    # normal case for most notes and stays legal: `scope=[]` reaching
    # `add_note` from a generator means the latter.
    scope = _split_csv(args.scope)
    if args.scope is not None and not scope:
        print(
            "error: --scope was given but names nothing; drop the flag if this "
            "note is not about specific files, rather than writing a note that "
            "can never be checked against the code.",
            file=sys.stderr,
        )
        return 1
    try:
        path = notes.add_note(
            args.path,
            kind=args.kind,
            title=args.title,
            body=args.body,
            tags=_split_csv(args.tags),
            scope=scope,
            source=args.source,
            cause=args.cause,
            solution=args.solution,
            metric=args.metric,
            session=args.session,
            procedure=args.procedure,
            execution=args.execution,
        )
    except (ValueError, FileExistsError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"Note written to {path}")
    return 0


def _no_notes_here(path: str) -> str:
    """The sentence for a project that has recorded nothing yet.

    Printing nothing is the one answer a reader cannot act on: an empty list
    and a misconfigured notes directory look identical on a terminal, and
    every FM service points [knowledge] dir somewhere outside the service, so
    "looking in the wrong place" is not hypothetical. Name the place."""
    return (f"No notes recorded here yet ({notes.notes_dir(path)}). "
            "`eos note add` records the first one.")


def cmd_note_list(args: argparse.Namespace) -> int:
    recorded = notes.load_notes(args.path)
    matches = [note for note in recorded if args.tag in note.tags] if args.tag else recorded
    for note in matches:
        print(f"{note.path.name}\t{note.kind}\t{note.title}")
    if matches:
        return 0
    if not recorded:
        print(_no_notes_here(args.path))
        return 0
    tags = sorted({tag for note in recorded for tag in note.tags})
    print(f"{len(recorded)} note(s) recorded, none tagged {args.tag!r}. "
          + (f"Tags in use: {', '.join(tags)}." if tags else "No note carries a tag."))
    return 0


def cmd_note_show(args: argparse.Namespace) -> int:
    """Print one note in full, by file name or by part of its title.

    `note search` and `note list` print titles, and until now nothing printed
    a body: the only way to read what an earlier session wrote was to phrase a
    `compose` task narrow enough to rank that note into the top few. Two
    surfaces sent readers the wrong way about it -- compose's own truncation
    footer told them to use `note search`, which returns titles -- and an eval
    session tested that instruction against a near-verbatim title, got 29
    loose matches and no body, and concluded the tool was contradicting
    itself. It was.
    """
    recorded = notes.load_notes(args.path)
    if not recorded:
        print(_no_notes_here(args.path))
        return 0
    needle = args.name.casefold()
    exact = [note for note in recorded if note.path.name.casefold() == needle]
    matches = exact or [note for note in recorded
                        if needle in note.path.name.casefold() or needle in note.title.casefold()]
    if not matches:
        print(f"No note matches {args.name!r} among {len(recorded)} recorded here. "
              f"`eos note search {args.path} \"{args.name}\"` searches their contents.",
              file=sys.stderr)
        return 1
    if len(matches) > 1:
        # Printing the first would be a coin toss presented as an answer.
        print(f"{len(matches)} notes match {args.name!r}; name one of them:", file=sys.stderr)
        for note in matches[:10]:
            print(f"  {note.path.name}\t{note.title}", file=sys.stderr)
        return 1
    print(matches[0].path.read_text(encoding="utf-8"))
    return 0


def cmd_note_search(args: argparse.Namespace) -> int:
    matches = notes.search_notes(args.path, args.query, limit=args.limit)
    for note in matches:
        print(f"{note.path.name}\t{note.kind}\t{note.title}")
    if matches:
        return 0
    recorded = notes.load_notes(args.path)
    if not recorded:
        print(_no_notes_here(args.path))
        return 0
    # It searched and found none, which is a finding -- and a different one
    # from having had nothing to search. A session that cannot tell those
    # apart re-derives from source what an earlier session already paid for.
    print(f"Searched {len(recorded)} note(s); none match {args.query!r}. "
          f"`eos note list {args.path}` lists every one of them.")
    return 0


def cmd_note_eval(args: argparse.Namespace) -> int:
    """Score note search against questions somebody wrote the answers for.

    Exits non-zero only when the golden file itself is broken -- unreadable, or
    naming a note that no longer exists. A low score is a measurement and must
    not fail a run, or the number stops being reported honestly.
    """
    from core import retrieval

    try:
        entries = retrieval.parse_golden(args.golden)
    except (retrieval.GoldenError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    report = retrieval.evaluate(args.path, entries, depth=args.depth)
    if args.format == "json":
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(retrieval.render(report))
    return 1 if report["broken"] else 0


def cmd_note_skip(args: argparse.Namespace) -> int:
    # `record_skip` had no refusal path until the placeholder guard, so this
    # handler was not needed and was not written -- and its absence turned an
    # instructive one-sentence refusal into a six-frame traceback with the
    # sentence buried at the bottom. Same shape as cmd_note_add's and
    # cmd_note_amend's.
    try:
        path = notes.record_skip(args.path, reason=args.reason, session=args.session)
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"Recorded: no note for this session ({args.reason}) -> {path}")
    return 0


def cmd_note_amend(args: argparse.Namespace) -> int:
    body = args.body
    if body == "-":
        body = sys.stdin.read()
    # _split_csv("") returns None, the same value it returns for --scope
    # never having been passed at all -- so a caller who typed `--scope ""`
    # (or `--scope ,`, which splits to []) would otherwise be told "amend
    # needs at least one of --body, --reaffirm, or --scope", a message that
    # contradicts what they actually typed. Normalize any explicitly-passed
    # but empty value to `[]` so amend_note's own refusal (which names
    # --scope specifically) is the one the caller sees.
    scope = None
    if args.scope is not None:
        scope = _split_csv(args.scope) or []
    try:
        path = notes.amend_note(
            args.note,
            args.path,
            body=body,
            reaffirm=args.reaffirm,
            scope=scope,
            session=args.session,
        )
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"Note amended: {path}")
    return 0


# Same wording the Stop hook's block message uses (automation/hooks/note_gate.py
# `_message`), verbatim -- an agent that has seen the hook's block and now sees
# this report should be taught the same rule once, not two slightly different
# ones.
_PLACEHOLDER_WARNING = (
    "Angle-bracketed text below is a placeholder: replace it, do not run it as printed."
)


def cmd_note_audit(args: argparse.Namespace) -> int:
    """Report notes whose scoped files have moved. Always exit 0: this is a
    report, not a gate -- the Stop hook is the gate, and it blocks only on
    the hand-written half."""
    handwritten, generated = [], []
    for item in notes.stale_notes(args.path):
        (generated if item["generated"] else handwritten).append(item)

    # Resolved the same way the Stop hook resolves it, through notes_dir() --
    # never rebuilt as `<root>/.eos/knowledge/<name>`. Every FM service
    # configures [knowledge] dir outside the service itself, so a hand-built
    # path would print an `eos note amend` command naming a file that does
    # not exist, on all 18 of them.
    directory = notes.notes_dir(args.path)
    root = Path(args.path).expanduser().resolve()

    def _note_path(item):
        return directory / item["note"]

    def _title(item):
        try:
            return notes.parse_note(_note_path(item)).title
        except Exception:
            return item["note"]

    def _kind(item):
        try:
            return notes.parse_note(_note_path(item)).kind
        except Exception:
            return ""

    if handwritten:
        print("Hand-written notes that may now be wrong:")
        for item in handwritten:
            scopes = ", ".join(issue["scope"] for issue in item["issues"])
            print(f"  {_title(item)!r} ({_note_path(item)})  [{scopes}]")
        print(f"  {_PLACEHOLDER_WARNING}")
        for item in handwritten:
            note_path = _note_path(item)
            # Classified by the whole note, in the same three shapes the Stop
            # hook's `_amend_flags` uses and for the same reason: `amend_note`
            # validates EVERY scope entry, so one entry's refusal governs the
            # command whatever the others report. Two surfaces printing
            # different remedies for one state is what this replaced -- the
            # split here was two-way, so the mixed note was handed `--scope`
            # alone, which exits 1.
            gone = [i["scope"] for i in item["issues"] if i.get("reason") == "removed"]
            changed = [i["scope"] for i in item["issues"] if i.get("reason") != "removed"]
            if not gone:
                flags = "--body '<what is true now>'"
            elif not changed:
                flags = "--scope '<the new project-relative path>'"
            else:
                flags = ("--scope '<the project-relative paths this note is still "
                         "about>' --body '<what is true now>'")
            if gone:
                print(f"  {', '.join(gone)} no longer exists, so --body and "
                      "--reaffirm can only refuse (they re-hash that path).")
                print(
                    f"  {', '.join(changed)} did change, so a scope-only amend is "
                    "refused too -- one command does both:"
                    if changed else
                    "  Re-point the note at where the code moved:"
                )
            print(f"  eos note amend {note_path} {root} {flags}")
            if "--body" in flags and _kind(item) == "defect":
                print("    (a defect note's new body must keep its Root cause, "
                      "Solution and Metric sections)")
            if gone:
                if not changed:
                    print("  ...and add --body '<what is true now>' to that same "
                          "command if the move changed what the note claims.")
                continue
            print(
                f"  ...or, if the change does not affect what the note claims: "
                f"eos note amend {note_path} {root} --reaffirm '<why it still holds>'"
            )
    if generated:
        print(f"Generated notes gone stale: {len(generated)}")
        for item in generated:
            print(f"  {_title(item)!r} ({_note_path(item)})  (source: {item['source']})")
        print("  Fix: regenerate them; do not edit these by hand.")
    if not handwritten and not generated:
        # How many were examined, not just that none failed. "No stale notes."
        # reads the same whether it checked forty notes or found the knowledge
        # directory empty, and those call for opposite next actions.
        checked = len(notes.load_notes(args.path))
        print(f"Checked {checked} note(s); every scoped path still matches what "
              "the note recorded." if checked else _no_notes_here(args.path))
    return 0


def cmd_note(args: argparse.Namespace) -> int:
    return {
        "add": cmd_note_add,
        "list": cmd_note_list,
        "search": cmd_note_search,
        "show": cmd_note_show,
        "eval": cmd_note_eval,
        "skip": cmd_note_skip,
        "amend": cmd_note_amend,
        "audit": cmd_note_audit,
    }[args.note_command](args)


def _no_work_here(path: str) -> str:
    """The sentence for a project where nothing is in flight.

    Same reasoning as `_no_notes_here`: an empty ledger and a knowledge
    directory pointed somewhere else look identical on a terminal, and every
    FM service points [knowledge] dir outside the service. Name the place, and
    say which question was asked -- "nothing open" and "nothing ever recorded"
    lead to opposite next actions.
    """
    from core import work

    return (f"Nothing is in flight here ({work.path_for(path)}). "
            "`eos work add` records the first item; finished and dropped work "
            "is shown by `eos work list --status all`.")


def _work_line(item, now) -> list[str]:
    """One item as the terminal shows it: a line, plus what qualifies it."""
    from core import work

    held = " and ".join(item.holder_labels) if item.holders else "-"
    lines = [f"{item.status:<8} {item.id:<24} {item.title[:48]:<48} "
             f"{(item.ticket or '-'):<12} {held:<22} {work.ago(item.updated_at, now)}"]
    if item.contested:
        # Two sessions holding one item is the failure this ledger is for, so
        # it is never folded into the status column where it would read as
        # ordinary.
        lines.append(f"    contested: claimed by {' and '.join(item.holder_labels)}")
    if item.is_stale(now):
        lines.append(f"    stale: no event for over {work.STALE_AFTER_HOURS}h "
                     "-- the session holding it may be gone")
    if item.status == work.BLOCKED and item.reason:
        lines.append(f"    blocked on: {item.reason}")
    elif item.last:
        lines.append(f"    last: {item.last}" + (f"  ({item.last_by})" if item.last_by else ""))
    return lines


def _resolve_work(args):
    """One item by id or title fragment, with both failures reported in full."""
    from core import work

    found = work.fold(work.load(args.path))
    if not found:
        print(_no_work_here(args.path), file=sys.stderr)
        return None
    try:
        return work.resolve(found, args.item)
    except KeyError:
        print(f"No work item matches {args.item!r} among {len(found)} recorded here. "
              f"`eos work list {args.path} --status all` lists every one of them.",
              file=sys.stderr)
        return None
    except LookupError as exc:
        # Appending to the wrong item is silent and unrecoverable by reading:
        # the event lands, the fold accepts it, and nothing downstream knows.
        print(f"Several items match {args.item!r}; name one of them:", file=sys.stderr)
        print(exc, file=sys.stderr)
        return None


def _record_work(args, event: str, body: str | None = None) -> int:
    """Append one event to a resolved item and print what it folded to."""
    import datetime

    from core import work

    item = _resolve_work(args)
    if item is None:
        return 1
    try:
        work.append(args.path, event, item.id, session=args.session,
                    agent=getattr(args, "agent", None), body=body)
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    # Re-folded rather than assumed: a claim on an item someone else holds
    # becomes contested at exactly this moment, and that is the moment the
    # session claiming it can still act on the information.
    updated = work.resolve(work.fold(work.load(args.path)), item.id)
    for line in _work_line(updated, datetime.datetime.now(datetime.timezone.utc)):
        print(line)
    return 0


def cmd_work_add(args: argparse.Namespace) -> int:
    from core import work

    try:
        entry = work.open_item(
            args.path, args.title, body=args.body, ticket=args.ticket,
            scope=_split_csv(args.scope), session=args.session,
            agent=args.agent, claim=args.claim,
        )
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    state = work.sync_state(args.path)
    print(f"{entry.id}\t{'claimed' if args.claim else 'open'}\t{args.title}")
    print(work.sync_sentence(state))
    return 0


def cmd_work_claim(args: argparse.Namespace) -> int:
    return _record_work(args, "claim", body=args.note)


def cmd_work_log(args: argparse.Namespace) -> int:
    return _record_work(args, "log", body=args.body)


def cmd_work_block(args: argparse.Namespace) -> int:
    return _record_work(args, "block", body=args.reason)


def cmd_work_unblock(args: argparse.Namespace) -> int:
    return _record_work(args, "unblock", body=args.note)


def cmd_work_done(args: argparse.Namespace) -> int:
    return _record_work(args, "done", body=args.note)


def cmd_work_drop(args: argparse.Namespace) -> int:
    return _record_work(args, "drop", body=args.reason)


def cmd_work_list(args: argparse.Namespace) -> int:
    """What is in flight, and whether anyone else can see it.

    The sync sentence is printed on every run rather than only when something
    is wrong: this ledger's entire purpose is that a second session reads what
    the first one wrote, and "committed but not pushed" is the state in which
    it silently is not.
    """
    import datetime

    from core import work

    now = datetime.datetime.now(datetime.timezone.utc)
    status = None if args.status == "live" else args.status

    if args.across:
        ledgers = work.across_ledgers(args.path)
        payload = []
        for label, ledger in ledgers:
            found = work.fold(work.load_path(ledger))
            if status != "all":
                keep = work.LIVE if status is None else (status,)
                found = [item for item in found if item.status in keep]
            payload.append((label, work.sort_items(found)))
        if args.format == "json":
            print(json.dumps([{"project": label, "items": [item.to_dict() for item in found]}
                              for label, found in payload], indent=2, ensure_ascii=False))
            return 0
        for label, found in payload:
            if not found:
                continue
            print(f"== {label}")
            for item in found:
                for line in _work_line(item, now):
                    print(line)
        total = sum(len(found) for _, found in payload)
        if not total:
            print(f"Nothing is in flight in any of {len(ledgers)} ledger(s) under "
                  f"{work.path_for(args.path).parent.parent}.")
        if len(ledgers) == 1:
            # --across that silently reads one directory looks like a broken
            # flag. It is the right answer for a project whose knowledge
            # directory is its own; say which of the two happened.
            print(f"One ledger only: no sibling ledgers under "
                  f"{work.path_for(args.path).parent.parent}. `[knowledge] dir` is "
                  "what puts several projects under one root.")
        print(work.sync_sentence(work.sync_state(args.path)))
        return 0

    found = work.items(args.path, status=status)
    if args.session:
        found = [item for item in found
                 if any(holder.get("session") == args.session for holder in item.holders)]
    if args.format == "json":
        print(json.dumps([item.to_dict() for item in found], indent=2, ensure_ascii=False))
        return 0
    for item in found:
        for line in _work_line(item, now):
            print(line)
    if not found:
        every = work.fold(work.load(args.path))
        if not every:
            print(_no_work_here(args.path))
        else:
            print(f"{len(every)} item(s) recorded here, none {args.status}. "
                  f"`eos work list {args.path} --status all` lists every one of them.")
    else:
        stale = [item for item in found if item.is_stale(now)]
        print(f"\n{len(found)} item(s) in flight"
              + (f", {len(stale)} stale (no event for over {work.STALE_AFTER_HOURS}h)"
                 if stale else "") + ".")
    print(work.sync_sentence(work.sync_state(args.path)))
    return 0


def cmd_work_show(args: argparse.Namespace) -> int:
    """One item in full: its events, and what git says about its ticket."""
    import datetime

    from core import work

    item = _resolve_work(args)
    if item is None:
        return 1
    history = [entry for entry in work.load(args.path) if entry.id == item.id]
    evidence = work.ticket_commits(args.path, item.ticket) if item.ticket else None

    if args.format == "json":
        print(json.dumps({"item": item.to_dict(),
                          "events": [entry.to_dict() for entry in history],
                          "ticket_commits": evidence}, indent=2, ensure_ascii=False))
        return 0

    now = datetime.datetime.now(datetime.timezone.utc)
    for line in _work_line(item, now):
        print(line)
    print()
    for entry in history:
        who = work.who(entry.session, entry.agent)
        where = f"  {entry.branch}@{entry.commit[:7]}" if entry.commit else ""
        print(f"{entry.at}  {entry.event:<8} {who}{where}")
        if entry.body:
            print(f"    {entry.body}")
    if evidence is None:
        print("\nNo ticket on this item, so there is nothing to check it against.")
    elif not evidence["indexed"]:
        print(f"\nWhether any commit names {item.ticket} is unknown here: there is no "
              f"index at .eos/data/eos.db. `eos index {args.path}` builds one.")
    elif not evidence["commits"]:
        print(f"\nThe index holds no commit naming {item.ticket}. That is either work "
              "that is not committed yet, or a claim that outran it -- this cannot "
              "tell those apart.")
    else:
        print(f"\nCommits naming {item.ticket}:")
        for commit in evidence["commits"]:
            print(f"  {commit['sha'][:9]}  {commit['at']}  {commit['subject']}")
    return 0


def cmd_brief(args: argparse.Namespace) -> int:
    """What a session needs before it starts, in one call it did not choose.

    Every other command answers a question somebody asked. This one answers
    the question nobody thinks to ask -- "is someone already on this, and was
    this already learned here" -- which is why it is wired to a hook rather
    than offered in a list.
    """
    from core import brief

    text = brief.build(args.path, session=args.session, agent=args.agent,
                       task=args.task, budget=args.budget, task_only=args.task_only)
    if text:
        print(text, end="" if text.endswith("\n") else "\n")
    return 0


def cmd_route(args: argparse.Namespace) -> int:
    """Which model and how much effort this task deserves, and why (ADR-025).

    Advice for the harness, not an action: EOS calls no model. A refused
    explicit choice -- an unknown model, an unknown effort, a malformed
    `[model_routing]` table -- exits 2 with the reason, never a substitute.
    """
    import core.routing as routing
    from core.routing import trace, usage

    if args.usage_from:
        # Called from a Stop hook with the harness transcript: always exit 0.
        if usage.record(args.path, args.session, args.usage_from):
            print(f"usage recorded for session {args.session}")
        return 0

    if args.stats:
        recorded, sessions = len(trace.load(args.path)), len(usage.load(args.path))
        rows = trace.stats(args.path)
        if not rows:
            print(f"No decisions recorded in runs yet ({recorded} recorded outside runs, "
                  f"model usage for {sessions} session(s)). A decision made between "
                  "`eos run start` and `eos run finish` is joined to that run's outcome.")
            _print_advised_vs_used(args.path)
            return 0
        total = sum(sum(counts.values()) for _, counts in rows)
        print(f"Routing decisions joined to runs: {total} of {recorded} recorded; "
              f"model usage for {sessions} session(s)")
        print()
        for (kind, level, model, effort), counts in rows:
            tally = "  ".join(f"{word} {counts[word]}" for word in ("ok", "failed", "abandoned", "open")
                              if counts[word])
            target = f"{model}/{effort}" if effort else model
            print(f"  {kind:<24} {level:<8} {target:<14} {tally}")
        _print_advised_vs_used(args.path)
        return 0

    if args.eval:
        from core.routing import evaluate

        try:
            result = evaluate.run(args.path, evaluate.load(args.eval), split=args.split)
        except (OSError, ValueError, routing.OverrideError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        if args.split == "test":
            result["earlier_test_runs_other_config"] = evaluate.record_test(args.path, args.eval, result)
        print(json.dumps(result, indent=2, ensure_ascii=False) if args.json else evaluate.render(result))
        if args.split == "test" and result["earlier_test_runs_other_config"]:
            print(f"warning: this test set was measured {result['earlier_test_runs_other_config']} time(s) "
                  "before under a different configuration -- tune on dev, not on test", file=sys.stderr)
        # 0 PASS, 1 FAIL: the gate is what a CI step reads.
        return 0 if result["gate"]["pass"] else 1

    task = " ".join(args.task or ()).strip()
    if not task:
        hint = "" if Path(args.path).is_dir() else f" ({args.path!r} is not a project directory)"
        print(f'error: a task is required: eos route <path> "<task>"{hint}', file=sys.stderr)
        return 2
    try:
        decision = routing.route(args.path, task, files=tuple(args.file or ()), session=args.session,
                                 model=args.model, effort=args.effort, record=not args.no_record,
                                 fresh=args.fresh)
    except (routing.OverrideError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(decision.to_dict(), indent=2, ensure_ascii=False))
        return 0

    from core.routing.score import WEIGHTS

    factors = ", ".join(f"{name} {decision.factors[name]:.2f}"
                        for name, _ in WEIGHTS if name in decision.factors)
    lines = [
        f"Task: {task}",
        "",
        f"Type:        {decision.task_type}",
        f"Complexity:  {decision.level}  (score {decision.score:.2f})",
        f"Model:       {decision.model}",
        f"Effort:      {decision.effort or '(none: the model takes no effort setting)'}",
        f"Confidence:  {decision.confidence:.2f}",
        f"Source:      {decision.override_source}" + ("  (reused)" if decision.reused else ""),
        "",
        "Reason:",
        decision.reason,
    ]
    if factors:
        lines += ["", f"Factors: {factors}"]
    lines.append(f"Alternatives: {', '.join(decision.alternatives) or 'none'}")
    print("\n".join(lines))
    return 0


def cmd_capabilities(args: argparse.Namespace) -> int:
    """What to run instead of a raw command (ADR-026).

    The list a session needs before it reaches for `curl` or a database client:
    each wrapper, what it answers, and -- with --command -- whether a command
    line is the raw form one of them covers.
    """
    from core import capabilities

    try:
        declared = capabilities.load(args.path)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.raw is not None:
        found = capabilities.match(args.raw, declared)
        wrapper = capabilities.wrapper_in(args.raw, declared)
        if args.format == "json":
            print(json.dumps({"wrapper": wrapper.name if wrapper else None,
                              "covered_by": found.capability.name if found else None,
                              "tier": found.tier if found else None,
                              "use": found.capability.run if found else None}, indent=2))
        elif wrapper is not None:
            print(f"already a wrapper: {wrapper.line()}")
        elif found is not None:
            print(f"{found.tier}: {capabilities.remedy(found)}")
        else:
            print("no capability covers this command")
        return 0
    shown = capabilities.for_task(args.task, declared) if args.task else declared
    if args.format == "json":
        print(json.dumps([dataclasses.asdict(c) for c in shown], indent=2, ensure_ascii=False))
        return 0
    if not declared:
        print(f"No capabilities declared ({capabilities.path_for(args.path)}). A project that routes "
              "its external calls through wrappers lists them there, one [[capability]] each.")
        return 0
    if not shown:
        print(f"No capability matches that task; {len(declared)} declared: "
              + ", ".join(c.name for c in declared))
        return 0
    for capability in shown:
        print(capability.line())
    return 0


def cmd_hook(args: argparse.Namespace) -> int:
    """The argparse face of `eos hook`; the harness path is dispatched before imports."""
    from core import hooks

    forwarded = [args.event]
    if args.agent:
        forwarded += ["--agent", args.agent]
    if args.project:
        forwarded += ["--project", args.project]
    return hooks.main(forwarded)


def _print_advised_vs_used(path) -> None:
    """The recommendation against what sessions ran at (claude plan 3.5)."""
    from core.routing import trace

    rows = trace.advised_vs_used(path)
    if not rows:
        print("\nAdvised vs used: no session has both a recorded decision and a usage line yet.")
        return
    main = sum(r["main_messages"] for r in rows)
    known = sum(r["main_efforts_known"] for r in rows)
    subs = sum(r["subagent_messages"] for r in rows)
    print(f"\nAdvised vs used, {len(rows)} session(s) (advice = the session's first decision):")
    print(f"  session messages on the advised model   {sum(r['main_on_model'] for r in rows)}/{main}")
    if known:
        print(f"  session messages at the advised effort  {sum(r['main_at_effort'] for r in rows)}/{known}")
    if subs:
        print(f"  subagent messages on the advised model  {sum(r['subagent_on_model'] for r in rows)}/{subs}")
    for r in rows[-8:]:
        print(f"    {r['session'][:8]}  advised {r['advised_model']}/{r['advised_effort']} ({r['level']})  "
              f"model {r['main_on_model']}/{r['main_messages']}  effort {r['main_at_effort']}/{r['main_efforts_known']}  "
              f"subagents {r['subagent_on_model']}/{r['subagent_messages']}")


def cmd_work_stats(args: argparse.Namespace) -> int:
    """What happened here, as opposed to how often a command was called.

    `eos cost` says whether the engine was reached for; this says whether
    reaching for it changed anything. Every number below should fall if the
    ledger is doing its job, and a rising one is the report doing its job.
    """
    from core import work

    report = work.statistics(args.path, since=args.since)
    if args.format == "json":
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0
    if not report.get("events"):
        window = f" since {args.since}" if args.since else ""
        print(f"No work has been recorded here{window} ({work.path_for(args.path)}). "
              "`eos work add` records the first item; there is nothing yet to measure.")
        return 0

    window = f" since {args.since}" if args.since else ""
    print(f"{report['items']} item(s) over {report['events']} event(s){window}, "
          f"{report['claimed']} of them claimed by a session.")
    print()
    # Each of these is a cost somebody already paid, not a score.
    print(f"Collisions        {report['contested']:>4}  item(s) claimed by two sessions at once")
    print(f"Went quiet        {report['went_quiet']:>4}  item(s) held with no event for over "
          f"{work.STALE_AFTER_HOURS}h")
    print(f"Closed            {report['closed']:>4}  ({report['done']} done, "
          f"{report['dropped']} dropped; {report['blocked']} block(s) recorded)")
    print(f"Open now          {report['open_now']:>4}  ({report['stale_now']} stale, "
          f"{report['contested_now']} contested)")
    if report["median_hours_to_close"] is not None:
        print(f"Claim to close      {_hours(report['median_hours_to_close'])} median, "
              f"{_hours(report['longest_hours_to_close'])} longest")
    print()
    print("These count what sessions recorded. Work done without closing an item, and "
          "an item closed without the work, are indistinguishable from here.")
    return 0


def _hours(value: float) -> str:
    if value < 1:
        return "under 1h"
    if value < 48:
        return f"{value:.0f}h"
    return f"{value / 24:.0f}d"


def _run_line(record) -> str:
    state = record.outcome or "open"
    return (f"{record.id}\t{state}\t{record.started_at or '-'}\t"
            f"{record.target or '-'}\t{len(record.events)} event(s)\t{record.title}")


def cmd_run_start(args: argparse.Namespace) -> int:
    from core import executions

    try:
        record = executions.start(args.path, args.title, procedure=args.procedure,
                                  work_item=args.work, session=args.session,
                                  agent=args.agent, target=args.target)
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(record.id)
    if not record.session:
        # Nothing to key the pointer on, so wrappers cannot find this run on
        # their own. Say how to reach it rather than let capture go quiet.
        print(f"No session id found; wrappers will not attach events on their own. "
              f"Export {executions.EXECUTION_ENV}={record.id} and "
              f"{executions.LEDGER_ENV}={executions.path_for(args.path)}, "
              f"or pass --session.", file=sys.stderr)
    _route_run(args.path, record)
    return 0


def _route_run(path, record) -> None:
    """Give a new run its routing decision (ADR-025), where routing is on.

    A run is the one unit that has both a task and an outcome, so this is
    where a decision is worth recording: the title is routed, the decision is
    appended to the run as a `decided` event and to the trace, and `eos route
    --stats` can later read it against the run's outcome. The ROUTE line goes
    to stderr -- stdout is the run id, and callers capture it. Any failure
    costs the line, never the run.
    """
    try:
        from core.routing import config as routing_config

        cfg = routing_config.load(path)
        if not (cfg.configured and cfg.enabled) or not record.session:
            return
        import core.routing as routing
        from core.routing import adapters

        decision = routing.route(path, record.title, session=record.session, record=True)
        print(adapters.headline(decision), file=sys.stderr)
    except Exception:  # noqa: BLE001 - routing is advice; the run is already open
        return


def cmd_run_event(args: argparse.Namespace) -> int:
    from core import executions

    try:
        entry = executions.event(args.path, args.execution, kind=args.kind, tool=args.tool,
                                 target=args.target, ref=args.ref, exit_code=args.exit,
                                 ms=args.ms, body=args.body, session=args.session,
                                 source="cli")
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"{entry.execution}\t{entry.kind}\t{entry.tool or '-'}\t{entry.target or '-'}")
    return 0


def cmd_run_finish(args: argparse.Namespace) -> int:
    from core import executions

    try:
        record = executions.finish(args.path, args.execution, outcome=args.outcome,
                                   lesson=args.lesson, session=args.session,
                                   next_time=args.next_time)
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(_run_line(record))
    return 0


def cmd_run_list(args: argparse.Namespace) -> int:
    from core import executions

    found = executions.load(args.path)
    if args.session:
        found = [r for r in found if r.session == args.session
                 or any(e.session == args.session for e in r.events)]
    if args.procedure:
        found = [r for r in found if r.procedure == args.procedure]
    if args.target:
        found = [r for r in found if r.target == args.target]
    if args.outcome:
        wanted = None if args.outcome == "open" else args.outcome
        found = [r for r in found if r.outcome == wanted]
    if args.since:
        found = [r for r in found if (r.started_at or "") >= args.since]
    found = found[-args.limit:] if args.limit else found
    if args.format == "json":
        print(json.dumps([r.to_dict() for r in found], indent=2, ensure_ascii=False))
        return 0
    if not found:
        total = len(executions.load(args.path))
        print(f"No execution matches ({total} recorded in {executions.path_for(args.path)}). "
              "`eos run start` opens one.")
        return 0
    for record in reversed(found):
        print(_run_line(record))
    return 0


def cmd_run_show(args: argparse.Namespace) -> int:
    from core import executions

    try:
        record = executions.resolve(executions.load(args.path), args.execution)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if args.format == "json":
        print(json.dumps(record.to_dict(), indent=2, ensure_ascii=False))
        return 0
    print(f"{record.id}  {record.title}")
    print(f"  outcome   {record.outcome or 'open'}")
    print(f"  started   {record.started_at or '-'}   finished {record.finished_at or '-'}")
    print(f"  session   {record.session or '-'}   agent {record.agent or '-'}")
    for label, value in (("procedure", record.procedure), ("work", record.work_item),
                         ("target", record.target), ("branch", record.branch),
                         ("commits", " .. ".join(c[:9] for c in (record.commit_start, record.commit_end) if c)),
                         ("lesson", record.lesson)):
        if value:
            print(f"  {label:<9} {value}")
    from core import verification
    checks = [v for v in verification.load(args.path) if v.execution == record.id]
    for check in checks:
        print(f"  verified  {check.code} {check.outcome} (exit {check.exit_code}) — {check.command}")
    sources = {}
    for e in record.events:
        sources[e.source or "wrapper/cli"] = sources.get(e.source or "wrapper/cli", 0) + 1
    by_source = ", ".join(f"{name} {count}" for name, count in sorted(sources.items()))
    print(f"  events    {len(record.events)}" + (f"  ({by_source})" if record.events else ""))
    for e in record.events:
        tail = " ".join(part for part in (
            f"exit={e.exit_code}" if e.exit_code is not None else "",
            f"{e.ms}ms" if e.ms is not None else "",
            f"[{e.status}]" if e.status and e.status != "ok" else "",
            f"by {e.agent}" if e.agent else "", e.ref or "") if part)
        print(f"    {e.ord:>3}  {e.at}  {e.kind:<8} {e.tool or '-':<12} {e.target or '-':<10} {tail}")
    return 0


def cmd_procedure_list(args: argparse.Namespace) -> int:
    from core import executions

    found = notes.procedures(args.path)
    if args.tool:
        found = [n for n in found if args.tool in notes.procedure_tools(n)]
    if args.target:
        ran_against = {r.procedure for r in executions.load(args.path) if r.target == args.target}
        found = [n for n in found if n.procedure in ran_against]
    if args.format == "json":
        print(json.dumps([{"procedure": n.procedure, "title": n.title, "runs_ok": n.runs_ok or 0,
                           "runs_failed": n.runs_failed or 0, "last_verified": n.last_verified,
                           "tools": notes.procedure_tools(n), "path": str(n.path)} for n in found],
                         indent=2, ensure_ascii=False))
        return 0
    if not found:
        print(f"No procedure recorded here ({notes.notes_dir(args.path)}). "
              "`eos procedure new --title \"…\" --step \"…\"` writes the first.")
        return 0
    for n in found:
        print(f"{n.procedure}\t{n.runs_ok or 0} ok / {n.runs_failed or 0} failed\t"
              f"{(n.last_verified or 'never')[:10]}\t{notes.procedure_confidence(n)}\t{n.title}")
    return 0


def cmd_procedure_show(args: argparse.Namespace) -> int:
    from core import executions

    try:
        note = notes.find_procedure(args.path, args.procedure)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    runs = executions.of_procedure(args.path, note.procedure or "")[-3:]
    if args.format == "json":
        print(json.dumps({"procedure": note.procedure, "title": note.title,
                          "steps": notes.procedure_steps(note),
                          "prerequisites": notes.procedure_prerequisites(note),
                          "success": notes.procedure_success(note),
                          "known_failures": notes.procedure_known_failures(note),
                          "runs_ok": note.runs_ok or 0, "runs_failed": note.runs_failed or 0,
                          "last_verified": note.last_verified,
                          "recent": [r.to_dict() for r in reversed(runs)],
                          "path": str(note.path)}, indent=2, ensure_ascii=False))
        return 0
    print(f"{note.title}   [{note.procedure}]")
    print(f"  runs      {note.runs_ok or 0} ok / {note.runs_failed or 0} failed   "
          f"last verified {note.last_verified or 'never'}   "
          f"confidence {notes.procedure_confidence(note)}")
    for label, items in (("prerequisites", notes.procedure_prerequisites(note)),
                         ("success", notes.procedure_success(note))):
        for i, item in enumerate(items):
            print(f"  {label if i == 0 else '':<13} {item}")
    print("  steps")
    for number, step in enumerate(notes.procedure_steps(note), start=1):
        print(f"    {number:>2}. {step}")
    avoid = notes._items(notes.section_in(note.body, "When not to use this"))
    for i, item in enumerate(avoid):
        print(f"  {'not for' if i == 0 else '':<13} {item}")
    failures = notes.procedure_known_failures(note)
    if failures:
        print("  known failures")
        for item in failures[-5:]:
            print(f"    - {item}")
    if runs:
        print("  recent runs")
        for r in reversed(runs):
            print(f"    {(r.finished_at or r.started_at or '')[:16]}  {r.outcome or 'open':<9} "
                  f"{r.target or '-':<10} {r.id}")
    print(f"  file      {note.path}")
    return 0


def cmd_procedure_new(args: argparse.Namespace) -> int:
    steps = list(args.step or [])
    if args.steps == "-":
        steps += [line.strip() for line in sys.stdin.read().splitlines() if line.strip()]
    sections = ["## Steps", ""] + [f"{n}. {s}" for n, s in enumerate(steps, start=1)]
    for heading, items in (("Prerequisites", args.prerequisite), ("Success", args.success)):
        if items:
            sections += ["", f"## {heading}", ""] + [f"- {item}" for item in items]
    if args.body:
        sections = [args.body.strip(), ""] + sections
    try:
        path = notes.add_note(args.path, kind="procedure", title=args.title,
                              body="\n".join(sections), tags=_split_csv(args.tags),
                              scope=_split_csv(args.scope), source=args.source,
                              session=args.session, procedure=args.slug)
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    note = notes.parse_note(path)
    print(f"{note.procedure}\t{path}")
    return 0


def cmd_procedure_audit(args: argparse.Namespace) -> int:
    from core import executions

    report = executions.audit_procedures(args.path)
    if args.format == "json":
        print(json.dumps(report, indent=2, ensure_ascii=False))
    elif not report:
        print("No procedure recorded here; nothing to audit.")
    else:
        for entry in report:
            state = "ok" if not entry["problems"] else "; ".join(entry["problems"])
            print(f"{entry['procedure']}\t{entry['runs_ok']} ok / {entry['runs_failed']} failed\t{state}")
    # Exit 1 only for the one problem that is an integrity failure: counters
    # the ledger does not support. The rest are observations for a person.
    return 1 if any(entry["mismatch"] for entry in report) else 0


def cmd_procedure(args: argparse.Namespace) -> int:
    return {
        "list": cmd_procedure_list,
        "show": cmd_procedure_show,
        "new": cmd_procedure_new,
        "audit": cmd_procedure_audit,
    }[args.procedure_command](args)


_FILL_SESSION = frozenset(
    {("work", verb) for verb in ("add", "claim", "log", "block", "unblock", "done", "drop")}
    | {("note", verb) for verb in ("add", "amend", "skip")}
    | {("procedure", "new")}
    | {("route", None)}
)


def cmd_run_tools(args: argparse.Namespace) -> int:
    from core import executions

    used = executions.tools(args.path, procedure=args.procedure, target=args.target)
    if args.format == "json":
        print(json.dumps([dataclasses.asdict(u) for u in used], indent=2, ensure_ascii=False))
        return 0
    if not used:
        print("No tool use recorded" + (f" for {args.procedure}" if args.procedure else "")
              + (f" against {args.target}" if args.target else "")
              + ". Wrappers record it with eos-event / eos run event inside an open run.")
        return 0
    for u in used:
        print(f"{u.tool:<16} {u.count:>4} call(s) in {u.runs} run(s)   {u.failures} non-zero   "
              f"last run {u.last_outcome or 'open'} {(u.last_at or '')[:10]}   {', '.join(u.targets) or '-'}")
    return 0


def cmd_run_diff(args: argparse.Namespace) -> int:
    from core import executions

    try:
        delta = executions.diff(args.path, args.execution)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if args.format == "json":
        print(json.dumps(delta, indent=2, ensure_ascii=False))
        return 0
    start, end = delta["commit_start"], delta["commit_end"]
    print(f"{delta['execution']}   commits {(start or '?')[:9]} .. {(end or 'open')[:9]}")
    print(f"  changed (recorded)  {len(delta['paths'])}")
    for path in delta["paths"]:
        print(f"    {path}")
    if delta["commits"]:
        print(f"  commits in range    {len(delta['commits'])}")
        for sha, subject in delta["commits"][:20]:
            print(f"    {sha[:9]}  {subject}")
        print(f"  files in range      {len(delta['committed_paths'])}")
        for path in delta["committed_paths"][:40]:
            print(f"    {path}")
    elif start and end and start == end:
        print("  no commit made during the run")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    return {
        "start": cmd_run_start,
        "event": cmd_run_event,
        "finish": cmd_run_finish,
        "list": cmd_run_list,
        "show": cmd_run_show,
        "tools": cmd_run_tools,
        "diff": cmd_run_diff,
    }[args.run_command](args)


def cmd_work(args: argparse.Namespace) -> int:
    return {
        "add": cmd_work_add,
        "claim": cmd_work_claim,
        "log": cmd_work_log,
        "block": cmd_work_block,
        "unblock": cmd_work_unblock,
        "done": cmd_work_done,
        "drop": cmd_work_drop,
        "list": cmd_work_list,
        "show": cmd_work_show,
        "stats": cmd_work_stats,
    }[args.work_command](args)


def _read_git_ref(repo: Path) -> str | None:
    head = repo / ".git" / "HEAD"
    if not head.is_file():
        return None
    content = head.read_text(encoding="utf-8", errors="replace").strip()
    if content.startswith("ref: refs/heads/"):
        return content[len("ref: refs/heads/"):]
    return content[:7] if content else None


def cmd_parent(args: argparse.Namespace) -> int:
    """Real parent source for a symbol, from the command line.

    This existed only as an MCP tool, and on an overlay codebase it is the
    tool most often needed: the class that decides the behaviour is in the
    parent, under a different name, and the project holds an override. Two
    eval sessions ran out of road here -- one could not check whether a fraud
    step enforces a limit, the other could not see what the dispatcher does
    with the flags the controller sets -- and both said so as the point at
    which the investigation stopped.
    """
    root = Path(args.path).resolve()
    if not links.read_links(root):
        print(f"No linked projects configured for {root.name}. "
              f"Link one with: eos init {root} --link-parent /path/to/parent-project",
              file=sys.stderr)
        return 1

    answer = inspector.get_parent_implementation(root, args.symbol, max_results=max(args.limit, 1))
    if args.format == "json":
        print(json.dumps(answer, indent=2, ensure_ascii=False))
        return 0

    matches = answer["matches"]
    if not matches:
        # Which of the two it is matters: a symbol that is not there and a
        # parent that was never indexed are different problems with different
        # fixes, and the same empty list.
        print(f"No parent symbol matches {args.symbol!r}. If the parent has never been "
              f"scanned here, run: eos scan {root} --with-parents")
        return 0

    for match in matches:
        print(f"{match['name']}  ({match['kind']})  {match['path']}:{match['line']}")
        if match["source"]:
            for line in match["source"].splitlines():
                print(f"    {line}")
        else:
            print("    (source not readable: the linked parent is missing on disk)")
        print()
    return 0


def cmd_parents(args: argparse.Namespace) -> int:
    root = Path(args.path).resolve()
    configured = links.read_links(root)
    if not configured:
        print(f"No linked projects configured for {root.name}.")
        print()
        print("  To link a parent project:")
        print(f"    eos init {root} --link-parent /path/to/parent-project")
        return 0

    for label, link in configured.items():
        resolved = links.resolve_link_path(root, link)
        exists = resolved.exists()
        print(f"[{label}] {link.role} ({'found' if exists else 'MISSING'})")
        print(f"  path: {resolved}")
        if link.ref:
            print(f"  product.version: {link.ref}")
        if exists:
            ref = _read_git_ref(resolved)
            if ref:
                print(f"  git: {ref}")
                if link.ref and link.ref not in ref:
                    print(f"  Note: ref may not match -- expected to see '{link.ref}' in '{ref}'")
    return 0


def cmd_mcp(args: argparse.Namespace) -> int:
    """Start the stdio server whatever state the project is in.

    require_project() used to run here, before the JSON-RPC loop, so pointing a
    client at a directory with no .eos -- the state every new user is in --
    killed the process with a traceback. An MCP client surfaces that only as
    "server disconnected", with the reason in a log file nobody reads. The
    server already turns a failing tool call into an isError result carrying
    the message, which a client can show the model, so let it.
    """
    from core.mcp_server import serve

    return serve(str(Path(args.path).expanduser().resolve()), VERSION)


def cmd_bench(args: argparse.Namespace) -> int:
    from core import bench

    root = Path(args.path).resolve()
    report = bench.run(root, samples=args.samples)
    print(report.to_text())
    out = root / ".eos" / "data" / "bench.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report.to_markdown(), encoding="utf-8")
    print(f"\nReport: {out.relative_to(root)}")
    return 0


def cmd_ui(args: argparse.Namespace) -> int:
    from core import preflight

    if args.ui_command == "uninstall":
        target = preflight.venv_path()
        if not target.exists():
            print(f"eos ui: nothing installed at {target}")
            return 0
        shutil.rmtree(target)
        print(f"eos ui: removed {target}")
        return 0

    if args.no_install:
        missing = preflight.ui_packages_missing()
        if missing:
            print(f"eos ui: {len(missing)} Python packages missing ({', '.join(missing)})")
            print(f"  Run `eos ui` (without --no-install) to install them into {preflight.venv_path()}.")
        else:
            print("eos ui: all UI packages are already installed.")
        return 0

    if not preflight.install_ui_packages(assume_yes=args.yes):
        # Refusing is not an error: the core CLI and MCP server never needed
        # these packages, and saying so is more useful than a non-zero exit.
        return 0

    start = _CORE_ROOT / "bin" / "start.sh"
    argv = [str(start)] + ([str(args.port)] if args.port else [])
    return subprocess.call(argv)


def cmd_ai(args: argparse.Namespace) -> int:
    from core.ai import writer

    root = Path(args.path).resolve()
    surface = args.surface or _read_surface(root)
    if args.surface:
        _store_surface(root, surface)
    claude = args.claude or _read_ai_key(root, "claude", writer.CLAUDE_MODES, "files")
    if args.claude:
        _store_ai_key(root, "claude", claude)
    for path in writer.write_all(root, VERSION, agents_md=not args.no_agents_md,
                                 surface=surface, route_hook=_route_hook_wanted(root), claude=claude):
        print(f"  {path.relative_to(root)}")
    if claude == "plugin":
        for path in writer.remove_claude_files(root):
            print(f"  removed {path.relative_to(root)} (the EOS plugin provides it)")
    _warn_stale_mcp(root, surface)
    return 0


def _store_ai_key(root: Path, key: str, value: str) -> None:
    config_path = root / ".eos" / "config.toml"
    config = ConfigIO.read_toml(config_path) if config_path.is_file() else {}
    if config.get("ai", {}).get(key) == value:
        return
    config.setdefault("ai", {})[key] = value
    ConfigIO.write_toml(config_path, config)


def _read_ai_key(root: Path, key: str, allowed: tuple, default: str) -> str:
    config_path = root / ".eos" / "config.toml"
    try:
        value = ConfigIO.read_toml(config_path).get("ai", {}).get(key) if config_path.is_file() else None
    except (OSError, ValueError):
        return default
    return value if value in allowed else default


def _route_hook_wanted(root: Path) -> bool:
    """`[model_routing] hook = true` (ADR-025, M9). A table that cannot be read
    leaves the hook off and says why, rather than failing the whole update."""
    from core.routing import config as routing_config

    try:
        return routing_config.load(root).hook
    except (OSError, ValueError) as exc:
        print(f"warning: [model_routing] not read ({exc}); the routing hook stays off", file=sys.stderr)
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="eos", description="EOS project intelligence engine")
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_path(p):
        # Support both `eos init path` and `eos init --path path`. argparse
        # assigns positional defaults after optionals are parsed, so a plain
        # default="." on the positional clobbered an already-parsed --path
        # back to "." whenever only the flag form was given (verified:
        # `eos note add --path /tmp ...` silently wrote into cwd's .eos/,
        # not /tmp/.eos/). SUPPRESS means "no positional token means don't
        # touch the namespace," leaving --path's own default in charge.
        p.add_argument("path", nargs="?", default=argparse.SUPPRESS, help="Project root path (default: current directory)")
        p.add_argument("--path", dest="path", default=".", help=argparse.SUPPRESS)

    init_p = sub.add_parser("init", help="Initialize .eos/ in a project")
    add_path(init_p)
    init_p.add_argument("--link-parent", dest="link_parent", default=None, help="Path to a linked source project this one overlays: a vendored dependency, a fork's upstream, or a platform repo")
    init_p.add_argument(
        "--link-label",
        dest="link_label",
        default=None,
        help="Label for the link (default: parent). Use a distinct label to add a second "
        "link, e.g. --link-label common for the shared upstream-common-* libraries.",
    )
    init_p.add_argument(
        "--no-ai",
        dest="no_ai",
        action="store_true",
        help="Do not write the .claude/, .mcp.json and AGENTS.md integration files",
    )
    init_p.add_argument(
        "--surface",
        choices=("cli", "mcp", "both"),
        default="cli",
        help="Which surface this project exposes. 'cli' (default) writes no MCP "
        "registration: an MCP tool roster is charged to every request whether "
        "or not a tool is called. 'mcp' or 'both' registers the server too.",
    )
    init_p.add_argument(
        "--claude",
        choices=("files", "plugin"),
        default="files",
        help="How Claude Code gets EOS's hooks, skill and agent: copied into .claude/ "
        "(files, default) or from the EOS plugin (plugin), which writes nothing there.",
    )

    scan_p = sub.add_parser("scan", help="Scan project and regenerate knowledge artifacts")
    add_path(scan_p)
    scan_p.add_argument("--full", action="store_true", help="Force full rescan ignoring cache")
    scan_p.add_argument("--with-parents", dest="with_parents", action="store_true", help="Also index linked parent projects")

    update_p = sub.add_parser("update", help="Update eos-core runtime from canonical core/")
    add_path(update_p)
    update_p.add_argument("--dry-run", action="store_true", help="Preview changes without applying")

    doctor_p = sub.add_parser("doctor", help="Validate .eos/ integrity")
    add_path(doctor_p)
    doctor_p.add_argument(
        "--memory", action="store_true",
        help="Instead: the operational-memory matrix for this project "
             "(docs/plans/operational-memory.md); exit 1 while any row is open")
    doctor_p.add_argument("--format", choices=("text", "json"), default="text")

    info_p = sub.add_parser("info", help="Show instance summary")
    add_path(info_p)

    clean_p = sub.add_parser("clean", help="Remove generated artifacts")
    add_path(clean_p)

    index_p = sub.add_parser(
        "index",
        help="Rebuild .eos/data/eos.db from notes, brain, graph, journeys and git history",
    )
    add_path(index_p)

    query_p = sub.add_parser("query", help="Run read-only SQL against .eos/data/eos.db")
    add_path(query_p)
    query_p.add_argument("sql", nargs="?", help="One read-only SQL statement")
    query_p.add_argument("--search", help="Full-text search over notes, brain docs and journeys instead of SQL, best match first (a word matching nothing costs rank, not the answer)")
    query_p.add_argument("--limit", type=int, default=20, help="Maximum --search results")

    status_p = sub.add_parser("status", help="Show project status and latest scan metadata")
    add_path(status_p)

    graph_p = sub.add_parser("graph", help="Export the generated project graph")
    add_path(graph_p)
    graph_p.add_argument("--type", default="import", help="Edge type to include, or 'all'")
    graph_p.add_argument("--format", choices=("json",), default="json", help="Output format")
    graph_p.add_argument("--output", default=None, help="Output path, or '-' for stdout")

    context_p = sub.add_parser(
        "context",
        help="Generate an AI-oriented project context (writes .eos/data/brain/llm_context.md)")
    add_path(context_p)
    context_p.add_argument("--budget", type=int, default=12000, help="Approximate token budget")
    context_p.add_argument("--stdout", action="store_true",
                           help="Print it instead of writing the file")

    compose_p = sub.add_parser("compose", help="Compose focused context for a task")
    add_path(compose_p)
    compose_p.add_argument("task", help="Task or focus description")
    compose_p.add_argument("target", nargs="?", help="Optional project-relative target file")
    compose_p.add_argument("--budget", type=int, default=12000, help="Approximate token budget")

    impact_p = sub.add_parser("impact", help="Analyze direct import impact for a file")
    add_path(impact_p)
    impact_p.add_argument("file", help="Project-relative file path")
    impact_p.add_argument("--depth", type=int, default=1,
                          help="How many hops to follow (1-5, default 1)")
    impact_p.add_argument("--include", action="append", choices=("facts", "coverage", "history"),
                          help="Add provenance, detector coverage, or this file's commits")

    verify_p = sub.add_parser(
        "verify", help="Record what an adapter ran for a behaviour code, and what happened")
    add_path(verify_p)
    verify_p.add_argument("code", help="The behaviour code the run was about")
    verify_p.add_argument("--outcome", required=True, choices=("passed", "failed", "errored"))
    # dest is not "command": the subparser already stores the subcommand name
    # there, and a flag writing to it replaced "verify" with the shell line,
    # which surfaced as a KeyError on dispatch.
    verify_p.add_argument("--command", required=True, dest="ran", help="Exactly what was run")
    verify_p.add_argument("--exit-code", type=int, dest="exit_code")
    verify_p.add_argument("--log", help="Where the full output was kept")
    verify_p.add_argument("--output", help="File holding the output, or - for stdin; only its digest is stored")
    verify_p.add_argument("--verdict", choices=(
        "expected-behaviour", "test-defect", "environment-failure",
        "potential-defect", "confirmed-defect"),
        help="What a person concluded. Never filled in automatically.")
    verify_p.add_argument("--note", help="One line of context for the run")

    cost_p = sub.add_parser("cost", help="What EOS has cost this project, per command")
    add_path(cost_p)
    cost_p.add_argument("--format", choices=("text", "json"), default="text")

    findings_p = sub.add_parser("findings", help="Recorded runs and what they were judged to be")
    add_path(findings_p)
    findings_p.add_argument("--failed-only", action="store_true", dest="failed_only")
    findings_p.add_argument("--format", choices=("text", "json"), default="text")

    draft_p = sub.add_parser(
        "draft-test", help="Draft a test for a behaviour code the suite does not assert")
    add_path(draft_p)
    draft_p.add_argument("code", help="The behaviour code, as `eos rules` lists it")
    draft_p.add_argument("--write", action="store_true",
                         help="Also save it under .eos/data/candidates/")
    draft_p.add_argument("--format", choices=("text", "json"), default="text")

    ask_p = sub.add_parser(
        "ask", help="Run a question this project's index extensions provide")
    add_path(ask_p)
    ask_p.add_argument("name", nargs="?", help="Question name; omit to list them")
    ask_p.add_argument("argument", nargs="?", help="Value for a question that takes one")
    ask_p.add_argument("--format", choices=("text", "json"), default="text")

    trace_p = sub.add_parser(
        "trace", help="What an entry point reaches, and what is wired at run time")
    add_path(trace_p)
    trace_p.add_argument("file", help="Project-relative file path")
    trace_p.add_argument("--depth", type=int, default=4, help="How many hops to follow (1-5)")
    trace_p.add_argument("--format", choices=("text", "json"), default="text")

    rules_p = sub.add_parser(
        "rules", help="Behaviour codes this project throws, and which have no test")
    add_path(rules_p)
    rules_p.add_argument("--untested", action="store_true",
                         help="Only codes no test names")
    rules_p.add_argument("--limit", type=int, default=20,
                         help="How many codes to list (0 for all); the tally always covers every one")
    rules_p.add_argument("--format", choices=("text", "json"), default="text")

    why_p = sub.add_parser(
        "why", help="Where a fact came from, and which detectors found nothing")
    add_path(why_p)
    why_p.add_argument("subject", nargs="?", help="Project-relative file path; omit for coverage only")
    why_p.add_argument("--predicate", help="Only facts with this predicate")
    why_p.add_argument("--min-confidence", type=float, dest="min_confidence",
                       help="Only facts at or above this confidence")
    why_p.add_argument("--format", choices=("text", "json"), default="text")

    mcp_p = sub.add_parser("mcp", help="Start the read-only stdio MCP server")
    add_path(mcp_p)

    bench_p = sub.add_parser("bench", help="Measure EOS tools against plain alternatives on this project")
    add_path(bench_p)
    bench_p.add_argument("--samples", type=int, default=50, help="How many symbols to sample (default: 50)")

    ui_p = sub.add_parser("ui", help="Start the multi-project dashboard")
    ui_sub = ui_p.add_subparsers(dest="ui_command")
    ui_p.set_defaults(ui_command="start", yes=False, port=None, no_install=False)
    # `eos ui` already means `eos ui start`, so the start flags have to parse
    # on the bare form too -- the README documents `eos ui --yes` and it exited
    # 2 with `unrecognized arguments`, which is a document sending a reader
    # into an argparse error.
    ui_p.add_argument("--yes", action="store_true", help="Install missing packages without asking")
    ui_p.add_argument("--no-install", dest="no_install", action="store_true",
                      help="Report missing packages and stop")
    ui_start_p = ui_sub.add_parser("start", help="Start the dashboard")
    ui_start_p.add_argument("port", nargs="?", type=int, default=None)
    ui_start_p.add_argument("--yes", action="store_true", help="Install missing packages without asking")
    ui_start_p.add_argument("--no-install", dest="no_install", action="store_true", help="Report missing packages and stop")
    ui_sub.add_parser("uninstall", help="Remove EOS's own venv")

    parents_p = sub.add_parser("parents", help="List configured parent-project links")
    add_path(parents_p)

    parent_p = sub.add_parser(
        "parent", help="Real source for a symbol from a linked parent project")
    add_path(parent_p)
    parent_p.add_argument("symbol", help="Symbol name, or part of one")
    parent_p.add_argument("--limit", type=int, default=3,
                          help="How many matches to show source for (default: 3)")
    parent_p.add_argument("--format", choices=("text", "json"), default="text")

    note_p = sub.add_parser("note", help="Manage authored knowledge notes")
    note_sub = note_p.add_subparsers(dest="note_command", required=True)

    note_add_p = note_sub.add_parser("add", help="Record a note")
    add_path(note_add_p)
    note_add_p.add_argument("--kind", choices=notes.KINDS, required=True)
    note_add_p.add_argument("--title", required=True)
    note_add_p.add_argument("--body", help="Note body (required for 'finding')")
    note_add_p.add_argument("--tags", help="Comma-separated tags")
    note_add_p.add_argument("--scope", help="Comma-separated files/classes this note is about")
    note_add_p.add_argument("--source", help="Issue or PR reference, e.g. TICKET-123")
    note_add_p.add_argument("--cause", help="Root cause (required for 'defect')")
    note_add_p.add_argument("--solution", help="Fix applied (required for 'defect')")
    note_add_p.add_argument("--metric", help="Measured before/after (required for 'defect')")
    note_add_p.add_argument("--session", default=None, help="Session id, set by the gate hook")
    note_add_p.add_argument("--execution", help="For a lesson: the run that taught it (eos run list)")
    note_add_p.add_argument("--procedure", help="For a lesson: the procedure it concerns; for a "
                            "procedure: its slug")

    note_list_p = note_sub.add_parser("list", help="List notes")
    add_path(note_list_p)
    note_list_p.add_argument("--tag", help="Only notes carrying this tag")

    note_show_p = note_sub.add_parser("show", help="Print one note in full")
    add_path(note_show_p)
    note_show_p.add_argument("name", help="Note file name, or part of its title")

    note_search_p = note_sub.add_parser("search", help="Search notes by relevance")
    add_path(note_search_p)
    note_search_p.add_argument("query")
    note_search_p.add_argument("--limit", type=int, default=None)

    note_eval_p = note_sub.add_parser(
        "eval", help="Score note search against a golden file of question/answer pairs")
    add_path(note_eval_p)
    note_eval_p.add_argument("golden", help="TSV: '<question><TAB><note filename>' per line")
    # Kept in step with core.retrieval.DEFAULT_DEPTH by the test below it; the
    # module is imported in the handler, not here, so the parser stays cheap.
    note_eval_p.add_argument("--depth", type=int, default=10,
                             help="How far down to look for the expected note")
    note_eval_p.add_argument("--format", choices=("text", "json"), default="text")

    note_skip_p = note_sub.add_parser("skip", help="Record that this session needs no note")
    add_path(note_skip_p)
    note_skip_p.add_argument("--reason", required=True, help="Why nothing durable was learned")
    note_skip_p.add_argument("--session", default=None, help="Session id, set by the gate hook")

    note_amend_p = note_sub.add_parser("amend", help="Revise a note and re-hash its scope")
    note_amend_p.add_argument("note", help="Path to the note file")
    add_path(note_amend_p)
    amend_what = note_amend_p.add_mutually_exclusive_group()
    amend_what.add_argument("--body", help="New body; '-' reads stdin")
    amend_what.add_argument("--reaffirm", help="The note still holds; say why")
    note_amend_p.add_argument(
        "--scope",
        help="Comma-separated files/classes this note is about; replaces the "
        "scope list wholesale. May stand alone or combine with --body/--reaffirm.",
    )
    note_amend_p.add_argument("--session", default=None, help="Session id, set by the gate hook")

    note_audit_p = note_sub.add_parser("audit", help="Report notes whose scoped files changed")
    add_path(note_audit_p)

    brief_p = sub.add_parser(
        "brief", help="What a session needs before it starts: in flight, and known here")
    add_path(brief_p)
    brief_p.add_argument("--session", default=None, help="Session id, so 'yours' means something")
    brief_p.add_argument("--agent", default=None, help="Which agent this is, e.g. claude or devin")
    brief_p.add_argument("--task", default=None,
                         help="What this session is about to do; leads the brief with the procedure, "
                              "its last runs and lessons. Used as a query and never stored")
    brief_p.add_argument("--budget", type=int, default=None,
                         help="Token budget for a --task brief (default 1500)")
    brief_p.add_argument("--task-only", action="store_true",
                         help="Only the task sections, and nothing at all when none found anything "
                              "(for a hook that fires on every prompt)")

    route_p = sub.add_parser(
        "route", help="Which model and how much effort a task deserves, and why (ADR-025)")
    add_path(route_p)
    route_p.add_argument("task", nargs="*",
                         help="What the task is; quoting is optional. Used as a query and never stored")
    route_p.add_argument("--file", action="append", default=None,
                         help="A project-relative file the task touches; repeatable. Feeds the "
                              "file-count and dependency factors")
    route_p.add_argument("--model", default=None, help="Fix the model (a registry id or alias), or 'auto'")
    route_p.add_argument("--effort", default=None, help="Fix the effort (low|medium|high|xhigh|max), or 'auto'")
    route_p.add_argument("--json", action="store_true", help="Print the decision as JSON")
    route_p.add_argument("--fresh", action="store_true",
                         help="Decide again even if this run already has a decision")
    route_p.add_argument("--no-record", action="store_true",
                         help="Decide without writing the trace line or the run event")
    route_p.add_argument("--session", default=None, help="Session id; filled from the harness when omitted")
    route_p.add_argument("--stats", action="store_true",
                         help="Recorded decisions joined to their runs' outcomes; ignores the task")
    route_p.add_argument("--usage-from", default=None, metavar="TRANSCRIPT",
                         help="Fold a harness transcript into this session's model and token totals "
                              "(for a Stop hook); ignores the task")
    route_p.add_argument("--eval", default=None, metavar="CORPUS",
                         help="Score the policy against a labelled TSV corpus (prompt, type, level, "
                              "model, effort, split); no model is called. Exit 0 when the gate "
                              "passes, 1 when it fails")
    route_p.add_argument("--split", choices=("dev", "test"), default=None,
                         help="--eval: only the rows of this split; test prints metrics and the "
                              "gate but no rows or suggestions, and is recorded")

    proc_p = sub.add_parser("procedure", help="How a recurring task is done here, and how it has gone (ADR-023)")
    proc_sub = proc_p.add_subparsers(dest="procedure_command", required=True)

    proc_list_p = proc_sub.add_parser("list", help="Procedures with their run counts")
    add_path(proc_list_p)
    proc_list_p.add_argument("--tool", help="Only procedures whose steps name this tool")
    proc_list_p.add_argument("--target", help="Only procedures that have run against this target")
    proc_list_p.add_argument("--format", choices=("text", "json"), default="text")

    proc_show_p = proc_sub.add_parser("show", help="One procedure: steps, counts, recent runs")
    add_path(proc_show_p)
    proc_show_p.add_argument("procedure", help="Slug, slug prefix, or part of the title")
    proc_show_p.add_argument("--format", choices=("text", "json"), default="text")

    proc_new_p = proc_sub.add_parser("new", help="Write a procedure note")
    add_path(proc_new_p)
    proc_new_p.add_argument("--title", required=True)
    proc_new_p.add_argument("--step", action="append", help="One step; repeat in order. `(tool: X)` names its tool")
    proc_new_p.add_argument("--steps", choices=("-",), help="Read steps from stdin, one per line")
    proc_new_p.add_argument("--prerequisite", action="append", help="Repeat for each")
    proc_new_p.add_argument("--success", action="append", help="What proves it worked; repeat for each")
    proc_new_p.add_argument("--body", help="Prose before the steps")
    proc_new_p.add_argument("--slug", help="Slug executions will name; defaults to the title's")
    proc_new_p.add_argument("--tags")
    proc_new_p.add_argument("--scope", help="Comma-separated files this procedure depends on")
    proc_new_p.add_argument("--source")
    proc_new_p.add_argument("--session", default=None)

    proc_audit_p = proc_sub.add_parser("audit", help="Counters against the ledger, failing and unverified procedures")
    add_path(proc_audit_p)
    proc_audit_p.add_argument("--format", choices=("text", "json"), default="text")

    run_p = sub.add_parser("run", help="What a session did: executions and their events (ADR-022)")
    run_sub = run_p.add_subparsers(dest="run_command", required=True)

    def run_actor(p):
        p.add_argument("--session", default=None,
                       help="Session id; defaults to the harness's own (EOS_SESSION, CLAUDE_CODE_SESSION_ID)")

    run_start_p = run_sub.add_parser("start", help="Open an execution; prints its id")
    add_path(run_start_p)
    run_start_p.add_argument("--title", required=True, help="What this run is, as the session declares it")
    run_start_p.add_argument("--procedure", help="Slug of the procedure this run follows")
    run_start_p.add_argument("--work", help="Work item id this run belongs to")
    run_start_p.add_argument("--target", help="Environment or system this run acts on")
    run_start_p.add_argument("--agent", help="Which agent this is, e.g. claude or devin")
    run_actor(run_start_p)

    run_event_p = run_sub.add_parser("event", help="Append one thing the session did")
    add_path(run_event_p)
    run_event_p.add_argument("execution", nargs="?", default=None,
                             help="Execution id; defaults to this session's open one")
    run_event_p.add_argument("--kind", required=True,
                             choices=("ran", "read", "changed", "called", "verified", "noted", "decided"))
    run_event_p.add_argument("--tool", help="The wrapper or program that did it")
    run_event_p.add_argument("--target", help="Environment or system it acted on")
    run_event_p.add_argument("--ref", help="Log path, cache path, build id or file -- a reference, never a payload")
    run_event_p.add_argument("--exit", type=int, default=None, help="Exit code")
    run_event_p.add_argument("--ms", type=int, default=None, help="Duration in milliseconds")
    run_event_p.add_argument("--body", help="One sentence, when the ref does not say it")
    run_actor(run_event_p)

    run_finish_p = run_sub.add_parser("finish", help="Close an execution with its outcome")
    add_path(run_finish_p)
    run_finish_p.add_argument("execution", nargs="?", default=None,
                              help="Execution id; defaults to this session's open one")
    run_finish_p.add_argument("--outcome", required=True, choices=("ok", "failed", "abandoned"))
    run_finish_p.add_argument("--lesson", help="What was learned; required for --outcome failed "
                              "unless a lesson note already names this run. Written as a lesson note")
    run_finish_p.add_argument("--next-time", help="What the next run of this should do differently")
    run_actor(run_finish_p)

    run_list_p = run_sub.add_parser("list", help="Executions, most recent first")
    add_path(run_list_p)
    run_list_p.add_argument("--session", default=None)
    run_list_p.add_argument("--procedure")
    run_list_p.add_argument("--target")
    run_list_p.add_argument("--outcome", choices=("ok", "failed", "abandoned", "open"))
    run_list_p.add_argument("--since", help="ISO date; runs started on or after it")
    run_list_p.add_argument("--limit", type=int, default=20)
    run_list_p.add_argument("--format", choices=("text", "json"), default="text")

    run_tools_p = run_sub.add_parser("tools", help="Which tools ran, how often, and how the last run went")
    add_path(run_tools_p)
    run_tools_p.add_argument("--procedure")
    run_tools_p.add_argument("--target")
    run_tools_p.add_argument("--format", choices=("text", "json"), default="text")

    run_diff_p = run_sub.add_parser("diff", help="What one execution changed: recorded paths and its commit range")
    add_path(run_diff_p)
    run_diff_p.add_argument("execution", help="Execution id, id prefix, or part of its title")
    run_diff_p.add_argument("--format", choices=("text", "json"), default="text")

    run_show_p = run_sub.add_parser("show", help="One execution and its timeline")
    add_path(run_show_p)
    run_show_p.add_argument("execution", help="Execution id, id prefix, or part of its title")
    run_show_p.add_argument("--format", choices=("text", "json"), default="text")

    work_p = sub.add_parser("work", help="What is in flight, across sessions")
    work_sub = work_p.add_subparsers(dest="work_command", required=True)

    def add_actor(p):
        # Both are optional and both are only ever markers: who wrote this
        # line, not a permission or an identity. The ledger tolerates their
        # absence -- an item held by "nobody" still reads as held.
        p.add_argument("--session", default=None, help="Session id, so a claim has a holder")
        p.add_argument("--agent", default=None, help="Which agent this is, e.g. claude or devin")

    work_add_p = work_sub.add_parser("add", help="Record a piece of work")
    add_path(work_add_p)
    work_add_p.add_argument("--title", required=True)
    work_add_p.add_argument("--body", help="What this is, in a sentence the next session can act on")
    work_add_p.add_argument("--ticket", help="Issue key, e.g. TICKET-123")
    work_add_p.add_argument("--scope", help="Comma-separated files this work touches")
    work_add_p.add_argument("--claim", action="store_true",
                            help="Claim it in the same command; the usual case")
    add_actor(work_add_p)

    work_claim_p = work_sub.add_parser("claim", help="Take an item; a second claim is reported")
    add_path(work_claim_p)
    work_claim_p.add_argument("item", help="Item id, or part of its title")
    work_claim_p.add_argument("--note", help="What you are about to do")
    add_actor(work_claim_p)

    work_log_p = work_sub.add_parser("log", help="Record progress without changing status")
    add_path(work_log_p)
    work_log_p.add_argument("item", help="Item id, or part of its title")
    work_log_p.add_argument("--body", required=True, help="Where this got to")
    add_actor(work_log_p)

    work_block_p = work_sub.add_parser("block", help="Record that this is stuck, and on what")
    add_path(work_block_p)
    work_block_p.add_argument("item", help="Item id, or part of its title")
    work_block_p.add_argument("--reason", required=True, help="What it is waiting on")
    add_actor(work_block_p)

    work_unblock_p = work_sub.add_parser("unblock", help="Record that the blocker cleared")
    add_path(work_unblock_p)
    work_unblock_p.add_argument("item", help="Item id, or part of its title")
    work_unblock_p.add_argument("--note", help="What cleared it")
    add_actor(work_unblock_p)

    work_done_p = work_sub.add_parser("done", help="Record that a session finished it")
    add_path(work_done_p)
    work_done_p.add_argument("item", help="Item id, or part of its title")
    work_done_p.add_argument("--note", help="What was done")
    add_actor(work_done_p)

    work_drop_p = work_sub.add_parser("drop", help="Record that this will not be done, and why")
    add_path(work_drop_p)
    work_drop_p.add_argument("item", help="Item id, or part of its title")
    work_drop_p.add_argument("--reason", required=True, help="Why it was dropped")
    add_actor(work_drop_p)

    work_list_p = work_sub.add_parser("list", help="What is in flight here")
    add_path(work_list_p)
    work_list_p.add_argument("--status", default="live",
                             choices=("live", "open", "active", "blocked", "done", "dropped", "all"),
                             help="Default 'live': active, blocked and untaken work")
    work_list_p.add_argument("--across", action="store_true",
                             help="Every sibling ledger under the same knowledge root")
    work_list_p.add_argument("--session", default=None, help="Only items this session holds")
    work_list_p.add_argument("--format", choices=("text", "json"), default="text")

    work_show_p = work_sub.add_parser("show", help="One item, its events, and its commits")
    add_path(work_show_p)
    work_show_p.add_argument("item", help="Item id, or part of its title")
    work_show_p.add_argument("--format", choices=("text", "json"), default="text")

    work_stats_p = work_sub.add_parser(
        "stats", help="What happened here: collisions, claims gone quiet, time to close")
    add_path(work_stats_p)
    work_stats_p.add_argument("--since", default=None,
                              help="Only events at or after this date, e.g. 2026-09-01")
    work_stats_p.add_argument("--format", choices=("text", "json"), default="text")

    ai_p = sub.add_parser("ai", help="Manage the AI integration files")
    ai_sub = ai_p.add_subparsers(dest="ai_command", required=True)
    ai_update_p = ai_sub.add_parser("update", help="Refresh the integration files for this EOS version")
    add_path(ai_update_p)
    ai_update_p.add_argument("--no-agents-md", dest="no_agents_md", action="store_true",
                             help="Leave AGENTS.md alone (for a repository that tracks it)")
    ai_update_p.add_argument("--surface", choices=("cli", "mcp", "both"), default=None,
                             help="Change which surface this project exposes; without it, "
                                  "the choice recorded at init is kept")
    ai_update_p.add_argument("--claude", choices=("files", "plugin"), default=None,
                             help="How Claude Code gets the hooks, skill and agent: copied into "
                                  ".claude/ (files) or from the EOS plugin (plugin), which also "
                                  "removes the copies; the choice is recorded")

    caps_p = sub.add_parser(
        "capabilities",
        help="The wrappers this project offers instead of raw commands (capabilities.toml, ADR-026)")
    add_path(caps_p)
    caps_p.add_argument("--for", dest="task", default=None,
                        help="Only the capabilities a task's words point at")
    caps_p.add_argument("--command", dest="raw", default=None,
                        help="Which capability, if any, covers this command line")
    caps_p.add_argument("--format", choices=("text", "json"), default="text")

    hook_p = sub.add_parser(
        "hook", help="Handle one harness hook event; the event JSON arrives on stdin (ADR-026)")
    hook_p.add_argument("event", help="session-start, user-prompt, post-tool, post-tool-failure, "
                                      "subagent-start, subagent-stop, instructions-loaded, "
                                      "stop-failure, session-end, pre-agent")
    hook_p.add_argument("--agent", default=None, help="Which agent the harness is, e.g. claude")
    hook_p.add_argument("--project", default=None, help="Project root (default: from the event's cwd)")

    args = parser.parse_args(argv)

    commands = {
        "init": cmd_init,
        "scan": cmd_scan,
        "update": cmd_update,
        "doctor": cmd_doctor,
        "info": cmd_info,
        "clean": cmd_clean,
        "index": cmd_index,
        "query": cmd_query,
        "status": cmd_status,
        "graph": cmd_graph,
        "context": cmd_context,
        "compose": cmd_compose,
        "impact": cmd_impact,
        "why": cmd_why,
        "rules": cmd_rules,
        "trace": cmd_trace,
        "ask": cmd_ask,
        "draft-test": cmd_draft_test,
        "verify": cmd_verify,
        "findings": cmd_findings,
        "cost": cmd_cost,
        "mcp": cmd_mcp,
        "bench": cmd_bench,
        "ui": cmd_ui,
        "parent": cmd_parent,
        "parents": cmd_parents,
        "note": cmd_note,
        "work": cmd_work,
        "run": cmd_run,
        "procedure": cmd_procedure,
        "brief": cmd_brief,
        "route": cmd_route,
        "ai": cmd_ai,
        "capabilities": cmd_capabilities,
        "hook": cmd_hook,
    }
    handler = commands[args.command]
    path = getattr(args, "path", None)
    # One session id across every store (ADR-022). A write that names no
    # session gets the harness's own, the way telemetry reads it, so "what did
    # this session do" joins executions, work and notes without anyone having
    # remembered to pass --session. Writes only: on `work list` and `run list`
    # --session is a filter, and filling it would silently narrow the answer.
    sub_command = getattr(args, f"{args.command}_command", None)
    if (args.command, sub_command) in _FILL_SESSION and not getattr(args, "session", None) and path:
        from core import telemetry
        args.session, _ = telemetry.detect_session(path)
    if path is None or args.command in ("init", "ui", "mcp", "cost"):
        # init has no project yet, ui and mcp are long-running rather than one
        # answer, and cost reading itself would be a call that changes what it
        # reports.
        return handler(args)

    from core import telemetry

    # Flag *names*, never their values: a search query, a task or a note body
    # is whatever somebody typed, and a log that captured them would be a
    # liability in every project EOS touches.
    flags = [f"--{name.replace('_', '-')}" for name, value in vars(args).items()
             if name not in ("command", "path", "func") and value not in (None, False)]
    # The environment is what makes this measurable at all: only a handful of
    # commands take --session, and the harness already knows which session it
    # is running. Claude Code exports CLAUDE_CODE_SESSION_ID into every
    # command it runs, so sessions are counted with nothing installed and
    # nothing configured; EOS_SESSION is for a harness that exports no id of
    # its own.
    session = getattr(args, "session", None)
    session_from = "--session" if session else None
    if not session:
        session, session_from = telemetry.detect_session(path)
    with telemetry.Timer(path, args.command, flags, session=session,
                         session_from=session_from) as timer:
        # A pass-through counter, not a buffer. Buffering stdout and replaying
        # it broke the escaping a non-UTF-8 terminal needs -- measured by the
        # test that exists for it -- and a statistic may not change what a
        # command prints.
        counter = _CountingStream(sys.stdout)
        sys.stdout = counter
        try:
            code = handler(args)
        finally:
            sys.stdout = counter.wrapped
            timer.chars = counter.chars
    return code


class _CountingStream:
    """Forwards everything to the real stream and counts the characters."""

    def __init__(self, wrapped):
        self.wrapped = wrapped
        self.chars = 0

    def write(self, text):
        self.chars += len(text)
        return self.wrapped.write(text)

    def __getattr__(self, name):
        return getattr(self.wrapped, name)


if __name__ == "__main__":
    sys.exit(main())
