"""CIP service and encapsulation command codes.

I/O-FORBIDDEN: this module must never import socket, ssl, asyncio, anyio,
selectors, socketserver, http, urllib, or requests.
"""

from enum import IntEnum
from typing import Final

__all__ = [
    "MULTI_PACKET_SERVICES",
    "CIPService",
    "ConnectionManagerService",
    "EncapsulationCommand",
    "FileObjectService",
]


class EncapsulationCommand(IntEnum):
    """EtherNet/IP encapsulation command codes (header.command field)."""

    NOP = 0x00
    LIST_TARGETS = 0x01
    LIST_SERVICES = 0x04
    LIST_IDENTITY = 0x63
    LIST_INTERFACES = 0x64
    REGISTER_SESSION = 0x65
    UNREGISTER_SESSION = 0x66
    SEND_RR_DATA = 0x6F
    SEND_UNIT_DATA = 0x70


class CIPService(IntEnum):
    """CIP service codes (low 7 bits of the service byte)."""

    # Common CIP services
    GET_ATTRIBUTES_ALL = 0x01
    SET_ATTRIBUTES_ALL = 0x02
    GET_ATTRIBUTE_LIST = 0x03
    SET_ATTRIBUTE_LIST = 0x04
    RESET = 0x05
    START = 0x06
    STOP = 0x07
    CREATE = 0x08
    DELETE = 0x09
    MULTIPLE_SERVICE_REQUEST = 0x0A
    APPLY_ATTRIBUTES = 0x0D
    GET_ATTRIBUTE_SINGLE = 0x0E
    SET_ATTRIBUTE_SINGLE = 0x10
    FIND_NEXT_OBJECT_INSTANCE = 0x11
    ERROR_RESPONSE = 0x14
    RESTORE = 0x15
    SAVE = 0x16
    NOP = 0x17
    GET_MEMBER = 0x18
    SET_MEMBER = 0x19
    INSERT_MEMBER = 0x1A
    REMOVE_MEMBER = 0x1B
    GROUP_SYNC = 0x1C

    # Logix-specific services
    READ_TAG = 0x4C
    WRITE_TAG = 0x4D
    READ_MODIFY_WRITE = 0x4E
    READ_TAG_FRAGMENTED = 0x52
    WRITE_TAG_FRAGMENTED = 0x53
    GET_INSTANCE_ATTRIBUTE_LIST = 0x55

    @classmethod
    def from_reply(cls, byte: int) -> "CIPService":
        """Extract service from a reply byte (strips the reply bit 0x80)."""
        return cls(byte & 0x7F)


class ConnectionManagerService(IntEnum):
    """Connection Manager Object (class 0x06) service codes."""

    FORWARD_CLOSE = 0x4E
    UNCONNECTED_SEND = 0x52
    FORWARD_OPEN = 0x54
    GET_CONNECTION_DATA = 0x56
    SEARCH_CONNECTION_DATA = 0x57
    GET_CONNECTION_OWNER = 0x5A
    LARGE_FORWARD_OPEN = 0x5B


class FileObjectService(IntEnum):
    """File Object service codes."""

    INITIATE_UPLOAD = 0x4B
    INITIATE_DOWNLOAD = 0x4C
    INITIATE_PARTIAL_READ = 0x4D
    INITIATE_PARTIAL_WRITE = 0x4E
    UPLOAD_TRANSFER = 0x4F
    DOWNLOAD_TRANSFER = 0x50
    CLEAR_FILE = 0x51


MULTI_PACKET_SERVICES: Final[frozenset[CIPService]] = frozenset(
    {
        CIPService.READ_TAG_FRAGMENTED,
        CIPService.WRITE_TAG_FRAGMENTED,
        CIPService.GET_INSTANCE_ATTRIBUTE_LIST,
        CIPService.MULTIPLE_SERVICE_REQUEST,
        CIPService.GET_ATTRIBUTE_LIST,
    }
)
