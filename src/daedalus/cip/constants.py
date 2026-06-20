"""CIP and EtherNet/IP wire constants.

I/O-FORBIDDEN: this module must never import socket, ssl, asyncio, anyio,
selectors, socketserver, http, urllib, or requests.
"""

from typing import Final

__all__ = [
    "EXTENDED_SYMBOL",
    "HEADER_SIZE",
    "INSUFFICIENT_PACKETS",
    "PRIORITY",
    "STRUCTURE_READ_REPLY",
    "SUCCESS",
    "TIMEOUT_MULTIPLIER",
    "TIMEOUT_TICKS",
    "TRANSPORT_CLASS",
]

HEADER_SIZE: Final[int] = 24

# CIP connection parameters (raw bytes used in Forward Open requests)
PRIORITY: Final[bytes] = b"\x0a"
TIMEOUT_TICKS: Final[bytes] = b"\x05"
TIMEOUT_MULTIPLIER: Final[bytes] = b"\x07"
TRANSPORT_CLASS: Final[bytes] = b"\xa3"

# Logix tag read response markers
STRUCTURE_READ_REPLY: Final[bytes] = b"\xa0\x02"
EXTENDED_SYMBOL: Final[bytes] = b"\x91"

# Common CIP status codes
SUCCESS: Final[int] = 0x00
INSUFFICIENT_PACKETS: Final[int] = 0x06
