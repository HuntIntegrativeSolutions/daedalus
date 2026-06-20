"""Pytest configuration. Package is installed editable via `uv sync`."""

from __future__ import annotations

from collections.abc import Generator

import pytest

from sim.server import CipSimServer


@pytest.fixture
def sim_server() -> Generator[CipSimServer, None, None]:
    """Yield a started CipSimServer on an ephemeral port; stop on teardown."""
    srv = CipSimServer()
    srv.start()
    yield srv
    srv.stop()
