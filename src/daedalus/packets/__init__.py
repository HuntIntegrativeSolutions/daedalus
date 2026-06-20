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

__all__ = [
    "MSG_ROUTER_PATH",
    "CPFItem",
    "CPFTypeCode",
    # encap
    "EncapsulationHeader",
    "build_cip_request",
    "build_cpf",
    "build_list_identity",
    "build_register_session",
    "build_send_rr_data",
    "build_send_unit_data",
    "build_unregister_session",
    "get_extended_status",
    "get_service_status",
    "parse_cip_response",
    "parse_cpf",
    # cip message builders
    "request_path",
    "tag_request_path",
    "wrap_unconnected_send",
]
