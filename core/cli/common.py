"""Helpers and constants the command modules share.

Moved out of core/eos.py (2.x roadmap F4), unchanged.
"""
from __future__ import annotations

import sys
from pathlib import Path
from core.lib.config_io import ConfigIO
from core import index
from core import notes
from core.plugins.registry import PluginRegistry

_HERE = Path(__file__).resolve().parent.parent
_CORE_ROOT = _HERE.parent


VERSION = (_HERE / "VERSION").read_text(encoding="utf-8").strip()


EOS_DIR = ".eos"


def _eos_dir(path: Path) -> Path:
    return path / EOS_DIR


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


def _query_cell(value) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bytes):
        return value.hex()
    return str(value).replace("\t", "\\t").replace("\r", "\\r").replace("\n", "\\n")


def _print_fact(entry: dict, prefix: str) -> None:
    value = f"{entry['predicate']}={entry['object']}" if entry["object"] else entry["predicate"]
    print(f"{prefix}{value:<44} {entry['origin']:<10} {entry['confidence']:.2f} "
          f"{entry['detector']:<16} {entry['source_ref'] or ''}")


def _split_csv(value: str | None) -> list[str] | None:
    if not value:
        return None
    return [item.strip() for item in value.split(",") if item.strip()]


def _no_notes_here(path: str) -> str:
    """The sentence for a project that has recorded nothing yet.

    Printing nothing is the one answer a reader cannot act on: an empty list
    and a misconfigured notes directory look identical on a terminal, and
    every FM service points [knowledge] dir somewhere outside the service, so
    "looking in the wrong place" is not hypothetical. Name the place."""
    return (f"No notes recorded here yet ({notes.notes_dir(path)}). "
            "`eos note add` records the first one.")


# Same wording the Stop hook's block message uses (automation/hooks/note_gate.py
# `_message`), verbatim -- an agent that has seen the hook's block and now sees
# this report should be taught the same rule once, not two slightly different
# ones.
_PLACEHOLDER_WARNING = (
    "Angle-bracketed text below is a placeholder: replace it, do not run it as printed."
)


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


def _read_git_ref(repo: Path) -> str | None:
    head = repo / ".git" / "HEAD"
    if not head.is_file():
        return None
    content = head.read_text(encoding="utf-8", errors="replace").strip()
    if content.startswith("ref: refs/heads/"):
        return content[len("ref: refs/heads/"):]
    return content[:7] if content else None


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
