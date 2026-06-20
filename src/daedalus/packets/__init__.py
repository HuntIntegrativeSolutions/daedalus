"""EtherNet/IP packet framing — L0 layer (I/O-FORBIDDEN).

This package provides the pure byte-level framing for EtherNet/IP encapsulation
and CIP message building. No layer in this package may import socket, ssl,
asyncio, anyio, selectors, socketserver, http, urllib, or requests.
"""

from daedalus.packets.cip import (
    MSG_ROUTER_PATH,
    build_cip_request,
    build_list_identity,
    build_register_session,
    build_send_rr_data,
    build_send_unit_data,
    build_unregister_session,
    get_extended_status,
    get_service_status,
    parse_cip_response,
    request_path,
    tag_request_path,
    wrap_unconnected_send,
)
from daedalus.packets.encap import (
    CPFItem,
    CPFTypeCode,
    EncapsulationHeader,
    build_cpf,
    parse_cpf,
)
from daedalus.packets.forward_open import (
    ForwardOpenReply,
    build_forward_close,
    build_forward_open,
    parse_forward_close_reply,
    parse_forward_open_reply,
)

__all__ = [
    "MSG_ROUTER_PATH",
    "CPFItem",
    "CPFTypeCode",
    "EncapsulationHeader",
    "ForwardOpenReply",
    "build_cip_request",
    "build_cpf",
    "build_forward_close",
    "build_forward_open",
    "build_list_identity",
    "build_register_session",
    "build_send_rr_data",
    "build_send_unit_data",
    "build_unregister_session",
    "get_extended_status",
    "get_service_status",
    "parse_cip_response",
    "parse_cpf",
    "parse_forward_close_reply",
    "parse_forward_open_reply",
    "request_path",
    "tag_request_path",
    "wrap_unconnected_send",
]
