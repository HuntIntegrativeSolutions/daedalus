"""Unit tests for the Session sans-I/O state machine.

No sockets here — all replies are built synthetically from L0 primitives.
"""

from __future__ import annotations

import pytest

from daedalus.cip.services import EncapsulationCommand
from daedalus.exceptions import DataError, RequestError, ResponseError
from daedalus.packets.encap import EncapsulationHeader
from daedalus.session import Session, SessionState

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_register_reply(
    session_handle: int = 0x1234,
    status: int = 0,
    command: int = int(EncapsulationCommand.REGISTER_SESSION),
) -> bytes:
    """Build a synthetic RegisterSession reply frame."""
    header = EncapsulationHeader(
        command=command,
        length=4,
        session_handle=session_handle,
        status=status,
        sender_context=b"\x00" * 8,
        options=0,
    )
    return header.encode() + b"\x01\x00\x00\x00"


# ---------------------------------------------------------------------------
# Initial state
# ---------------------------------------------------------------------------


def test_initial_state_is_idle() -> None:
    session = Session()
    assert session.state == SessionState.IDLE
    assert not session.registered
    assert session.session_handle == 0


# ---------------------------------------------------------------------------
# register_request
# ---------------------------------------------------------------------------


def test_register_request_bytes_length() -> None:
    session = Session()
    data = session.register_request()
    assert len(data) == 28  # 24-byte header + 4-byte payload (version + options)


def test_register_request_transitions_to_registering() -> None:
    session = Session()
    session.register_request()
    assert session.state == SessionState.REGISTERING


def test_register_request_from_idle_only() -> None:
    """register_request() is only valid from IDLE; a second call must raise."""
    session = Session()
    session.register_request()
    with pytest.raises(RequestError):
        session.register_request()


# ---------------------------------------------------------------------------
# register_reply — success path
# ---------------------------------------------------------------------------


def test_register_reply_sets_handle() -> None:
    session = Session()
    session.register_request()
    session.register_reply(_make_register_reply(session_handle=0xABCD))
    assert session.registered
    assert session.session_handle == 0xABCD
    assert session.state == SessionState.REGISTERED


# ---------------------------------------------------------------------------
# register_reply — error paths
# ---------------------------------------------------------------------------


def test_register_reply_bad_command() -> None:
    session = Session()
    session.register_request()
    bad_reply = _make_register_reply(command=0x6F)  # SEND_RR_DATA, not REGISTER_SESSION
    with pytest.raises(ResponseError, match="REGISTER_SESSION"):
        session.register_reply(bad_reply)


def test_register_reply_nonzero_status() -> None:
    session = Session()
    session.register_request()
    with pytest.raises(ResponseError, match="status"):
        session.register_reply(_make_register_reply(status=0x0001))


def test_register_reply_zero_handle() -> None:
    session = Session()
    session.register_request()
    with pytest.raises(ResponseError, match="session_handle=0"):
        session.register_reply(_make_register_reply(session_handle=0))


def test_register_reply_short_frame() -> None:
    """Frames shorter than 24 bytes must surface as DataError, not struct.error."""
    session = Session()
    session.register_request()
    with pytest.raises(DataError):
        session.register_reply(b"\x65\x00\x04\x00")  # 4 bytes — far too short


def test_register_reply_requires_registering_state() -> None:
    """register_reply() from IDLE (without a prior request) must raise."""
    session = Session()
    with pytest.raises(RequestError):
        session.register_reply(_make_register_reply())


# ---------------------------------------------------------------------------
# unregister_request
# ---------------------------------------------------------------------------


def test_unregister_request_bytes_length() -> None:
    session = Session()
    session.register_request()
    session.register_reply(_make_register_reply())
    data = session.unregister_request()
    assert len(data) == 24  # header only; UnregisterSession carries no payload


def test_unregister_resets_state() -> None:
    session = Session()
    session.register_request()
    session.register_reply(_make_register_reply())
    assert session.registered
    session.unregister_request()
    assert not session.registered
    assert session.session_handle == 0
    assert session.state == SessionState.IDLE


def test_unregister_requires_registered() -> None:
    session = Session()
    with pytest.raises(RequestError):
        session.unregister_request()


def test_full_lifecycle() -> None:
    """IDLE → REGISTERING → REGISTERED → IDLE round-trip."""
    session = Session()
    session.register_request()
    assert session.state == SessionState.REGISTERING
    session.register_reply(_make_register_reply(session_handle=42))
    assert session.registered  # REGISTERED state via bool property (avoids mypy narrowing)
    assert session.session_handle == 42
    session.unregister_request()
    assert not session.registered  # back to IDLE via bool property
    assert session.session_handle == 0


# ---------------------------------------------------------------------------
# Class 3 sequence counter
# ---------------------------------------------------------------------------


def _make_fo_success_reply() -> bytes:
    """Minimal Forward_Open success reply (full SendRRData frame)."""
    import struct as _struct

    from daedalus.cip.services import ConnectionManagerService
    from daedalus.packets.encap import CPFItem, CPFTypeCode, EncapsulationHeader, build_cpf

    ot_conn_id = 0xDEADBEEF
    fo_payload = _struct.pack(
        "<IIHHIIIBB",
        ot_conn_id,
        0x71190427,
        0x0427,
        0x1009,
        0x71191009,
        0x00204001,
        0x00204001,
        0,
        0,
    )
    svc = int(ConnectionManagerService.FORWARD_OPEN)
    cip_reply = bytes([svc | 0x80, 0x00, 0x00, 0x00]) + fo_payload
    cpf = (
        b"\x00\x00\x00\x00"
        + b"\x00\x00"
        + build_cpf(
            [
                CPFItem(CPFTypeCode.NULL_ADDRESS),
                CPFItem(CPFTypeCode.UNCONNECTED_DATA, cip_reply),
            ]
        )
    )
    header = EncapsulationHeader.for_command(0x6F, data_length=len(cpf), session_handle=0x1234)
    return header.encode() + cpf


def _make_fc_success_reply() -> bytes:
    """Minimal Forward_Close success reply (full SendRRData frame)."""
    from daedalus.cip.services import ConnectionManagerService
    from daedalus.packets.encap import CPFItem, CPFTypeCode, EncapsulationHeader, build_cpf

    svc = int(ConnectionManagerService.FORWARD_CLOSE)
    cip_reply = bytes([svc | 0x80, 0x00, 0x00, 0x00])
    cpf = (
        b"\x00\x00\x00\x00"
        + b"\x00\x00"
        + build_cpf(
            [
                CPFItem(CPFTypeCode.NULL_ADDRESS),
                CPFItem(CPFTypeCode.UNCONNECTED_DATA, cip_reply),
            ]
        )
    )
    header = EncapsulationHeader.for_command(0x6F, data_length=len(cpf), session_handle=0x1234)
    return header.encode() + cpf


def _registered_session() -> Session:
    session = Session()
    session.register_request()
    session.register_reply(_make_register_reply(session_handle=0x1234))
    return session


def test_sequence_count_first_call_returns_one() -> None:
    session = Session()
    assert session.next_sequence_count() == 1


def test_sequence_count_increments() -> None:
    session = Session()
    assert session.next_sequence_count() == 1
    assert session.next_sequence_count() == 2
    assert session.next_sequence_count() == 3


def test_sequence_count_wraps() -> None:
    session = Session()
    session._sequence_count = 0xFFFE
    assert session.next_sequence_count() == 0xFFFF
    assert session.next_sequence_count() == 0  # wraps: (0xFFFF+1)&0xFFFF = 0


def test_sequence_count_resets_on_fo_reply() -> None:
    session = _registered_session()
    session.forward_open_request(large=False)
    session._sequence_count = 5
    session.forward_open_reply(_make_fo_success_reply())
    # Reset to 0 so next call returns 1
    assert session._sequence_count == 0
    assert session.next_sequence_count() == 1


def test_sequence_count_resets_on_fc_reply() -> None:
    session = _registered_session()
    session.forward_open_request(large=False)
    session.forward_open_reply(_make_fo_success_reply())
    session._sequence_count = 7
    session.forward_close_request()
    session.forward_close_reply(_make_fc_success_reply())
    # Reset to 0 so next FO gets a fresh counter
    assert session._sequence_count == 0
