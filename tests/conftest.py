"""Pytest configuration. Package is installed editable via `uv sync`."""

from __future__ import annotations

from collections.abc import Generator
from typing import Any

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


@pytest.fixture
def make_tag_server() -> Generator[Any, None, None]:
    """Factory fixture: call with a tag store dict to get a started CipSimServer.

    Usage::

        def test_foo(make_tag_server):
            srv = make_tag_server({"MyTag": (0xC4, DINT.encode(42))})
            ...
    """
    servers: list[CipSimServer] = []

    def factory(
        tag_store: dict[str, tuple[int, bytes]],
        frag_threshold: int = 480,
    ) -> CipSimServer:
        srv = CipSimServer(tag_store=tag_store, frag_threshold=frag_threshold)
        srv.start()
        servers.append(srv)
        return srv

    yield factory

    for srv in servers:
        srv.stop()
