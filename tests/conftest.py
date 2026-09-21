"""Shared test setup.

The Graphify queue publishes its state to a file that the API process reads
back, so every test gets its own instead of the developer's real ~/.eos-ui one.
"""
import pytest

from core import telemetry
from ui.app import graphify_queue


@pytest.fixture(autouse=True)
def _isolated_queue_state(tmp_path, monkeypatch):
    monkeypatch.setenv(
        graphify_queue.QUEUE_STATE_ENV, str(tmp_path / "graphify-queue.json")
    )


@pytest.fixture(autouse=True)
def _no_inherited_session(monkeypatch):
    """Run every test outside whatever session is running the suite.

    Telemetry reads a session id from the harness's own environment, and a
    coding agent running these tests exports one -- so without this the suite
    counts the developer's session among the ones under test, and two
    telemetry tests passed or failed depending on who typed `pytest`. A test
    that depends on that is measuring the room, not the code.
    """
    for name in telemetry.SESSION_VARIABLES:
        monkeypatch.delenv(name, raising=False)
