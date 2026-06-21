"""Forward_Open / Large_Forward_Open / Forward_Close builders and parsers.

Pure byte-level builders — no state, no I/O.

I/O-FORBIDDEN: this module must never import socket, ssl, asyncio, anyio,
selectors, socketserver, http, urllib, or requests.

Default parameter values match pycomm3's static cfg defaults (before the
per-connection randomisation of cid/vsn that occurs at connect time).  This
keeps the parity oracle test deterministic while allowing callers to override
all fields for interop with real hardware.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Final

from daedalus.cip.constants import (
    PRIORITY,
    TIMEOUT_MULTIPLIER,
    TIMEOUT_TICKS,
    TRANSPORT_CLASS,
)
from daedalus.cip.data_types import UDINT, UINT
from daedalus.cip.object_library import ClassCode
from daedalus.cip.services import ConnectionManagerService
from daedalus.exceptions import DataError, ForwardOpenError, LargeForwardOpenRejected, ResponseError
from daedalus.packets.cip import (
    MSG_ROUTER_PATH,
    build_send_rr_data,
    parse_cip_response,
    request_path,
)
from daedalus.packets.encap import CPFTypeCode, EncapsulationHeader, parse_cpf

__all__ = [
    "ForwardOpenReply",
    "build_forward_close",
    "build_forward_open",
    "parse_forward_close_reply",
    "parse_forward_open_reply",
]

# CIP Vol 1 3-5.5.1.1 — point-to-point, low-priority, connection-type bits
_INIT_NET_PARAMS: Final[int] = 0b_0100_0010_0000_0000  # 0x4200

# Hardcoded RPI (µs) for explicit messaging — pycomm3: "RPIs are not important
# for us so fixed value is fine".  0x00204001 ≈ 2.1 s.
_DEFAULT_RPI_US: Final[int] = 0x00204001

# Default connection IDs / serial — match pycomm3 static cfg defaults
# (randomised per-connection at real connect time, but fixed here for parity).
_DEFAULT_TO_CONN_ID: Final[int] = 0x71190427  # cid = b"\x27\x04\x19\x71"
_DEFAULT_CONN_SERIAL: Final[int] = 0x0427  # csn = b"\x27\x04"
_DEFAULT_ORIG_VENDOR_ID: Final[int] = 0x1009  # vid = b"\x09\x10"
_DEFAULT_ORIG_SERIAL: Final[int] = 0x71191009  # vsn = b"\x09\x10\x19\x71"

_DEFAULT_LARGE_CONN_SIZE: Final[int] = 4000
_DEFAULT_STD_CONN_SIZE: Final[int] = 500

# CIP status that means the device does not support Large_Forward_Open.
# Any other non-zero status is a hard failure and raises ForwardOpenError.
_LARGE_FO_UNSUPPORTED_STATUSES: Final[frozenset[int]] = frozenset({0x08})

# FO success reply payload struct (after parse_cip_response strips the 4-byte header)
# <IIHHIIIBB = ot_conn_id, to_conn_id, serial(H), vendor_id(H), orig_serial,
#              ot_api, to_api, app_reply_size(B), reserved(B) = 26 bytes
_FO_REPLY_STRUCT: Final[str] = "<IIHHIIIBB"
_FO_REPLY_SIZE: Final[int] = struct.calcsize(_FO_REPLY_STRUCT)  # 26


@dataclass(frozen=True)
class ForwardOpenReply:
    """Parsed Forward_Open / Large_Forward_Open success reply."""

    ot_connection_id: int
    to_connection_id: int
    connection_serial: int
    originator_vendor_id: int
    originator_serial: int
    ot_actual_api: int
    to_actual_api: int


# ---------------------------------------------------------------------------
# Internal builder — exposed for parity oracle tests
# ---------------------------------------------------------------------------


def _build_forward_open_data(
    *,
    large: bool,
    connection_size: int,
    rpi: int,
    to_connection_id: int,
    connection_serial: int,
    originator_vendor_id: int,
    originator_serial: int,
    connection_path: bytes,
) -> bytes:
    """Return the raw FO/LFO request data bytes (before CIP wrapping).

    Standard Forward_Open: 36 bytes + connection_path.
    Large_Forward_Open:    40 bytes + connection_path (net_params are UDINT).
    """
    if large:
        # ODVA Vol 1: Large FO net params are a UDINT
        net_params = UDINT.encode((connection_size & 0xFFFF) | (_INIT_NET_PARAMS << 16))
    else:
        net_params = UINT.encode((connection_size & 0x01FF) | _INIT_NET_PARAMS)

    return b"".join(
        [
            PRIORITY,
            TIMEOUT_TICKS,
            b"\x00\x00\x00\x00",  # O→T conn ID (device assigns; we leave blank)
            UDINT.encode(to_connection_id),
            UINT.encode(connection_serial),
            UINT.encode(originator_vendor_id),
            UDINT.encode(originator_serial),
            TIMEOUT_MULTIPLIER,
            b"\x00\x00\x00",  # reserved
            UDINT.encode(rpi),  # O→T RPI
            net_params,  # O→T net params
            UDINT.encode(rpi),  # T→O RPI
            net_params,  # T→O net params
            TRANSPORT_CLASS,
            connection_path,
        ]
    )


# ---------------------------------------------------------------------------
# Public builders
# ---------------------------------------------------------------------------


def build_forward_open(
    *,
    session_handle: int,
    large: bool = True,
    connection_size: int | None = None,
    rpi: int = _DEFAULT_RPI_US,
    to_connection_id: int = _DEFAULT_TO_CONN_ID,
    connection_serial: int = _DEFAULT_CONN_SERIAL,
    originator_vendor_id: int = _DEFAULT_ORIG_VENDOR_ID,
    originator_serial: int = _DEFAULT_ORIG_SERIAL,
    connection_path: bytes = MSG_ROUTER_PATH,
) -> bytes:
    """Build a Forward_Open or Large_Forward_Open SendRRData frame.

    Args:
        session_handle: EtherNet/IP session handle from RegisterSession.
        large: If True, use Large_Forward_Open (0x5B); False uses standard (0x54).
        connection_size: Max CIP payload size in bytes.  Defaults to 4000 (large)
            or 500 (standard).
        rpi: Requested Packet Interval in µs.
        to_connection_id: Originator-assigned T→O connection ID.
        connection_serial: Connection serial number (used in Forward_Close).
        originator_vendor_id: Our vendor ID.
        originator_serial: Our serial number.
        connection_path: Encoded PADDED_EPATH (with word-count prefix) for the
            connection target.  Default is the Message Router (direct connection).

    Returns:
        Fully-framed SendRRData bytes ready to send.
    """
    if connection_size is None:
        connection_size = _DEFAULT_LARGE_CONN_SIZE if large else _DEFAULT_STD_CONN_SIZE

    service = (
        ConnectionManagerService.LARGE_FORWARD_OPEN
        if large
        else ConnectionManagerService.FORWARD_OPEN
    )
    cm_path = request_path(ClassCode.CONNECTION_MANAGER, 0x01)
    fo_data = _build_forward_open_data(
        large=large,
        connection_size=connection_size,
        rpi=rpi,
        to_connection_id=to_connection_id,
        connection_serial=connection_serial,
        originator_vendor_id=originator_vendor_id,
        originator_serial=originator_serial,
        connection_path=connection_path,
    )
    cip_msg = bytes([int(service)]) + cm_path + fo_data
    return build_send_rr_data(session_handle, cip_msg)


def _build_forward_close_data(
    *,
    connection_serial: int,
    originator_vendor_id: int,
    originator_serial: int,
    connection_path: bytes,
) -> bytes:
    """Return the raw FC request data bytes (before CIP wrapping).

    Layout per CIP Vol 1 Table 3-5.28:
      PRIORITY + TIMEOUT_TICKS + UINT(csn) + UINT(vid) + UDINT(vsn)
      + path_size_byte + 0x00 (Reserved) + path_bytes
    """
    path_size_byte = connection_path[:1]
    path_bytes = connection_path[1:]
    return b"".join(
        [
            PRIORITY,
            TIMEOUT_TICKS,
            UINT.encode(connection_serial),
            UINT.encode(originator_vendor_id),
            UDINT.encode(originator_serial),
            path_size_byte,  # Connection_Path_Size (USINT, in 16-bit words)
            b"\x00",  # Reserved per CIP Vol 1 Table 3-5.28
            path_bytes,  # Connection_Path (EPATH)
        ]
    )


def build_forward_close(
    *,
    session_handle: int,
    connection_serial: int,
    originator_vendor_id: int,
    originator_serial: int,
    connection_path: bytes = MSG_ROUTER_PATH,
) -> bytes:
    """Build a Forward_Close SendRRData frame.

    Args:
        session_handle: Current EtherNet/IP session handle.
        connection_serial: Must match the serial used in the Forward_Open.
        originator_vendor_id: Must match the vendor ID used in the Forward_Open.
        originator_serial: Must match the serial used in the Forward_Open.
        connection_path: Same path used in the Forward_Open.

    Returns:
        Fully-framed SendRRData bytes ready to send.
    """
    fc_data = _build_forward_close_data(
        connection_serial=connection_serial,
        originator_vendor_id=originator_vendor_id,
        originator_serial=originator_serial,
        connection_path=connection_path,
    )
    cm_path = request_path(ClassCode.CONNECTION_MANAGER, 0x01)
    cip_msg = bytes([int(ConnectionManagerService.FORWARD_CLOSE)]) + cm_path + fc_data
    return build_send_rr_data(session_handle, cip_msg)


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------


def _extract_unconnected_cip_payload(frame: bytes) -> tuple[int, int, int, bytes]:
    """Decode an encap frame → CPF → UNCONNECTED_DATA item → parse_cip_response.

    Returns (service, general_status, extended_status, payload).
    Raises DataError on malformed frames.
    """
    if len(frame) < 24:
        raise DataError(f"Encapsulation frame too short: {len(frame)} bytes (minimum 24)")
    header = EncapsulationHeader.decode(frame)
    # payload after the encap header: 4-byte interface_handle + 2-byte timeout + CPF
    cpf_offset = 24 + 6
    if len(frame) < cpf_offset:
        raise DataError("SendRRData frame truncated before CPF items")
    items = parse_cpf(frame[cpf_offset:])
    unconnected = next(
        (it for it in items if it.type_code == int(CPFTypeCode.UNCONNECTED_DATA)), None
    )
    if unconnected is None:
        raise DataError(
            f"No UNCONNECTED_DATA item in reply (session=0x{header.session_handle:08x})"
        )
    return parse_cip_response(unconnected.data)


def parse_forward_open_reply(frame: bytes, *, was_large: bool = False) -> ForwardOpenReply:
    """Parse a Forward_Open or Large_Forward_Open reply frame.

    Args:
        frame: Raw bytes received from the device (full SendRRData encap frame).
        was_large: Set to True when the request was a Large_Forward_Open; enables
            the typed fallback exception on status 0x08.

    Returns:
        ForwardOpenReply with device-assigned connection IDs and actual RPIs.

    Raises:
        DataError: Frame is malformed or too short.
        LargeForwardOpenRejected: was_large=True and device returned status 0x08.
        ForwardOpenError: Device returned any other non-zero status.
    """
    _service, general_status, _ext_status, payload = _extract_unconnected_cip_payload(frame)
    if general_status != 0:
        if was_large and general_status in _LARGE_FO_UNSUPPORTED_STATUSES:
            raise LargeForwardOpenRejected(
                f"Large_Forward_Open rejected (CIP status 0x{general_status:02x})"
            )
        raise ForwardOpenError(f"Forward_Open failed (CIP status 0x{general_status:02x})")
    if len(payload) < _FO_REPLY_SIZE:
        raise DataError(
            f"Forward_Open reply payload too short: {len(payload)} bytes "
            f"(expected {_FO_REPLY_SIZE})"
        )
    (
        ot_conn_id,
        to_conn_id,
        serial,
        vendor_id,
        orig_serial,
        ot_api,
        to_api,
        _app_reply_size,
        _reserved,
    ) = struct.unpack_from(_FO_REPLY_STRUCT, payload)
    return ForwardOpenReply(
        ot_connection_id=ot_conn_id,
        to_connection_id=to_conn_id,
        connection_serial=serial,
        originator_vendor_id=vendor_id,
        originator_serial=orig_serial,
        ot_actual_api=ot_api,
        to_actual_api=to_api,
    )


def parse_forward_close_reply(frame: bytes) -> None:
    """Parse a Forward_Close reply frame.

    Raises:
        DataError: Frame is malformed or too short.
        ResponseError: Device returned a non-zero CIP status.
    """
    _service, general_status, _ext_status, _payload = _extract_unconnected_cip_payload(frame)
    if general_status != 0:
        raise ResponseError(f"Forward_Close failed (CIP status 0x{general_status:02x})")
