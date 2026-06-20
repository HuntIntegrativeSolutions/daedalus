"""Parity oracle tests — byte-identical output vs pycomm3 for fixed code paths.

These tests require the [oracle] extra: uv sync --extra oracle

Skipped automatically if pycomm3 is not installed.
"""

from __future__ import annotations

from typing import Any

import pytest

pycomm3 = pytest.importorskip("pycomm3")

import pycomm3.cip.data_types as pc  # noqa: E402

import daedalus.cip.data_types as dt  # noqa: E402

# ---------------------------------------------------------------------------
# Type-code parity — catches transcription errors in .code attributes
# ---------------------------------------------------------------------------

TYPE_CODE_PAIRS = [
    ("BOOL", dt.BOOL, pc.BOOL),
    ("SINT", dt.SINT, pc.SINT),
    ("INT", dt.INT, pc.INT),
    ("DINT", dt.DINT, pc.DINT),
    ("LINT", dt.LINT, pc.LINT),
    ("USINT", dt.USINT, pc.USINT),
    ("UINT", dt.UINT, pc.UINT),
    ("UDINT", dt.UDINT, pc.UDINT),
    ("ULINT", dt.ULINT, pc.ULINT),
    ("REAL", dt.REAL, pc.REAL),
    ("LREAL", dt.LREAL, pc.LREAL),
    ("BYTE", dt.BYTE, pc.BYTE),
    ("WORD", dt.WORD, pc.WORD),
    ("DWORD", dt.DWORD, pc.DWORD),
    ("LWORD", dt.LWORD, pc.LWORD),
    ("STRING", dt.STRING, pc.STRING),
    ("STRING2", dt.STRING2, pc.STRING2),
    ("SHORT_STRING", dt.SHORT_STRING, pc.SHORT_STRING),
    ("STRINGN", dt.STRINGN, pc.STRINGN),
    ("STRINGI", dt.STRINGI, pc.STRINGI),
    ("DT", dt.DT, pc.DT),
    ("TIME", dt.TIME, pc.TIME),
    ("LTIME", dt.LTIME, pc.LTIME),
    ("ITIME", dt.ITIME, pc.ITIME),
    ("DATE", dt.DATE, pc.DATE),
    ("TIME_OF_DAY", dt.TIME_OF_DAY, pc.TIME_OF_DAY),
    ("DATE_AND_TIME", dt.DATE_AND_TIME, pc.DATE_AND_TIME),
    ("FTIME", dt.FTIME, pc.FTIME),
    ("LDT", dt.LDT, pc.LDT),
    ("ENGUNIT", dt.ENGUNIT, pc.ENGUNIT),
    # Skip LOGIX_STRING (code=0x00, not a standard CIP code — intentional)
    # Skip STIME/TIME32 (aliases, not canonical)
]


@pytest.mark.parametrize("name,ours,theirs", TYPE_CODE_PAIRS, ids=[t[0] for t in TYPE_CODE_PAIRS])
def test_type_code_matches_pycomm3(name: Any, ours: Any, theirs: Any) -> None:
    assert ours.code == theirs.code, (
        f"{name}: daedalus code=0x{ours.code:02X}, pycomm3 code=0x{theirs.code:02X}"
    )


# ---------------------------------------------------------------------------
# Byte-identical encode parity for elementary types
# ---------------------------------------------------------------------------

INT_SAMPLE_VALUES = [0, 1, -1, 127, -128, 100]
UINT_SAMPLE_VALUES = [0, 1, 255, 1000, 65535]


@pytest.mark.parametrize("v", INT_SAMPLE_VALUES)
def test_sint_encode_matches_pycomm3(v: Any) -> None:
    assert dt.SINT.encode(v) == pc.SINT.encode(v)


@pytest.mark.parametrize("v", INT_SAMPLE_VALUES)
def test_int_encode_matches_pycomm3(v: Any) -> None:
    assert dt.INT.encode(v) == pc.INT.encode(v)


@pytest.mark.parametrize("v", INT_SAMPLE_VALUES)
def test_dint_encode_matches_pycomm3(v: Any) -> None:
    assert dt.DINT.encode(v) == pc.DINT.encode(v)


@pytest.mark.parametrize("v", UINT_SAMPLE_VALUES)
def test_uint_encode_matches_pycomm3(v: Any) -> None:
    assert dt.UINT.encode(v) == pc.UINT.encode(v)


@pytest.mark.parametrize("v", UINT_SAMPLE_VALUES)
def test_udint_encode_matches_pycomm3(v: Any) -> None:
    assert dt.UDINT.encode(v) == pc.UDINT.encode(v)


def test_real_encode_matches_pycomm3() -> None:
    for v in [0.0, 1.0, -1.0, 3.14]:
        assert dt.REAL.encode(v) == pc.REAL.encode(v), f"REAL mismatch for {v}"


def test_string_encode_matches_pycomm3() -> None:
    for s in ["", "hello", "test string"]:
        assert dt.STRING.encode(s) == pc.STRING.encode(s), f"STRING mismatch for {s!r}"


def test_bool_encode_matches_pycomm3() -> None:
    assert dt.BOOL.encode(True) == pc.BOOL.encode(True)
    assert dt.BOOL.encode(False) == pc.BOOL.encode(False)


# ---------------------------------------------------------------------------
# Segment encode parity (pycomm3 encode works; decode raises NotImplementedError)
# ---------------------------------------------------------------------------


def test_logical_segment_encode_matches_pycomm3_8bit() -> None:
    from pycomm3.cip.data_types import LogicalSegment as PSegment

    from daedalus.cip.segments import LogicalSegment as DSegment

    for ltype in ["class_id", "instance_id", "attribute_id"]:
        for val in [0x01, 0x6B, 0xFF]:
            d_enc = DSegment.encode(DSegment(val, ltype), padded=False)
            p_enc = PSegment.encode(PSegment(val, ltype), padded=False)
            assert d_enc == p_enc, f"LogicalSegment encode mismatch for {ltype}={val:#x}"


def test_data_segment_encode_matches_pycomm3() -> None:
    from pycomm3.cip.data_types import DataSegment as PSegment

    from daedalus.cip.segments import DataSegment as DSegment

    for name in ["MyTag", "AB", "ProgramTag"]:
        d_enc = DSegment.encode(DSegment(name))
        p_enc = PSegment.encode(PSegment(name))
        assert d_enc == p_enc, f"DataSegment encode mismatch for {name!r}"


def test_port_segment_encode_matches_pycomm3_backplane() -> None:
    from pycomm3.cip.data_types import PortSegment as PPortSeg

    from daedalus.cip.segments import PortSegment as DPortSeg

    for slot in [0, 1, 5]:
        d_enc = DPortSeg.encode(DPortSeg(port=1, link_address=slot))
        p_enc = PPortSeg.encode(PPortSeg(port=1, link_address=slot))
        assert d_enc == p_enc, f"PortSegment encode mismatch for slot {slot}"


def test_padded_epath_encode_matches_pycomm3() -> None:
    from pycomm3.cip.data_types import PADDED_EPATH as PP
    from pycomm3.cip.data_types import LogicalSegment as PS

    from daedalus.cip.segments import PADDED_EPATH
    from daedalus.cip.segments import LogicalSegment as DS

    d_segs = [DS(0x6B, "class_id"), DS(0x01, "instance_id")]
    p_segs = [PS(0x6B, "class_id"), PS(0x01, "instance_id")]

    d_enc = PADDED_EPATH.encode(d_segs, length=True)
    p_enc = PP.encode(p_segs, length=True)
    assert d_enc == p_enc


# ---------------------------------------------------------------------------
# Request path parity
# ---------------------------------------------------------------------------


def test_request_path_matches_pycomm3() -> None:
    from pycomm3.cip.object_library import ClassCode as PC_ClassCode
    from pycomm3.packets.util import request_path as p_rp

    from daedalus.cip.object_library import ClassCode
    from daedalus.packets.cip import request_path as d_rp

    d = d_rp(ClassCode.MESSAGE_ROUTER, 0x01)
    p = p_rp(PC_ClassCode.message_router, b"\x01")
    assert d == p, f"request_path mismatch: daedalus={d.hex()}, pycomm3={p.hex()}"


# ---------------------------------------------------------------------------
# Forward_Open data parity — byte-identical to pycomm3's static defaults
# ---------------------------------------------------------------------------
#
# pycomm3 hardcodes these values (before per-connection randomisation):
#   cid  (T→O conn ID)   = b"\x27\x04\x19\x71" → 0x71190427
#   csn  (conn serial)   = b"\x27\x04"          → 0x0427
#   vid  (vendor ID)     = b"\x09\x10"          → 0x1009
#   vsn  (orig serial)   = b"\x09\x10\x19\x71"  → 0x71191009
#   RPI                  = b"\x01\x40\x20\x00"  → 0x00204001 µs
#
# Sources: pycomm3/cip_driver.py lines 134-137, 378.


def test_forward_open_data_matches_pycomm3_standard() -> None:
    """Standard (0x54) FO data bytes are byte-identical to pycomm3's builder."""
    import pycomm3.const as pc
    from pycomm3.cip.data_types import UDINT as pc_UDINT
    from pycomm3.cip.data_types import UINT as pc_UINT

    from daedalus.packets.cip import MSG_ROUTER_PATH
    from daedalus.packets.forward_open import _build_forward_open_data

    connection_size = 500
    rpi = 0x00204001
    init_net_params = 0b_0100_0010_0000_0000
    net_params = pc_UINT.encode((connection_size & 0x01FF) | init_net_params)
    connection_path = MSG_ROUTER_PATH  # PADDED_EPATH bytes — parity already verified

    expected = b"".join(
        [
            pc.PRIORITY,
            pc.TIMEOUT_TICKS,
            b"\x00\x00\x00\x00",  # O→T conn ID (blank)
            b"\x27\x04\x19\x71",  # T→O conn ID = 0x71190427 LE (cid default)
            b"\x27\x04",  # connection serial = 0x0427 LE
            b"\x09\x10",  # originator vendor ID = 0x1009 LE
            b"\x09\x10\x19\x71",  # originator serial = 0x71191009 LE (vsn default)
            pc.TIMEOUT_MULTIPLIER,
            b"\x00\x00\x00",  # reserved
            pc_UDINT.encode(rpi),  # O→T RPI
            net_params,  # O→T net params (UINT)
            pc_UDINT.encode(rpi),  # T→O RPI
            net_params,  # T→O net params (UINT)
            pc.TRANSPORT_CLASS,
            connection_path,
        ]
    )
    actual = _build_forward_open_data(
        large=False,
        connection_size=connection_size,
        rpi=rpi,
        to_connection_id=0x71190427,
        connection_serial=0x0427,
        originator_vendor_id=0x1009,
        originator_serial=0x71191009,
        connection_path=connection_path,
    )
    assert actual == expected, (
        f"Standard FO data mismatch:\n  actual  : {actual.hex()}\n  expected: {expected.hex()}"
    )


def test_forward_open_data_matches_pycomm3_large() -> None:
    """Large (0x5B) FO data: net_params are UDINT instead of UINT."""
    import pycomm3.const as pc
    from pycomm3.cip.data_types import UDINT as pc_UDINT

    from daedalus.packets.cip import MSG_ROUTER_PATH
    from daedalus.packets.forward_open import _build_forward_open_data

    connection_size = 4000
    rpi = 0x00204001
    init_net_params = 0b_0100_0010_0000_0000
    net_params = pc_UDINT.encode((connection_size & 0xFFFF) | (init_net_params << 16))
    connection_path = MSG_ROUTER_PATH

    expected = b"".join(
        [
            pc.PRIORITY,
            pc.TIMEOUT_TICKS,
            b"\x00\x00\x00\x00",
            b"\x27\x04\x19\x71",
            b"\x27\x04",
            b"\x09\x10",
            b"\x09\x10\x19\x71",
            pc.TIMEOUT_MULTIPLIER,
            b"\x00\x00\x00",
            pc_UDINT.encode(rpi),
            net_params,  # O→T net params (UDINT for large)
            pc_UDINT.encode(rpi),
            net_params,  # T→O net params (UDINT for large)
            pc.TRANSPORT_CLASS,
            connection_path,
        ]
    )
    actual = _build_forward_open_data(
        large=True,
        connection_size=connection_size,
        rpi=rpi,
        to_connection_id=0x71190427,
        connection_serial=0x0427,
        originator_vendor_id=0x1009,
        originator_serial=0x71191009,
        connection_path=connection_path,
    )
    assert actual == expected, (
        f"Large FO data mismatch:\n  actual  : {actual.hex()}\n  expected: {expected.hex()}"
    )
