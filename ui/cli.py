#!/usr/bin/env python3
"""eos-ui CLI — manage scan-roots, reconcile, and list instances.

Commands::

  roots add <path>...   Register scan-roots.
  roots list            List scan-roots.
  roots remove <id>     Remove a scan-root by id.
  reconcile             Walk roots and upsert .eos instances by ID.
  instances [id]        List all instances, or show one in detail.
  scan <id>             Run ``eos scan`` on the instance's project path.
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

# Bootstrap repo root so `ui.app` is importable when run as a script.
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from ui.app import db, models, reconcile  # noqa: E402


def _conn():
    return db.connect()


def _print_roots(conn) -> None:
    rows = db.list_scan_roots(conn)
    if not rows:
        print("(no scan-roots registered)")
        return
    print(f"{'ID':<4} {'LAST_SCAN':<20} PATH")
    for r in rows:
        print(f"{r['id']:<4} {(r['last_scan_at'] or 'never')[:20]:<20} {r['path']}")


def cmd_roots_add(args: argparse.Namespace) -> int:
    conn = _conn()
    for p in args.paths:
        db.upsert_scan_root(conn, str(Path(p).expanduser().resolve()))
    _print_roots(conn)
    conn.close()
    return 0


def cmd_roots_list(args: argparse.Namespace) -> int:
    conn = _conn()
    _print_roots(conn)
    conn.close()
    return 0


def cmd_roots_remove(args: argparse.Namespace) -> int:
    conn = _conn()
    match = next((r for r in db.list_scan_roots(conn) if r["id"] == args.id), None)
    if not match:
        print(f"No scan-root with id {args.id}")
        conn.close()
        return 1
    db.delete_scan_root(conn, match["path"])
    _print_roots(conn)
    conn.close()
    return 0


def cmd_reconcile(args: argparse.Namespace) -> int:
    conn = _conn()
    report = reconcile.reconcile(conn)
    print(f"Roots scanned : {len(report.roots_scanned)}")
    for r in report.roots_scanned:
        print(f"  - {r}")
    print(f"Added         : {len(report.added)}")
    print(f"Updated       : {len(report.updated)}")
    print(f"Marked missing: {report.missing_marked}")
    conn.close()
    return 0


def cmd_instances(args: argparse.Namespace) -> int:
    conn = _conn()
    if args.id:
        row = db.get_instance(conn, args.id)
        if not row:
            print(f"No instance with id {args.id}")
            conn.close()
            return 1
        print(json.dumps(models.row_to_instance(row).model_dump(), indent=2, ensure_ascii=False))
    else:
        rows = db.list_instances(conn)
        print(f"{'ID':<10} {'STATUS':<8} {'ENGINE':<8} {'NAME':<20} PATH")
        for r in rows:
            inst = models.row_to_instance(r)
            print(f"{inst.id[:8]:<10} {inst.status:<8} {inst.engine_version:<8} {(inst.name or '')[:20]:<20} {inst.path}")
    conn.close()
    return 0


def cmd_scan(args: argparse.Namespace) -> int:
    conn = _conn()
    row = db.get_instance(conn, args.id)
    conn.close()
    if not row:
        print(f"No instance with id {args.id}")
        return 1
    eos_py = _ROOT / "core" / "eos.py"
    print(f"Scanning {row['path']} ...")
    return subprocess.call([sys.executable, str(eos_py), "scan", row["path"]])


def cmd_watch(args: argparse.Namespace) -> int:
    """Watch all scan-roots and rescan affected projects on file change."""
    from ui.watcher import WatcherService

    conn = _conn()
    roots = db.list_scan_roots(conn)
    if not roots:
        print("No scan-roots registered. Add one with 'eos-ui roots add <path>' first.")
        conn.close()
        return 1
    print(f"Watching {len(roots)} scan-root(s):")
    for r in roots:
        print(f"  - {r['path']}")
    print(f"Rescan delay: {args.delay}s. Ctrl-C to stop.\n")

    def on_scan(project_path, result):
        status = "ok" if result.returncode == 0 else "FAILED"
        print(f"[scan {status}] {project_path}")

    queue = None
    if args.graphify:
        from ui.app.graphify_queue import GraphifyQueue, queue_state_path

        # Publish queue state so the (separate) API process can report failures.
        queue = GraphifyQueue(delay=args.graphify_delay, state_path=queue_state_path())
        print(f"Graphify refresh enabled (delay {args.graphify_delay}s).")

    watcher = WatcherService(scan_delay=args.delay, on_scan=on_scan, graphify_queue=queue)
    watcher.start(conn)
    conn.close()
    try:
        import time
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        print("\nStopping watcher...")
        watcher.stop()
        if queue is not None:
            for project_path in queue.stop():
                print(f"[graphify] refresh still running: {project_path}")
        print("Stopped.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="eos-ui", description="EOS UI management CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    roots = sub.add_parser("roots", help="Manage scan-roots")
    roots_sub = roots.add_subparsers(dest="roots_cmd", required=True)
    add_p = roots_sub.add_parser("add", help="Add scan-roots")
    add_p.add_argument("paths", nargs="+")
    roots_sub.add_parser("list", help="List scan-roots")
    rm_p = roots_sub.add_parser("remove", help="Remove a scan-root by id")
    rm_p.add_argument("id", type=int)

    sub.add_parser("reconcile", help="Reconcile .eos instances from roots")
    inst = sub.add_parser("instances", help="List or show instances")
    inst.add_argument("id", nargs="?", default=None)
    scan_p = sub.add_parser("scan", help="Run eos scan on an instance's project")
    scan_p.add_argument("id")
    watch_p = sub.add_parser("watch", help="Watch scan-roots and rescan on change")
    watch_p.add_argument("--delay", type=float, default=1.5, help="Debounce delay in seconds")
    watch_p.add_argument(
        "--graphify",
        action="store_true",
        help="Refresh the Graphify artifact after each successful scan",
    )
    watch_p.add_argument(
        "--graphify-delay",
        type=float,
        default=5.0,
        help="Debounce delay in seconds for Graphify refreshes",
    )

    args = parser.parse_args(argv)
    if args.command == "roots":
        return {
            "add": cmd_roots_add,
            "list": cmd_roots_list,
            "remove": cmd_roots_remove,
        }[args.roots_cmd](args)
    return {
        "reconcile": cmd_reconcile,
        "instances": cmd_instances,
        "scan": cmd_scan,
        "watch": cmd_watch,
    }[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
