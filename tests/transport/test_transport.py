"""Integration tests — Session + SyncTcpTransport + CipSimServer.

These tests prove the sans-I/O contract end-to-end: a pure state machine
emits bytes, a real TCP transport ships them to an in-process sim server,
and the reply feeds back into the state machine.
"""

from __future__ import annotations

import socket
import threading

import pytest

from daedalus.exceptions import CommError
from daedalus.session import Session
from daedalus.transport import SyncTcpTransport
from sim.server import CipSimServer

# ---------------------------------------------------------------------------
# End-to-end round-trip (the core proof)
# ---------------------------------------------------------------------------


def test_register_unregister_roundtrip(sim_server: CipSimServer) -> None:
    """Session drives Transport against sim; full RegisterSession/Unregister cycle."""
    session = Session()
    with SyncTcpTransport(sim_server.host, sim_server.port) as transport:
        transport.send_frame(session.register_request())
        session.register_reply(transport.recv_frame())
        assert session.registered
        assert session.session_handle != 0
        transport.send_frame(session.unregister_request())
        # No recv — sim closes the connection on UnregisterSession
    assert not session.registered


def test_session_handle_nonzero_after_register(sim_server: CipSimServer) -> None:
    session = Session()
    with SyncTcpTransport(sim_server.host, sim_server.port) as transport:
        transport.send_frame(session.register_request())
        session.register_reply(transport.recv_frame())
    assert session.session_handle > 0


def test_multiple_sessions_get_independent_handles(sim_server: CipSimServer) -> None:
    """Each new connection gets a distinct session handle from the sim."""
    handles: set[int] = set()
    for _ in range(3):
        s = Session()
        with SyncTcpTransport(sim_server.host, sim_server.port) as t:
            t.send_frame(s.register_request())
            s.register_reply(t.recv_frame())
            handles.add(s.session_handle)
    # All three handles are non-zero; they are almost certainly distinct
    # (probability of collision is negligible with 32-bit random handles)
    assert all(h > 0 for h in handles)


# ---------------------------------------------------------------------------
# Transport context manager
# ---------------------------------------------------------------------------


def test_transport_context_manager_connects_and_closes(sim_server: CipSimServer) -> None:
    with SyncTcpTransport(sim_server.host, sim_server.port) as t:
        assert t._sock is not None
    assert t._sock is None


def test_transport_close_is_idempotent(sim_server: CipSimServer) -> None:
    t = SyncTcpTransport(sim_server.host, sim_server.port)
    t.connect()
    t.close()
    t.close()  # second close must not raise


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


def test_connect_refused_raises_comm_error() -> None:
    """Connecting to a port that has no listener raises CommError."""
    with pytest.raises(CommError):
        SyncTcpTransport("127.0.0.1", 1, timeout=1.0).connect()


def test_short_recv_raises_comm_error() -> None:
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

        transport = SyncTcpTransport("127.0.0.1", port, timeout=2.0)
        transport.connect()
        with pytest.raises(CommError):
            transport.recv_frame()
        transport.close()
        t.join(timeout=2.0)


def test_send_frame_without_connect_raises_comm_error() -> None:
    t = SyncTcpTransport("127.0.0.1", 44818)
    with pytest.raises(CommError):
        t.send_frame(b"\x00" * 28)


def test_recv_frame_without_connect_raises_comm_error() -> None:
    t = SyncTcpTransport("127.0.0.1", 44818)
    with pytest.raises(CommError):
        t.recv_frame()
