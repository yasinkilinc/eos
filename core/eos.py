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

from core.lib.cache_store import CacheStore
from core.lib.config_io import ConfigIO
from core.knowledge.builder import KnowledgeBuilder
from core.scanner import Scanner
from core.generators.markdown.brain import BrainGenerator
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

        written = writer.write_all(root, VERSION)
        print("  AI integration:")
        for path in written:
            print(f"    {path.relative_to(root)}")
    return 0


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

    # Update metadata
    metadata = {
        "languages": project.detected_languages,
        "files_parsed": len(project.files),
        "nodes": len(graph.nodes),
        "edges": len(graph.edges),
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
    output_path = root / ".eos" / "data" / "brain" / "llm_context.md"
    output_path.write_text(context, encoding="utf-8")
    print(f"Context written to {output_path} ({len(context)} characters)")
    return 0


def cmd_compose(args: argparse.Namespace) -> int:
    print(inspector.compose(args.path, args.task, args.target, budget=args.budget))
    return 0


def cmd_impact(args: argparse.Namespace) -> int:
    print(json.dumps(inspector.impact(args.path, args.file), indent=2, ensure_ascii=False))
    return 0


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
        )
    except (ValueError, FileExistsError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"Note written to {path}")
    return 0


def cmd_note_list(args: argparse.Namespace) -> int:
    matches = notes.load_notes(args.path)
    if args.tag:
        matches = [note for note in matches if args.tag in note.tags]
    for note in matches:
        print(f"{note.path.name}\t{note.kind}\t{note.title}")
    return 0


def cmd_note_search(args: argparse.Namespace) -> int:
    for note in notes.search_notes(args.path, args.query, limit=args.limit):
        print(f"{note.path.name}\t{note.kind}\t{note.title}")
    return 0


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
        print("No stale notes.")
    return 0


def cmd_note(args: argparse.Namespace) -> int:
    return {
        "add": cmd_note_add,
        "list": cmd_note_list,
        "search": cmd_note_search,
        "skip": cmd_note_skip,
        "amend": cmd_note_amend,
        "audit": cmd_note_audit,
    }[args.note_command](args)


def _read_git_ref(repo: Path) -> str | None:
    head = repo / ".git" / "HEAD"
    if not head.is_file():
        return None
    content = head.read_text(encoding="utf-8", errors="replace").strip()
    if content.startswith("ref: refs/heads/"):
        return content[len("ref: refs/heads/"):]
    return content[:7] if content else None


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
    for path in writer.write_all(root, VERSION):
        print(f"  {path.relative_to(root)}")
    return 0


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

    scan_p = sub.add_parser("scan", help="Scan project and regenerate knowledge artifacts")
    add_path(scan_p)
    scan_p.add_argument("--full", action="store_true", help="Force full rescan ignoring cache")
    scan_p.add_argument("--with-parents", dest="with_parents", action="store_true", help="Also index linked parent projects")

    update_p = sub.add_parser("update", help="Update eos-core runtime from canonical core/")
    add_path(update_p)
    update_p.add_argument("--dry-run", action="store_true", help="Preview changes without applying")

    doctor_p = sub.add_parser("doctor", help="Validate .eos/ integrity")
    add_path(doctor_p)

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
    query_p.add_argument("--search", help="Full-text search over notes, brain docs and journeys instead of SQL")
    query_p.add_argument("--limit", type=int, default=20, help="Maximum --search results")

    status_p = sub.add_parser("status", help="Show project status and latest scan metadata")
    add_path(status_p)

    graph_p = sub.add_parser("graph", help="Export the generated project graph")
    add_path(graph_p)
    graph_p.add_argument("--type", default="import", help="Edge type to include, or 'all'")
    graph_p.add_argument("--format", choices=("json",), default="json", help="Output format")
    graph_p.add_argument("--output", default=None, help="Output path, or '-' for stdout")

    context_p = sub.add_parser("context", help="Generate an AI-oriented project context")
    add_path(context_p)
    context_p.add_argument("--budget", type=int, default=12000, help="Approximate token budget")

    compose_p = sub.add_parser("compose", help="Compose focused context for a task")
    add_path(compose_p)
    compose_p.add_argument("task", help="Task or focus description")
    compose_p.add_argument("target", nargs="?", help="Optional project-relative target file")
    compose_p.add_argument("--budget", type=int, default=12000, help="Approximate token budget")

    impact_p = sub.add_parser("impact", help="Analyze direct import impact for a file")
    add_path(impact_p)
    impact_p.add_argument("file", help="Project-relative file path")

    mcp_p = sub.add_parser("mcp", help="Start the read-only stdio MCP server")
    add_path(mcp_p)

    bench_p = sub.add_parser("bench", help="Measure EOS tools against plain alternatives on this project")
    add_path(bench_p)
    bench_p.add_argument("--samples", type=int, default=50, help="How many symbols to sample (default: 50)")

    ui_p = sub.add_parser("ui", help="Start the multi-project dashboard")
    ui_sub = ui_p.add_subparsers(dest="ui_command")
    ui_p.set_defaults(ui_command="start", yes=False, port=None, no_install=False)
    ui_start_p = ui_sub.add_parser("start", help="Start the dashboard")
    ui_start_p.add_argument("port", nargs="?", type=int, default=None)
    ui_start_p.add_argument("--yes", action="store_true", help="Install missing packages without asking")
    ui_start_p.add_argument("--no-install", dest="no_install", action="store_true", help="Report missing packages and stop")
    ui_sub.add_parser("uninstall", help="Remove EOS's own venv")

    parents_p = sub.add_parser("parents", help="List configured parent-project links")
    add_path(parents_p)

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

    note_list_p = note_sub.add_parser("list", help="List notes")
    add_path(note_list_p)
    note_list_p.add_argument("--tag", help="Only notes carrying this tag")

    note_search_p = note_sub.add_parser("search", help="Search notes by relevance")
    add_path(note_search_p)
    note_search_p.add_argument("query")
    note_search_p.add_argument("--limit", type=int, default=None)

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

    ai_p = sub.add_parser("ai", help="Manage the AI integration files")
    ai_sub = ai_p.add_subparsers(dest="ai_command", required=True)
    ai_update_p = ai_sub.add_parser("update", help="Refresh the integration files for this EOS version")
    add_path(ai_update_p)

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
        "mcp": cmd_mcp,
        "bench": cmd_bench,
        "ui": cmd_ui,
        "parents": cmd_parents,
        "note": cmd_note,
        "ai": cmd_ai,
    }
    return commands[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
