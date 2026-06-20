"""L0 unit tests for src/daedalus/cip/templates.py.

All tests are offline — no I/O, no sim, no sockets.  The helpers under test are
pure functions (parse_template_attr_reply, parse_template_data, decode_struct)
and the data models (TemplateAttributes, RawMember, ResolvedMember, ResolvedTemplate).
"""

from __future__ import annotations

import struct

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from daedalus.cip.data_types import (
    BOOL,
    DINT,
    DWORD,
    REAL,
    SINT,
    UINT,
)
from daedalus.cip.templates import (
    ResolvedMember,
    ResolvedTemplate,
    TemplateAttributes,
    decode_struct,
    parse_template_attr_reply,
    parse_template_data,
)
from daedalus.drivers._logix import _build_template_attr_request, _build_template_read_request

# ---------------------------------------------------------------------------
# Helpers for constructing template bytes
# ---------------------------------------------------------------------------


def _make_attr_payload(
    obj_def_size: int,
    structure_size: int,
    member_count: int,
    structure_handle: int,
) -> bytes:
    """Build a GET_ATTRIBUTE_LIST reply payload for 4 makeup attributes."""
    return (
        struct.pack("<H", 4)  # count = 4
        + struct.pack("<H", 4)
        + struct.pack("<H", 0)
        + struct.pack("<I", obj_def_size)
        + struct.pack("<H", 5)
        + struct.pack("<H", 0)
        + struct.pack("<I", structure_size)
        + struct.pack("<H", 2)
        + struct.pack("<H", 0)
        + struct.pack("<H", member_count)
        + struct.pack("<H", 1)
        + struct.pack("<H", 0)
        + struct.pack("<H", structure_handle)
    )


def _make_member_info(type_info: int, typ: int, offset: int) -> bytes:
    """Build one 8-byte member-info entry."""
    return struct.pack("<HHI", type_info, typ, offset)


def _make_names_blob(template_name_with_semi: str, member_names: list[str]) -> bytes:
    """Build null-separated names blob for a user-defined type."""
    parts = [template_name_with_semi.encode("ascii")] + [n.encode("ascii") for n in member_names]
    return b"\x00".join(parts) + b"\x00"


def _make_predefined_names_blob(template_name: str, member_names: list[str]) -> bytes:
    """Build null-separated names blob for a predefined type (no semicolon in first name)."""
    parts = [template_name.encode("ascii")] + [n.encode("ascii") for n in member_names]
    return b"\x00".join(parts) + b"\x00"


def _make_resolved(
    name: str,
    offset: int,
    atomic_type: type,
    *,
    is_private: bool = False,
    is_bool: bool = False,
    bit_number: int = 0,
    is_array: bool = False,
    array_length: int = 0,
    nested_template: ResolvedTemplate | None = None,
) -> ResolvedMember:
    return ResolvedMember(
        name=name,
        offset=offset,
        is_private=is_private,
        is_bool=is_bool,
        bit_number=bit_number,
        is_array=is_array,
        array_length=array_length,
        atomic_type=atomic_type,
        nested_template=nested_template,
    )


# ---------------------------------------------------------------------------
# parse_template_attr_reply
# ---------------------------------------------------------------------------


def test_parse_template_attr_reply_round_trip() -> None:
    payload = _make_attr_payload(50, 8, 2, 0xABCD)
    attrs = parse_template_attr_reply(payload)
    assert attrs.object_definition_size == 50
    assert attrs.structure_size == 8
    assert attrs.member_count == 2
    assert attrs.structure_handle == 0xABCD


def test_parse_template_attr_reply_max_values() -> None:
    payload = _make_attr_payload(0xFFFFFFFF, 0xFFFFFFFF, 0xFFFF, 0xFFFF)
    attrs = parse_template_attr_reply(payload)
    assert attrs.object_definition_size == 0xFFFFFFFF
    assert attrs.structure_handle == 0xFFFF


def test_parse_template_attr_reply_too_short_raises() -> None:
    from daedalus.exceptions import DataError

    with pytest.raises(DataError, match="too short"):
        parse_template_attr_reply(b"\x04")


@given(
    obj_def=st.integers(min_value=6, max_value=1000),
    struct_size=st.integers(min_value=1, max_value=500),
    member_count=st.integers(min_value=0, max_value=255),
    handle=st.integers(min_value=0, max_value=0xFFFF),
)
@settings(max_examples=100)
def test_parse_template_attr_reply_hypothesis(
    obj_def: int,
    struct_size: int,
    member_count: int,
    handle: int,
) -> None:
    payload = _make_attr_payload(obj_def, struct_size, member_count, handle)
    attrs = parse_template_attr_reply(payload)
    assert attrs.object_definition_size == obj_def
    assert attrs.structure_size == struct_size
    assert attrs.member_count == member_count
    assert attrs.structure_handle == handle


# ---------------------------------------------------------------------------
# parse_template_data
# ---------------------------------------------------------------------------


def test_parse_template_data_flat_udt() -> None:
    # Flat UDT: DINT x @offset 0, REAL y @offset 4
    member_info = _make_member_info(0, DINT.code, 0) + _make_member_info(0, REAL.code, 4)
    names = _make_names_blob("MyUDT;DEADBEEF", ["x", "y"])
    data = member_info + names
    attrs = TemplateAttributes(
        object_definition_size=10,
        structure_size=8,
        member_count=2,
        structure_handle=1,
    )
    template_name, pairs = parse_template_data(data, attrs, instance_id=0x1234)
    assert template_name == "MyUDT"
    names_out = [n for n, _ in pairs]
    assert names_out == ["x", "y"]
    assert pairs[0][1].offset == 0
    assert pairs[0][1].typ == DINT.code
    assert pairs[1][1].offset == 4
    assert pairs[1][1].typ == REAL.code


def test_parse_template_data_bool_member() -> None:
    # DWORD host @ offset 0, then two BOOL aliases
    member_info = (
        _make_member_info(0, DWORD.code, 0)  # host DWORD
        + _make_member_info(0, BOOL.code, 0)  # bit 0 alias
        + _make_member_info(1, BOOL.code, 0)  # bit 1 alias
    )
    names = _make_names_blob("BoolUDT;AA", ["host", "b0", "b1"])
    data = member_info + names
    attrs = TemplateAttributes(
        object_definition_size=8, structure_size=4, member_count=3, structure_handle=2
    )
    _, pairs = parse_template_data(data, attrs, instance_id=0x200)
    assert pairs[1][0] == "b0"
    assert pairs[1][1].typ == BOOL.code
    assert pairs[1][1].type_info == 0  # bit 0
    assert pairs[2][1].type_info == 1  # bit 1


def test_parse_template_data_array_member() -> None:
    # SINT[4] arr @ offset 0: type_info = 4 (array length)
    member_info = _make_member_info(4, SINT.code, 0)
    names = _make_names_blob("ArrUDT;BB", ["arr"])
    data = member_info + names
    attrs = TemplateAttributes(
        object_definition_size=8, structure_size=4, member_count=1, structure_handle=3
    )
    _, pairs = parse_template_data(data, attrs, instance_id=0x300)
    assert pairs[0][0] == "arr"
    assert pairs[0][1].type_info == 4
    assert pairs[0][1].typ == SINT.code


def test_parse_template_data_predefined_name_pop() -> None:
    # Predefined type (instance_id < 0x100): first name is template name, no ";"
    member_info = _make_member_info(0, DINT.code, 0) + _make_member_info(0, UINT.code, 4)
    names = _make_predefined_names_blob("MY_PREDEFINED", ["member1", "member2"])
    data = member_info + names
    attrs = TemplateAttributes(
        object_definition_size=8, structure_size=6, member_count=2, structure_handle=4
    )
    template_name, pairs = parse_template_data(data, attrs, instance_id=0x50)
    assert template_name == "MY_PREDEFINED"
    assert [n for n, _ in pairs] == ["member1", "member2"]


def test_parse_template_data_asciistring82_renamed() -> None:
    # ASCIISTRING82 → STRING
    member_info = _make_member_info(0, DINT.code, 0) + _make_member_info(82, SINT.code, 4)
    names = _make_names_blob("ASCIISTRING82;CC", ["LEN", "DATA"])
    data = member_info + names
    attrs = TemplateAttributes(
        object_definition_size=30, structure_size=86, member_count=2, structure_handle=5
    )
    template_name, _ = parse_template_data(data, attrs, instance_id=0x400)
    assert template_name == "STRING"


def test_parse_template_data_too_short_raises() -> None:
    from daedalus.exceptions import DataError

    attrs = TemplateAttributes(
        object_definition_size=10, structure_size=8, member_count=3, structure_handle=1
    )
    # Only 8 bytes but 3 members need 24
    with pytest.raises(DataError, match="too short"):
        parse_template_data(b"\x00" * 8, attrs, instance_id=0x1234)


# ---------------------------------------------------------------------------
# decode_struct — flat
# ---------------------------------------------------------------------------


def _flat_template() -> ResolvedTemplate:
    """DINT x @0, REAL y @4 — structure_size=8."""
    return ResolvedTemplate(
        name="MyUDT",
        structure_size=8,
        structure_handle=1,
        members=[
            _make_resolved("x", 0, DINT),
            _make_resolved("y", 4, REAL),
        ],
    )


def test_decode_struct_flat() -> None:
    member_data = DINT.encode(42) + REAL.encode(3.14)
    result = decode_struct(member_data, _flat_template())
    assert result == {"x": 42, "y": pytest.approx(3.14, rel=1e-4)}


def test_decode_struct_excludes_private() -> None:
    template = ResolvedTemplate(
        name="T",
        structure_size=8,
        structure_handle=1,
        members=[
            _make_resolved("pub", 0, DINT),
            _make_resolved("ZZZZZZZZZZ_priv", 4, DINT, is_private=True),
        ],
    )
    result = decode_struct(DINT.encode(10) + DINT.encode(99), template)
    assert "pub" in result
    assert "ZZZZZZZZZZ_priv" not in result


# ---------------------------------------------------------------------------
# decode_struct — BOOL bit-aliasing
# ---------------------------------------------------------------------------


def test_decode_struct_bool_bits() -> None:
    # In Logix the BOOL host word is private (ZZZZZZZZZZ prefix), so only
    # the BOOL alias members appear in the decoded dict.
    # Value = 0b0000_0011 → bits 0 and 1 set, bit 2 clear.
    host_val = 0b11
    template = ResolvedTemplate(
        name="BoolUDT",
        structure_size=4,
        structure_handle=2,
        members=[
            # Private host storage word — excluded from result
            _make_resolved("ZZZZZZZZZZ_host", 0, DWORD, is_private=True),
            ResolvedMember(
                name="b0",
                offset=0,
                is_private=False,
                is_bool=True,
                bit_number=0,
                is_array=False,
                array_length=0,
                atomic_type=BOOL,
                nested_template=None,
            ),
            ResolvedMember(
                name="b1",
                offset=0,
                is_private=False,
                is_bool=True,
                bit_number=1,
                is_array=False,
                array_length=0,
                atomic_type=BOOL,
                nested_template=None,
            ),
            ResolvedMember(
                name="b2",
                offset=0,
                is_private=False,
                is_bool=True,
                bit_number=2,
                is_array=False,
                array_length=0,
                atomic_type=BOOL,
                nested_template=None,
            ),
        ],
    )
    member_data = struct.pack("<I", host_val)  # 4 bytes little-endian
    result = decode_struct(member_data, template)
    assert isinstance(result, dict)
    assert "ZZZZZZZZZZ_host" not in result
    assert result["b0"] is True
    assert result["b1"] is True
    assert result["b2"] is False


def test_decode_struct_bool_bit_precise() -> None:
    # raw[offset] & (1 << bit_number) — exact pycomm3 formula
    template = ResolvedTemplate(
        name="T",
        structure_size=1,
        structure_handle=1,
        members=[
            ResolvedMember(
                name="flag",
                offset=0,
                is_private=False,
                is_bool=True,
                bit_number=7,
                is_array=False,
                array_length=0,
                atomic_type=BOOL,
                nested_template=None,
            )
        ],
    )
    assert decode_struct(b"\x80", template) == {"flag": True}
    assert decode_struct(b"\x7f", template) == {"flag": False}


# ---------------------------------------------------------------------------
# decode_struct — array member
# ---------------------------------------------------------------------------


def test_decode_struct_array_member() -> None:
    template = ResolvedTemplate(
        name="ArrUDT",
        structure_size=4,
        structure_handle=3,
        members=[
            ResolvedMember(
                name="arr",
                offset=0,
                is_private=False,
                is_bool=False,
                bit_number=0,
                is_array=True,
                array_length=4,
                atomic_type=SINT,
                nested_template=None,
            )
        ],
    )
    member_data = bytes([1, 2, 3, 4])
    result = decode_struct(member_data, template)
    assert isinstance(result, dict)
    assert result["arr"] == [1, 2, 3, 4]


# ---------------------------------------------------------------------------
# decode_struct — nested UDT
# ---------------------------------------------------------------------------


def test_decode_struct_nested() -> None:
    inner_template = ResolvedTemplate(
        name="Inner",
        structure_size=8,
        structure_handle=10,
        members=[
            _make_resolved("x", 0, DINT),
            _make_resolved("y", 4, REAL),
        ],
    )
    outer_template = ResolvedTemplate(
        name="Outer",
        structure_size=10,
        structure_handle=11,
        members=[
            _make_resolved("counter", 0, UINT),
            ResolvedMember(
                name="inner",
                offset=2,
                is_private=False,
                is_bool=False,
                bit_number=0,
                is_array=False,
                array_length=0,
                atomic_type=None,
                nested_template=inner_template,
            ),
        ],
    )
    inner_data = DINT.encode(42) + REAL.encode(1.5)
    member_data = UINT.encode(7) + inner_data
    result = decode_struct(member_data, outer_template)
    assert isinstance(result, dict)
    assert result["counter"] == 7
    inner = result["inner"]
    assert isinstance(inner, dict)
    assert inner["x"] == 42
    assert inner["y"] == pytest.approx(1.5, rel=1e-4)


# ---------------------------------------------------------------------------
# decode_struct — string
# ---------------------------------------------------------------------------


def test_decode_struct_string() -> None:
    # STRING template: LEN (DINT) @0, DATA (SINT[82]) @4; is_string=True
    # string_length=82 — actual data is structure_size - 4 bytes
    text = "hello"
    len_bytes = DINT.encode(len(text))
    data_bytes = text.encode("ascii") + b"\x00" * (82 - len(text))
    member_data = len_bytes + data_bytes

    template = ResolvedTemplate(
        name="STRING",
        structure_size=86,
        structure_handle=5,
        members=[
            _make_resolved("LEN", 0, DINT),
            ResolvedMember(
                name="DATA",
                offset=4,
                is_private=False,
                is_bool=False,
                bit_number=0,
                is_array=True,
                array_length=82,
                atomic_type=SINT,
                nested_template=None,
            ),
        ],
        is_string=True,
        string_length=82,
    )
    result = decode_struct(member_data, template)
    assert result == "hello"


def test_decode_struct_empty_string() -> None:
    template = ResolvedTemplate(
        name="STRING",
        structure_size=86,
        structure_handle=5,
        members=[
            _make_resolved("LEN", 0, DINT),
            ResolvedMember(
                name="DATA",
                offset=4,
                is_private=False,
                is_bool=False,
                bit_number=0,
                is_array=True,
                array_length=82,
                atomic_type=SINT,
                nested_template=None,
            ),
        ],
        is_string=True,
        string_length=82,
    )
    member_data = DINT.encode(0) + b"\x00" * 82
    assert decode_struct(member_data, template) == ""


# ---------------------------------------------------------------------------
# Template request bytes parity
# ---------------------------------------------------------------------------


def test_template_read_request_bytes_formula() -> None:
    """_build_template_read_request uses (object_definition_size * 4) - 21 - offset verbatim."""

    obj_def = 50
    offset = 0
    expected_bytes_to_read = (obj_def * 4) - 21 - offset  # = 179
    assert expected_bytes_to_read == 179

    req = _build_template_read_request(
        instance_id=0x1234, object_definition_size=obj_def, offset=offset
    )
    # Parse to verify the request data contains DINT(0) + UINT(179)
    # The CIP request has: [service, path_word_count, path..., data...]
    path_word_count = req[1]
    data_offset = 2 + path_word_count * 2
    req_data = req[data_offset:]
    parsed_offset = struct.unpack_from("<i", req_data, 0)[0]
    parsed_bytes_to_read = struct.unpack_from("<H", req_data, 4)[0]
    assert parsed_offset == 0
    assert parsed_bytes_to_read == 179


def test_template_read_request_bytes_with_continuation_offset() -> None:
    """At offset=100 with obj_def=50, bytes_to_read = (50*4)-21-100 = 79."""
    obj_def = 50
    offset = 100
    expected_bytes_to_read = (obj_def * 4) - 21 - offset  # = 79
    assert expected_bytes_to_read == 79

    req = _build_template_read_request(
        instance_id=0x01, object_definition_size=obj_def, offset=offset
    )
    path_word_count = req[1]
    data_start = 2 + path_word_count * 2
    parsed_offset = struct.unpack_from("<i", req, data_start)[0]
    parsed_bytes_to_read = struct.unpack_from("<H", req, data_start + 4)[0]
    assert parsed_offset == 100
    assert parsed_bytes_to_read == 79


def test_template_attr_request_attrs_order() -> None:
    """GET_ATTRIBUTE_LIST request data must request attrs 4, 5, 2, 1 in order."""
    req = _build_template_attr_request(instance_id=0x1234)
    path_word_count = req[1]
    data_start = 2 + path_word_count * 2
    # Data: UINT(4) + UINT(4) + UINT(5) + UINT(2) + UINT(1)
    count = struct.unpack_from("<H", req, data_start)[0]
    assert count == 4
    attrs = struct.unpack_from("<HHHH", req, data_start + 2)
    assert attrs == (4, 5, 2, 1)
