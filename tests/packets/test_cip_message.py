"""Tests for daedalus.packets.cip builders."""

from daedalus.cip.object_library import ClassCode
from daedalus.cip.services import CIPService
from daedalus.packets.cip import (
    MSG_ROUTER_PATH,
    build_cip_request,
    build_list_identity,
    build_register_session,
    build_send_rr_data,
    build_send_unit_data,
    build_unregister_session,
    parse_cip_response,
    request_path,
    wrap_unconnected_send,
)
from daedalus.packets.encap import EncapsulationHeader


def test_build_register_session_length() -> None:
    pkt = build_register_session()
    assert len(pkt) == 24 + 4  # 24-byte header + protocol_version + options


def test_build_register_session_header() -> None:
    pkt = build_register_session()
    hdr = EncapsulationHeader.decode(pkt[:24])
    assert hdr.command == 0x65  # REGISTER_SESSION
    assert hdr.session_handle == 0
    assert hdr.length == 4


def test_build_unregister_session() -> None:
    pkt = build_unregister_session(0x12345678)
    assert len(pkt) == 24
    hdr = EncapsulationHeader.decode(pkt)
    assert hdr.command == 0x66  # UNREGISTER_SESSION
    assert hdr.session_handle == 0x12345678
    assert hdr.length == 0


def test_build_list_identity() -> None:
    pkt = build_list_identity()
    assert len(pkt) == 24
    hdr = EncapsulationHeader.decode(pkt)
    assert hdr.command == 0x63  # LIST_IDENTITY


def test_request_path_msg_router() -> None:
    path = request_path(ClassCode.MESSAGE_ROUTER, 0x01)
    # 2 segments of 2 bytes each → 4 bytes → word_count=2
    assert path[0] == 2
    assert path[1:] == b"\x20\x02\x24\x01"


def test_request_path_symbol_object() -> None:
    path = request_path(ClassCode.SYMBOL_OBJECT, 0x01)
    # class=0x6B, instance=0x01
    assert path[0] == 2  # word count
    assert path[1:] == b"\x20\x6b\x24\x01"


def test_msg_router_path_constant() -> None:
    expected = request_path(ClassCode.MESSAGE_ROUTER, 0x01)
    assert expected == MSG_ROUTER_PATH


def test_build_cip_request() -> None:
    path = request_path(ClassCode.MESSAGE_ROUTER, 0x01)
    data = b"\xaa\xbb"
    req = build_cip_request(CIPService.READ_TAG, path, data)
    assert req[0] == int(CIPService.READ_TAG)
    assert req[1:] == path + data


def test_parse_cip_response_success() -> None:
    # Build a minimal success CIP reply: service|0x80, reserved, status=0, ext_count=0, payload
    payload = b"\x01\x02\x03"
    response = bytes([int(CIPService.READ_TAG) | 0x80, 0x00, 0x00, 0x00]) + payload
    service, general, extended, data = parse_cip_response(response)
    assert service == int(CIPService.READ_TAG)
    assert general == 0
    assert extended == 0
    assert data == payload


def test_build_send_rr_data_structure() -> None:
    session = 0x0001ABCD
    message = b"\x4c\x02\x20\x02\x24\x01"  # typical CIP read
    pkt = build_send_rr_data(session, message)
    hdr = EncapsulationHeader.decode(pkt[:24])
    assert hdr.command == 0x6F  # SEND_RR_DATA
    assert hdr.session_handle == session
    assert hdr.length == len(pkt) - 24


def test_build_send_unit_data_structure() -> None:
    session = 0x00ABCDEF
    conn_id = 0x12345678
    seq = 1
    message = b"\x01\x02\x03"
    pkt = build_send_unit_data(session, conn_id, seq, message)
    hdr = EncapsulationHeader.decode(pkt[:24])
    assert hdr.command == 0x70  # SEND_UNIT_DATA
    assert hdr.session_handle == session


def test_wrap_unconnected_send() -> None:
    message = b"\x4c\x02\x20\x02\x24\x01"
    route = b"\x01\x00\x01\x00"  # simple route
    wrapped = wrap_unconnected_send(message, route)
    # Should start with unconnected_send service code
    from daedalus.cip.services import ConnectionManagerService

    assert wrapped[0] == int(ConnectionManagerService.UNCONNECTED_SEND)
