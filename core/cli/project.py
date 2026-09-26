"""Project lifecycle commands: init, scan, update, doctor, index, graph, context, ai.

Moved out of core/eos.py (2.x roadmap F4), unchanged.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
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
from core.cli.common import VERSION, _describe_index, _eos_dir, _escape_what_stdout_cannot_encode, _parent_ref_for, _read_ai_key, _read_surface, _report_ledger, _route_hook_wanted, _store_ai_key, _store_surface, _warn_stale_mcp, _CORE_ROOT


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


def cmd_index(args: argparse.Namespace) -> int:
    _escape_what_stdout_cannot_encode()
    try:
        result = index.build(args.path)
    except index.IndexBuildError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(_describe_index(result))
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


def cmd_hook(args: argparse.Namespace) -> int:
    """The argparse face of `eos hook`; the harness path is dispatched before imports."""
    from core import hooks

    forwarded = [args.event]
    if args.agent:
        forwarded += ["--agent", args.agent]
    if args.project:
        forwarded += ["--project", args.project]
    return hooks.main(forwarded)


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
