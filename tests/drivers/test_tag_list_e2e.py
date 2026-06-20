"""End-to-end tag-list tests through SyncTcpTransport + CipSimServer.

Each test spins up a sim server with a pre-loaded symbol_store, opens a real
TCP connection, and calls driver.get_tag_list().
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from daedalus.cip.data_types import DINT
from daedalus.drivers import LogixDriver
from daedalus.session import Session
from daedalus.transport import SyncTcpTransport
from sim.server import CipSimServer

# Symbol-type constants (same as unit test file)
_ST_DINT_SCALAR = 0x00C4
_ST_DINT_1D = 0x20C4
_ST_STRUCT = 0x8123
_ST_SYSTEM = 0x10C4


def _make_send_recv(transport: SyncTcpTransport) -> Callable[[bytes], bytes]:
    def _inner(frame: bytes) -> bytes:
        transport.send_frame(frame)
        return transport.recv_frame()

    return _inner


def _open_connection(sim: CipSimServer) -> tuple[Session, SyncTcpTransport, LogixDriver]:
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
    transport.send_frame(session.forward_close_request())
    session.forward_close_reply(transport.recv_frame())
    transport.send_frame(session.unregister_request())
    transport.close()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_e2e_dint_scalar(make_symbol_server: Any) -> None:
    store = {"controller": [{"name": "MyDINT", "instance_id": 1, "symbol_type": _ST_DINT_SCALAR}]}
    srv = make_symbol_server(store)
    session, transport, driver = _open_connection(srv)
    try:
        tags = driver.get_tag_list()
    finally:
        _close_connection(session, transport)

    assert any(t.tag_name == "MyDINT" and t.data_type == "DINT" for t in tags)
    t = next(t for t in tags if t.tag_name == "MyDINT")
    assert t.scope == "controller"
    assert t.dimensions == ()
    assert t.is_struct is False


def test_e2e_array_tag(make_symbol_server: Any) -> None:
    store = {
        "controller": [
            {"name": "MyArr", "instance_id": 1, "symbol_type": _ST_DINT_1D, "dims": (8, 0, 0)}
        ]
    }
    srv = make_symbol_server(store)
    session, transport, driver = _open_connection(srv)
    try:
        tags = driver.get_tag_list()
    finally:
        _close_connection(session, transport)

    t = next(t for t in tags if t.tag_name == "MyArr")
    assert t.dimensions == (8,)


def test_e2e_struct_tag(make_symbol_server: Any) -> None:
    store = {"controller": [{"name": "MyUDT", "instance_id": 1, "symbol_type": _ST_STRUCT}]}
    srv = make_symbol_server(store)
    session, transport, driver = _open_connection(srv)
    try:
        tags = driver.get_tag_list()
    finally:
        _close_connection(session, transport)

    t = next(t for t in tags if t.tag_name == "MyUDT")
    assert t.is_struct is True
    assert t.template_instance_id == 0x123


def test_e2e_system_tag_excluded(make_symbol_server: Any) -> None:
    store = {
        "controller": [
            {"name": "UserTag", "instance_id": 1, "symbol_type": _ST_DINT_SCALAR},
            {"name": "SysTag", "instance_id": 2, "symbol_type": _ST_SYSTEM},
        ]
    }
    srv = make_symbol_server(store)
    session, transport, driver = _open_connection(srv)
    try:
        tags = driver.get_tag_list()
    finally:
        _close_connection(session, transport)

    names = {t.tag_name for t in tags}
    assert "UserTag" in names
    assert "SysTag" not in names


def test_e2e_program_scope_present(make_symbol_server: Any) -> None:
    store = {
        "controller": [
            {"name": "CtrlTag", "instance_id": 1, "symbol_type": _ST_DINT_SCALAR},
            {"name": "Program:Main", "instance_id": 2, "symbol_type": 0x0000},
        ],
        "Program:Main": [
            {"name": "LocalTag", "instance_id": 1, "symbol_type": _ST_DINT_SCALAR},
        ],
    }
    srv = make_symbol_server(store)
    session, transport, driver = _open_connection(srv)
    try:
        tags = driver.get_tag_list()
    finally:
        _close_connection(session, transport)

    names = {t.tag_name for t in tags}
    assert "CtrlTag" in names
    assert "Program:Main.LocalTag" in names


def test_e2e_multi_reply_assembled(make_symbol_server: Any) -> None:
    """Small frag_size forces continuation (status 0x06); full list is assembled."""
    # Two entries; serialize one and measure its byte size to pick a frag_size that
    # forces a split after the first entry.

    from daedalus.cip.data_types import STRING

    def _entry_size(name: str) -> int:
        return (
            4  # UDINT instance_id
            + len(STRING.encode(name))  # STRING: UINT + bytes
            + 2  # UINT symbol_type
            + 12  # 3x UDINT address fields
            + 12  # 3x UDINT dims
        )

    # Pick frag_size = first entry size (forces split after entry 0)
    entry0_size = _entry_size("TagA")
    frag_size = entry0_size  # exactly one entry per chunk

    store = {
        "controller": [
            {"name": "TagA", "instance_id": 0, "symbol_type": _ST_DINT_SCALAR},
            {"name": "TagB", "instance_id": 1, "symbol_type": _ST_DINT_SCALAR},
        ]
    }
    srv = make_symbol_server(store, tag_list_frag_size=frag_size)
    session, transport, driver = _open_connection(srv)
    try:
        tags = driver.get_tag_list()
    finally:
        _close_connection(session, transport)

    names = {t.tag_name for t in tags}
    assert "TagA" in names
    assert "TagB" in names


def test_e2e_full_lifecycle(make_symbol_server: Any, make_tag_server: Any) -> None:
    """register → FO → read_tag → get_tag_list → FC → unregister."""
    symbol_store = {
        "controller": [{"name": "Counter", "instance_id": 1, "symbol_type": _ST_DINT_SCALAR}]
    }
    tag_store = {"Counter": (DINT.code, DINT.encode(99))}

    # We need both stores in the same server.  Start a server with both.
    from sim.server import CipSimServer

    srv = CipSimServer(tag_store=tag_store, symbol_store=symbol_store)
    srv.start()
    try:
        session, transport, driver = _open_connection(srv)
        try:
            tag = driver.read_tag("Counter")
            assert tag.value == 99

            tags = driver.get_tag_list()
            assert any(t.tag_name == "Counter" for t in tags)
        finally:
            _close_connection(session, transport)
    finally:
        srv.stop()
