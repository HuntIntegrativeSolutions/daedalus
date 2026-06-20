"""Unit tests for tag-list helpers and LogixDriver.get_tag_list — no sockets.

All payloads are built synthetically; the driver's send_recv is stubbed with
pre-baked reply frames so the full parse path is exercised without a sim server.
"""

from __future__ import annotations

import struct
from collections.abc import Callable
from typing import Any

import pytest

from daedalus.cip.data_types import STRING
from daedalus.cip.segments import PADDED_EPATH, DataSegment, LogicalSegment
from daedalus.drivers import LogixDriver
from daedalus.drivers._logix import (
    _build_tag_list_request,
    _is_system_tag,
    _parse_tag_list_reply,
)
from daedalus.exceptions import DataError, ResponseError
from daedalus.packets.encap import CPFItem, CPFTypeCode, EncapsulationHeader, build_cpf
from daedalus.session import Session

# ---------------------------------------------------------------------------
# Wire-format helpers
# ---------------------------------------------------------------------------

_SESSION_HANDLE = 0x5678
_OT_CONN_ID = 0xCAFEBABE

# Symbol-type constants
_DINT_CODE = 0xC4
_ST_DINT_SCALAR = 0x00C4  # atomic DINT, scalar
_ST_DINT_1D = 0x20C4  # 1D (bits 14:13 = 01) DINT array
_ST_DINT_2D = 0x40C4  # 2D (bits 14:13 = 10) DINT array
_ST_STRUCT = 0x8123  # struct, template_instance_id = 0x123
_ST_SYSTEM = 0x10C4  # system-flagged (bit 12 set)


def _make_tag_list_payload(entries: list[dict[str, Any]]) -> bytes:
    """Serialize a list of symbol entries to a tag-list reply payload.

    Wire format per entry:
        UDINT  instance_id
        STRING name  (UINT length + bytes, NO pad)
        UINT   symbol_type
        UDINT  0, 0, 0  (address fields)
        UDINT  dim1, dim2, dim3
    """
    buf = b""
    for e in entries:
        dims: tuple[int, int, int] = e.get("dims", (0, 0, 0))
        buf += struct.pack("<I", e["instance_id"])
        buf += STRING.encode(e["name"])
        buf += struct.pack("<H", e["symbol_type"])
        buf += struct.pack("<III", 0, 0, 0)  # address fields (ignored)
        buf += struct.pack("<III", dims[0], dims[1], dims[2])
    return buf


def _make_tag_list_cip_reply(payload: bytes, status: int = 0x00) -> bytes:
    """Build a CIP Get Instance Attribute List reply (service byte 0xD5)."""
    return bytes([0xD5, 0x00, status, 0x00]) + payload


def _make_connected_frame(cip_payload: bytes, seq: int = 1) -> bytes:
    """Wrap a CIP payload in a full SendUnitData reply frame."""
    connected_data = struct.pack("<H", seq) + cip_payload
    cpf = (
        b"\x00\x00\x00\x00"
        + b"\x00\x00"
        + build_cpf(
            [
                CPFItem(CPFTypeCode.CONNECTED_ADDRESS, struct.pack("<I", _OT_CONN_ID)),
                CPFItem(CPFTypeCode.CONNECTED_DATA, connected_data),
            ]
        )
    )
    header = EncapsulationHeader(
        command=0x70,
        length=len(cpf),
        session_handle=_SESSION_HANDLE,
        status=0,
        sender_context=b"\x00" * 8,
        options=0,
    )
    return header.encode() + cpf


def _stub(frames: list[bytes]) -> Callable[[bytes], bytes]:
    """Return a send_recv stub that pops frames in order."""
    it = iter(frames)

    def _inner(_sent: bytes) -> bytes:
        return next(it)

    return _inner


def _make_connected_session() -> Session:
    """Return a Session in CONNECTED state via synthetic reply bytes."""
    from daedalus.cip.services import ConnectionManagerService

    s = Session()
    s.register_request()
    reg_header = EncapsulationHeader(
        command=0x65,
        length=4,
        session_handle=_SESSION_HANDLE,
        status=0,
        sender_context=b"\x00" * 8,
        options=0,
    )
    s.register_reply(reg_header.encode() + b"\x01\x00\x00\x00")

    s.forward_open_request(large=False)
    fo_payload = struct.pack(
        "<IIHHIIIBB",
        _OT_CONN_ID,
        0x71190427,
        0x0427,
        0x1009,
        0x71191009,
        0x00204001,
        0x00204001,
        0,
        0,
    )
    svc = int(ConnectionManagerService.FORWARD_OPEN)
    cip_reply = bytes([svc | 0x80, 0x00, 0x00, 0x00]) + fo_payload
    fo_cpf = (
        b"\x00\x00\x00\x00"
        + b"\x00\x00"
        + build_cpf(
            [
                CPFItem(CPFTypeCode.NULL_ADDRESS),
                CPFItem(CPFTypeCode.UNCONNECTED_DATA, cip_reply),
            ]
        )
    )
    fo_header = EncapsulationHeader.for_command(
        0x6F, data_length=len(fo_cpf), session_handle=_SESSION_HANDLE
    )
    s.forward_open_reply(fo_header.encode() + fo_cpf)
    return s


# ---------------------------------------------------------------------------
# Tests: _parse_tag_list_reply
# ---------------------------------------------------------------------------


def test_parse_scalar_dint():
    entries = [{"instance_id": 1, "name": "MyDINT", "symbol_type": _ST_DINT_SCALAR}]
    payload = _make_tag_list_payload(entries)
    tags, _programs, last = _parse_tag_list_reply(payload, "controller")
    assert len(tags) == 1
    t = tags[0]
    assert t.tag_name == "MyDINT"
    assert t.instance_id == 1
    assert t.is_struct is False
    assert t.data_type == "DINT"
    assert t.template_instance_id is None
    assert t.dimensions == ()
    assert t.scope == "controller"
    assert last == 1


def test_parse_1d_array():
    entries = [{"instance_id": 2, "name": "MyArr", "symbol_type": _ST_DINT_1D, "dims": (10, 0, 0)}]
    payload = _make_tag_list_payload(entries)
    tags, _, _ = _parse_tag_list_reply(payload, "controller")
    assert tags[0].dimensions == (10,)


def test_parse_2d_array():
    entries = [{"instance_id": 3, "name": "My2D", "symbol_type": _ST_DINT_2D, "dims": (4, 5, 0)}]
    payload = _make_tag_list_payload(entries)
    tags, _, _ = _parse_tag_list_reply(payload, "controller")
    assert tags[0].dimensions == (4, 5)


def test_parse_struct():
    entries = [{"instance_id": 4, "name": "MyUDT", "symbol_type": _ST_STRUCT}]
    payload = _make_tag_list_payload(entries)
    tags, _, _ = _parse_tag_list_reply(payload, "controller")
    t = tags[0]
    assert t.is_struct is True
    assert t.data_type is None
    assert t.template_instance_id == 0x123


def test_program_scope_entry_not_in_user_tags():
    entries = [
        {"instance_id": 1, "name": "MyDINT", "symbol_type": _ST_DINT_SCALAR},
        {"instance_id": 2, "name": "Program:Main", "symbol_type": 0x0000},
    ]
    payload = _make_tag_list_payload(entries)
    tags, programs, _ = _parse_tag_list_reply(payload, "controller")
    assert len(tags) == 1
    assert tags[0].tag_name == "MyDINT"
    assert programs == ["Program:Main"]


def test_odd_length_name_parses_cleanly():
    """3-char name 'abc' is 5 bytes in STRING encoding; stream must land on symbol_type."""
    # "abc" → STRING.encode → 03 00 61 62 63 (5 bytes, no pad)
    # After consuming: instance_id(4) + STRING(5) = 9 bytes; symbol_type is at byte 9.
    entries = [{"instance_id": 7, "name": "abc", "symbol_type": _ST_DINT_SCALAR}]
    payload = _make_tag_list_payload(entries)
    tags, _, _ = _parse_tag_list_reply(payload, "controller")
    # If a stray pad byte were consumed, symbol_type would be misread and DINT wouldn't match.
    assert tags[0].data_type == "DINT"
    assert tags[0].tag_name == "abc"


# ---------------------------------------------------------------------------
# Tests: _is_system_tag
# ---------------------------------------------------------------------------


def test_system_flag_bit12_filtered():
    assert _is_system_tag("SomeTag", 0x1000) is True


def test_routine_tag_filtered():
    assert _is_system_tag("Routine:Main.Sub", 0x0000) is True


def test_task_tag_filtered():
    assert _is_system_tag("Task:MainTask", 0x0000) is True


def test_double_underscore_filtered():
    assert _is_system_tag("__SysCtrl", 0x0000) is True


def test_map_cxn_filtered():
    assert _is_system_tag("Map:Chassis", 0x0000) is True
    assert _is_system_tag("Cxn:Main", 0x0000) is True


def test_colon_catch_all_filtered():
    # Colon-containing name with NO I/O suffix (:I/:O/:C/:S) → filtered.
    # Note: "Private:Internal" is NOT a good example because it contains ":I"
    # and pycomm3 would treat it as an I/O tag (io_tag=True shields the colon rule).
    assert _is_system_tag("NSE:Hidden", 0x0000) is True
    assert _is_system_tag("Private:Unknown", 0x0000) is True


def test_io_tag_kept():
    # I/O tags containing :I are KEPT (io_tag=True shields the colon rule)
    assert _is_system_tag("LocalIO:I", 0x0000) is False


def test_io_tag_with_colon_kept():
    # :O also shields the colon catch-all
    assert _is_system_tag("Mod:O.Data", 0x0000) is False


def test_plain_user_tag_kept():
    assert _is_system_tag("MyDINT", 0x0000) is False


# ---------------------------------------------------------------------------
# Tests: _build_tag_list_request
# ---------------------------------------------------------------------------


def test_request_service_byte():
    req = _build_tag_list_request(0)
    assert req[0] == 0x55


def test_request_controller_path_has_no_data_segment():
    req = _build_tag_list_request(0)
    path_word_count = req[1]
    path_bytes = req[2 : 2 + path_word_count * 2]
    segments = PADDED_EPATH.decode(path_bytes)
    assert not any(isinstance(s, DataSegment) for s in segments)


def test_request_program_path_has_data_segment():
    req = _build_tag_list_request(0, "Program:Main")
    path_word_count = req[1]
    path_bytes = req[2 : 2 + path_word_count * 2]
    segments = PADDED_EPATH.decode(path_bytes)
    data_segs = [s for s in segments if isinstance(s, DataSegment)]
    assert any(s.data == "Program:Main" for s in data_segs)


def test_request_attr_list():
    req = _build_tag_list_request(0)
    path_word_count = req[1]
    data_start = 2 + path_word_count * 2
    data = req[data_start:]
    # First UINT is the attribute count (6)
    count = struct.unpack_from("<H", data, 0)[0]
    assert count == 6
    # Attribute IDs: 1, 2, 3, 5, 6, 8
    attrs = struct.unpack_from("<HHHHHH", data, 2)
    assert attrs == (1, 2, 3, 5, 6, 8)


def test_request_continuation_instance():
    req = _build_tag_list_request(42)
    path_word_count = req[1]
    path_bytes = req[2 : 2 + path_word_count * 2]
    segments = PADDED_EPATH.decode(path_bytes)
    inst_segs = [
        s for s in segments if isinstance(s, LogicalSegment) and s.logical_type == "instance_id"
    ]
    assert inst_segs[0].logical_value == 42


# ---------------------------------------------------------------------------
# Tests: LogixDriver.get_tag_list (stubbed send_recv)
# ---------------------------------------------------------------------------


def _tag_list_frames(entries_per_reply: list[tuple[list[dict[str, Any]], int]]) -> list[bytes]:
    """Build a list of full SendUnitData reply frames for tag list requests.

    Each item in *entries_per_reply* is (entries, cip_status).
    """
    frames = []
    for entries, status in entries_per_reply:
        payload = _make_tag_list_payload(entries)
        cip_reply = _make_tag_list_cip_reply(payload, status)
        frames.append(_make_connected_frame(cip_reply))
    return frames


def test_driver_continuation():
    """Two-chunk reply (0x06 then 0x00) produces the full combined tag list."""
    chunk1 = [{"instance_id": 0, "name": "TagA", "symbol_type": _ST_DINT_SCALAR}]
    chunk2 = [{"instance_id": 1, "name": "TagB", "symbol_type": _ST_DINT_SCALAR}]
    frames = _tag_list_frames([(chunk1, 0x06), (chunk2, 0x00)])
    session = _make_connected_session()
    driver = LogixDriver(session, _stub(frames))
    tags = driver.get_tag_list()
    names = [t.tag_name for t in tags]
    assert "TagA" in names
    assert "TagB" in names


def test_driver_program_scope_iterated():
    """A 'Program:Main' entry in the controller scan triggers a program-scope scan."""
    ctrl_entries = [
        {"instance_id": 0, "name": "CtrlTag", "symbol_type": _ST_DINT_SCALAR},
        {"instance_id": 1, "name": "Program:Main", "symbol_type": 0x0000},
    ]
    prog_entries = [{"instance_id": 0, "name": "LocalCounter", "symbol_type": _ST_DINT_SCALAR}]
    frames = _tag_list_frames([(ctrl_entries, 0x00), (prog_entries, 0x00)])
    session = _make_connected_session()
    driver = LogixDriver(session, _stub(frames))
    tags = driver.get_tag_list()
    names = [t.tag_name for t in tags]
    assert "CtrlTag" in names
    assert "Program:Main.LocalCounter" in names


def test_driver_error_raises():
    """Non-zero non-partial status raises ResponseError."""
    cip_reply = bytes([0xD5, 0x00, 0x08, 0x00])  # status 0x08 = error
    frames = [_make_connected_frame(cip_reply)]
    session = _make_connected_session()
    driver = LogixDriver(session, _stub(frames))
    with pytest.raises(ResponseError):
        driver.get_tag_list()


def test_truncated_payload_raises():
    """Partial entry in the payload triggers DataError."""
    # Build a valid entry then chop it — ensure we have at least the UDINT instance_id
    # but truncate before the STRING name is complete.
    partial = struct.pack("<I", 99) + b"\x05\x00ab"  # UDINT + UINT(5) + only 2 bytes of name
    with pytest.raises(DataError, match="truncated"):
        _parse_tag_list_reply(partial, "controller")
