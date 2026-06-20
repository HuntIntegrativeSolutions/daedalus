"""End-to-end Forward_Open / Forward_Close integration tests.

Session state machine + SyncTcpTransport + CipSimServer — proves the
sans-I/O contract for connection establishment without touching hardware.
"""

from __future__ import annotations

import pytest

from daedalus.exceptions import LargeForwardOpenRejected
from daedalus.session import Session
from daedalus.transport import SyncTcpTransport
from sim.server import CipSimServer


def _register(session: Session, transport: SyncTcpTransport) -> None:
    """Run RegisterSession round-trip."""
    transport.send_frame(session.register_request())
    session.register_reply(transport.recv_frame())


def test_large_fo_roundtrip(sim_server: CipSimServer) -> None:
    """Large_Forward_Open → Forward_Close cycle via the sim."""
    session = Session()
    with SyncTcpTransport(sim_server.host, sim_server.port) as transport:
        _register(session, transport)
        transport.send_frame(session.forward_open_request(large=True))
        session.forward_open_reply(transport.recv_frame())
        assert session.connected

        transport.send_frame(session.forward_close_request())
        session.forward_close_reply(transport.recv_frame())
        assert session.registered


def test_standard_fo_roundtrip(sim_server: CipSimServer) -> None:
    """Standard Forward_Open → Forward_Close cycle via the sim."""
    session = Session()
    with SyncTcpTransport(sim_server.host, sim_server.port) as transport:
        _register(session, transport)
        transport.send_frame(session.forward_open_request(large=False))
        session.forward_open_reply(transport.recv_frame())
        assert session.connected

        transport.send_frame(session.forward_close_request())
        session.forward_close_reply(transport.recv_frame())
        assert session.registered


def test_ot_connection_id_nonzero_after_fo(sim_server: CipSimServer) -> None:
    """Sim assigns a non-zero O→T connection ID."""
    session = Session()
    with SyncTcpTransport(sim_server.host, sim_server.port) as transport:
        _register(session, transport)
        transport.send_frame(session.forward_open_request(large=False))
        session.forward_open_reply(transport.recv_frame())
    assert session.ot_connection_id > 0


def test_large_fo_fallback_to_standard(sim_server_rejecting_large: CipSimServer) -> None:
    """Sim rejects Large_FO; caller catches LargeForwardOpenRejected and retries standard."""
    session = Session()
    with SyncTcpTransport(
        sim_server_rejecting_large.host, sim_server_rejecting_large.port
    ) as transport:
        _register(session, transport)

        # Large FO — sim returns CIP status 0x08
        transport.send_frame(session.forward_open_request(large=True))
        with pytest.raises(LargeForwardOpenRejected):
            session.forward_open_reply(transport.recv_frame())
        assert session.registered

        # Standard FO retry — should succeed
        transport.send_frame(session.forward_open_request(large=False))
        session.forward_open_reply(transport.recv_frame())
        assert session.connected


def test_fo_then_unregister_after_fc(sim_server: CipSimServer) -> None:
    """Full teardown: FO → FC → UnregisterSession."""
    session = Session()
    with SyncTcpTransport(sim_server.host, sim_server.port) as transport:
        _register(session, transport)
        transport.send_frame(session.forward_open_request(large=False))
        session.forward_open_reply(transport.recv_frame())

        transport.send_frame(session.forward_close_request())
        session.forward_close_reply(transport.recv_frame())
        assert session.registered

        transport.send_frame(session.unregister_request())
        # No reply expected — sim closes the connection
    assert not session.registered
