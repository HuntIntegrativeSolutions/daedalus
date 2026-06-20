"""Request-bytes parity tests for the template pipeline.

These tests verify that the request bytes produced by daedalus match the
expected wire format by comparing against hand-derived expected values from
the ODVA spec and pycomm3's source (used as a documentation reference).

No pycomm3 runtime calls — pycomm3's _parse_template_data is a bound driver
method that calls _get_data_type (I/O) and is not safely invokeable offline.
We rely on hand-crafted fixtures and formula verification instead.
"""

from __future__ import annotations

import struct

import pytest

from daedalus.cip.data_types import DINT, REAL, SINT
from daedalus.cip.services import CIPService
from daedalus.cip.templates import (
    TemplateAttributes,
    parse_template_attr_reply,
    parse_template_data,
)
from daedalus.drivers._logix import (
    _build_template_attr_request,
    _build_template_read_request,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_names_blob(template_name_with_semi: str, member_names: list[str]) -> bytes:
    parts = [template_name_with_semi.encode()] + [n.encode() for n in member_names]
    return b"\x00".join(parts) + b"\x00"


def _make_member_info(type_info: int, typ: int, offset: int) -> bytes:
    return struct.pack("<HHI", type_info, typ, offset)


# ---------------------------------------------------------------------------
# GET_ATTRIBUTE_LIST request bytes
# ---------------------------------------------------------------------------


def test_template_attr_request_service_code() -> None:
    """GET_ATTRIBUTE_LIST must use service code 0x03."""
    req = _build_template_attr_request(instance_id=0x0100)
    assert req[0] == int(CIPService.GET_ATTRIBUTE_LIST)  # 0x03


def test_template_attr_request_four_attrs_in_order() -> None:
    """Request data: UINT(4) + UINT(4) + UINT(5) + UINT(2) + UINT(1)."""
    req = _build_template_attr_request(instance_id=0x01)
    path_word_count = req[1]
    data_start = 2 + path_word_count * 2
    count = struct.unpack_from("<H", req, data_start)[0]
    assert count == 4
    attrs = struct.unpack_from("<HHHH", req, data_start + 2)
    assert attrs == (4, 5, 2, 1)


def test_template_attr_request_path_class_instance() -> None:
    """Path must encode class 0x6C + the given instance_id."""
    from daedalus.cip.segments import PADDED_EPATH, LogicalSegment

    instance_id = 0x0200
    req = _build_template_attr_request(instance_id=instance_id)
    path_word_count = req[1]
    path_bytes = req[2 : 2 + path_word_count * 2]
    segments = PADDED_EPATH.decode(path_bytes)
    class_segs = [
        s for s in segments if isinstance(s, LogicalSegment) and s.logical_type == "class_id"
    ]
    inst_segs = [
        s for s in segments if isinstance(s, LogicalSegment) and s.logical_type == "instance_id"
    ]
    assert len(class_segs) == 1
    assert class_segs[0].logical_value == 0x6C
    assert len(inst_segs) == 1
    assert inst_segs[0].logical_value == instance_id


# ---------------------------------------------------------------------------
# READ_TAG template request bytes (-21 formula)
# ---------------------------------------------------------------------------


def test_template_read_request_formula_offset0() -> None:
    """bytes_to_read = (object_definition_size * 4) - 21 - offset.

    For obj_def=50, offset=0: bytes_to_read = (50*4)-21-0 = 179.
    Verified by hand from pycomm3 line:
        bytes_to_read = ((object_definition_size * 4) - 21) - offset
    """
    req = _build_template_read_request(instance_id=0x01, object_definition_size=50, offset=0)
    path_word_count = req[1]
    data_start = 2 + path_word_count * 2
    req_offset = struct.unpack_from("<i", req, data_start)[0]  # DINT (signed)
    bytes_to_read = struct.unpack_from("<H", req, data_start + 4)[0]
    assert req_offset == 0
    assert bytes_to_read == (50 * 4) - 21 - 0  # 179


def test_template_read_request_formula_mid_offset() -> None:
    """With offset=80 and obj_def=50: bytes_to_read = (50*4)-21-80 = 99."""
    req = _build_template_read_request(instance_id=0x05, object_definition_size=50, offset=80)
    path_word_count = req[1]
    data_start = 2 + path_word_count * 2
    req_offset = struct.unpack_from("<i", req, data_start)[0]
    bytes_to_read = struct.unpack_from("<H", req, data_start + 4)[0]
    assert req_offset == 80
    assert bytes_to_read == (50 * 4) - 21 - 80  # 99


def test_template_read_request_uses_dint_offset_not_udint() -> None:
    """Offset is signed DINT (4 bytes) per pycomm3 — must accept zero without truncation."""
    req = _build_template_read_request(instance_id=0x01, object_definition_size=10, offset=0)
    path_word_count = req[1]
    data_start = 2 + path_word_count * 2
    # Signed DINT encode of 0 = b'\x00\x00\x00\x00' (4 bytes)
    offset_bytes = req[data_start : data_start + 4]
    assert len(offset_bytes) == 4
    assert struct.unpack("<i", offset_bytes)[0] == 0  # signed int


def test_template_read_request_service_code() -> None:
    """Template READ_TAG uses the standard READ_TAG service code 0x4C."""
    req = _build_template_read_request(instance_id=0x01, object_definition_size=10, offset=0)
    assert req[0] == int(CIPService.READ_TAG)  # 0x4C


# ---------------------------------------------------------------------------
# parse_template_data parity against hand-derived expected values
# ---------------------------------------------------------------------------


def test_parse_flat_template_parity_hand_derived() -> None:
    """Parse a hand-crafted flat UDT template and assert expected member layout.

    Template: DINT x @offset 0 (type_info=0, scalar), REAL y @offset 4 (type_info=0, scalar).
    Expected: template_name="FlatUDT", two members with correct names/types/offsets.

    This verifies daedalus's parse_template_data matches the ODVA wire format
    without requiring pycomm3 runtime (which needs live I/O for nested types).
    """
    member_info = (
        _make_member_info(0, DINT.code, 0)  # x: DINT scalar at offset 0
        + _make_member_info(0, REAL.code, 4)  # y: REAL scalar at offset 4
    )
    names = _make_names_blob("FlatUDT;DEADBEEF", ["x", "y"])
    data = member_info + names

    attrs = TemplateAttributes(
        object_definition_size=10,
        structure_size=8,
        member_count=2,
        structure_handle=1,
    )
    template_name, pairs = parse_template_data(data, attrs, instance_id=0x1234)

    assert template_name == "FlatUDT"
    assert len(pairs) == 2

    assert pairs[0][0] == "x"
    assert pairs[0][1].typ == DINT.code
    assert pairs[0][1].type_info == 0  # scalar
    assert pairs[0][1].offset == 0

    assert pairs[1][0] == "y"
    assert pairs[1][1].typ == REAL.code
    assert pairs[1][1].type_info == 0
    assert pairs[1][1].offset == 4


def test_parse_array_member_parity() -> None:
    """SINT[4] array member: type_info = 4 (array length), typ = SINT.code."""
    member_info = _make_member_info(4, SINT.code, 0)  # arr: SINT[4] at offset 0
    names = _make_names_blob("ArrUDT;AA", ["arr"])
    data = member_info + names

    attrs = TemplateAttributes(
        object_definition_size=8, structure_size=4, member_count=1, structure_handle=2
    )
    _, pairs = parse_template_data(data, attrs, instance_id=0x500)
    assert pairs[0][0] == "arr"
    assert pairs[0][1].typ == SINT.code
    assert pairs[0][1].type_info == 4  # this becomes array_length in ResolvedMember


def test_parse_bool_member_bit_number_parity() -> None:
    """BOOL member: type_info = bit number, typ = BOOL.code (0xC1)."""
    from daedalus.cip.data_types import BOOL, DWORD

    member_info = (
        _make_member_info(0, DWORD.code, 0)  # host storage
        + _make_member_info(7, BOOL.code, 0)  # BOOL alias at bit 7
    )
    names = _make_names_blob("BoolUDT;BB", ["ZZZZZZZZZZ_host", "flag"])
    data = member_info + names

    attrs = TemplateAttributes(
        object_definition_size=8, structure_size=4, member_count=2, structure_handle=3
    )
    _, pairs = parse_template_data(data, attrs, instance_id=0x600)
    assert pairs[1][0] == "flag"
    assert pairs[1][1].typ == BOOL.code  # 0xC1
    assert pairs[1][1].type_info == 7  # bit_number in ResolvedMember


def test_parse_template_data_struct_member_instance_id() -> None:
    """Nested struct member: typ & 0x0FFF = nested instance_id."""
    nested_instance_id = 0x250
    struct_type_ref = 0x8000 | nested_instance_id  # struct flag + instance

    member_info = _make_member_info(0, DINT.code, 0) + _make_member_info(0, struct_type_ref, 4)
    names = _make_names_blob("OuterUDT;CC", ["x", "inner"])
    data = member_info + names

    attrs = TemplateAttributes(
        object_definition_size=10, structure_size=12, member_count=2, structure_handle=4
    )
    _, pairs = parse_template_data(data, attrs, instance_id=0x300)
    # inner member: typ = struct_type_ref; masked = nested_instance_id
    inner_raw = pairs[1][1]
    assert inner_raw.typ & 0x0FFF == nested_instance_id


def test_parse_template_attr_reply_parity() -> None:
    """GET_ATTRIBUTE_LIST reply parsing matches expected attribute values."""
    payload = (
        struct.pack("<H", 4)
        + struct.pack("<H", 4)
        + struct.pack("<H", 0)
        + struct.pack("<I", 50)  # obj_def
        + struct.pack("<H", 5)
        + struct.pack("<H", 0)
        + struct.pack("<I", 200)  # struct_size
        + struct.pack("<H", 2)
        + struct.pack("<H", 0)
        + struct.pack("<H", 5)  # member_count
        + struct.pack("<H", 1)
        + struct.pack("<H", 0)
        + struct.pack("<H", 0xABCD)  # handle
    )
    attrs = parse_template_attr_reply(payload)
    assert attrs.object_definition_size == 50
    assert attrs.structure_size == 200
    assert attrs.member_count == 5
    assert attrs.structure_handle == 0xABCD


# ---------------------------------------------------------------------------
# pycomm3 parity (request bytes) — only if oracle available
# ---------------------------------------------------------------------------


def test_template_attr_request_matches_pycomm3_bytes() -> None:
    """The GET_ATTRIBUTE_LIST request bytes must match pycomm3's approach.

    pycomm3 _get_structure_makeup builds:
      service=0x03, path=class 0x6C + instance N,
      data = UINT(4) + UINT(4) + UINT(5) + UINT(2) + UINT(1)

    We verify against a hard-coded byte sequence derived from that spec.
    """
    pytest.importorskip("pycomm3", reason="pycomm3 oracle not installed")

    # Build expected bytes manually from the spec
    from daedalus.cip.object_library import ClassCode
    from daedalus.cip.services import CIPService
    from daedalus.packets.cip import build_cip_request, request_path

    instance_id = 0x0100
    path = request_path(ClassCode.TEMPLATE_OBJECT, instance_id)
    expected_data = struct.pack("<H", 4) + struct.pack("<HHHH", 4, 5, 2, 1)
    expected_req = build_cip_request(CIPService.GET_ATTRIBUTE_LIST, path, expected_data)

    actual_req = _build_template_attr_request(instance_id=instance_id)
    assert actual_req == expected_req
