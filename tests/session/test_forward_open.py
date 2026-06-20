"""Unit tests for Session Forward_Open / Forward_Close state machine.

No sockets — all replies are built synthetically from L0 primitives.
"""

from __future__ import annotations

import struct

import pytest

from daedalus.cip.services import ConnectionManagerService
from daedalus.exceptions import DataError, ForwardOpenError, LargeForwardOpenRejected, RequestError
from daedalus.packets.encap import CPFItem, CPFTypeCode, EncapsulationHeader, build_cpf
from daedalus.session import Session, SessionState

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SESSION_HANDLE = 0x1234


def _make_fo_success_reply(
    *,
    ot_connection_id: int = 0xDEADBEEF,
    to_connection_id: int = 0x71190427,
    connection_serial: int = 0x0427,
    originator_vendor_id: int = 0x1009,
    originator_serial: int = 0x71191009,
    ot_actual_api: int = 0x00204001,
    to_actual_api: int = 0x00204001,
    service: int = int(ConnectionManagerService.FORWARD_OPEN),
) -> bytes:
    """Build a synthetic Forward_Open success reply (full SendRRData frame)."""
    fo_payload = struct.pack(
        "<IIHHIIIBB",
        ot_connection_id,
        to_connection_id,
        connection_serial,
        originator_vendor_id,
        originator_serial,
        ot_actual_api,
        to_actual_api,
        0,  # app_reply_size
        0,  # reserved
    )
    cip_reply = bytes([service | 0x80, 0x00, 0x00, 0x00]) + fo_payload
    cpf = (
        b"\x00\x00\x00\x00"  # interface handle
        + b"\x00\x00"  # timeout
        + build_cpf(
            [
                CPFItem(CPFTypeCode.NULL_ADDRESS),
                CPFItem(CPFTypeCode.UNCONNECTED_DATA, cip_reply),
            ]
        )
    )
    header = EncapsulationHeader.for_command(
        0x6F,  # SEND_RR_DATA
        data_length=len(cpf),
        session_handle=_SESSION_HANDLE,
    )
    return header.encode() + cpf


def _make_fo_error_reply(
    *,
    status: int = 0x08,
    service: int = int(ConnectionManagerService.LARGE_FORWARD_OPEN),
) -> bytes:
    """Build a synthetic Forward_Open error reply (full SendRRData frame)."""
    cip_reply = bytes([service | 0x80, 0x00, status, 0x00])
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
    header = EncapsulationHeader.for_command(
        0x6F, data_length=len(cpf), session_handle=_SESSION_HANDLE
    )
    return header.encode() + cpf


def _make_fc_success_reply(
    service: int = int(ConnectionManagerService.FORWARD_CLOSE),
) -> bytes:
    """Build a synthetic Forward_Close success reply."""
    cip_reply = bytes([service | 0x80, 0x00, 0x00, 0x00])
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
    header = EncapsulationHeader.for_command(
        0x6F, data_length=len(cpf), session_handle=_SESSION_HANDLE
    )
    return header.encode() + cpf


def _registered_session() -> Session:
    """Return a Session in the REGISTERED state."""
    from daedalus.packets.encap import EncapsulationHeader

    s = Session()
    s.register_request()
    reply_header = EncapsulationHeader(
        command=0x65,
        length=4,
        session_handle=_SESSION_HANDLE,
        status=0,
        sender_context=b"\x00" * 8,
        options=0,
    )
    s.register_reply(reply_header.encode() + b"\x01\x00\x00\x00")
    return s


def _connected_session() -> Session:
    """Return a Session in the CONNECTED state."""
    s = _registered_session()
    s.forward_open_request(large=False)
    s.forward_open_reply(_make_fo_success_reply(service=int(ConnectionManagerService.FORWARD_OPEN)))
    return s


# ---------------------------------------------------------------------------
# Initial state
# ---------------------------------------------------------------------------


def test_initial_no_ot_connection_id() -> None:
    s = Session()
    assert s.ot_connection_id == 0
    assert not s.connected


def test_initial_connection_serial_zero() -> None:
    s = Session()
    assert s.connection_serial == 0


# ---------------------------------------------------------------------------
# forward_open_request
# ---------------------------------------------------------------------------


def test_fo_request_transitions_to_connecting() -> None:
    s = _registered_session()
    s.forward_open_request()
    assert s.state == SessionState.CONNECTING


def test_fo_request_returns_bytes() -> None:
    s = _registered_session()
    frame = s.forward_open_request()
    assert isinstance(frame, bytes)
    assert len(frame) > 24


def test_fo_request_large_service_byte_in_frame() -> None:
    """large=True must encode service 0x5B inside the CIP payload."""
    s = _registered_session()
    frame = s.forward_open_request(large=True)
    # SendRRData: 24-byte encap + 6-byte (interface+timeout) + 2-byte item count
    # + NULL_ADDRESS item (4 bytes: type+len) + UNCONNECTED_DATA item header (4 bytes)
    # + CIP message starts — service byte is first byte of CIP payload
    # Locate UNCONNECTED_DATA content: parse manually
    cpf_offset = 24 + 6
    item_count = struct.unpack_from("<H", frame, cpf_offset)[0]
    assert item_count == 2
    # NULL_ADDRESS item: type(2) + len(2) + 0 data = 4 bytes
    # UNCONNECTED_DATA item: type(2) + len(2) = 4 bytes header, then data
    uc_data_start = cpf_offset + 2 + 4 + 4  # item_count_word + null_item + uc_item_header
    assert frame[uc_data_start] == int(ConnectionManagerService.LARGE_FORWARD_OPEN)


def test_fo_request_standard_service_byte_in_frame() -> None:
    """large=False must encode service 0x54."""
    s = _registered_session()
    frame = s.forward_open_request(large=False)
    cpf_offset = 24 + 6
    uc_data_start = cpf_offset + 2 + 4 + 4
    assert frame[uc_data_start] == int(ConnectionManagerService.FORWARD_OPEN)


def test_fo_request_from_idle_raises() -> None:
    s = Session()
    with pytest.raises(RequestError, match="REGISTERED"):
        s.forward_open_request()


def test_fo_request_from_connected_raises() -> None:
    s = _connected_session()
    with pytest.raises(RequestError, match="REGISTERED"):
        s.forward_open_request()


# ---------------------------------------------------------------------------
# forward_open_reply — success
# ---------------------------------------------------------------------------


def test_fo_reply_transitions_to_connected() -> None:
    s = _registered_session()
    s.forward_open_request(large=False)
    s.forward_open_reply(_make_fo_success_reply())
    assert s.state == SessionState.CONNECTED
    assert s.connected


def test_fo_reply_stores_ot_connection_id() -> None:
    s = _registered_session()
    s.forward_open_request(large=False)
    s.forward_open_reply(_make_fo_success_reply(ot_connection_id=0xCAFEBABE))
    assert s.ot_connection_id == 0xCAFEBABE


def test_fo_reply_requires_connecting_state() -> None:
    s = _registered_session()
    with pytest.raises(RequestError, match="CONNECTING"):
        s.forward_open_reply(_make_fo_success_reply())


# ---------------------------------------------------------------------------
# forward_open_reply — error paths
# ---------------------------------------------------------------------------


def test_large_fo_rejected_raises_typed_exception() -> None:
    s = _registered_session()
    s.forward_open_request(large=True)
    with pytest.raises(LargeForwardOpenRejected):
        s.forward_open_reply(_make_fo_error_reply(status=0x08))


def test_large_fo_rejected_resets_to_registered() -> None:
    s = _registered_session()
    s.forward_open_request(large=True)
    with pytest.raises(LargeForwardOpenRejected):
        s.forward_open_reply(_make_fo_error_reply(status=0x08))
    assert s.state == SessionState.REGISTERED


def test_fo_other_error_raises_forward_open_error() -> None:
    s = _registered_session()
    s.forward_open_request(large=False)
    with pytest.raises(ForwardOpenError):
        s.forward_open_reply(
            _make_fo_error_reply(
                status=0x01,
                service=int(ConnectionManagerService.FORWARD_OPEN),
            )
        )


def test_fo_other_error_resets_to_registered() -> None:
    s = _registered_session()
    s.forward_open_request(large=False)
    with pytest.raises(ForwardOpenError):
        s.forward_open_reply(
            _make_fo_error_reply(
                status=0x01,
                service=int(ConnectionManagerService.FORWARD_OPEN),
            )
        )
    assert s.state == SessionState.REGISTERED


def test_fo_reply_short_frame_raises_data_error() -> None:
    s = _registered_session()
    s.forward_open_request()
    with pytest.raises(DataError):
        s.forward_open_reply(b"\x6f\x00" * 4)  # < 24 bytes and malformed


def test_non_0x08_on_large_raises_forward_open_error_not_large_rejected() -> None:
    """A non-0x08 error on a large FO attempt is ForwardOpenError, not LargeForwardOpenRejected."""
    s = _registered_session()
    s.forward_open_request(large=True)
    with pytest.raises(ForwardOpenError) as exc_info:
        s.forward_open_reply(_make_fo_error_reply(status=0x01))
    assert not isinstance(exc_info.value, LargeForwardOpenRejected)


# ---------------------------------------------------------------------------
# Forward_Close
# ---------------------------------------------------------------------------


def test_fc_request_requires_connected() -> None:
    s = _registered_session()
    with pytest.raises(RequestError, match="CONNECTED"):
        s.forward_close_request()


def test_fc_request_transitions_to_closing() -> None:
    s = _connected_session()
    s.forward_close_request()
    assert s.state == SessionState.CLOSING


def test_fc_reply_returns_to_registered() -> None:
    s = _connected_session()
    s.forward_close_request()
    s.forward_close_reply(_make_fc_success_reply())
    assert s.state == SessionState.REGISTERED


def test_fc_reply_clears_ot_connection_id() -> None:
    s = _connected_session()
    assert s.ot_connection_id != 0
    s.forward_close_request()
    s.forward_close_reply(_make_fc_success_reply())
    assert s.ot_connection_id == 0


def test_fc_reply_requires_closing_state() -> None:
    s = _connected_session()
    with pytest.raises(RequestError, match="CLOSING"):
        s.forward_close_reply(_make_fc_success_reply())


# ---------------------------------------------------------------------------
# Full lifecycle
# ---------------------------------------------------------------------------


def test_full_lifecycle_with_fo_fc() -> None:
    """IDLE → REGISTERING → REGISTERED → CONNECTING → CONNECTED → CLOSING → REGISTERED → IDLE."""
    s = Session()
    # Register
    s.register_request()
    from daedalus.packets.encap import EncapsulationHeader as EH

    reg_reply = EH(0x65, 4, _SESSION_HANDLE, 0, b"\x00" * 8, 0).encode() + b"\x01\x00\x00\x00"
    s.register_reply(reg_reply)
    assert s.state == SessionState.REGISTERED

    # Forward Open
    s.forward_open_request(large=False)
    assert s.connecting  # avoids mypy literal-narrowing false positive
    s.forward_open_reply(_make_fo_success_reply(ot_connection_id=0xABCD1234))
    assert s.connected
    assert s.ot_connection_id == 0xABCD1234

    # Forward Close
    s.forward_close_request()
    assert not s.connected  # avoids mypy literal-narrowing false positive (CLOSING ≠ CONNECTED)
    s.forward_close_reply(_make_fc_success_reply())
    assert s.registered

    # Unregister
    s.unregister_request()
    assert not s.registered


def test_large_then_standard_fallback_cycle() -> None:
    """Large FO rejection → standard FO retry → CONNECTED."""
    s = _registered_session()

    # Large FO attempt → rejection
    s.forward_open_request(large=True)
    with pytest.raises(LargeForwardOpenRejected):
        s.forward_open_reply(_make_fo_error_reply(status=0x08))
    assert s.state == SessionState.REGISTERED

    # Standard FO retry → success
    s.forward_open_request(large=False)
    s.forward_open_reply(_make_fo_success_reply(service=int(ConnectionManagerService.FORWARD_OPEN)))
    assert s.connected  # avoids mypy literal-narrowing false positive
