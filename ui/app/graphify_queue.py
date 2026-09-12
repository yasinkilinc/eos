"""Debounced, per-project Graphify refresh queue.

The watcher forwards project-change events here instead of regenerating a
Graphify artifact per file event. A burst of events collapses into one refresh,
a project already refreshing is never refreshed concurrently (a request landing
mid-run is coalesced into exactly one follow-up), and failures retry with a
bounded number of attempts before the entry stays ``failed`` with the last
error retained for the UI.

The refresh executable is resolved from a trusted anchor only, never from the
watched scan tree, and the queue publishes its state to a file so the API
process can report what the watcher process is doing.

Stdlib only: this module must stay importable without ``watchdog``.
"""
import json
import logging
import os
import shlex
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional

_LOG = logging.getLogger(__name__)

# Shell command template; ``{project}`` is the project path, ``{key}`` its dir name.
REFRESH_CMD_ENV = "EOS_GRAPHIFY_REFRESH_CMD"

# ``os.pathsep``-separated candidate paths to the Graphify coordinator script,
# tried in order. Unset means no coordinator is configured.
COORDINATOR_ROOT_ENV = "EOS_GRAPHIFY_COORDINATOR_ROOT"

# Where the watcher process publishes its queue snapshot for the API to read.
QUEUE_STATE_ENV = "EOS_GRAPHIFY_QUEUE_STATE"
DEFAULT_QUEUE_STATE = Path.home() / ".eos-ui" / "graphify-queue.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_project_path(project_path: str) -> str:
    """Absolute, user-expanded project path; the key an entry is tracked under."""
    return os.path.abspath(os.path.expanduser(str(project_path)))


def project_key(project_path: str) -> str:
    return Path(normalize_project_path(project_path)).name


def coordinator_candidates() -> List[Path]:
    """Candidate coordinator script paths from EOS_GRAPHIFY_COORDINATOR_ROOT, in order."""
    configured = os.environ.get(COORDINATOR_ROOT_ENV)
    if not configured:
        return []
    return [
        Path(value).expanduser().resolve()
        for value in configured.split(os.pathsep)
        if value
    ]


def find_refresh_script() -> Optional[Path]:
    """Resolve the Graphify coordinator from EOS_GRAPHIFY_COORDINATOR_ROOT.

    Nothing is searched implicitly: the scanned project's own ancestry is never
    consulted (it is the watched scan tree, so a repository checked out inside
    it could otherwise plant the executable the watcher runs), and with the
    setting unset this simply resolves nothing rather than guessing a path.
    """
    for candidate in coordinator_candidates():
        if candidate.is_file():
            return candidate
    return None


def build_refresh_argv(project_path: str) -> List[str]:
    """Resolve the refresh command as an argv list (never a shell string)."""
    path = normalize_project_path(project_path)
    key = project_key(path)
    template = os.environ.get(REFRESH_CMD_ENV)
    if template:
        # Tokenise first, then substitute, so a project path containing spaces or
        # quotes stays exactly one argv element instead of being re-parsed into
        # extra arguments.
        argv = [
            token.replace("{project}", path).replace("{key}", key)
            for token in shlex.split(template)
        ]
        if not argv:
            raise RuntimeError(f"{REFRESH_CMD_ENV} is empty")
        return argv
    script = find_refresh_script()
    if script is None:
        raise RuntimeError(f"No Graphify refresh script found for {path}")
    return [str(script), "--project", key, "--update"]


def run_refresh(
    project_path: str,
    timeout: float = 900.0,
    on_start: Optional[Callable[[subprocess.Popen], None]] = None,
) -> None:
    """Default refresh: run the resolved argv without a shell.

    ``on_start`` receives the live process so a caller can terminate it while it
    is still running (see :meth:`GraphifyQueue.stop`).
    """
    argv = build_refresh_argv(project_path)
    process = subprocess.Popen(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        shell=False,
    )
    if on_start is not None:
        on_start(process)
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()
        raise
    if process.returncode != 0:
        output = (stderr or stdout or "").strip().splitlines()
        detail = output[-1] if output else f"exit code {process.returncode}"
        raise RuntimeError(f"Graphify refresh failed ({process.returncode}): {detail}")


def queue_state_path() -> Path:
    """File the watcher publishes its queue snapshot to."""
    value = os.environ.get(QUEUE_STATE_ENV)
    return Path(value).expanduser() if value else DEFAULT_QUEUE_STATE


def read_queue_state() -> Optional[List[dict]]:
    """Entries published by a watcher process, or None when none is running."""
    try:
        payload = json.loads(queue_state_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    entries = payload.get("entries") if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        return None
    return [
        entry
        for entry in entries
        if isinstance(entry, dict)
        and isinstance(entry.get("project_path"), str)
        and isinstance(entry.get("project_key"), str)
    ]


class _Entry:
    """Mutable per-project queue state; guarded by the queue lock."""

    def __init__(self, project_path: str):
        self.project_path = project_path
        self.project_key = Path(project_path).name
        self.state = "idle"
        self.attempts = 0
        self.last_error: Optional[str] = None
        self.last_started_at: Optional[str] = None
        self.last_finished_at: Optional[str] = None
        self.queued_at: Optional[str] = None
        self.timer: Optional[threading.Timer] = None
        self.running = False
        self.pending = False

    def snapshot(self) -> dict:
        return {
            "project_path": self.project_path,
            "project_key": self.project_key,
            "state": self.state,
            "attempts": self.attempts,
            "last_error": self.last_error,
            "last_started_at": self.last_started_at,
            "last_finished_at": self.last_finished_at,
            "queued_at": self.queued_at,
        }


class GraphifyQueue:
    """Debounced Graphify refreshes with a per-project lock and bounded retries."""

    def __init__(
        self,
        refresh: Optional[Callable[[str], None]] = None,
        delay: float = 2.0,
        max_attempts: int = 3,
        retry_delay: float = 5.0,
        timeout: float = 900.0,
        state_path: Optional[Path] = None,
    ):
        self.delay = delay
        self.max_attempts = max(1, max_attempts)
        self.retry_delay = retry_delay
        self.timeout = timeout
        self._refresh = refresh or self._default_refresh
        self._entries: Dict[str, _Entry] = {}
        self._workers: Dict[str, threading.Thread] = {}
        self._processes: Dict[str, subprocess.Popen] = {}
        self._state_path = Path(state_path) if state_path else None
        self._lock = threading.Lock()
        self._stopping = threading.Event()

    def enqueue(self, project_path: str) -> None:
        key = normalize_project_path(project_path)
        with self._lock:
            if self._stopping.is_set():
                return
            entry = self._entries.get(key)
            if entry is None:
                entry = _Entry(key)
                self._entries[key] = entry
            entry.queued_at = _now()
            if entry.running:
                # Coalesce into exactly one follow-up run.
                entry.pending = True
            else:
                self._schedule(entry)
        self._persist()

    def status(self) -> List[dict]:
        with self._lock:
            entries = sorted(self._entries.values(), key=lambda e: e.project_path)
            return [entry.snapshot() for entry in entries]

    def status_for(self, project_path: str) -> Optional[dict]:
        with self._lock:
            entry = self._entries.get(normalize_project_path(project_path))
            return entry.snapshot() if entry else None

    def stop(self, timeout: float = 5.0) -> List[str]:
        """Stop the queue; returns the projects whose refresh outlived the join."""
        self._stopping.set()
        with self._lock:
            for entry in self._entries.values():
                if entry.timer is not None:
                    entry.timer.cancel()
                    entry.timer = None
                entry.pending = False
                if entry.state == "pending":
                    entry.state = "idle"
            workers = dict(self._workers)
            processes = list(self._processes.values())
        # A refresh subprocess cannot be interrupted by a flag; end it directly.
        for process in processes:
            try:
                process.terminate()
            except OSError:
                _LOG.warning("Could not terminate a Graphify refresh", exc_info=True)
        current = threading.current_thread()
        unfinished: List[str] = []
        for key, worker in workers.items():
            if worker is current:
                continue
            worker.join(timeout=timeout)
            if worker.is_alive():
                unfinished.append(key)
        self._clear_state()
        return unfinished

    def _default_refresh(self, project_path: str) -> None:
        run_refresh(project_path, timeout=self.timeout, on_start=self._track_process)

    def _track_process(self, process: subprocess.Popen) -> None:
        """Record the live refresh process of the calling worker, so stop() can end it."""
        current = threading.current_thread()
        with self._lock:
            for key, worker in self._workers.items():
                if worker is current:
                    self._processes[key] = process
                    break

    def _persist(self) -> None:
        """Publish the snapshot so the API process can report queue state."""
        if self._state_path is None:
            return
        payload = {"pid": os.getpid(), "updated_at": _now(), "entries": self.status()}
        # One temp name per publish. Every worker thread shared a single fixed
        # ".tmp" path, so two concurrent publishes raced: the first os.replace
        # moved the file out from under the second, which then failed with
        # FileNotFoundError. Measured at ~8 lost publishes in 40, and it is what
        # made tests/test_graphify_queue.py fail intermittently.
        temp_path = self._state_path.with_name(
            f"{self._state_path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
        )
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            temp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            os.replace(temp_path, self._state_path)
        except OSError:
            _LOG.warning("Could not publish queue state to %s", self._state_path, exc_info=True)
            try:
                temp_path.unlink()
            except OSError:
                pass

    def _clear_state(self) -> None:
        if self._state_path is None:
            return
        try:
            self._state_path.unlink()
        except FileNotFoundError:
            return
        except OSError:
            _LOG.warning("Could not remove queue state %s", self._state_path, exc_info=True)

    def _schedule(self, entry: _Entry) -> None:
        """Start (or restart) the debounce timer. Caller holds the lock."""
        if entry.timer is not None:
            entry.timer.cancel()
        entry.state = "pending"
        timer = threading.Timer(self.delay, self._fire, args=(entry.project_path,))
        timer.daemon = True
        entry.timer = timer
        timer.start()

    def _fire(self, key: str) -> None:
        with self._lock:
            entry = self._entries.get(key)
            if entry is None or self._stopping.is_set():
                return
            entry.timer = None
            if entry.running:
                entry.pending = True
                return
            entry.running = True
            entry.pending = False
            entry.attempts = 0
            entry.state = "running"
            entry.last_started_at = _now()
            self._workers[key] = threading.current_thread()
        self._persist()
        try:
            self._run(entry)
        finally:
            with self._lock:
                self._workers.pop(key, None)
                self._processes.pop(key, None)
                entry.running = False
                entry.last_finished_at = _now()
                if entry.pending and not self._stopping.is_set():
                    entry.pending = False
                    self._schedule(entry)
            self._persist()

    def _run(self, entry: _Entry) -> None:
        while True:
            with self._lock:
                entry.attempts += 1
                entry.state = "running"
                attempt = entry.attempts
            try:
                self._refresh(entry.project_path)
            except Exception as exc:
                message = str(exc) or exc.__class__.__name__
                with self._lock:
                    entry.state = "failed"
                    entry.last_error = message
                self._persist()
                if attempt >= self.max_attempts or self._stopping.is_set():
                    return
                if self._stopping.wait(self.retry_delay):
                    return
                continue
            with self._lock:
                entry.state = "ok"
                entry.last_error = None
            self._persist()
            return
