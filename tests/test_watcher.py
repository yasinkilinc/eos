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


class _FakeTimer:
    """threading.Timer look-alike whose firing is driven by _FakeClock.advance()
    instead of a real background thread waiting on wall-clock time."""

    def __init__(self, clock: "_FakeClock", interval: float, function, args=()):
        self._clock = clock
        self._remaining = interval
        self._function = function
        self._args = args
        self._cancelled = False
        self.daemon = False
        clock._pending.append(self)

    def start(self) -> None:
        pass  # already registered with the clock at construction

    def cancel(self) -> None:
        self._cancelled = True


class _FakeClock:
    """Deterministic stand-in for real time for debounce tests.

    A wall-clock sleep only *probably* falls on the right side of a debounce
    deadline; under CI load it sometimes doesn't (this is what made
    test_debouncer_retrigger_extends_wait fail on a loaded macOS runner with
    a 50ms margin). advance() instead moves a virtual clock forward by an
    exact amount and fires, synchronously and in the calling thread, every
    timer whose remaining delay reaches zero -- so "retrigger resets the
    window" becomes a statement about _Debouncer's own bookkeeping, true
    regardless of machine speed.
    """

    def __init__(self):
        self._pending: list[_FakeTimer] = []

    def timer_factory(self, interval: float, function, args=()) -> _FakeTimer:
        return _FakeTimer(self, interval, function, args)

    def advance(self, seconds: float) -> None:
        # Snapshot first: a fired timer's callback may re-arm a new one (a
        # retrigger, or _Debouncer._fire's own pending-run re-arm), and that
        # new timer must wait for its own future advance(), not fire within
        # this same call.
        live = [t for t in self._pending if not t._cancelled]
        for t in live:
            t._remaining -= seconds
        for t in live:
            if t._cancelled or t._remaining > 0:
                continue
            t._cancelled = True
            t._function(*t._args)
        self._pending = [t for t in self._pending if not t._cancelled]


def test_is_noise_filters_caches_and_venvs():
    assert _is_noise(Path("proj/.git/HEAD"))
    assert _is_noise(Path("proj/node_modules/x/index.js"))
    assert _is_noise(Path("proj/foo.pyc"))
    assert _is_noise(Path("proj/.DS_Store"))
    assert not _is_noise(Path("proj/src/main.py"))
    assert not _is_noise(Path("proj/lib/utils.ts"))


def test_debouncer_coalesces_bursts_into_one_call():
    calls: list[str] = []
    clock = _FakeClock()

    deb = _Debouncer(delay=0.05, callback=lambda p: calls.append(p), timer_factory=clock.timer_factory)
    for _ in range(5):
        deb.trigger("/proj/a")
    clock.advance(0.05)
    assert calls == ["/proj/a"], f"expected one call, got {calls}"


def test_debouncer_distinct_projects_each_fire():
    calls: list[str] = []
    clock = _FakeClock()

    deb = _Debouncer(delay=0.05, callback=lambda p: calls.append(p), timer_factory=clock.timer_factory)
    deb.trigger("/proj/a")
    deb.trigger("/proj/b")
    clock.advance(0.05)
    assert sorted(calls) == ["/proj/a", "/proj/b"]


def test_debouncer_retrigger_extends_wait():
    """A retrigger must restart the debounce window rather than being
    absorbed by the one already running -- driven by a fake clock so the
    assertion is about _Debouncer's bookkeeping, not about winning a race
    against a live threading.Timer under CI load (see _FakeClock)."""
    calls: list[str] = []
    clock = _FakeClock()
    deb = _Debouncer(delay=0.1, callback=lambda p: calls.append(p), timer_factory=clock.timer_factory)
    deb.trigger("/proj/a")
    clock.advance(0.05)
    deb.trigger("/proj/a")  # should reset the timer
    clock.advance(0.08)
    assert calls == [], "retrigger should extend the wait window"
    clock.advance(0.1)
    assert calls == ["/proj/a"]


def test_debouncer_defers_a_trigger_that_fires_while_running():
    """A timer firing for a project that is already running must not start
    a second, concurrent invocation -- it defers into _pending, and
    _Debouncer arms exactly one follow-up once the in-progress run
    completes.

    test_debouncer_never_runs_one_project_concurrently used to check this
    indirectly, via a real burst of retriggers racing a real
    threading.Timer -- and that is exactly why it flaked: how many runs a
    real burst produces depends on OS scheduling, not on this logic. A
    fake clock cannot reproduce "a timer fires while a previous run is
    still executing" either, since its callback always runs synchronously
    to completion before the clock advances again -- there is no second
    thread for a new timer to interleave with. So this drives _fire()
    directly against seeded state instead: deterministic either way,
    because it is a statement about what _fire() does when it observes
    self._running already holding the project, not about elapsed time.
    """
    calls: list[str] = []
    clock = _FakeClock()
    deb = _Debouncer(delay=0.05, callback=calls.append, timer_factory=clock.timer_factory)

    with deb._lock:
        deb._running.add("/proj/a")  # a run is (hypothetically) in progress
    deb._fire("/proj/a")  # a retrigger's timer fires while it is
    assert calls == [], "must not invoke the callback while already running"
    assert "/proj/a" in deb._pending, "a trigger during a run must be deferred"

    # The in-progress run finishing is exactly _fire()'s own finally block.
    with deb._lock:
        deb._running.discard("/proj/a")
        deb._pending.discard("/proj/a")
        deb._arm("/proj/a")
    assert calls == [], "the deferred run must wait for its own timer"
    clock.advance(0.05)
    assert calls == ["/proj/a"], "deferred run must fire exactly once, after the delay"


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
    """A rescan far outlives the debounce window; overlapping runs mean two scans.

    Left on real time deliberately: this asserts that two *real* threads
    (the timer thread invoking the callback, and the main thread still
    calling trigger()) never run the callback concurrently. A fake clock
    would remove the second thread entirely -- there would be nothing left
    to race -- so it cannot express what this test is checking.

    The only claim made is the entry/exit counter never exceeding 1: that
    is true regardless of machine load, thread scheduling, or how many
    separate runs a real burst happens to produce, which is exactly why it
    is the right thing to assert here. This test used to also assert on
    the number of runs a burst collapses into (`1 <= len(calls) <= 2`) --
    that is a timing *consequence* of the debounce window, not the
    exclusion invariant, and it failed on a loaded runner (3 runs instead
    of <= 2) while this invariant held. That coalescing behaviour is real
    and worth testing, just not with a wall-clock race: see
    test_debouncer_defers_a_trigger_that_fires_while_running below, which
    drives the same code path deterministically.
    """
    lock = threading.Lock()
    live = {"now": 0, "peak": 0}

    def callback(project_path: str) -> None:
        with lock:
            live["now"] += 1
            live["peak"] = max(live["peak"], live["now"])
        time.sleep(0.3)
        with lock:
            live["now"] -= 1

    deb = _Debouncer(delay=0.02, callback=callback)
    for _ in range(6):
        deb.trigger("/proj/a")
        time.sleep(0.03)
    time.sleep(0.8)

    assert live["peak"] == 1, f"concurrent rescans of one project: {live['peak']}"


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
