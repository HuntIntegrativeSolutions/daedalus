"""Tests for daedalus.cip.segments — encode/decode round-trips."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from daedalus.cip.segments import (
    PACKED_EPATH,
    PADDED_EPATH,
    DataSegment,
    LogicalSegment,
    PortSegment,
)
from daedalus.exceptions import DataError
from strategies import (
    logical_types,
    logical_values_8bit,
    logical_values_16bit,
    logical_values_32bit,
)

# ---------------------------------------------------------------------------
# LogicalSegment
# ---------------------------------------------------------------------------


def test_logical_segment_encode_8bit_class_id() -> None:
    seg = LogicalSegment(0x01, "class_id")
    assert LogicalSegment.encode(seg, padded=False) == b"\x20\x01"


def test_logical_segment_encode_8bit_instance_id() -> None:
    seg = LogicalSegment(0x01, "instance_id")
    assert LogicalSegment.encode(seg, padded=False) == b"\x24\x01"


def test_logical_segment_encode_symbol_class_0x6b() -> None:
    seg = LogicalSegment(0x6B, "class_id")
    encoded = LogicalSegment.encode(seg, padded=False)
    assert encoded == b"\x20\x6b"


def test_logical_segment_encode_16bit_padded() -> None:
    seg = LogicalSegment(0x0100, "instance_id")
    encoded = LogicalSegment.encode(seg, padded=True)
    # fmt=0b01 → seg_byte = 0x25, pad byte, then UINT
    assert encoded == b"\x25\x00\x00\x01"


def test_logical_segment_encode_16bit_packed() -> None:
    seg = LogicalSegment(0x0100, "instance_id")
    encoded = LogicalSegment.encode(seg, padded=False)
    # fmt=0b01 → seg_byte = 0x25, no pad
    assert encoded == b"\x25\x00\x01"


def test_logical_segment_encode_32bit_packed() -> None:
    seg = LogicalSegment(0x00010000, "instance_id")
    encoded = LogicalSegment.encode(seg, padded=False)
    assert encoded[0] == 0x27  # segment_type|instance_id|fmt=0b11


def test_logical_segment_decode_8bit() -> None:
    encoded = b"\x20\x01"
    seg = LogicalSegment.decode(encoded, padded=False)
    assert seg.logical_type == "class_id"
    assert seg.logical_value == 0x01


def test_logical_segment_decode_16bit_padded() -> None:
    encoded = b"\x25\x00\x00\x01"
    seg = LogicalSegment.decode(encoded, padded=True)
    assert seg.logical_type == "instance_id"
    assert seg.logical_value == 0x0100


def test_logical_segment_decode_reserved_fmt_raises() -> None:
    # fmt=0b10 is reserved → DataError
    bad_byte = bytes([0b_001_001_10])  # logical, instance_id, reserved fmt
    with pytest.raises(DataError):
        LogicalSegment.decode(bad_byte + b"\x00", padded=False)


@given(logical_types, logical_values_8bit)
def test_logical_segment_round_trip_8bit(ltype: str, lval: int) -> None:
    seg = LogicalSegment(lval, ltype)
    decoded = LogicalSegment.decode(LogicalSegment.encode(seg, padded=False), padded=False)
    assert decoded.logical_type == ltype
    assert decoded.logical_value == lval


@given(logical_types, logical_values_16bit)
def test_logical_segment_round_trip_16bit_packed(ltype: str, lval: int) -> None:
    seg = LogicalSegment(lval, ltype)
    encoded = LogicalSegment.encode(seg, padded=False)
    decoded = LogicalSegment.decode(encoded, padded=False)
    assert decoded.logical_type == ltype
    assert decoded.logical_value == lval


@given(logical_types, logical_values_32bit)
def test_logical_segment_round_trip_32bit_packed(ltype: str, lval: int) -> None:
    seg = LogicalSegment(lval, ltype)
    encoded = LogicalSegment.encode(seg, padded=False)
    decoded = LogicalSegment.decode(encoded, padded=False)
    assert decoded.logical_type == ltype
    assert decoded.logical_value == lval


# ---------------------------------------------------------------------------
# PortSegment
# ---------------------------------------------------------------------------


def test_port_segment_backplane_slot_0() -> None:
    seg = PortSegment(port=1, link_address=0)
    encoded = PortSegment.encode(seg)
    # port=1 (0x01), link_address=0 (USINT=0x00)  → 0x01 0x00
    assert encoded == b"\x01\x00"


def test_port_segment_backplane_slot_1() -> None:
    seg = PortSegment(port=1, link_address=1)
    encoded = PortSegment.encode(seg)
    assert encoded == b"\x01\x01"


def test_port_segment_ip_extended_link() -> None:
    seg = PortSegment(port=2, link_address="192.168.1.1")
    encoded = PortSegment.encode(seg)
    # Extended link bit set, port=2|0x10=0x12, len=11, IP ASCII, + pad
    assert encoded[0] == 0x12  # port 2 | ext_link bit
    assert encoded[1] == 11  # len of "192.168.1.1"
    assert encoded[2:13] == b"192.168.1.1"


def test_port_segment_ipv6_raises() -> None:
    seg = PortSegment(port=2, link_address="::1")
    with pytest.raises(DataError):
        PortSegment.encode(seg)


def test_port_segment_decode_simple() -> None:
    # Backplane slot 0: encode then decode
    seg = PortSegment(port=1, link_address=0)
    encoded = PortSegment.encode(seg)
    decoded = PortSegment.decode(encoded)
    assert decoded.port == 1
    assert decoded.link_address == 0


def test_port_segment_decode_ip() -> None:
    seg = PortSegment(port=2, link_address="192.168.1.1")
    encoded = PortSegment.encode(seg)
    decoded = PortSegment.decode(encoded)
    assert decoded.port == 2
    assert decoded.link_address == "192.168.1.1"


@given(
    st.integers(min_value=0, max_value=14),
    st.integers(min_value=0, max_value=255),
)
def test_port_segment_round_trip_simple(port: int, slot: int) -> None:
    seg = PortSegment(port=port, link_address=slot)
    decoded = PortSegment.decode(PortSegment.encode(seg))
    assert decoded.port == port
    assert decoded.link_address == slot


# ---------------------------------------------------------------------------
# DataSegment
# ---------------------------------------------------------------------------


def test_data_segment_encode_string() -> None:
    seg = DataSegment("MyTag")
    encoded = DataSegment.encode(seg)
    # 0x91 (segment_type|ext_symbol), 0x05 (len), "MyTag", 0x00 (pad)
    assert encoded == b"\x91\x05MyTag\x00"


def test_data_segment_encode_string_even_length() -> None:
    seg = DataSegment("AB")
    encoded = DataSegment.encode(seg)
    # len=2, no pad needed
    assert encoded == b"\x91\x02AB"


def test_data_segment_decode_string() -> None:
    encoded = b"\x91\x05MyTag\x00"
    seg = DataSegment.decode(encoded)
    assert seg.data == "MyTag"


def test_data_segment_round_trip_string() -> None:
    seg = DataSegment("SomeTag")
    decoded = DataSegment.decode(DataSegment.encode(seg))
    assert decoded.data == seg.data


@given(
    st.text(
        alphabet=st.characters(min_codepoint=0x20, max_codepoint=0x7E),
        min_size=1,
        max_size=50,
    )
)
def test_data_segment_round_trip_hypothesis(name: str) -> None:
    seg = DataSegment(name)
    decoded = DataSegment.decode(DataSegment.encode(seg))
    assert decoded.data == seg.data


# ---------------------------------------------------------------------------
# EPATH / PADDED_EPATH / PACKED_EPATH
# ---------------------------------------------------------------------------


def test_padded_epath_encode_class_instance() -> None:
    segments = [
        LogicalSegment(0x6B, "class_id"),
        LogicalSegment(0x01, "instance_id"),
    ]
    encoded = PADDED_EPATH.encode(segments)
    # class 0x6B: 0x20 0x6B; instance 0x01: 0x24 0x01
    assert encoded == b"\x20\x6b\x24\x01"


def test_padded_epath_encode_with_length_prefix() -> None:
    segments = [
        LogicalSegment(0x6B, "class_id"),
        LogicalSegment(0x01, "instance_id"),
    ]
    encoded = PADDED_EPATH.encode(segments, length=True)
    # 4 bytes path → word count = 2 → USINT(2) + path
    assert encoded[0:1] == b"\x02"
    assert encoded[1:] == b"\x20\x6b\x24\x01"


def test_epath_decode_class_instance() -> None:
    # Packed EPATH for class=0x6B instance=0x01
    data = b"\x20\x6b\x24\x01"
    segments = PACKED_EPATH.decode(data)
    assert len(segments) == 2
    assert isinstance(segments[0], LogicalSegment)
    assert segments[0].logical_type == "class_id"
    assert segments[0].logical_value == 0x6B
    assert isinstance(segments[1], LogicalSegment)
    assert segments[1].logical_type == "instance_id"
    assert segments[1].logical_value == 0x01


def test_padded_epath_decode_with_length_prefix() -> None:
    # request_path output: USINT word_count, then segments
    data = b"\x02\x20\x6b\x24\x01"
    segments = PADDED_EPATH.decode_with_length_prefix(data)
    assert len(segments) == 2
    assert isinstance(segments[0], LogicalSegment)
    assert segments[0].logical_value == 0x6B


def test_epath_round_trip_packed() -> None:
    segments_in = [
        LogicalSegment(0x02, "class_id"),
        LogicalSegment(0x01, "instance_id"),
    ]
    encoded = PACKED_EPATH.encode(segments_in)
    segments_out = PACKED_EPATH.decode(encoded)
    assert len(segments_out) == 2
    assert isinstance(segments_out[0], LogicalSegment)
    assert isinstance(segments_out[1], LogicalSegment)
    assert segments_out[0].logical_value == segments_in[0].logical_value
    assert segments_out[0].logical_type == segments_in[0].logical_type
    assert segments_out[1].logical_value == segments_in[1].logical_value


def test_epath_decode_with_data_segment() -> None:
    seg = DataSegment("PLC_Tag")
    encoded = PACKED_EPATH.encode([seg])
    decoded = PACKED_EPATH.decode(encoded)
    assert len(decoded) == 1
    assert isinstance(decoded[0], DataSegment)
    assert decoded[0].data == "PLC_Tag"


def test_epath_unsupported_segment_type_raises() -> None:
    # 0b011_00000 = 0x60 is not a valid segment type
    with pytest.raises(DataError):
        PACKED_EPATH.decode(b"\x60\x00")
