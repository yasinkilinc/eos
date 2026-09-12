"""File-watcher service for eos-ui.

Watches registered scan-roots with watchdog and, on file changes, triggers
an incremental ``eos scan`` on the affected project plus a reconcile so the
SQLite metadata stays current. Debounces bursts so a save-storm produces one
rescan, not fifty. When a ``GraphifyQueue`` is supplied, a successful scan also
enqueues a debounced Graphify refresh for that project.

Optional dependency: ``watchdog``. The rest of eos-ui does not import this
module unless watching is enabled, so the core API stays runnable without it.
"""
import logging
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional, Set

from ui.app import db, reconcile

_LOG = logging.getLogger(__name__)

# Wall-clock budget for one incremental ``eos scan``.
_SCAN_TIMEOUT = 120

try:
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer

    _WATCHDOG_OK = True
except ImportError:  # pragma: no cover - exercised only without watchdog
    _WATCHDOG_OK = False
    Observer = None  # type: ignore
    FileSystemEventHandler = object  # type: ignore

# Paths whose changes never warrant a rescan.
#
# This has to stay a superset of core/scanner.py's DEFAULT_IGNORE, and it is
# spelled out rather than imported because ui/ has no dependency on core/ at all
# -- only ui/cli.py puts tools/eos on sys.path, so an import here would work
# under `python -m ui.cli` and break every other entry path, tests included.
#
# The four Maven/Gradle entries are what a build writes, and the scanner throws
# them away afterwards. Without them one `mvn install` measured 2,555 files into
# */target, ~90 rescans, 74.5 MB rewritten under */.eos/data and 706.3 MB under
# graphify-out -- for zero changed source files. That burst is also what armed
# 16 concurrent scans and pushed five of them past the 120s timeout.
_IGNORE_DIRS = {
    ".git", "__pycache__", ".venv", "venv", "node_modules",
    "dist", "build", "target", "bin", ".mvn", ".gradle",
    ".next", ".turbo", ".mypy_cache",
    ".pytest_cache", ".egg-info", ".tox", ".eos",
}
_IGNORE_SUFFIXES = {".pyc", ".pyo", ".log", ".swp"}
_IGNORE_NAMES = {".DS_Store", "Thumbs.db"}


def _is_noise(path: Path) -> bool:
    if any(part in _IGNORE_DIRS for part in path.parts):
        return True
    if path.suffix in _IGNORE_SUFFIXES:
        return True
    if path.name in _IGNORE_NAMES:
        return True
    return False


class _Debouncer:
    """Coalesce rapid events for the same project into a single scan trigger.

    A project already being rescanned is never rescanned concurrently: the
    callback outlives the debounce window by far, so a trigger landing mid-run is
    coalesced into exactly one follow-up instead of starting a second scan.
    """

    def __init__(self, delay: float, callback):
        self.delay = delay
        self.callback = callback
        self._timers: Dict[str, threading.Timer] = {}
        self._running: Set[str] = set()
        self._pending: Set[str] = set()
        self._stopping = False
        self._lock = threading.Lock()

    def trigger(self, project_path: str) -> None:
        with self._lock:
            if self._stopping:
                return
            self._arm(project_path)

    def cancel_all(self) -> None:
        """Drop every pending timer and refuse further callbacks (shutdown)."""
        with self._lock:
            self._stopping = True
            for timer in self._timers.values():
                timer.cancel()
            self._timers.clear()
            self._pending.clear()

    def _arm(self, project_path: str) -> None:
        """Start (or restart) the debounce timer. Caller holds the lock."""
        old = self._timers.pop(project_path, None)
        if old:
            old.cancel()
        t = threading.Timer(self.delay, self._fire, args=(project_path,))
        t.daemon = True
        self._timers[project_path] = t
        t.start()

    def _fire(self, project_path: str) -> None:
        with self._lock:
            self._timers.pop(project_path, None)
            if self._stopping:
                return
            if project_path in self._running:
                self._pending.add(project_path)
                return
            self._running.add(project_path)
        try:
            self.callback(project_path)
        except Exception:
            # Keep the timer thread alive, but never lose the failure.
            _LOG.exception("Rescan failed for %s", project_path)
        finally:
            with self._lock:
                self._running.discard(project_path)
                if project_path in self._pending and not self._stopping:
                    self._pending.discard(project_path)
                    self._arm(project_path)


class WatcherService:
    """Observe scan-roots and rescan affected projects on change."""

    def __init__(
        self,
        scan_delay: float = 1.5,
        on_scan: Optional[callable] = None,
        graphify_queue=None,
    ):
        if not _WATCHDOG_OK:
            raise RuntimeError("watchdog is not installed; pip install watchdog")
        self.scan_delay = scan_delay
        self.on_scan = on_scan  # optional callback(project_path, result)
        self.graphify_queue = graphify_queue  # optional GraphifyQueue
        self._observer: Optional[Observer] = None
        self._debouncer = _Debouncer(scan_delay, self._rescan_project)
        self._watched_roots: List[str] = []
        self._lock = threading.Lock()

    def start(self, conn) -> None:
        """Begin watching all currently registered scan-roots."""
        if self._observer is not None:
            return
        # Fresh debounce state: a previous stop() disarmed the old one for good.
        self._debouncer = _Debouncer(self.scan_delay, self._rescan_project)
        self._observer = Observer()
        for root in db.list_scan_roots(conn):
            self._watch(root["path"])
        self._observer.start()

    def stop(self) -> None:
        # Queued rescans first: a timer firing after shutdown would spawn a scan
        # subprocess and reopen SQLite behind the caller's back.
        self._debouncer.cancel_all()
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=2.0)
            self._observer = None
            self._watched_roots.clear()

    def add_root(self, path: str) -> None:
        with self._lock:
            if path in self._watched_roots:
                return
        self._watch(path)

    def _watch(self, path: str) -> None:
        if not Path(path).is_dir() or self._observer is None:
            return
        self._observer.schedule(
            _Handler(self._debouncer, path),
            path,
            recursive=True,
        )
        with self._lock:
            if path not in self._watched_roots:
                self._watched_roots.append(path)

    def _rescan_project(self, project_path: str) -> None:
        """Run an incremental eos scan on a project and reconcile metadata."""
        import subprocess
        import sys

        repo_root = Path(__file__).resolve().parents[1]
        eos_py = repo_root / "core" / "eos.py"
        if not eos_py.is_file():
            return
        try:
            result = subprocess.run(
                [sys.executable, str(eos_py), "scan", project_path],
                capture_output=True,
                text=True,
                timeout=_SCAN_TIMEOUT,
            )
        except subprocess.TimeoutExpired as exc:
            # Report it as a failed scan instead of aborting the rescan invisibly.
            _LOG.warning("eos scan timed out after %ss: %s", exc.timeout, project_path)
            result = subprocess.CompletedProcess(
                exc.cmd,
                returncode=124,
                stdout="",
                stderr=f"eos scan timed out after {exc.timeout}s",
            )
        # Refresh SQLite metadata for this instance.
        conn = db.connect()
        try:
            reconcile.reconcile(conn)
        finally:
            conn.close()
        # Graphify refreshes only after the scan for this project succeeded.
        if self.graphify_queue is not None and result.returncode == 0:
            self.graphify_queue.enqueue(project_path)
        if self.on_scan:
            self.on_scan(project_path, result)


class _Handler(FileSystemEventHandler):  # type: ignore
    """Map filesystem events to debounced project rescans."""

    def __init__(self, debouncer: _Debouncer, root: str):
        self.debouncer = debouncer
        self.root = Path(root).resolve()

    def _project_for(self, event_path: str) -> Optional[str]:
        p = Path(event_path).resolve()
        try:
            rel = p.relative_to(self.root)
        except ValueError:
            return None
        # Walk up from the event's dir to the root; the first dir containing
        # .eos is the owning project.
        current = p.parent if p.is_file() else p
        while True:
            if (current / ".eos").is_dir():
                return str(current)
            if current == self.root or current.parent == current:
                break
            current = current.parent
        return None

    def _handle(self, event):
        if event.is_directory:
            return
        p = Path(event.src_path)
        if _is_noise(p):
            return
        project = self._project_for(event.src_path)
        if project:
            self.debouncer.trigger(project)

    def on_created(self, event):
        self._handle(event)

    def on_modified(self, event):
        self._handle(event)

    def on_moved(self, event):
        self._handle(event)

    def on_deleted(self, event):
        # Deletions can change the graph (removed import targets); rescan.
        self._handle(event)
