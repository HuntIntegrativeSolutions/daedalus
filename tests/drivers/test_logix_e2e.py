"""End-to-end LogixDriver tests through SyncTcpTransport + CipSimServer.

Each test spins up a sim server with a pre-loaded tag store, opens a real TCP
connection, does register → forward_open → read(s) → forward_close →
unregister, and asserts the decoded values match the store.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from daedalus.cip.data_types import DINT, REAL
from daedalus.drivers import LogixDriver
from daedalus.session import Session
from daedalus.transport import SyncTcpTransport
from sim.server import CipSimServer


def _make_send_recv(transport: SyncTcpTransport) -> Callable[[bytes], bytes]:
    """Wire a SyncTcpTransport into a send_recv callable for LogixDriver."""

    def _inner(frame: bytes) -> bytes:
        transport.send_frame(frame)
        return transport.recv_frame()

    return _inner


def _open_connection(
    sim: CipSimServer,
) -> tuple[Session, SyncTcpTransport, LogixDriver]:
    """Register + Forward_Open; return (session, transport, driver)."""
    session = Session()
    transport = SyncTcpTransport(sim.host, sim.port)
    transport.connect()
    transport.send_frame(session.register_request())
    session.register_reply(transport.recv_frame())
    transport.send_frame(session.forward_open_request(large=False))
    session.forward_open_reply(transport.recv_frame())
    driver = LogixDriver(session, _make_send_recv(transport))
    return session, transport, driver


def _close_connection(session: Session, transport: SyncTcpTransport) -> None:
    """Forward_Close + UnregisterSession."""
    transport.send_frame(session.forward_close_request())
    session.forward_close_reply(transport.recv_frame())
    transport.send_frame(session.unregister_request())
    transport.close()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_e2e_read_dint(make_tag_server: Any) -> None:
    srv = make_tag_server({"MyDINT": (DINT.code, DINT.encode(12345))})
    session, transport, driver = _open_connection(srv)
    try:
        tag = driver.read_tag("MyDINT")
        assert tag.value == 12345
        assert tag.type == "DINT"
        assert tag.error is None
    finally:
        _close_connection(session, transport)


def test_e2e_read_real(make_tag_server: Any) -> None:
    srv = make_tag_server({"Pi": (REAL.code, REAL.encode(3.14159))})
    session, transport, driver = _open_connection(srv)
    try:
        tag = driver.read_tag("Pi")
        assert abs(tag.value - 3.14159) < 1e-4
        assert tag.type == "REAL"
    finally:
        _close_connection(session, transport)


def test_e2e_read_array(make_tag_server: Any) -> None:
    value_bytes = b"".join(DINT.encode(i * 10) for i in range(8))
    srv = make_tag_server({"Counts": (DINT.code, value_bytes)})
    session, transport, driver = _open_connection(srv)
    try:
        tag = driver.read_tag("Counts", element_count=8)
        assert tag.value == [i * 10 for i in range(8)]
    finally:
        _close_connection(session, transport)


def test_e2e_read_struct(make_tag_server: Any) -> None:
    # Struct payload: 2-byte handle + 4 data bytes
    struct_bytes = b"\x01\x00" + b"\xde\xad\xbe\xef"
    srv = make_tag_server({"MyUDT": (0x02A0, struct_bytes)})
    session, transport, driver = _open_connection(srv)
    try:
        tag = driver.read_tag("MyUDT")
        assert tag.type == "STRUCT"
        assert isinstance(tag.value, bytes)
        assert tag.value == b"\xde\xad\xbe\xef"
    finally:
        _close_connection(session, transport)


def test_e2e_multi_read(make_tag_server: Any) -> None:
    store = {
        "TagA": (DINT.code, DINT.encode(111)),
        "TagB": (REAL.code, REAL.encode(2.22)),
    }
    srv = make_tag_server(store)
    session, transport, driver = _open_connection(srv)
    try:
        tags = driver.read_tags(["TagA", "TagB"])
        assert len(tags) == 2
        assert tags[0].value == 111
        assert abs(tags[1].value - 2.22) < 1e-4
    finally:
        _close_connection(session, transport)


def test_e2e_fragmented_read(make_tag_server: Any) -> None:
    # Large array: 50 DINTs = 200 bytes; force fragmentation with threshold=40
    n = 50
    value_bytes = b"".join(DINT.encode(i) for i in range(n))
    srv = make_tag_server({"BigArr": (DINT.code, value_bytes)}, frag_threshold=40)
    session, transport, driver = _open_connection(srv)
    try:
        tag = driver.read_tag("BigArr", element_count=n)
        assert isinstance(tag.value, list)
        assert len(tag.value) == n
        assert tag.value == list(range(n))
    finally:
        _close_connection(session, transport)


def test_e2e_full_lifecycle(make_tag_server: Any) -> None:
    """register → fo → read → read_tags → fc → unregister — all assertions pass."""
    store = {
        "X": (DINT.code, DINT.encode(1)),
        "Y": (DINT.code, DINT.encode(2)),
    }
    srv = make_tag_server(store)
    session, transport, driver = _open_connection(srv)

    tag_x = driver.read_tag("X")
    assert tag_x.value == 1

    tags = driver.read_tags(["X", "Y"])
    assert tags[0].value == 1
    assert tags[1].value == 2

    _close_connection(session, transport)
    assert not session.connected
    assert not session.registered


def test_e2e_missing_tag_in_msp_captured_not_raised(make_tag_server: Any) -> None:
    """A missing tag in a batch read produces Tag.error, doesn't raise."""
    store = {"Present": (DINT.code, DINT.encode(7))}
    srv = make_tag_server(store)
    session, transport, driver = _open_connection(srv)
    try:
        tags = driver.read_tags(["Present", "Missing"])
        assert len(tags) == 2
        assert tags[0].value == 7
        assert tags[0].error is None
        assert tags[1].value is None
        assert tags[1].error is not None
    finally:
        _close_connection(session, transport)
