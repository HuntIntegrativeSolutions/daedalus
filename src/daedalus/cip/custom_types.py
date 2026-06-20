"""CIP custom composite types (IP address, revision, identity objects).

I/O-FORBIDDEN: this module must never import socket, ssl, asyncio, anyio,
selectors, socketserver, http, urllib, or requests.
"""

from __future__ import annotations

import ipaddress
from io import BytesIO
from typing import Any, cast

from daedalus.cip.data_types import (
    DWORD,
    INT,
    SHORT_STRING,
    UDINT,
    UINT,
    ULINT,
    USINT,
    DataType,
    DerivedDataType,
    StringDataType,
    Struct,
    _stream_read,
)
from daedalus.cip.status import PRODUCT_TYPES, VENDORS

__all__ = [
    "FixedSizeString",
    "IPAddress",
    "ListIdentityObject",
    "ModuleIdentityObject",
    "Revision",
]


class IPAddress(DerivedDataType[str]):
    """IPv4 address: encoded as 4-byte packed big-endian, decoded as dotted-decimal string."""

    @classmethod
    def _encode(cls, value: str) -> bytes:
        return ipaddress.IPv4Address(value).packed

    @classmethod
    def _decode(cls, stream: BytesIO) -> str:
        data = _stream_read(stream, 4)
        return ipaddress.IPv4Address(data).exploded


def FixedSizeString(size_: int, len_type_: type[DataType[int]] = UDINT) -> type[StringDataType]:
    """Create a fixed-width string type (Logix UDT string members)."""

    class _FixedSizeString(StringDataType):
        size = size_
        len_type = len_type_  # type: ignore[assignment]
        encoding = "iso-8859-1"

        @classmethod
        def _encode(cls, value: str) -> bytes:
            encoded = value.encode(cls.encoding)
            return cls.len_type.encode(len(encoded)) + encoded + b"\x00" * (cls.size - len(encoded))

        @classmethod
        def _decode(cls, stream: BytesIO) -> str:
            _len = cls.len_type.decode(stream)
            data = _stream_read(stream, cls.size)[:_len]
            return data.decode(cls.encoding)

    _FixedSizeString.__name__ = f"FixedSizeString({size_})"
    _FixedSizeString.__qualname__ = _FixedSizeString.__name__
    return _FixedSizeString


Revision = Struct(USINT("major"), USINT("minor"))


class ModuleIdentityObject(
    Struct(  # type: ignore[misc]
        UINT("vendor"),
        UINT("product_type"),
        UINT("product_code"),
        Struct(USINT("major"), USINT("minor"))("revision"),
        DWORD("status"),
        UDINT("serial"),
        SHORT_STRING("product_name"),
    )
):
    """Decoded identity object (from GetAttributesAll response)."""

    @classmethod
    def _decode(cls, stream: BytesIO) -> dict[str, Any]:
        values = cast(dict[str, Any], super()._decode(stream))
        values["product_type"] = PRODUCT_TYPES.get(values["product_type"], "UNKNOWN")
        values["vendor"] = VENDORS.get(values["vendor"], "UNKNOWN")
        values["serial"] = f"{values['serial']:08x}"
        return values

    @classmethod
    def _encode(cls, values: dict[str, Any]) -> bytes:
        v = dict(values)
        v["product_type"] = PRODUCT_TYPES[v["product_type"]]
        v["vendor"] = VENDORS[v["vendor"]]
        v["serial"] = int.from_bytes(bytes.fromhex(v["serial"]), "big")
        return cast(bytes, super()._encode(v))


class ListIdentityObject(
    Struct(  # type: ignore[misc]
        UINT("item_type"),
        UINT("item_length"),
        UINT("encap_protocol_version"),
        INT("sin_family"),
        UINT("sin_port"),
        IPAddress("ip_address"),
        ULINT("sin_zero"),
        UINT("vendor"),
        UINT("product_type"),
        UINT("product_code"),
        Struct(USINT("major"), USINT("minor"))("revision"),
        DWORD("status"),
        UDINT("serial"),
        SHORT_STRING("product_name"),
        USINT("state"),
    )
):
    """Parsed reply from a ListIdentity broadcast."""

    @classmethod
    def _decode(cls, stream: BytesIO) -> dict[str, Any]:
        values = cast(dict[str, Any], super()._decode(stream))
        values["product_type"] = PRODUCT_TYPES.get(values["product_type"], "UNKNOWN")
        values["vendor"] = VENDORS.get(values["vendor"], "UNKNOWN")
        values["serial"] = f"{values['serial']:08x}"
        return values
