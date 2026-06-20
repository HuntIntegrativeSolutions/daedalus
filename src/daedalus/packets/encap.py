"""EtherNet/IP encapsulation layer — header and Common Packet Format (CPF).

I/O-FORBIDDEN: this module must never import socket, ssl, asyncio, anyio,
selectors, socketserver, http, urllib, or requests.
"""

from __future__ import annotations

import struct
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import IntEnum
from io import BytesIO
from struct import pack, unpack
from typing import Final

from daedalus.cip.data_types import UINT, _as_stream, _stream_read
from daedalus.cip.services import EncapsulationCommand

__all__ = [
    "CPFItem",
    "CPFTypeCode",
    "EncapsulationHeader",
    "build_cpf",
    "parse_cpf",
]

# EtherNet/IP encapsulation header is always 24 bytes
# command, length, session_handle, status, sender_context, options
_HEADER_FORMAT: Final[str] = "<HHII8sI"
_HEADER_SIZE: Final[int] = 24
assert struct.calcsize(_HEADER_FORMAT) == _HEADER_SIZE


@dataclass
class EncapsulationHeader:
    """24-byte EtherNet/IP encapsulation header."""

    command: int
    length: int
    session_handle: int
    status: int
    sender_context: bytes
    options: int

    def encode(self) -> bytes:
        ctx = self.sender_context[:8].ljust(8, b"\x00")
        return pack(
            _HEADER_FORMAT,
            self.command,
            self.length,
            self.session_handle,
            self.status,
            ctx,
            self.options,
        )

    @classmethod
    def decode(cls, buffer: bytes | BytesIO) -> EncapsulationHeader:
        stream = _as_stream(buffer)
        data = _stream_read(stream, _HEADER_SIZE)
        command, length, session_handle, status, sender_context, options = unpack(
            _HEADER_FORMAT, data
        )
        return cls(
            command=command,
            length=length,
            session_handle=session_handle,
            status=status,
            sender_context=sender_context,
            options=options,
        )

    @classmethod
    def for_command(
        cls,
        command: EncapsulationCommand | int,
        data_length: int,
        session_handle: int = 0,
        status: int = 0,
        sender_context: bytes = b"\x00" * 8,
        options: int = 0,
    ) -> EncapsulationHeader:
        return cls(
            command=int(command),
            length=data_length,
            session_handle=session_handle,
            status=status,
            sender_context=sender_context,
            options=options,
        )


class CPFTypeCode(IntEnum):
    """Common Packet Format item type codes."""

    NULL_ADDRESS = 0x0000
    CONNECTED_ADDRESS = 0x00A1
    CONNECTED_DATA = 0x00B1
    UNCONNECTED_DATA = 0x00B2
    SOCKADDR_INFO_O_TO_T = 0x8000
    SOCKADDR_INFO_T_TO_O = 0x8001
    SEQUENCED_ADDRESS = 0x8002


@dataclass
class CPFItem:
    """One item in a Common Packet Format payload."""

    type_code: int
    data: bytes = field(default=b"")

    def encode(self) -> bytes:
        return UINT.encode(self.type_code) + UINT.encode(len(self.data)) + self.data

    @classmethod
    def decode(cls, buffer: bytes | BytesIO) -> CPFItem:
        stream = buffer if isinstance(buffer, BytesIO) else BytesIO(buffer)
        type_code = UINT.decode(stream)
        length = UINT.decode(stream)
        data = _stream_read(stream, length) if length else b""
        return cls(type_code=type_code, data=data)


def build_cpf(items: Sequence[CPFItem]) -> bytes:
    """Encode a CPF payload (item count + items)."""
    return UINT.encode(len(items)) + b"".join(item.encode() for item in items)


def parse_cpf(buffer: bytes | BytesIO) -> list[CPFItem]:
    """Decode a CPF payload from a buffer."""
    stream = buffer if isinstance(buffer, BytesIO) else BytesIO(buffer)
    count = UINT.decode(stream)
    items: list[CPFItem] = []
    for _ in range(count):
        items.append(CPFItem.decode(stream))
    return items
