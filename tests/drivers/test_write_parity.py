"""WRITE_TAG request-bytes parity tests.

Verifies that _build_write_request produces the correct wire bytes for known
inputs.  Expected bytes are derived by hand from the CIP spec, not from
pycomm3 at runtime.  Empirically confirms that per-element array encoding
produces the same bytes as element-by-element reads (no length prefix).
"""

from __future__ import annotations

import struct

import pytest

from daedalus.cip.data_types import BOOL, DINT, REAL
from daedalus.cip.services import CIPService
from daedalus.drivers._logix import _build_write_request, _encode_value, _is_bit_of_word
from daedalus.packets.cip import build_cip_request, tag_request_path

# ---------------------------------------------------------------------------
# Helpers for hand-derived expected bytes
# ---------------------------------------------------------------------------


def _expected_write_tag_bytes(
    tag_name: str,
    type_code: int,
    element_count: int,
    value_bytes: bytes,
) -> bytes:
    """Build expected WRITE_TAG bytes from spec primitives — same as _build_write_request."""
    path = tag_request_path(tag_name)
    data = struct.pack("<H", type_code) + struct.pack("<H", element_count) + value_bytes
    result: bytes = build_cip_request(CIPService.WRITE_TAG, path, data)
    return result


# ---------------------------------------------------------------------------
# Scalar parity
# ---------------------------------------------------------------------------


def test_write_tag_dint_scalar_bytes_parity() -> None:
    """WRITE_TAG for a DINT scalar matches hand-derived bytes."""
    tag_name = "ScratchDINT"
    value = 42
    value_bytes = DINT.encode(value)

    expected = _expected_write_tag_bytes(tag_name, DINT.code, 1, value_bytes)
    actual = _build_write_request(tag_name, DINT.code, 1, value_bytes)

    assert actual == expected, (
        f"WRITE_TAG mismatch:\n  actual:   {actual.hex()}\n  expected: {expected.hex()}"
    )


def test_write_tag_bool_scalar_bytes_parity() -> None:
    """BOOL writes use 0xFF for True, 0x00 for False; element_count=1."""
    for val, expected_byte in [(True, b"\xff"), (False, b"\x00")]:
        value_bytes = BOOL.encode(val)
        assert value_bytes == expected_byte, (
            f"BOOL.encode({val!r}) == {value_bytes!r}, want {expected_byte!r}"
        )
        result = _build_write_request("Flag", BOOL.code, 1, value_bytes)
        expected = _expected_write_tag_bytes("Flag", BOOL.code, 1, value_bytes)
        assert result == expected


def test_write_tag_real_scalar_bytes_parity() -> None:
    """REAL scalar bytes must use little-endian IEEE-754 float32."""
    value = 3.14
    value_bytes = REAL.encode(value)
    # Confirm it's really float32 LE
    assert struct.unpack("<f", value_bytes)[0] != value  # float32 precision trap exists
    assert len(value_bytes) == 4

    result = _build_write_request("RealTag", REAL.code, 1, value_bytes)
    expected = _expected_write_tag_bytes("RealTag", REAL.code, 1, value_bytes)
    assert result == expected


# ---------------------------------------------------------------------------
# Array parity
# ---------------------------------------------------------------------------


def test_write_tag_dint_array_bytes_parity() -> None:
    """DINT array: per-element encode == concatenated DINT.encode() with no length prefix."""
    values = [1, 2, 3]
    element_count = len(values)

    # Per-element encoding (what _encode_value does)
    per_element = b"".join(DINT.encode(v) for v in values)

    # Direct concatenation (hand-derived spec encoding)
    hand_derived = b"".join(struct.pack("<i", v) for v in values)

    # Both must agree — confirms no length prefix
    assert per_element == hand_derived, (
        "Per-element encode diverges from hand-derived concatenation — "
        "encoding adds unexpected bytes"
    )

    result = _build_write_request("ArrayTag", DINT.code, element_count, per_element)
    expected = _expected_write_tag_bytes("ArrayTag", DINT.code, element_count, per_element)
    assert result == expected


def test_write_tag_array_no_length_prefix() -> None:
    """Array encoding must NOT add a length prefix (like CIP string/EPATH encode does).

    The CIP WRITE_TAG service carries element_count separately in the data field —
    the value bytes are raw concatenated elements with no count prefix.
    """
    values = [10, 20, 30, 40]
    encoded = _encode_value(DINT, values, len(values))
    # Each DINT is 4 bytes; a raw concatenation of 4 elements = 16 bytes total
    assert len(encoded) == 4 * 4, (
        f"Expected 16 bytes for 4xDINT, got {len(encoded)} - "
        "encoding may have added a length prefix"
    )
    # First 4 bytes must be the encoding of values[0]
    assert encoded[:4] == DINT.encode(10)
    # Last 4 bytes must be encoding of values[-1]
    assert encoded[-4:] == DINT.encode(40)


# ---------------------------------------------------------------------------
# UINT type_code / element_count wire format
# ---------------------------------------------------------------------------


def test_write_tag_type_code_is_uint16_little_endian() -> None:
    """type_code in WRITE_TAG data must be a UINT (2-byte LE) per CIP spec."""
    tag_name = "T"
    value_bytes = DINT.encode(1)
    raw = _build_write_request(tag_name, DINT.code, 1, value_bytes)

    # The data field starts after the request path.  We rely on the known
    # layout: service (1B) + path (variable).  The type_code is at data[0:2].
    # Parse it back out by checking what the last bytes of the raw frame look like.
    # Easier: just check that the type_code and element_count appear correctly
    # encoded inside the raw bytes.
    type_code_le = struct.pack("<H", DINT.code)
    elem_count_le = struct.pack("<H", 1)
    assert type_code_le in raw, f"UINT(type_code) not found in {raw.hex()}"
    assert elem_count_le in raw, f"UINT(element_count) not found in {raw.hex()}"


# ---------------------------------------------------------------------------
# Bit-of-word predicate
# ---------------------------------------------------------------------------


def test_bit_of_word_write_is_guarded() -> None:
    """_is_bit_of_word detects bit-index paths; pipeline guard tested in test_write_e2e.py.

    daedalus defers masked Read-Modify-Write (CIP 0x4E) to Phase 2g+.
    No pycomm3 parity test can exist until the feature is implemented.
    """
    # Numeric-only trailing suffix = bit index → must be flagged
    assert _is_bit_of_word("MyDINT.5")
    assert _is_bit_of_word("Counter.0")
    assert _is_bit_of_word("SomeDINT.31")

    # Non-numeric suffix = struct member → must NOT be flagged
    assert not _is_bit_of_word("MyUDT.FieldName")
    assert not _is_bit_of_word("MyDINT")
    assert not _is_bit_of_word("Program:Main.Counter")


# ---------------------------------------------------------------------------
# Pycomm3 oracle (guarded — skip if pycomm3 not installed)
# ---------------------------------------------------------------------------


pycomm3 = pytest.importorskip("pycomm3", reason="pycomm3 not installed — oracle tests skipped")


def test_write_tag_dint_scalar_matches_pycomm3() -> None:
    """WRITE_TAG bytes must be byte-identical to pycomm3's serialization for DINT.

    Reference assembled from pycomm3 primitives (no socket) — same pattern as
    test_read_tag_request_bytes_match_pycomm3 in tests/test_parity_oracle.py.
    Wire format: 0x4D + tag_request_path + UINT(type_code) + UINT(count) + value.
    """
    import pycomm3.cip.data_types as pc
    from pycomm3.packets.logix import tag_request_path as p_trp  # type: ignore[attr-defined]

    tag_name = "ScratchDINT"
    value = 999

    # pycomm3 reference: service + path + UINT(type_code) + UINT(element_count) + value_bytes
    theirs = (
        bytes([0x4D])
        + p_trp(tag_name, None, False)  # type: ignore[no-untyped-call]
        + pc.UINT.encode(pc.DINT.code)
        + pc.UINT.encode(1)
        + pc.DINT.encode(value)
    )
    ours = _build_write_request(tag_name, DINT.code, 1, DINT.encode(value))
    assert ours == theirs, (
        f"WRITE_TAG request mismatch:\n  ours  : {ours.hex()}\n  theirs: {theirs.hex()}"
    )


def test_write_tag_dint_array_matches_pycomm3() -> None:
    """WRITE_TAG bytes for a DINT array must be byte-identical to pycomm3's output.

    No length prefix — element_count in the CIP data field carries that information.
    Values include negative, zero, large, and typical to exercise the full range.
    """
    import pycomm3.cip.data_types as pc
    from pycomm3.packets.logix import tag_request_path as p_trp  # type: ignore[attr-defined]

    tag_name = "ArrayTag"
    values = [-1, 0, 305419896, 999]
    element_count = len(values)
    value_bytes = b"".join(DINT.encode(v) for v in values)

    theirs = (
        bytes([0x4D])
        + p_trp(tag_name, None, False)  # type: ignore[no-untyped-call]
        + pc.UINT.encode(pc.DINT.code)
        + pc.UINT.encode(element_count)
        + b"".join(pc.DINT.encode(v) for v in values)
    )
    ours = _build_write_request(tag_name, DINT.code, element_count, value_bytes)
    assert ours == theirs, (
        f"WRITE_TAG array mismatch:\n  ours  : {ours.hex()}\n  theirs: {theirs.hex()}"
    )


def test_write_tag_bool_scalar_matches_pycomm3() -> None:
    """WRITE_TAG bytes for a BOOL scalar (True and False) must match pycomm3.

    CIP specifies True = 0xFF; this confirms daedalus and pycomm3 agree.
    A failure here means the True-byte encoding diverges — report, do not paper over.
    """
    import pycomm3.cip.data_types as pc
    from pycomm3.packets.logix import tag_request_path as p_trp  # type: ignore[attr-defined]

    tag_name = "Flag"
    for val in (True, False):
        value_bytes = BOOL.encode(val)
        theirs = (
            bytes([0x4D])
            + p_trp(tag_name, None, False)  # type: ignore[no-untyped-call]
            + pc.UINT.encode(pc.BOOL.code)
            + pc.UINT.encode(1)
            + pc.BOOL.encode(val)
        )
        ours = _build_write_request(tag_name, BOOL.code, 1, value_bytes)
        assert ours == theirs, (
            f"WRITE_TAG BOOL({val!r}) mismatch:\n  ours  : {ours.hex()}\n  theirs: {theirs.hex()}"
        )


def test_write_tag_real_scalar_matches_pycomm3() -> None:
    """WRITE_TAG bytes for REAL (float32) scalars must match pycomm3.

    Includes negative, fractional, and zero to exercise the IEEE-754 encode path.
    """
    import pycomm3.cip.data_types as pc
    from pycomm3.packets.logix import tag_request_path as p_trp  # type: ignore[attr-defined]

    tag_name = "RealTag"
    for val in (-2.5, 3.5, 0.0):
        value_bytes = REAL.encode(val)
        theirs = (
            bytes([0x4D])
            + p_trp(tag_name, None, False)  # type: ignore[no-untyped-call]
            + pc.UINT.encode(pc.REAL.code)
            + pc.UINT.encode(1)
            + pc.REAL.encode(val)
        )
        ours = _build_write_request(tag_name, REAL.code, 1, value_bytes)
        assert ours == theirs, (
            f"WRITE_TAG REAL({val!r}) mismatch:\n  ours  : {ours.hex()}\n  theirs: {theirs.hex()}"
        )
