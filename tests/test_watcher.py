"""Tests for the file-watcher debounce + project-mapping logic.

The watchdog Observer itself is not exercised here (it needs real FS events
and is slow/flaky in CI); instead we test the pure pieces: the debouncer
coalesces bursts, the handler maps an event path to its owning project, and
noise paths are ignored.
"""
import logging
import subprocess
import threading
import time
from pathlib import Path

import pytest

from ui.watcher import _Debouncer, _Handler, _is_noise


def test_is_noise_filters_caches_and_venvs():
    assert _is_noise(Path("proj/.git/HEAD"))
    assert _is_noise(Path("proj/node_modules/x/index.js"))
    assert _is_noise(Path("proj/foo.pyc"))
    assert _is_noise(Path("proj/.DS_Store"))
    assert not _is_noise(Path("proj/src/main.py"))
    assert not _is_noise(Path("proj/lib/utils.ts"))


def test_debouncer_coalesces_bursts_into_one_call():
    calls: list[str] = []

    deb = _Debouncer(delay=0.05, callback=lambda p: calls.append(p))
    for _ in range(5):
        deb.trigger("/proj/a")
    time.sleep(0.2)
    assert calls == ["/proj/a"], f"expected one call, got {calls}"


def test_debouncer_distinct_projects_each_fire():
    calls: list[str] = []

    deb = _Debouncer(delay=0.05, callback=lambda p: calls.append(p))
    deb.trigger("/proj/a")
    deb.trigger("/proj/b")
    time.sleep(0.2)
    assert sorted(calls) == ["/proj/a", "/proj/b"]


def test_debouncer_retrigger_extends_wait():
    calls: list[str] = []
    deb = _Debouncer(delay=0.1, callback=lambda p: calls.append(p))
    deb.trigger("/proj/a")
    time.sleep(0.05)
    deb.trigger("/proj/a")  # should reset the timer
    time.sleep(0.08)
    assert calls == [], "retrigger should extend the wait window"
    time.sleep(0.1)
    assert calls == ["/proj/a"]


def test_handler_maps_event_to_owning_project(tmp_path):
    root = tmp_path / "workspace"
    proj = root / "myproj"
    (proj / "src").mkdir(parents=True)
    (proj / ".eos").mkdir()
    (proj / "src" / "main.py").write_text("pass\n")

    deb = _Debouncer(delay=0.01, callback=lambda p: None)
    handler = _Handler(deb, str(root))

    assert handler._project_for(str(proj / "src" / "main.py")) == str(proj)
    # File in the root but outside any project -> None.
    (root / "loose.py").write_text("pass\n")
    assert handler._project_for(str(root / "loose.py")) is None


def test_handler_ignores_noise_paths(tmp_path):
    triggered: list[str] = []
    deb = _Debouncer(delay=0.01, callback=lambda p: triggered.append(p))
    handler = _Handler(deb, str(tmp_path))

    class E:
        is_directory = False
        src_path = str(tmp_path / "proj" / "node_modules" / "x.js")

    (tmp_path / "proj" / "node_modules").mkdir(parents=True)
    handler.on_modified(E())
    time.sleep(0.05)
    assert triggered == [], "noise paths must not trigger a rescan"


def test_watcher_service_requires_watchdog(monkeypatch):
    import ui.watcher as mod

    if mod._WATCHDOG_OK:
        pytest.skip("watchdog installed; import-error path not reachable")
    with pytest.raises(RuntimeError):
        mod.WatcherService()


def test_debouncer_never_runs_one_project_concurrently():
    """A rescan far outlives the debounce window; overlapping runs mean two scans."""
    lock = threading.Lock()
    live = {"now": 0, "peak": 0}
    calls: list[str] = []

    def callback(project_path: str) -> None:
        with lock:
            live["now"] += 1
            live["peak"] = max(live["peak"], live["now"])
            calls.append(project_path)
        time.sleep(0.3)
        with lock:
            live["now"] -= 1

    deb = _Debouncer(delay=0.02, callback=callback)
    for _ in range(6):
        deb.trigger("/proj/a")
        time.sleep(0.03)
    time.sleep(0.8)

    assert live["peak"] == 1, f"concurrent rescans of one project: {live['peak']}"
    assert 1 <= len(calls) <= 2, f"a burst must collapse into one run plus a follow-up: {calls}"


def test_debouncer_logs_a_failing_callback(caplog):
    """A swallowed rescan failure leaves the operator with no signal at all."""

    def callback(project_path: str) -> None:
        raise RuntimeError("scan exploded")

    deb = _Debouncer(delay=0.01, callback=callback)
    with caplog.at_level(logging.ERROR, logger="ui.watcher"):
        deb.trigger("/proj/a")
        time.sleep(0.2)

    assert "Rescan failed for /proj/a" in caplog.text
    assert "scan exploded" in caplog.text


def test_stop_cancels_a_pending_rescan():
    import ui.watcher as mod

    if not mod._WATCHDOG_OK:
        pytest.skip("watchdog is not installed")

    calls: list[str] = []
    service = mod.WatcherService(scan_delay=0.1)
    service._debouncer.callback = calls.append
    service._debouncer.trigger("/proj/a")
    service.stop()
    time.sleep(0.3)

    assert calls == [], "stop() must cancel rescans queued before shutdown"


def test_rescan_reports_a_scan_timeout_as_a_failed_scan(monkeypatch):
    import ui.watcher as mod

    if not mod._WATCHDOG_OK:
        pytest.skip("watchdog is not installed")

    seen: list[tuple] = []
    enqueued: list[str] = []

    class _Conn:
        def close(self):
            pass

    class _Queue:
        def enqueue(self, project_path: str) -> None:
            enqueued.append(project_path)

    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout", 1))

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(mod.db, "connect", lambda *a, **k: _Conn())
    monkeypatch.setattr(mod.reconcile, "reconcile", lambda conn: None)

    service = mod.WatcherService(
        scan_delay=0.05,
        on_scan=lambda project_path, result: seen.append((project_path, result.returncode)),
        graphify_queue=_Queue(),
    )
    service._rescan_project("/proj/a")

    assert seen == [("/proj/a", 124)], "a scan timeout must surface as a failed scan"
    assert enqueued == [], "a failed scan must not trigger a Graphify refresh"
