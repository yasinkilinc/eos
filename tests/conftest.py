"""Shared test setup.

The Graphify queue publishes its state to a file that the API process reads
back, so every test gets its own instead of the developer's real ~/.eos-ui one.
"""
import pytest

from ui.app import graphify_queue


@pytest.fixture(autouse=True)
def _isolated_queue_state(tmp_path, monkeypatch):
    monkeypatch.setenv(
        graphify_queue.QUEUE_STATE_ENV, str(tmp_path / "graphify-queue.json")
    )
