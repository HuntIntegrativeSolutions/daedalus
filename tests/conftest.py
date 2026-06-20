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


@pytest.fixture
def sim_server_rejecting_large() -> Generator[CipSimServer, None, None]:
    """CipSimServer that rejects Large_Forward_Open with CIP status 0x08."""
    srv = CipSimServer(reject_large_fo=True)
    srv.start()
    yield srv
    srv.stop()
