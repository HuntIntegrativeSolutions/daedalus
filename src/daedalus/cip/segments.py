"""CIP path segments and EPATH encode/decode.

This module fixes pycomm3 Bug #3: all segment decode methods raised
NotImplementedError. Full encode + decode is implemented here.

I/O-FORBIDDEN: this module must never import socket, ssl, asyncio, anyio,
selectors, socketserver, http, urllib, or requests.
"""

from __future__ import annotations

import ipaddress
import reprlib
from collections.abc import Sequence
from io import BytesIO
from typing import ClassVar

from daedalus.cip.data_types import (
    UDINT,
    UINT,
    USINT,
    DataType,
    _as_stream,
    _stream_read,
)
from daedalus.exceptions import BufferEmptyError, DataError

__all__ = [
    "EPATH",
    "PACKED_EPATH",
    "PADDED_EPATH",
    "CIPSegment",
    "DataSegment",
    "LogicalSegment",
    "PortSegment",
]


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------


class CIPSegment:
    """Abstract base for CIP path segments.

    Bit layout of the first segment byte:
        [7:5] segment type  [4:0] segment format / sub-type
    """

    segment_type: ClassVar[int] = 0b_000_00000

    def __init__(self, name: str | None = None) -> None:
        self.name = name

    @classmethod
    def encode(cls, segment: CIPSegment, padded: bool = False) -> bytes:
        try:
            return cls._encode(segment, padded)
        except DataError:
            raise
        except Exception as exc:
            raise DataError(f"Error packing {reprlib.repr(segment)} as {cls.__name__}") from exc

    @classmethod
    def _encode(cls, segment: CIPSegment, padded: bool = False) -> bytes:
        raise NotImplementedError

    @classmethod
    def decode(cls, buffer: bytes | BytesIO, padded: bool = False) -> CIPSegment:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Logical Segment
# ---------------------------------------------------------------------------


class LogicalSegment(CIPSegment):
    """Logical segment encodes a class/instance/attribute/etc. address.

    First byte:  001 | logical_type[2:0] | logical_format[1:0]
    """

    segment_type: ClassVar[int] = 0b_001_00000

    _LOGICAL_TYPES: ClassVar[dict[str, int]] = {
        "class_id": 0b_000_000_00,
        "instance_id": 0b_000_001_00,
        "member_id": 0b_000_010_00,
        "connection_point": 0b_000_011_00,
        "attribute_id": 0b_000_100_00,
        "special": 0b_000_101_00,
        "service_id": 0b_000_110_00,
    }
    _LOGICAL_TYPE_REVERSE: ClassVar[dict[int, str]] = {v: k for k, v in _LOGICAL_TYPES.items()}

    _FORMAT_BY_SIZE: ClassVar[dict[int, int]] = {
        1: 0b00,  # 8-bit
        2: 0b01,  # 16-bit
        4: 0b11,  # 32-bit
    }

    def __init__(
        self,
        logical_value: int | bytes,
        logical_type: str,
        name: str | None = None,
    ) -> None:
        super().__init__(name)
        self.logical_value = logical_value
        self.logical_type = logical_type

    @classmethod
    def _encode(cls, segment: LogicalSegment, padded: bool = False) -> bytes:  # type: ignore[override]
        ltype = cls._LOGICAL_TYPES.get(segment.logical_type)
        if ltype is None:
            raise DataError(f"Invalid logical type: {segment.logical_type!r}")

        value = segment.logical_value
        if isinstance(value, int):
            if value <= 0xFF:
                value = USINT.encode(value)
            elif value <= 0xFFFF:
                value = UINT.encode(value)
            elif value <= 0xFFFF_FFFF:
                value = UDINT.encode(value)
            else:
                raise DataError(f"Logical value out of range: {segment.logical_value!r}")

        fmt = cls._FORMAT_BY_SIZE.get(len(value))
        if fmt is None:
            raise DataError(f"Segment value size {len(value)} not valid for logical segment")

        seg_byte = bytes([cls.segment_type | ltype | fmt])
        # For padded EPATH: a pad byte follows the type byte when value > 8-bit
        if padded and fmt != 0b00:
            seg_byte += b"\x00"
        return seg_byte + value

    @classmethod
    def decode(cls, buffer: bytes | BytesIO, padded: bool = False) -> LogicalSegment:
        try:
            stream = buffer if isinstance(buffer, BytesIO) else BytesIO(buffer)
            seg_byte = _stream_read(stream, 1)[0]
            logical_type_bits = seg_byte & 0b_000_111_00
            fmt = seg_byte & 0b_000_000_11

            if padded and fmt != 0b00:
                _stream_read(stream, 1)  # consume pad byte

            if fmt == 0b00:
                value = USINT.decode(stream)
            elif fmt == 0b01:
                value = UINT.decode(stream)
            elif fmt == 0b11:
                value = UDINT.decode(stream)
            else:
                raise DataError(
                    f"Reserved logical format bits 0b10 in segment byte 0x{seg_byte:02X}"
                )

            logical_type = cls._LOGICAL_TYPE_REVERSE.get(logical_type_bits)
            if logical_type is None:
                raise DataError(f"Unknown logical type bits: 0b{logical_type_bits:08b}")

            return LogicalSegment(value, logical_type)
        except Exception as exc:
            if isinstance(exc, (BufferEmptyError, DataError)):
                raise
            raise DataError("Error decoding LogicalSegment") from exc

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, LogicalSegment):
            return NotImplemented
        return self.logical_value == other.logical_value and self.logical_type == other.logical_type

    def __repr__(self) -> str:
        return (
            f"LogicalSegment(logical_value={self.logical_value!r}, "
            f"logical_type={self.logical_type!r})"
        )


# ---------------------------------------------------------------------------
# Port Segment
# ---------------------------------------------------------------------------


class PortSegment(CIPSegment):
    """Port segment for routing through a backplane or network port.

    First byte:  000 | ext_link | port_id[3:0]

    Bug fix #5: IPv6 addresses are rejected with DataError (pycomm3 silently
    mis-encodes them as ASCII text).
    """

    segment_type: ClassVar[int] = 0b_000_0_0000
    _EXTENDED_LINK_BIT: ClassVar[int] = 0b_000_1_0000
    _EXT_PORT_NIBBLE: ClassVar[int] = 0x0F  # port_id==0x0F means 2-byte extended port follows

    _PORT_NAMES: ClassVar[dict[str, int]] = {
        "backplane": 0x01,
        "bp": 0x01,
        "enet": 0x02,
        "dhrio-a": 0x02,
        "dhrio-b": 0x03,
        "dnet": 0x02,
        "cnet": 0x02,
        "dh485-a": 0x02,
        "dh485-b": 0x03,
    }

    def __init__(
        self,
        port: int | str,
        link_address: int | str | bytes,
        name: str | None = None,
    ) -> None:
        super().__init__(name)
        self.port = port
        self.link_address = link_address

    @classmethod
    def _encode(cls, segment: PortSegment, padded: bool = False) -> bytes:  # type: ignore[override]
        port = cls._PORT_NAMES[segment.port] if isinstance(segment.port, str) else segment.port

        link_address = segment.link_address
        if isinstance(link_address, str):
            if link_address.isnumeric():
                link = USINT.encode(int(link_address))
            else:
                parsed = ipaddress.ip_address(link_address)
                if isinstance(parsed, ipaddress.IPv6Address):
                    raise DataError(
                        f"PortSegment does not support IPv6 addresses: {link_address!r}"
                    )
                link = link_address.encode("ascii")
        elif isinstance(link_address, int):
            link = USINT.encode(link_address)
        else:
            link = link_address

        if len(link) > 1:
            port |= cls._EXTENDED_LINK_BIT
            prefix = USINT.encode(port) + USINT.encode(len(link))
        else:
            prefix = USINT.encode(port)

        segment_bytes = prefix + link
        if len(segment_bytes) % 2:
            segment_bytes += b"\x00"
        return segment_bytes

    @classmethod
    def decode(cls, buffer: bytes | BytesIO, padded: bool = False) -> PortSegment:
        try:
            stream = buffer if isinstance(buffer, BytesIO) else BytesIO(buffer)
            seg_byte = _stream_read(stream, 1)[0]
            port_nibble = seg_byte & 0x0F
            ext_link = bool(seg_byte & cls._EXTENDED_LINK_BIT)

            if port_nibble == cls._EXT_PORT_NIBBLE:
                port_id: int = UINT.decode(stream)
            else:
                port_id = port_nibble

            if ext_link:
                link_len = USINT.decode(stream)
                link_data = _stream_read(stream, link_len)
                try:
                    link_address: int | str = int(link_data.decode("ascii"))
                except ValueError:
                    link_address = link_data.decode("ascii")
                # Pad to word boundary: 1 (seg byte) + 1 (len byte) + link_len
                if (2 + link_len) % 2:
                    _stream_read(stream, 1)
            else:
                link_address = USINT.decode(stream)

            return PortSegment(port=port_id, link_address=link_address)
        except Exception as exc:
            if isinstance(exc, (BufferEmptyError, DataError)):
                raise
            raise DataError("Error decoding PortSegment") from exc

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, PortSegment):
            return NotImplemented
        return self.encode(self) == self.encode(other)

    def __repr__(self) -> str:
        return f"PortSegment(port={self.port!r}, link_address={self.link_address!r})"


# ---------------------------------------------------------------------------
# Data Segment
# ---------------------------------------------------------------------------


class DataSegment(CIPSegment):
    """Data segment carrying an ANSI extended symbol or raw bytes."""

    segment_type: ClassVar[int] = 0b_100_00000
    _EXTENDED_SYMBOL: ClassVar[int] = 0b_000_10001

    def __init__(self, data: str | bytes, name: str | None = None) -> None:
        super().__init__(name)
        self.data = data

    @classmethod
    def _encode(cls, segment: DataSegment, padded: bool = False) -> bytes:  # type: ignore[override]
        if isinstance(segment.data, str):
            sub = cls.segment_type | cls._EXTENDED_SYMBOL
            raw = segment.data.encode("ascii")
            byte_len = len(raw)
            if byte_len % 2:
                raw += b"\x00"
            return USINT.encode(sub) + USINT.encode(byte_len) + raw
        else:
            sub = cls.segment_type
            word_count = len(segment.data) // 2
            return USINT.encode(sub) + USINT.encode(word_count) + segment.data

    @classmethod
    def decode(cls, buffer: bytes | BytesIO, padded: bool = False) -> DataSegment:
        try:
            stream = buffer if isinstance(buffer, BytesIO) else BytesIO(buffer)
            seg_byte = _stream_read(stream, 1)[0]
            subtype = seg_byte & 0x1F

            if subtype == cls._EXTENDED_SYMBOL:
                byte_len = USINT.decode(stream)
                raw = _stream_read(stream, byte_len)
                if byte_len % 2:
                    _stream_read(stream, 1)  # pad
                return DataSegment(raw.decode("ascii"))
            else:
                word_count = USINT.decode(stream)
                data = _stream_read(stream, word_count * 2)
                return DataSegment(data)
        except Exception as exc:
            if isinstance(exc, (BufferEmptyError, DataError)):
                raise
            raise DataError("Error decoding DataSegment") from exc

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, DataSegment):
            return NotImplemented
        return self.data == other.data

    def __repr__(self) -> str:
        return f"DataSegment(data={self.data!r})"


# ---------------------------------------------------------------------------
# EPATH
# ---------------------------------------------------------------------------

_SegmentOrBytes = CIPSegment | bytes


class EPATH(DataType[list[CIPSegment]]):
    """CIP Electronic Path (sequence of segments).

    pycomm3 Bug #3 fix: decode is fully implemented.

    ``padded=True`` means each segment's value is padded to word boundaries.
    PADDED_EPATH is used in standard CIP requests; PACKED_EPATH in some contexts.
    """

    code: ClassVar[int] = 0xDC
    padded: ClassVar[bool] = False

    @classmethod
    def encode(
        cls,
        segments: Sequence[_SegmentOrBytes],
        length: bool = False,
        pad_length: bool = False,
    ) -> bytes:
        """Encode a sequence of segments to bytes.

        Args:
            segments: Sequence of CIPSegment instances or raw bytes.
            length: If True, prepend a USINT word-count prefix.
            pad_length: If True the length prefix is padded to 2 bytes (UINT).
        """
        try:
            path = b"".join(
                seg if isinstance(seg, bytes) else seg.__class__.encode(seg, padded=cls.padded)
                for seg in segments
            )
            if length:
                word_count = len(path) // 2
                prefix = (
                    (USINT.encode(word_count) + b"\x00") if pad_length else USINT.encode(word_count)
                )
                return prefix + path
            return path
        except DataError:
            raise
        except Exception as exc:
            raise DataError("Error encoding EPATH") from exc

    @classmethod
    def decode(cls, buffer: bytes | BytesIO) -> list[CIPSegment]:
        """Decode a raw (no length-prefix) byte buffer into a list of segments."""
        # Work on a single BytesIO so segment decoders advance it in place.
        # Each decoder receives the stream positioned AT the first byte of the segment.
        data = bytes(buffer) if isinstance(buffer, (bytes, bytearray)) else buffer.read()

        stream = BytesIO(data)
        segments: list[CIPSegment] = []
        try:
            while True:
                peek = stream.read(1)
                if not peek:
                    break
                first_byte = peek[0]
                seg_type = (first_byte & 0xE0) >> 5

                # Rewind one byte so the segment decoder sees the type byte
                stream.seek(stream.tell() - 1)

                seg: CIPSegment
                if seg_type == 0b000:  # Port segment
                    seg = PortSegment.decode(stream, padded=cls.padded)
                elif seg_type == 0b001:  # Logical segment
                    seg = LogicalSegment.decode(stream, padded=cls.padded)
                elif seg_type == 0b100:  # Data segment
                    seg = DataSegment.decode(stream, padded=cls.padded)
                else:
                    raise DataError(
                        f"Unsupported segment type bits 0b{seg_type:03b} in byte 0x{first_byte:02X}"
                    )

                segments.append(seg)
        except Exception as exc:
            if isinstance(exc, (BufferEmptyError, DataError)):
                raise
            raise DataError("Error decoding EPATH") from exc
        return segments

    @classmethod
    def decode_with_length_prefix(cls, buffer: bytes | BytesIO) -> list[CIPSegment]:
        """Decode a length-prefixed EPATH (USINT word-count followed by segments)."""
        stream = _as_stream(buffer)
        word_count = USINT.decode(stream)
        data = _stream_read(stream, word_count * 2)
        return cls.decode(data)


class PADDED_EPATH(EPATH):
    """EPATH with padded logical segments (standard CIP request paths)."""

    padded: ClassVar[bool] = True


class PACKED_EPATH(EPATH):
    """EPATH with packed (unpadded) logical segments."""

    padded: ClassVar[bool] = False
