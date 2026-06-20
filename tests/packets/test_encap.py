"""Tests for daedalus.packets.encap."""

import struct

from daedalus.cip.services import EncapsulationCommand
from daedalus.packets.encap import (
    CPFItem,
    CPFTypeCode,
    EncapsulationHeader,
    build_cpf,
    parse_cpf,
)


def test_encapsulation_header_encode_decode_round_trip() -> None:
    hdr = EncapsulationHeader(
        command=0x65,
        length=4,
        session_handle=0,
        status=0,
        sender_context=b"\x00" * 8,
        options=0,
    )
    encoded = hdr.encode()
    assert len(encoded) == 24
    decoded = EncapsulationHeader.decode(encoded)
    assert decoded.command == hdr.command
    assert decoded.length == hdr.length
    assert decoded.session_handle == hdr.session_handle
    assert decoded.status == hdr.status
    assert decoded.options == hdr.options


def test_encapsulation_header_size_is_24() -> None:
    hdr = EncapsulationHeader.for_command(EncapsulationCommand.REGISTER_SESSION, 4)
    assert len(hdr.encode()) == 24


def test_encapsulation_header_for_command() -> None:
    hdr = EncapsulationHeader.for_command(
        EncapsulationCommand.SEND_RR_DATA,
        data_length=20,
        session_handle=0xABCD1234,
    )
    encoded = hdr.encode()
    decoded = EncapsulationHeader.decode(encoded)
    assert decoded.command == 0x6F
    assert decoded.length == 20
    assert decoded.session_handle == 0xABCD1234


def test_encapsulation_header_register_session_wire() -> None:
    # Fixed known vector: RegisterSession, length=4, all zeros
    hdr = EncapsulationHeader.for_command(EncapsulationCommand.REGISTER_SESSION, 4)
    encoded = hdr.encode()
    expected = struct.pack("<HHII8sI", 0x65, 4, 0, 0, b"\x00" * 8, 0)
    assert encoded == expected


def test_cpf_item_null_address_encode() -> None:
    item = CPFItem(CPFTypeCode.NULL_ADDRESS)
    encoded = item.encode()
    assert encoded == b"\x00\x00\x00\x00"


def test_cpf_item_unconnected_data_encode() -> None:
    payload = b"\x01\x02\x03"
    item = CPFItem(CPFTypeCode.UNCONNECTED_DATA, payload)
    encoded = item.encode()
    # type=0xB200, length=3, data
    assert encoded[:2] == b"\xb2\x00"
    assert encoded[2:4] == b"\x03\x00"
    assert encoded[4:] == payload


def test_cpf_item_decode_round_trip() -> None:
    item = CPFItem(CPFTypeCode.CONNECTED_DATA, b"\xab\xcd")
    decoded = CPFItem.decode(item.encode())
    assert decoded.type_code == item.type_code
    assert decoded.data == item.data


def test_build_cpf_two_items() -> None:
    items = [
        CPFItem(CPFTypeCode.NULL_ADDRESS),
        CPFItem(CPFTypeCode.UNCONNECTED_DATA, b"\x01\x02"),
    ]
    data = build_cpf(items)
    # item count = 2
    assert data[:2] == b"\x02\x00"


def test_parse_cpf_round_trip() -> None:
    items = [
        CPFItem(CPFTypeCode.NULL_ADDRESS),
        CPFItem(CPFTypeCode.UNCONNECTED_DATA, b"\xaa\xbb"),
    ]
    parsed = parse_cpf(build_cpf(items))
    assert len(parsed) == 2
    assert parsed[0].type_code == CPFTypeCode.NULL_ADDRESS
    assert parsed[1].data == b"\xaa\xbb"
