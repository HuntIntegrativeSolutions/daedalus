"""CIP message builders and parsers.

Pure builder functions — no state, no I/O.

I/O-FORBIDDEN: this module must never import socket, ssl, asyncio, anyio,
selectors, socketserver, http, urllib, or requests.
"""

from __future__ import annotations

from io import BytesIO
from typing import Final

from daedalus.cip.constants import (
    PRIORITY,
    TIMEOUT_TICKS,
)
from daedalus.cip.data_types import UDINT, UINT, USINT
from daedalus.cip.object_library import ClassCode
from daedalus.cip.segments import PADDED_EPATH, DataSegment, LogicalSegment
from daedalus.cip.services import (
    CIPService,
    ConnectionManagerService,
    EncapsulationCommand,
)
from daedalus.cip.status import EXTEND_CODES, SERVICE_STATUS
from daedalus.exceptions import DataError
from daedalus.packets.encap import (
    CPFItem,
    CPFTypeCode,
    EncapsulationHeader,
    build_cpf,
)

__all__ = [
    "MSG_ROUTER_PATH",
    "build_cip_request",
    "build_list_identity",
    "build_register_session",
    "build_send_rr_data",
    "build_send_unit_data",
    "build_unregister_session",
    "get_extended_status",
    "get_service_status",
    "parse_cip_response",
    "request_path",
    "tag_request_path",
    "wrap_unconnected_send",
]

_DEFAULT_SENDER_CONTEXT: Final[bytes] = b"pylogix\x00"
_REGISTER_SESSION_VERSION: Final[bytes] = b"\x01\x00"  # EIP protocol version 1
_REGISTER_SESSION_OPTIONS: Final[bytes] = b"\x00\x00"
_INTERFACE_HANDLE: Final[bytes] = b"\x00\x00\x00\x00"  # must be 0 for CIP
_TIMEOUT_10: Final[bytes] = b"\x0a\x00"  # 10 seconds, little-endian UINT


def request_path(
    class_code: int | bytes,
    instance: int | bytes,
    attribute: int | bytes | None = None,
) -> bytes:
    """Build a padded EPATH with word-count prefix for class/instance/[attribute].

    Returns bytes ready to use as the message request path.
    """

    def _to_int(v: int | bytes) -> int:
        return v if isinstance(v, int) else int.from_bytes(v, "little")

    segments = [
        LogicalSegment(_to_int(class_code), "class_id"),
        LogicalSegment(_to_int(instance), "instance_id"),
    ]
    if attribute is not None:
        segments.append(
            LogicalSegment(
                attribute if isinstance(attribute, int) else int.from_bytes(attribute, "little"),
                "attribute_id",
            )
        )
    return PADDED_EPATH.encode(segments, length=True)


def tag_request_path(tag_name: str) -> bytes:
    """Build a padded EPATH for a Logix tag name (ANSI extended symbol segment).

    For dotted paths (e.g. ``Program:Main.Tag.SubField``) the path is split
    on ``'.'`` and each element becomes a DataSegment.
    """
    parts = tag_name.split(".")
    segments: list[LogicalSegment | DataSegment] = []
    for part in parts:
        # Strip array index from the segment name; indices become member_id segments
        base, indices = _split_tag_index(part)
        segments.append(DataSegment(base))
        segments.extend(LogicalSegment(int(idx), "member_id") for idx in indices)
    return PADDED_EPATH.encode(segments, length=True)


def _split_tag_index(tag: str) -> tuple[str, list[str]]:
    if "[" not in tag:
        return tag, []
    name = tag[: tag.index("[")]
    inside = tag[tag.index("[") + 1 : -1]  # strip brackets
    return name, inside.split(",")


# ---------------------------------------------------------------------------
# Encapsulation-level builders
# ---------------------------------------------------------------------------


def build_register_session() -> bytes:
    """Build a RegisterSession encapsulation packet (session_handle=0)."""
    data = _REGISTER_SESSION_VERSION + _REGISTER_SESSION_OPTIONS
    header = EncapsulationHeader.for_command(
        EncapsulationCommand.REGISTER_SESSION, data_length=len(data)
    )
    return header.encode() + data


def build_unregister_session(session_handle: int) -> bytes:
    """Build an UnRegisterSession encapsulation packet."""
    header = EncapsulationHeader.for_command(
        EncapsulationCommand.UNREGISTER_SESSION,
        data_length=0,
        session_handle=session_handle,
    )
    return header.encode()


def build_list_identity() -> bytes:
    """Build a ListIdentity broadcast packet (no session required)."""
    header = EncapsulationHeader.for_command(EncapsulationCommand.LIST_IDENTITY, data_length=0)
    return header.encode()


# ---------------------------------------------------------------------------
# CIP service message builders
# ---------------------------------------------------------------------------


def build_cip_request(
    service: CIPService | int,
    path: bytes,
    data: bytes = b"",
) -> bytes:
    """Build a raw CIP service request (service byte + path + data)."""
    return bytes([int(service)]) + path + data


def parse_cip_response(data: bytes) -> tuple[int, int, int, bytes]:
    """Parse a raw CIP service reply.

    Returns:
        (service_code, general_status, extended_status, payload)

    Raises ResponseError on non-SUCCESS status (caller should still inspect
    INSUFFICIENT_PACKETS for fragmented reads).
    """
    if len(data) < 4:
        raise DataError(f"CIP response too short: {len(data)} bytes")
    service = data[0] & 0x7F  # strip reply bit
    # reserved = data[1]
    general_status = data[2]
    ext_status_words = data[3]
    payload_offset = 4 + ext_status_words * 2
    extended_status = 0
    if ext_status_words:
        ext_bytes = data[4 : 4 + ext_status_words * 2]
        extended_status = int.from_bytes(ext_bytes[:2], "little")
    payload = data[payload_offset:]
    return service, general_status, extended_status, payload


def build_send_rr_data(
    session_handle: int,
    message: bytes,
    timeout: int = 10,
    sender_context: bytes = _DEFAULT_SENDER_CONTEXT,
) -> bytes:
    """Build a SendRRData packet (unconnected CIP request)."""
    cpf = (
        _INTERFACE_HANDLE
        + UINT.encode(timeout)
        + build_cpf(
            [
                CPFItem(CPFTypeCode.NULL_ADDRESS),
                CPFItem(CPFTypeCode.UNCONNECTED_DATA, message),
            ]
        )
    )
    header = EncapsulationHeader.for_command(
        EncapsulationCommand.SEND_RR_DATA,
        data_length=len(cpf),
        session_handle=session_handle,
        sender_context=sender_context[:8].ljust(8, b"\x00"),
    )
    return header.encode() + cpf


def build_send_unit_data(
    session_handle: int,
    connection_id: int,
    sequence_count: int,
    message: bytes,
    timeout: int = 10,
    sender_context: bytes = _DEFAULT_SENDER_CONTEXT,
) -> bytes:
    """Build a SendUnitData packet (connected CIP request).

    The connection sequence count is the first UINT of the Connected Data (0xB1)
    item, NOT inside the Connected Address (0xA1) item.
    """
    connected_data = UINT.encode(sequence_count) + message
    cpf = (
        _INTERFACE_HANDLE
        + UINT.encode(timeout)
        + build_cpf(
            [
                CPFItem(CPFTypeCode.CONNECTED_ADDRESS, UDINT.encode(connection_id)),
                CPFItem(CPFTypeCode.CONNECTED_DATA, connected_data),
            ]
        )
    )
    header = EncapsulationHeader.for_command(
        EncapsulationCommand.SEND_UNIT_DATA,
        data_length=len(cpf),
        session_handle=session_handle,
        sender_context=sender_context[:8].ljust(8, b"\x00"),
    )
    return header.encode() + cpf


def wrap_unconnected_send(message: bytes, route_path: bytes) -> bytes:
    """Wrap a CIP message in an UnconnectedSend (for routing through backplane)."""
    rp = request_path(ClassCode.CONNECTION_MANAGER, 0x01)
    msg_len = len(message)
    pad = b"\x00" if msg_len % 2 else b""
    return b"".join(
        [
            USINT.encode(int(ConnectionManagerService.UNCONNECTED_SEND)),
            rp,
            PRIORITY,
            TIMEOUT_TICKS,
            UINT.encode(msg_len),
            message,
            pad,
            route_path,
        ]
    )


# ---------------------------------------------------------------------------
# Status helpers
# ---------------------------------------------------------------------------


def get_service_status(status: int) -> str:
    """Human-readable general status description."""
    return SERVICE_STATUS.get(status, f"Unknown Error (0x{status:02x})")


def get_extended_status(data: bytes, start: int) -> str | None:
    """Parse extended status from a CIP reply payload."""
    stream = BytesIO(data[start:])
    try:
        status = USINT.decode(stream)
        ext_size_words = USINT.decode(stream)
        ext_status = 0
        if ext_size_words:
            ext_bytes = ext_size_words * 2
            if ext_bytes == 1:
                ext_status = USINT.decode(stream)
            elif ext_bytes == 2:
                ext_status = UINT.decode(stream)
            elif ext_bytes == 4:
                ext_status = UDINT.decode(stream)
            else:
                return "[ERROR] Extended Status Size Unknown"
        codes = EXTEND_CODES.get(status, {})
        if ext_status in codes:
            return f"{codes[ext_status]}  ({status:02x}, {ext_status:04x})"
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Pre-built paths
# ---------------------------------------------------------------------------

MSG_ROUTER_PATH: Final[bytes] = request_path(ClassCode.MESSAGE_ROUTER, 0x01)
