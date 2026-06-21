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


def test_backplane_path_matches_pycomm3() -> None:
    """backplane_path(slot) is byte-identical to pycomm3's PADDED_EPATH for slots 0, 1, 13."""
    from pycomm3.cip.data_types import PADDED_EPATH as pc_PP
    from pycomm3.cip.data_types import LogicalSegment as pc_LS
    from pycomm3.cip.data_types import PortSegment as pc_Port

    from daedalus.packets.cip import backplane_path

    for slot in [0, 1, 13]:
        expected = pc_PP.encode(
            [
                pc_Port(port=1, link_address=slot),
                pc_LS(0x02, "class_id"),
                pc_LS(0x01, "instance_id"),
            ],
            length=True,
        )
        actual = backplane_path(slot)
        assert actual == expected, (
            f"backplane_path({slot}) mismatch: {actual.hex()} != {expected.hex()}"
        )
        assert actual[0] == 0x03, "word count must be 3"
        assert actual[1] == 0x01, "port byte must be 1 (backplane)"
        assert actual[2] == slot, f"link address byte must be {slot}"


def test_msg_router_path_unchanged_by_backplane_path() -> None:
    """MSG_ROUTER_PATH default is not altered by the new backplane_path function."""
    from pycomm3.cip.data_types import PADDED_EPATH as pc_PP
    from pycomm3.cip.data_types import LogicalSegment as pc_LS

    from daedalus.packets.cip import MSG_ROUTER_PATH

    expected = pc_PP.encode([pc_LS(0x02, "class_id"), pc_LS(0x01, "instance_id")], length=True)
    assert expected == MSG_ROUTER_PATH


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


# ---------------------------------------------------------------------------
# Forward_Close data parity — byte-identical to pycomm3 primitives + CIP spec
# ---------------------------------------------------------------------------
#
# pycomm3 _forward_close() builds the route_path with pad_length=True, which
# inserts a null byte after the word-count prefix.  That null byte IS the
# Reserved byte required by CIP Vol 1 Table 3-5.28.  Using pad_length=True
# here gives us the strongest offline oracle without needing a live connection.


def test_forward_close_data_matches_pycomm3_spec() -> None:
    """FC data bytes match pycomm3 primitives + CIP Vol 1 Table 3-5.28 layout."""
    import pycomm3.const as pc
    from pycomm3.cip.data_types import PADDED_EPATH as pc_PP
    from pycomm3.cip.data_types import UDINT as pc_UDINT
    from pycomm3.cip.data_types import UINT as pc_UINT
    from pycomm3.cip.data_types import LogicalSegment as pc_LS

    from daedalus.packets.cip import MSG_ROUTER_PATH
    from daedalus.packets.forward_open import _build_forward_close_data

    connection_serial = 0x0427
    originator_vendor_id = 0x1009
    originator_serial = 0x71191009

    # pad_length=True inserts 0x00 after the word-count — that is the CIP Reserved byte
    expected = b"".join(
        [
            pc.PRIORITY,
            pc.TIMEOUT_TICKS,
            pc_UINT.encode(connection_serial),
            pc_UINT.encode(originator_vendor_id),
            pc_UDINT.encode(originator_serial),
            pc_PP.encode(
                [pc_LS(0x02, "class_id"), pc_LS(0x01, "instance_id")],
                length=True,
                pad_length=True,
            ),
        ]
    )
    actual = _build_forward_close_data(
        connection_serial=connection_serial,
        originator_vendor_id=originator_vendor_id,
        originator_serial=originator_serial,
        connection_path=MSG_ROUTER_PATH,
    )
    assert actual == expected, (
        f"FC data mismatch:\n  actual  : {actual.hex()}\n  expected: {expected.hex()}"
    )


def test_forward_close_reserved_byte_position() -> None:
    """Reserved 0x00 must sit immediately after Connection_Path_Size per CIP Vol 1 Table 3-5.28."""
    from daedalus.packets.cip import MSG_ROUTER_PATH
    from daedalus.packets.forward_open import _build_forward_close_data

    fc_data = _build_forward_close_data(
        connection_serial=0x0427,
        originator_vendor_id=0x1009,
        originator_serial=0x71191009,
        connection_path=MSG_ROUTER_PATH,
    )
    # PRIORITY(1) + TICKS(1) + csn(2) + vid(2) + vsn(4) = 10 bytes → path_size at [10]
    PATH_SIZE_OFFSET = 10
    assert fc_data[PATH_SIZE_OFFSET] == MSG_ROUTER_PATH[0], "path_size_byte mismatch"
    assert fc_data[PATH_SIZE_OFFSET + 1] == 0x00, "Reserved byte must be 0x00"
    assert fc_data[PATH_SIZE_OFFSET + 2 :] == MSG_ROUTER_PATH[1:], "path bytes must follow reserved"


# ---------------------------------------------------------------------------
# Phase 2c parity — READ_TAG request bytes and MSP wrapper
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tag_name,element_count",
    [
        ("MyTag", 1),
        ("Program:Main.Counter", 1),
        ("ArrTag", 5),
    ],
    ids=["simple", "dotted-path", "array-elements"],
)
def test_read_tag_request_bytes_match_pycomm3(tag_name: str, element_count: int) -> None:
    """Full READ_TAG CIP request bytes (service + path + element_count) match pycomm3."""
    import pycomm3.cip.data_types as pc
    from pycomm3.packets.logix import tag_request_path as p_trp  # type: ignore[attr-defined]

    from daedalus.cip.data_types import UINT as d_UINT
    from daedalus.cip.services import CIPService
    from daedalus.packets.cip import build_cip_request
    from daedalus.packets.cip import tag_request_path as d_trp

    ours = build_cip_request(
        CIPService.READ_TAG,
        d_trp(tag_name),
        d_UINT.encode(element_count),
    )
    # pycomm3 tag_only_message equivalent: service + path + UINT(elements)
    theirs = bytes([0x4C]) + p_trp(tag_name, None, False) + pc.UINT.encode(element_count)  # type: ignore[no-untyped-call]

    assert ours == theirs, (
        f"READ_TAG request mismatch for '{tag_name}' (count={element_count}):\n"
        f"  ours  : {ours.hex()}\n"
        f"  theirs: {theirs.hex()}"
    )


@pytest.mark.parametrize(
    "tag_names",
    [
        ["TagA", "TagB"],
        ["Program:Main.X", "Program:Main.Y", "Program:Main.Z"],
    ],
    ids=["two-tags", "three-dotted-tags"],
)
def test_msp_request_bytes_match_pycomm3(tag_names: list[str]) -> None:
    """MSP request (count word + offset table + sub-requests) matches pycomm3."""
    import struct as _s

    import pycomm3.cip.data_types as pc
    from pycomm3.packets.logix import tag_request_path as p_trp  # type: ignore[attr-defined]

    from daedalus.cip.data_types import UINT as d_UINT
    from daedalus.cip.services import CIPService
    from daedalus.packets.cip import build_cip_request
    from daedalus.packets.cip import tag_request_path as d_trp

    # Build daedalus MSP payload (same layout as LogixDriver.read_tags)
    sub_reqs_d = [
        build_cip_request(CIPService.READ_TAG, d_trp(n), d_UINT.encode(1)) for n in tag_names
    ]
    count = len(sub_reqs_d)
    base = 2 + 2 * count
    pos = 0
    offsets: list[int] = []
    for req in sub_reqs_d:
        offsets.append(base + pos)
        pos += len(req)
    d_msp_data = (
        _s.pack("<H", count) + b"".join(_s.pack("<H", o) for o in offsets) + b"".join(sub_reqs_d)
    )

    # Build pycomm3 MSP payload
    sub_reqs_p = [bytes([0x4C]) + p_trp(n, None, False) + pc.UINT.encode(1) for n in tag_names]  # type: ignore[no-untyped-call]
    p_base = 2 + 2 * count
    p_pos = 0
    p_offsets: list[int] = []
    for req in sub_reqs_p:
        p_offsets.append(p_base + p_pos)
        p_pos += len(req)
    p_msp_data = (
        _s.pack("<H", count) + b"".join(_s.pack("<H", o) for o in p_offsets) + b"".join(sub_reqs_p)
    )

    assert d_msp_data == p_msp_data, (
        f"MSP data mismatch for {tag_names}:\n"
        f"  ours  : {d_msp_data.hex()}\n"
        f"  theirs: {p_msp_data.hex()}"
    )


# ---------------------------------------------------------------------------
# Phase 2d parity — Get Instance Attribute List request bytes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "program,instance",
    [
        (None, 0),  # controller scope, instance 0
        ("Program:Main", 0),  # program scope
        (None, 42),  # continuation — instance 42
    ],
    ids=["ctrl-inst0", "prog-inst0", "ctrl-inst42"],
)
def test_get_tag_list_request_bytes_match_pycomm3(program: Any, instance: int) -> None:
    """Get Instance Attribute List request bytes match pycomm3 BASE attribute list.

    Attr 10 (external_access) is excluded from both sides — daedalus always uses
    the base-6 list; pycomm3 only appends attr 10 when revision_major >= 18 (a
    live, firmware-gated decision).  Parity is asserted on the base-6 form only.
    """
    from pycomm3.cip.data_types import PADDED_EPATH as PC_PADDED_EPATH
    from pycomm3.cip.data_types import UINT as PC_UINT
    from pycomm3.cip.data_types import DataSegment as PC_DataSegment
    from pycomm3.cip.data_types import LogicalSegment as PC_LogicalSegment
    from pycomm3.cip.object_library import ClassCode as PC_ClassCode

    from daedalus.drivers._logix import _build_tag_list_request

    # pycomm3 side — base-6 attributes only (no attr 10)
    pc_segs: list[Any] = []
    if program is not None:
        pc_segs.append(PC_DataSegment(program))
    pc_segs += [
        PC_LogicalSegment(PC_ClassCode.symbol_object, "class_id"),
        PC_LogicalSegment(instance, "instance_id"),
    ]
    pc_path = PC_PADDED_EPATH.encode(pc_segs, length=True)
    pc_attrs = [b"\x01\x00", b"\x02\x00", b"\x03\x00", b"\x05\x00", b"\x06\x00", b"\x08\x00"]
    theirs = b"\x55" + pc_path + PC_UINT.encode(len(pc_attrs)) + b"".join(pc_attrs)

    # daedalus side
    ours = _build_tag_list_request(instance, program)

    assert ours == theirs, (
        f"Get Instance Attribute List request mismatch "
        f"(program={program!r}, instance={instance}):\n"
        f"  ours  : {ours.hex()}\n"
        f"  theirs: {theirs.hex()}"
    )


def test_get_tag_list_parse_matches_pycomm3() -> None:
    """Parse of a synthetic reply payload produces equivalent tag info to pycomm3.

    Builds a payload with: DINT scalar, 1D array, struct tag, I/O tag, system tag.
    Encodes the payload using pycomm3's STRING/UDINT/UINT primitives to prove
    both libraries share the same wire format, then parses with daedalus and
    manually re-parses with pycomm3 primitives to compare field-by-field.

    Asserts:
    - daedalus parsed names/instance_ids/dims match pycomm3-decoded values
    - I/O tag present in daedalus result (kept by _is_system_tag filter)
    - system-flagged tag absent from daedalus user-tag list
    """
    from io import BytesIO as _BytesIO

    from pycomm3.cip.data_types import STRING as PC_STRING
    from pycomm3.cip.data_types import UDINT as PC_UDINT
    from pycomm3.cip.data_types import UINT as PC_UINT

    from daedalus.drivers._logix import _is_system_tag, _parse_tag_list_reply

    _ST_DINT_SCALAR = 0x00C4
    _ST_DINT_1D = 0x20C4
    _ST_STRUCT = 0x8123
    _ST_SYSTEM = 0x10C4

    entries: list[dict[str, Any]] = [
        {"instance_id": 1, "name": "ScalarDINT", "symbol_type": _ST_DINT_SCALAR, "dims": (0, 0, 0)},
        {"instance_id": 2, "name": "ArrayDINT", "symbol_type": _ST_DINT_1D, "dims": (10, 0, 0)},
        {"instance_id": 3, "name": "MyUDT", "symbol_type": _ST_STRUCT, "dims": (0, 0, 0)},
        {"instance_id": 4, "name": "LocalIO:I", "symbol_type": _ST_DINT_SCALAR, "dims": (0, 0, 0)},
        {"instance_id": 5, "name": "SysTag", "symbol_type": _ST_SYSTEM, "dims": (0, 0, 0)},
    ]

    # Build payload using pycomm3 primitives (proves shared wire format)
    payload = b""
    for e in entries:
        dims: tuple[int, int, int] = e["dims"]
        payload += (
            PC_UDINT.encode(e["instance_id"])
            + PC_STRING.encode(e["name"])
            + PC_UINT.encode(e["symbol_type"])
            + PC_UDINT.encode(0)  # symbol_address
            + PC_UDINT.encode(0)  # symbol_object_address
            + PC_UDINT.encode(0)  # software_control
            + PC_UDINT.encode(dims[0])
            + PC_UDINT.encode(dims[1])
            + PC_UDINT.encode(dims[2])
        )

    # Re-parse with pycomm3 primitives to build expected dict (base-6 attrs, no attr 10)
    pc_parsed: list[dict[str, Any]] = []
    stream = _BytesIO(payload)
    while stream.tell() < len(payload):
        inst = PC_UDINT.decode(stream)
        name = PC_STRING.decode(stream)
        sym_type = PC_UINT.decode(stream)
        _addr = PC_UDINT.decode(stream)
        _obj_addr = PC_UDINT.decode(stream)
        _sw_ctrl = PC_UDINT.decode(stream)
        d1 = PC_UDINT.decode(stream)
        d2 = PC_UDINT.decode(stream)
        d3 = PC_UDINT.decode(stream)
        pc_parsed.append(
            {
                "instance_id": inst,
                "tag_name": name,
                "symbol_type": sym_type,
                "dimensions": [d1, d2, d3],
            }
        )
    pc_by_name = {t["tag_name"]: t for t in pc_parsed}

    # Parse with daedalus (includes all entries before filtering)
    d_tags, _, _ = _parse_tag_list_reply(payload, "controller")
    d_by_name = {t.tag_name: t for t in d_tags}

    # Field-by-field comparison for each entry (excluding system-flagged entries
    # which daedalus filters during parse, but pycomm3 returns raw)
    for e in entries:
        name = e["name"]
        assert name in pc_by_name, f"pycomm3 parsing missed entry '{name}'"
        p = pc_by_name[name]

        # system-flagged entries are dropped by daedalus during parse
        if e["symbol_type"] & 0x1000:
            assert name not in d_by_name, f"system-flagged '{name}' should be filtered"
            continue

        assert name in d_by_name, f"daedalus missing entry '{name}'"
        d = d_by_name[name]
        assert d.instance_id == p["instance_id"], f"instance_id mismatch for '{name}'"
        pc_dims = tuple(x for x in p["dimensions"] if x)
        assert d.dimensions == pc_dims, (
            f"dimensions mismatch for '{name}': daedalus={d.dimensions} pycomm3={pc_dims}"
        )

    # I/O tag must pass the _is_system_tag filter (io_tag shields the colon rule)
    assert "LocalIO:I" in d_by_name, "I/O tag 'LocalIO:I' should be kept by _parse_tag_list_reply"
    assert not _is_system_tag("LocalIO:I", _ST_DINT_SCALAR), (
        "I/O tag should not be filtered by _is_system_tag"
    )
