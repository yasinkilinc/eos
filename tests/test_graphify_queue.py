"""Tests for the debounced Graphify refresh queue.

The refresh callable is always injected, so nothing here shells out to real
Graphify. Delays are kept tiny (0.05s) so the suite stays fast; assertions poll
instead of sleeping a fixed amount wherever a thread has to make progress.
"""
import json
import os
import subprocess
import threading
import time

import pytest

from ui.app.graphify_queue import (
    COORDINATOR_ROOT_ENV,
    REFRESH_CMD_ENV,
    GraphifyQueue,
    build_refresh_argv,
    find_refresh_script,
)


def _wait_for(predicate, timeout: float = 2.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return bool(predicate())


def test_debounce_collapses_burst_into_one_refresh():
    calls: list[str] = []
    queue = GraphifyQueue(refresh=calls.append, delay=0.05)
    try:
        for _ in range(5):
            queue.enqueue("/proj/a")
        assert _wait_for(lambda: len(calls) == 1), f"expected one refresh, got {calls}"
        time.sleep(0.15)
        assert calls == ["/proj/a"]
        assert queue.status_for("/proj/a")["state"] == "ok"
    finally:
        queue.stop()


def test_distinct_projects_each_refresh():
    calls: list[str] = []
    queue = GraphifyQueue(refresh=calls.append, delay=0.05)
    try:
        queue.enqueue("/proj/a")
        queue.enqueue("/proj/b")
        assert _wait_for(lambda: len(calls) == 2)
        assert sorted(calls) == ["/proj/a", "/proj/b"]
    finally:
        queue.stop()


def test_enqueue_during_run_produces_exactly_one_followup():
    calls: list[str] = []
    started = threading.Event()
    release = threading.Event()

    def refresh(project_path: str) -> None:
        calls.append(project_path)
        if len(calls) == 1:
            started.set()
            release.wait(2.0)

    queue = GraphifyQueue(refresh=refresh, delay=0.05)
    try:
        queue.enqueue("/proj/a")
        assert started.wait(2.0), "first refresh never started"
        for _ in range(3):
            queue.enqueue("/proj/a")
        assert queue.status_for("/proj/a")["state"] == "running"
        release.set()
        assert _wait_for(lambda: len(calls) == 2), f"expected one follow-up, got {calls}"
        time.sleep(0.2)
        assert calls == ["/proj/a", "/proj/a"], "requests must coalesce into one follow-up"
    finally:
        release.set()
        queue.stop()


def test_failure_retries_to_cap_then_stays_failed():
    calls: list[str] = []

    def refresh(project_path: str) -> None:
        calls.append(project_path)
        raise RuntimeError("graphify blew up")

    queue = GraphifyQueue(refresh=refresh, delay=0.05, max_attempts=3, retry_delay=0.01)
    try:
        queue.enqueue("/proj/a")
        assert _wait_for(lambda: len(calls) == 3), f"expected 3 attempts, got {calls}"
        time.sleep(0.15)
        assert len(calls) == 3, "retries must stop at the cap"
        entry = queue.status_for("/proj/a")
        assert entry["state"] == "failed"
        assert entry["attempts"] == 3
        assert entry["last_error"] == "graphify blew up"
        assert entry["last_started_at"] and entry["last_finished_at"]
    finally:
        queue.stop()


def test_success_clears_error_and_sets_ok():
    fail = {"value": True}

    def refresh(project_path: str) -> None:
        if fail["value"]:
            raise RuntimeError("transient")

    queue = GraphifyQueue(refresh=refresh, delay=0.05, max_attempts=1, retry_delay=0.01)
    try:
        queue.enqueue("/proj/a")
        assert _wait_for(lambda: queue.status_for("/proj/a")["state"] == "failed")
        assert queue.status_for("/proj/a")["last_error"] == "transient"

        fail["value"] = False
        queue.enqueue("/proj/a")
        assert _wait_for(lambda: queue.status_for("/proj/a")["state"] == "ok")
        entry = queue.status_for("/proj/a")
        assert entry["last_error"] is None
        assert entry["attempts"] == 1
    finally:
        queue.stop()


def test_status_is_json_serializable():
    queue = GraphifyQueue(refresh=lambda path: None, delay=0.05)
    try:
        queue.enqueue("/proj/a")
        queue.enqueue("/work/b")
        assert _wait_for(lambda: all(e["state"] == "ok" for e in queue.status()) and len(queue.status()) == 2)
        payload = queue.status()
        assert json.loads(json.dumps(payload)) == payload
        assert [entry["project_key"] for entry in payload] == ["a", "b"]
        assert set(payload[0]) == {
            "project_path",
            "project_key",
            "state",
            "attempts",
            "last_error",
            "last_started_at",
            "last_finished_at",
            "queued_at",
        }
        assert queue.status_for("/proj/missing") is None
    finally:
        queue.stop()


def test_stop_cancels_pending_work():
    calls: list[str] = []
    queue = GraphifyQueue(refresh=calls.append, delay=0.3)
    queue.enqueue("/proj/a")
    queue.stop()
    time.sleep(0.4)
    assert calls == [], "pending refresh must be cancelled by stop()"
    assert queue.status_for("/proj/a")["state"] == "idle"
    queue.enqueue("/proj/a")
    time.sleep(0.4)
    assert calls == [], "enqueue after stop() must be a no-op"


def test_build_refresh_argv_uses_env_template(monkeypatch):
    monkeypatch.setenv(REFRESH_CMD_ENV, "/bin/echo refresh {key} {project}")
    argv = build_refresh_argv("/work/projects/myproj")
    assert argv == ["/bin/echo", "refresh", "myproj", "/work/projects/myproj"]


def test_env_template_keeps_a_hostile_path_in_one_argument(tmp_path, monkeypatch):
    """A path is one argv element, never re-tokenised into extra flags."""
    monkeypatch.setenv(REFRESH_CMD_ENV, "/usr/local/bin/refresh.sh --project {project}")
    project = tmp_path / "my repo --exec=pwn.sh"
    assert build_refresh_argv(str(project)) == [
        "/usr/local/bin/refresh.sh",
        "--project",
        str(project),
    ]


def test_env_template_accepts_a_quote_in_the_project_path(tmp_path, monkeypatch):
    """A legitimate directory name must not permanently break its refreshes."""
    monkeypatch.setenv(REFRESH_CMD_ENV, "/bin/echo {key} {project}")
    project = tmp_path / "o'brien"
    assert build_refresh_argv(str(project)) == ["/bin/echo", "o'brien", str(project)]


def test_build_refresh_argv_discovers_configured_script(tmp_path, monkeypatch):
    monkeypatch.delenv(REFRESH_CMD_ENV, raising=False)
    script = tmp_path / "coordinator" / "refresh.sh"
    script.parent.mkdir(parents=True)
    script.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    monkeypatch.setenv(COORDINATOR_ROOT_ENV, str(script))

    project = tmp_path / "projects" / "myproj"
    assert find_refresh_script() == script
    assert build_refresh_argv(str(project)) == [str(script), "--project", "myproj", "--update"]


def test_build_refresh_argv_never_builds_a_shell_string(tmp_path, monkeypatch):
    monkeypatch.delenv(REFRESH_CMD_ENV, raising=False)
    script = tmp_path / "coordinator" / "refresh.sh"
    script.parent.mkdir(parents=True)
    script.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    monkeypatch.setenv(COORDINATOR_ROOT_ENV, str(script))

    project = tmp_path / "projects" / "myproj; touch pwned"
    argv = build_refresh_argv(str(project))
    assert isinstance(argv, list) and all(isinstance(part, str) for part in argv)
    # Shell metacharacters stay inside a single argv element, never concatenated.
    assert argv == [str(script), "--project", "myproj; touch pwned", "--update"]


def test_coordinator_root_tries_candidates_in_order(tmp_path, monkeypatch):
    """A missing earlier candidate falls through to the next configured one."""
    monkeypatch.delenv(REFRESH_CMD_ENV, raising=False)
    missing = tmp_path / "missing" / "refresh.sh"
    real = tmp_path / "coordinator" / "refresh.sh"
    real.parent.mkdir(parents=True)
    real.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    decoy = tmp_path / "decoy" / "refresh.sh"
    decoy.parent.mkdir(parents=True)
    decoy.write_text("#!/usr/bin/env bash\ntouch pwned\n", encoding="utf-8")

    monkeypatch.setenv(
        COORDINATOR_ROOT_ENV, os.pathsep.join([str(missing), str(real), str(decoy)])
    )
    assert find_refresh_script() == real


def test_coordinator_is_never_discovered_outside_its_configuration(tmp_path, monkeypatch):
    """A script that exists on disk is never picked up unless explicitly configured."""
    monkeypatch.delenv(REFRESH_CMD_ENV, raising=False)
    monkeypatch.setenv(COORDINATOR_ROOT_ENV, str(tmp_path / "trusted" / "refresh.sh"))
    planted = tmp_path / "ws" / "projects" / "myproj" / "refresh.sh"
    planted.parent.mkdir(parents=True)
    planted.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    project = tmp_path / "ws" / "projects" / "myproj"

    assert find_refresh_script() is None
    with pytest.raises(RuntimeError):
        build_refresh_argv(str(project))


def test_coordinator_root_unset_leaves_the_feature_off(tmp_path, monkeypatch):
    monkeypatch.delenv(REFRESH_CMD_ENV, raising=False)
    monkeypatch.delenv(COORDINATOR_ROOT_ENV, raising=False)
    project = tmp_path / "projects" / "myproj"

    assert find_refresh_script() is None
    with pytest.raises(RuntimeError):
        build_refresh_argv(str(project))


def test_build_refresh_argv_without_script_raises(tmp_path, monkeypatch):
    monkeypatch.delenv(REFRESH_CMD_ENV, raising=False)
    monkeypatch.setattr("ui.app.graphify_queue.find_refresh_script", lambda: None)
    with pytest.raises(RuntimeError):
        build_refresh_argv(str(tmp_path / "projects" / "myproj"))


def test_stop_reports_a_refresh_it_could_not_join():
    """stop() must not claim success while a refresh is still running."""
    started = threading.Event()
    release = threading.Event()

    def refresh(project_path: str) -> None:
        started.set()
        release.wait(2.0)

    queue = GraphifyQueue(refresh=refresh, delay=0.05)
    try:
        queue.enqueue("/proj/a")
        assert started.wait(2.0), "refresh never started"
        assert queue.stop(timeout=0.2) == ["/proj/a"]
    finally:
        release.set()


def test_stop_terminates_the_running_refresh_process(monkeypatch):
    """A flag cannot interrupt subprocess.run; the process handle can."""
    monkeypatch.setenv(REFRESH_CMD_ENV, "/bin/sleep 30")
    queue = GraphifyQueue(delay=0.05, max_attempts=1, retry_delay=0.01)
    queue.enqueue("/proj/a")
    assert _wait_for(lambda: bool(queue._processes)), "refresh process never started"

    started = time.time()
    assert queue.stop(timeout=5.0) == []
    assert time.time() - started < 5.0, "stop() waited for the full sleep"
    assert queue.status_for("/proj/a")["state"] == "failed"


def test_queue_state_is_published_for_the_api_process(tmp_path):
    """The API runs in another process, so failures reach it through a file."""
    state_path = tmp_path / "queue.json"

    def refresh(project_path: str) -> None:
        raise RuntimeError("graphify blew up")

    queue = GraphifyQueue(
        refresh=refresh,
        delay=0.05,
        max_attempts=1,
        retry_delay=0.01,
        state_path=state_path,
    )
    try:
        queue.enqueue("/proj/a")
        assert _wait_for(lambda: queue.status_for("/proj/a")["state"] == "failed")
        assert _wait_for(state_path.is_file)
        published = json.loads(state_path.read_text(encoding="utf-8"))["entries"]
        assert [entry["state"] for entry in published] == ["failed"]
        assert published[0]["last_error"] == "graphify blew up"
    finally:
        queue.stop()
    assert not state_path.exists(), "stop() must retract the published state"


def test_watcher_enqueues_graphify_after_successful_scan(monkeypatch):
    import ui.watcher as watcher_mod

    if not watcher_mod._WATCHDOG_OK:
        pytest.skip("watchdog is not installed")

    events: list[str] = []

    class _Result:
        returncode = 0

    class _Conn:
        def close(self):
            pass

    def fake_run(*args, **kwargs):
        events.append("scan")
        return _Result()

    class _Queue:
        def enqueue(self, project_path: str) -> None:
            events.append(f"graphify:{project_path}")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(watcher_mod.db, "connect", lambda *a, **k: _Conn())
    monkeypatch.setattr(watcher_mod.reconcile, "reconcile", lambda conn: None)

    watcher = watcher_mod.WatcherService(scan_delay=0.05, graphify_queue=_Queue())
    watcher._rescan_project("/proj/a")
    assert events == ["scan", "graphify:/proj/a"], "Graphify must run after the scan"


def test_watcher_skips_graphify_when_scan_fails(monkeypatch):
    import ui.watcher as watcher_mod

    if not watcher_mod._WATCHDOG_OK:
        pytest.skip("watchdog is not installed")

    enqueued: list[str] = []

    class _Result:
        returncode = 1

    class _Conn:
        def close(self):
            pass

    class _Queue:
        def enqueue(self, project_path: str) -> None:
            enqueued.append(project_path)

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Result())
    monkeypatch.setattr(watcher_mod.db, "connect", lambda *a, **k: _Conn())
    monkeypatch.setattr(watcher_mod.reconcile, "reconcile", lambda conn: None)

    watcher = watcher_mod.WatcherService(scan_delay=0.05, graphify_queue=_Queue())
    watcher._rescan_project("/proj/a")
    assert enqueued == []


def test_watcher_without_queue_is_unchanged(monkeypatch):
    import ui.watcher as watcher_mod

    if not watcher_mod._WATCHDOG_OK:
        pytest.skip("watchdog is not installed")

    seen: list[str] = []

    class _Result:
        returncode = 0

    class _Conn:
        def close(self):
            pass

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Result())
    monkeypatch.setattr(watcher_mod.db, "connect", lambda *a, **k: _Conn())
    monkeypatch.setattr(watcher_mod.reconcile, "reconcile", lambda conn: None)

    watcher = watcher_mod.WatcherService(
        scan_delay=0.05,
        on_scan=lambda project_path, result: seen.append(project_path),
    )
    assert watcher.graphify_queue is None
    watcher._rescan_project("/proj/a")
    assert seen == ["/proj/a"]
