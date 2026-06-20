"""Tests for daedalus.cip.data_types."""

from __future__ import annotations

import struct

import pytest
from hypothesis import given

from daedalus.cip.data_types import (
    BOOL,
    BYTE,
    DATA_TYPES_BY_CODE,
    DATA_TYPES_BY_NAME,
    DATE_AND_TIME,
    DINT,
    DWORD,
    FTIME,
    INT,
    ITIME,
    LDT,
    LINT,
    LOGIX_STRING,
    LREAL,
    LTIME,
    LWORD,
    REAL,
    SHORT_STRING,
    SINT,
    STIME,
    STRING,
    STRING2,
    STRINGI,
    STRINGN,
    TIME,
    TIME32,
    UDINT,
    UINT,
    ULINT,
    USINT,
    WORD,
    Array,
    DateAndTimeValue,
    StringIEntry,
    Struct,
)
from daedalus.exceptions import BufferEmptyError, DataError
from strategies import (
    ascii_text,
    bool_values,
    byte_array_values,
    dint_values,
    dword_array_values,
    int_values,
    lint_values,
    lreal_values,
    lword_array_values,
    real_values,
    sint_values,
    udint_values,
    uint_values,
    ulint_values,
    usint_values,
    word_array_values,
)

# ---------------------------------------------------------------------------
# BOOL
# ---------------------------------------------------------------------------


def test_bool_true_encodes_as_0xff() -> None:
    assert BOOL.encode(True) == b"\xff"


def test_bool_false_encodes_as_0x00() -> None:
    assert BOOL.encode(False) == b"\x00"


def test_bool_decode_nonzero_is_true() -> None:
    for byte in [b"\x01", b"\xff", b"\x80", b"\x40"]:
        assert BOOL.decode(byte) is True


def test_bool_decode_zero_is_false() -> None:
    assert BOOL.decode(b"\x00") is False


@given(bool_values)
def test_bool_round_trip(v: bool) -> None:
    assert BOOL.decode(BOOL.encode(v)) == v


# ---------------------------------------------------------------------------
# Integer round-trips
# ---------------------------------------------------------------------------


@given(sint_values)
def test_sint_round_trip(v: int) -> None:
    assert SINT.decode(SINT.encode(v)) == v


@given(int_values)
def test_int_round_trip(v: int) -> None:
    assert INT.decode(INT.encode(v)) == v


@given(dint_values)
def test_dint_round_trip(v: int) -> None:
    assert DINT.decode(DINT.encode(v)) == v


@given(lint_values)
def test_lint_round_trip(v: int) -> None:
    assert LINT.decode(LINT.encode(v)) == v


@given(usint_values)
def test_usint_round_trip(v: int) -> None:
    assert USINT.decode(USINT.encode(v)) == v


@given(uint_values)
def test_uint_round_trip(v: int) -> None:
    assert UINT.decode(UINT.encode(v)) == v


@given(udint_values)
def test_udint_round_trip(v: int) -> None:
    assert UDINT.decode(UDINT.encode(v)) == v


@given(ulint_values)
def test_ulint_round_trip(v: int) -> None:
    assert ULINT.decode(ULINT.encode(v)) == v


# ---------------------------------------------------------------------------
# Float round-trips
# ---------------------------------------------------------------------------


@given(real_values)
def test_real_round_trip(v: float) -> None:
    # Struct f is 32-bit, decode may differ slightly from Python float
    encoded = REAL.encode(v)
    decoded = REAL.decode(encoded)
    # Re-encode decoded to compare (struct.pack("f",...) truncates precision)
    assert REAL.encode(decoded) == encoded


@given(lreal_values)
def test_lreal_round_trip(v: float) -> None:
    assert LREAL.decode(LREAL.encode(v)) == v


# ---------------------------------------------------------------------------
# Bit arrays (BYTE/WORD/DWORD/LWORD)
# ---------------------------------------------------------------------------


@given(byte_array_values)
def test_byte_round_trip(v: list[bool]) -> None:
    assert BYTE.decode(BYTE.encode(v)) == v


@given(word_array_values)
def test_word_round_trip(v: list[bool]) -> None:
    assert WORD.decode(WORD.encode(v)) == v


@given(dword_array_values)
def test_dword_round_trip(v: list[bool]) -> None:
    assert DWORD.decode(DWORD.encode(v)) == v


@given(lword_array_values)
def test_lword_round_trip(v: list[bool]) -> None:
    assert LWORD.decode(LWORD.encode(v)) == v


# ---------------------------------------------------------------------------
# TIME / duration types (backed by integer primitives)
# ---------------------------------------------------------------------------


@given(dint_values)
def test_time_round_trip(v: int) -> None:
    assert TIME.decode(TIME.encode(v)) == v


@given(sint_values)
def test_itime_round_trip(v: int) -> None:
    assert ITIME.decode(ITIME.encode(v)) == v


@given(lint_values)
def test_ltime_round_trip(v: int) -> None:
    assert LTIME.decode(LTIME.encode(v)) == v


@given(dint_values)
def test_ftime_round_trip(v: int) -> None:
    assert FTIME.decode(FTIME.encode(v)) == v


@given(ulint_values)
def test_ldt_round_trip(v: int) -> None:
    assert LDT.decode(LDT.encode(v)) == v


# ---------------------------------------------------------------------------
# DATE_AND_TIME — 6 bytes on wire (not 8 as pycomm3 declares)
# ---------------------------------------------------------------------------


def test_date_and_time_encodes_to_6_bytes() -> None:
    v = DateAndTimeValue(time_of_day=3_600_000, date=18000)
    encoded = DATE_AND_TIME.encode(v)
    assert len(encoded) == 6


def test_date_and_time_round_trip() -> None:
    v = DateAndTimeValue(time_of_day=3_600_000, date=18000)
    assert DATE_AND_TIME.decode(DATE_AND_TIME.encode(v)) == v


def test_date_and_time_wire_format() -> None:
    v = DateAndTimeValue(time_of_day=0x00AAB820, date=0x0002)
    encoded = DATE_AND_TIME.encode(v)
    expected = struct.pack("<IH", 0x00AAB820, 0x0002)
    assert encoded == expected


# ---------------------------------------------------------------------------
# String types
# ---------------------------------------------------------------------------


@given(ascii_text)
def test_string_round_trip(v: str) -> None:
    assert STRING.decode(STRING.encode(v)) == v


@given(ascii_text)
def test_short_string_round_trip(v: str) -> None:
    # SHORT_STRING has 1-byte length, so limit to 255 chars
    if len(v) > 255:
        v = v[:255]
    assert SHORT_STRING.decode(SHORT_STRING.encode(v)) == v


@given(ascii_text)
def test_logix_string_round_trip(v: str) -> None:
    assert LOGIX_STRING.decode(LOGIX_STRING.encode(v)) == v


def test_stringn_round_trip_utf8() -> None:
    v = "hello world"
    assert STRINGN.decode(STRINGN.encode(v, char_size=1)) == v


def test_string2_round_trip() -> None:
    v = "hello"
    assert STRING2.decode(STRING2.encode(v)) == v


# ---------------------------------------------------------------------------
# STRINGI — symmetric encode/decode with StringIEntry
# ---------------------------------------------------------------------------


def test_stringi_round_trip_single_entry() -> None:
    entry = StringIEntry(text="hello", language="eng", char_set=4, string_type=STRING)
    result = STRINGI.decode(STRINGI.encode([entry]))
    assert len(result) == 1
    r = result[0]
    assert r.text == "hello"
    assert r.language == "eng"
    assert r.char_set == 4
    assert r.string_type is STRING


def test_stringi_round_trip_multiple_entries() -> None:
    entries = [
        StringIEntry(text="hello", language="eng", char_set=4, string_type=STRING),
        StringIEntry(text="bonjour", language="fra", char_set=4, string_type=STRING),
    ]
    result = STRINGI.decode(STRINGI.encode(entries))
    assert len(result) == 2
    assert result[0].text == "hello"
    assert result[1].text == "bonjour"
    assert result[0].language == "eng"
    assert result[1].language == "fra"


def test_stringi_empty() -> None:
    result = STRINGI.decode(STRINGI.encode([]))
    assert result == []


# ---------------------------------------------------------------------------
# Array factory
# ---------------------------------------------------------------------------


def test_array_fixed_length_encode_decode() -> None:
    ArrType = Array(3, UINT)
    encoded = ArrType.encode([1, 2, 3])
    assert encoded == UINT.encode(1) + UINT.encode(2) + UINT.encode(3)
    decoded = ArrType.decode(encoded)
    assert decoded == [1, 2, 3]


def test_array_length_zero_is_not_confused_with_none() -> None:
    # Bug fix: length=0 must not be treated as None (length or cls.length breaks on 0)
    ArrType = Array(0, UINT)
    encoded = ArrType.encode([])
    assert encoded == b""
    assert ArrType.decode(b"") == []


def test_array_unbound_consumes_all() -> None:
    ArrType = Array(None, UINT)
    data = UINT.encode(1) + UINT.encode(2) + UINT.encode(3)
    assert ArrType.decode(data) == [1, 2, 3]


def test_array_length_as_datatype_encode() -> None:
    # Bug fix: Array length-as-DataType must prefix count bytes
    ArrType = Array(USINT, UINT)
    encoded = ArrType.encode([10, 20, 30])
    # Should start with count prefix (USINT=3), then three UINTs
    assert encoded[0:1] == USINT.encode(3)
    assert encoded[1:] == UINT.encode(10) + UINT.encode(20) + UINT.encode(30)


def test_array_length_as_datatype_decode() -> None:
    # Bug fix: decode must read count from buffer, not iterate over DataType class
    ArrType = Array(USINT, UINT)
    data = USINT.encode(2) + UINT.encode(100) + UINT.encode(200)
    result = ArrType.decode(data)
    assert result == [100, 200]


def test_array_buffer_empty_stops_cleanly() -> None:
    ArrType = Array(None, UINT)
    # Empty buffer → _decode_all catches BufferEmptyError and returns []
    result = ArrType.decode(b"")
    assert result == []


def test_array_unbound_stops_at_exact_boundary() -> None:
    ArrType = Array(None, UINT)
    # Exactly 3 UINTs → returns all 3, stops at end of buffer cleanly
    data = UINT.encode(1) + UINT.encode(2) + UINT.encode(3)
    result = ArrType.decode(data)
    assert result == [1, 2, 3]


# ---------------------------------------------------------------------------
# Struct factory
# ---------------------------------------------------------------------------


def test_struct_encode_decode_from_dict() -> None:
    RevStruct = Struct(USINT("major"), USINT("minor"))
    encoded = RevStruct.encode({"major": 1, "minor": 2})
    assert encoded == b"\x01\x02"
    decoded = RevStruct.decode(encoded)
    assert decoded == {"major": 1, "minor": 2}


def test_struct_encode_decode_from_list() -> None:
    RevStruct = Struct(USINT("major"), USINT("minor"))
    encoded = RevStruct.encode([3, 4])
    decoded = RevStruct.decode(encoded)
    assert decoded == {"major": 3, "minor": 4}


def test_struct_unnamed_members_excluded() -> None:
    # Unnamed members consumed but not in result
    S = Struct(UINT("a"), UINT(), UINT("b"))
    encoded = UINT.encode(1) + UINT.encode(99) + UINT.encode(2)
    decoded = S.decode(encoded)
    assert "a" in decoded
    assert "b" in decoded
    assert len(decoded) == 2


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def test_data_types_by_code_canonical_0xcc() -> None:
    assert DATA_TYPES_BY_CODE[0xCC] is LDT


def test_data_types_by_code_canonical_0xd6() -> None:
    assert DATA_TYPES_BY_CODE[0xD6] is FTIME


def test_data_types_by_code_bool() -> None:
    assert DATA_TYPES_BY_CODE[0xC1] is BOOL


def test_data_types_by_name_aliases() -> None:
    assert DATA_TYPES_BY_NAME["stime"] is STIME
    assert DATA_TYPES_BY_NAME["time32"] is TIME32


def test_data_types_by_name_lower() -> None:
    assert DATA_TYPES_BY_NAME["bool"] is BOOL
    assert DATA_TYPES_BY_NAME["uint"] is UINT


# ---------------------------------------------------------------------------
# Buffer errors
# ---------------------------------------------------------------------------


def test_decode_empty_buffer_raises_buffer_empty_error() -> None:
    with pytest.raises(BufferEmptyError):
        UINT.decode(b"")


def test_decode_partial_buffer_raises_data_error() -> None:
    with pytest.raises((DataError, BufferEmptyError)):
        UINT.decode(b"\x01")  # UINT needs 2 bytes
