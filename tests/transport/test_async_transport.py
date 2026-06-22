"""Integration tests — Session + AsyncTcpTransport + CipSimServer.

Proves the same sans-I/O contract as test_transport.py, but over anyio.
The sim server is thread-based; an anyio TCP client connects to it from
any backend without modification.

Each test runs on both asyncio and trio backends via the module-level
anyio_backend fixture.
"""

from __future__ import annotations

import socket
import threading
from typing import cast

import pytest

from daedalus.exceptions import CommError
from daedalus.session import Session
from daedalus.transport import AsyncTcpTransport, SyncTcpTransport
from sim.server import CipSimServer


@pytest.fixture(params=["asyncio", "trio"])
def anyio_backend(request: pytest.FixtureRequest) -> str:
    return cast(str, request.param)


# ---------------------------------------------------------------------------
# End-to-end round-trips (core proof)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_async_register_unregister_roundtrip(sim_server: CipSimServer) -> None:
    """send_frame/recv_frame against sim; full RegisterSession/Unregister cycle."""
    session = Session()
    async with AsyncTcpTransport(sim_server.host, sim_server.port) as transport:
        await transport.send_frame(session.register_request())
        session.register_reply(await transport.recv_frame())
        assert session.registered
        assert session.session_handle != 0
        await transport.send_frame(session.unregister_request())
        # No recv — sim closes the connection on UnregisterSession
    assert not session.registered


@pytest.mark.anyio
async def test_async_send_recv_roundtrip(sim_server: CipSimServer) -> None:
    """send_recv convenience method completes a register round-trip."""
    session = Session()
    async with AsyncTcpTransport(sim_server.host, sim_server.port) as transport:
        reply = await transport.send_recv(session.register_request())
        session.register_reply(reply)
        assert session.registered


@pytest.mark.anyio
async def test_async_session_handle_nonzero_after_register(sim_server: CipSimServer) -> None:
    session = Session()
    async with AsyncTcpTransport(sim_server.host, sim_server.port) as transport:
        await transport.send_frame(session.register_request())
        session.register_reply(await transport.recv_frame())
    assert session.session_handle > 0


@pytest.mark.anyio
async def test_async_multiple_sessions_get_independent_handles(sim_server: CipSimServer) -> None:
    """Each new connection gets a distinct session handle from the sim."""
    handles: set[int] = set()
    for _ in range(3):
        s = Session()
        async with AsyncTcpTransport(sim_server.host, sim_server.port) as t:
            await t.send_frame(s.register_request())
            s.register_reply(await t.recv_frame())
            handles.add(s.session_handle)
    assert all(h > 0 for h in handles)


# ---------------------------------------------------------------------------
# Context manager
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_async_transport_context_manager_connects_and_closes(
    sim_server: CipSimServer,
) -> None:
    async with AsyncTcpTransport(sim_server.host, sim_server.port) as t:
        assert t._stream is not None
    assert t._stream is None


@pytest.mark.anyio
async def test_async_transport_close_is_idempotent(sim_server: CipSimServer) -> None:
    t = AsyncTcpTransport(sim_server.host, sim_server.port)
    await t.connect()
    await t.close()
    await t.close()  # second close must not raise


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_async_connect_refused_raises_comm_error() -> None:
    """Connecting to a port that has no listener raises CommError."""
    with pytest.raises(CommError):
        await AsyncTcpTransport("127.0.0.1", 1, timeout=1.0).connect()


@pytest.mark.anyio
async def test_async_short_recv_raises_comm_error() -> None:
    """CommError when the remote closes the connection before a full frame arrives."""
    with socket.socket() as srv:
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port: int = srv.getsockname()[1]

        def _close_immediately() -> None:
            conn, _ = srv.accept()
            conn.close()

        t = threading.Thread(target=_close_immediately, daemon=True)
        t.start()

        transport = AsyncTcpTransport("127.0.0.1", port, timeout=2.0)
        await transport.connect()
        with pytest.raises(CommError):
            await transport.recv_frame()
        await transport.close()
        t.join(timeout=2.0)


@pytest.mark.anyio
async def test_async_send_frame_without_connect_raises_comm_error() -> None:
    t = AsyncTcpTransport("127.0.0.1", 44818)
    with pytest.raises(CommError):
        await t.send_frame(b"\x00" * 28)


@pytest.mark.anyio
async def test_async_recv_frame_without_connect_raises_comm_error() -> None:
    t = AsyncTcpTransport("127.0.0.1", 44818)
    with pytest.raises(CommError):
        await t.recv_frame()


# ---------------------------------------------------------------------------
# Frame-byte parity with SyncTcpTransport
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_frame_byte_parity_with_sync_transport(sim_server: CipSimServer) -> None:
    """Async recv_frame produces structurally identical framing to the sync transport."""
    session_sync = Session()
    session_async = Session()

    with SyncTcpTransport(sim_server.host, sim_server.port) as sync_t:
        sync_t.send_frame(session_sync.register_request())
        sync_reply = sync_t.recv_frame()

    async with AsyncTcpTransport(sim_server.host, sim_server.port) as async_t:
        await async_t.send_frame(session_async.register_request())
        async_reply = await async_t.recv_frame()

    # Frame length must be identical (same RegisterSession reply structure)
    assert len(sync_reply) == len(async_reply)
    # Bytes 0-1: command code (0x65 0x00)
    assert sync_reply[:2] == async_reply[:2]
    # Bytes 2-3: payload length field
    assert sync_reply[2:4] == async_reply[2:4]
    # Bytes 8-11: status (both must be 0x00000000 = success)
    assert sync_reply[8:12] == async_reply[8:12]
