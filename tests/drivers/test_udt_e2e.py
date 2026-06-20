"""Offline end-to-end UDT decode tests through SyncTcpTransport + CipSimServer.

Each test builds a sim server with:
  - symbol_store: tag list entries so get_tag_list() populates _tag_info_cache
  - template_store: TemplateEntry objects the driver fetches via GET_ATTRIBUTE_LIST
    then READ_TAG on the Template Object
  - tag_store: struct read payload = reply_handle (2B) + member_data

Flow: get_tag_list() → read_tag("TAG_NAME") → assert decoded dict.
All tests are offline — no real PLC required.
"""

from __future__ import annotations

import struct
from collections.abc import Callable
from typing import Any

import pytest

from daedalus.cip.data_types import DINT, REAL, SINT, UINT
from daedalus.drivers import LogixDriver
from daedalus.session import Session
from daedalus.transport import SyncTcpTransport
from sim.server import CipSimServer, TemplateEntry

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_send_recv(transport: SyncTcpTransport) -> Callable[[bytes], bytes]:
    def _inner(frame: bytes) -> bytes:
        transport.send_frame(frame)
        return transport.recv_frame()

    return _inner


def _open(sim: CipSimServer) -> tuple[Session, SyncTcpTransport, LogixDriver]:
    session = Session()
    transport = SyncTcpTransport(sim.host, sim.port)
    transport.connect()
    transport.send_frame(session.register_request())
    session.register_reply(transport.recv_frame())
    transport.send_frame(session.forward_open_request(large=False))
    session.forward_open_reply(transport.recv_frame())
    return session, transport, LogixDriver(session, _make_send_recv(transport))


def _close(session: Session, transport: SyncTcpTransport) -> None:
    transport.send_frame(session.forward_close_request())
    session.forward_close_reply(transport.recv_frame())
    transport.send_frame(session.unregister_request())
    transport.close()


# Symbol-type constants
_ST_STRUCT_BASE = 0x8000  # bit 15 = struct flag

# Template instance IDs used in tests
_FLAT_INST = 0x200
_ARRAY_INST = 0x201
_BOOL_INST = 0x202
_NESTED_INNER_INST = 0x203
_NESTED_OUTER_INST = 0x204
_STRING_INST = 0x205

# Reply handles (opaque values the sim sends as the first 2 bytes of struct reads)
_HANDLE_FLAT = 0x0010
_HANDLE_ARRAY = 0x0011
_HANDLE_BOOL = 0x0012
_HANDLE_NESTED = 0x0013
_HANDLE_STRING = 0x0014


def _symbol_entry(
    name: str,
    instance_id: int,
    template_instance: int,
    scope: str = "controller",
) -> dict[str, Any]:
    """Build a symbol store entry for a struct tag."""
    return {
        "name": name,
        "instance_id": instance_id,
        "symbol_type": _ST_STRUCT_BASE | (template_instance & 0x0FFF),
        "dims": (0, 0, 0),
    }


def _make_member_info(type_info: int, typ: int, offset: int) -> bytes:
    return struct.pack("<HHI", type_info, typ, offset)


def _make_names_blob(template_name_with_semi: str, member_names: list[str]) -> bytes:
    parts = [template_name_with_semi.encode("ascii")] + [n.encode("ascii") for n in member_names]
    return b"\x00".join(parts) + b"\x00"


# ---------------------------------------------------------------------------
# Flat UDT: DINT x @0, REAL y @4
# ---------------------------------------------------------------------------


def test_e2e_flat_udt() -> None:
    x_val, y_val = 42, 3.14

    member_info = _make_member_info(0, DINT.code, 0) + _make_member_info(0, REAL.code, 4)
    names = _make_names_blob("FlatUDT;AA", ["x", "y"])
    tmpl_data = member_info + names

    member_data = DINT.encode(x_val) + REAL.encode(y_val)
    reply_payload = struct.pack("<H", _HANDLE_FLAT) + member_data

    sim = CipSimServer(
        symbol_store={"controller": [_symbol_entry("FlatTag", 1, _FLAT_INST)]},
        template_store={
            _FLAT_INST: TemplateEntry(
                template_data=tmpl_data,
                structure_size=8,
                member_count=2,
                structure_handle=_HANDLE_FLAT,
            )
        },
        tag_store={"FlatTag": (0x02A0, reply_payload)},
    )
    sim.start()
    session, transport, driver = _open(sim)
    try:
        driver.get_tag_list()
        tag = driver.read_tag("FlatTag")
        assert tag.type == "FlatUDT"
        assert isinstance(tag.value, dict)
        assert tag.value["x"] == x_val
        assert abs(tag.value["y"] - y_val) < 1e-4
    finally:
        _close(session, transport)
        sim.stop()


# ---------------------------------------------------------------------------
# Array member: SINT[4] arr @0
# ---------------------------------------------------------------------------


def test_e2e_array_member_udt() -> None:
    member_info = _make_member_info(4, SINT.code, 0)  # type_info=4 → array of 4
    names = _make_names_blob("ArrUDT;BB", ["arr"])
    tmpl_data = member_info + names

    arr_data = bytes([10, 20, 30, 40])
    reply_payload = struct.pack("<H", _HANDLE_ARRAY) + arr_data

    sim = CipSimServer(
        symbol_store={"controller": [_symbol_entry("ArrTag", 2, _ARRAY_INST)]},
        template_store={
            _ARRAY_INST: TemplateEntry(
                template_data=tmpl_data,
                structure_size=4,
                member_count=1,
                structure_handle=_HANDLE_ARRAY,
            )
        },
        tag_store={"ArrTag": (0x02A0, reply_payload)},
    )
    sim.start()
    session, transport, driver = _open(sim)
    try:
        driver.get_tag_list()
        tag = driver.read_tag("ArrTag")
        assert tag.type == "ArrUDT"
        assert tag.value["arr"] == [10, 20, 30, 40]
    finally:
        _close(session, transport)
        sim.stop()


# ---------------------------------------------------------------------------
# BOOL bit members (private host DWORD + two BOOL aliases)
# ---------------------------------------------------------------------------


def test_e2e_bool_bit_members() -> None:
    from daedalus.cip.data_types import BOOL, DWORD

    # Private DWORD host (ZZZZZZZZZZ prefix) + two visible BOOL aliases
    member_info = (
        _make_member_info(0, DWORD.code, 0)  # host word
        + _make_member_info(0, BOOL.code, 0)  # b0 at bit 0
        + _make_member_info(1, BOOL.code, 0)  # b1 at bit 1
    )
    # Private host name + two user names
    tmpl_data = member_info + _make_names_blob("BoolUDT;CC", ["ZZZZZZZZZZ_host", "b0", "b1"])

    # host word value = 0b01 (b0=True, b1=False)
    host_word = struct.pack("<I", 0b01)
    reply_payload = struct.pack("<H", _HANDLE_BOOL) + host_word

    sim = CipSimServer(
        symbol_store={"controller": [_symbol_entry("BoolTag", 3, _BOOL_INST)]},
        template_store={
            _BOOL_INST: TemplateEntry(
                template_data=tmpl_data,
                structure_size=4,
                member_count=3,
                structure_handle=_HANDLE_BOOL,
            )
        },
        tag_store={"BoolTag": (0x02A0, reply_payload)},
    )
    sim.start()
    session, transport, driver = _open(sim)
    try:
        driver.get_tag_list()
        tag = driver.read_tag("BoolTag")
        assert tag.type == "BoolUDT"
        assert "ZZZZZZZZZZ_host" not in tag.value
        assert tag.value["b0"] is True
        assert tag.value["b1"] is False
    finally:
        _close(session, transport)
        sim.stop()


# ---------------------------------------------------------------------------
# Nested UDT: outer has UINT counter + InnerUDT inner
# ---------------------------------------------------------------------------


def test_e2e_nested_udt() -> None:
    # Inner template: DINT x @0, REAL y @4
    inner_info = _make_member_info(0, DINT.code, 0) + _make_member_info(0, REAL.code, 4)
    inner_names = _make_names_blob("InnerUDT;DD", ["x", "y"])
    inner_tmpl_data = inner_info + inner_names

    # Outer template: UINT counter @0, InnerUDT inner @2
    # Inner struct ref: type_info=0, typ = 0x8000 | _NESTED_INNER_INST (struct type code)
    inner_type_code = 0x8000 | _NESTED_INNER_INST
    outer_info = _make_member_info(0, UINT.code, 0) + _make_member_info(0, inner_type_code, 2)
    outer_names = _make_names_blob("OuterUDT;EE", ["counter", "inner"])
    outer_tmpl_data = outer_info + outer_names

    counter_val = 7
    inner_data = DINT.encode(42) + REAL.encode(1.5)
    outer_data = UINT.encode(counter_val) + inner_data
    reply_payload = struct.pack("<H", _HANDLE_NESTED) + outer_data

    sim = CipSimServer(
        symbol_store={"controller": [_symbol_entry("NestedTag", 4, _NESTED_OUTER_INST)]},
        template_store={
            _NESTED_INNER_INST: TemplateEntry(
                template_data=inner_tmpl_data,
                structure_size=8,
                member_count=2,
                structure_handle=0xAA,
            ),
            _NESTED_OUTER_INST: TemplateEntry(
                template_data=outer_tmpl_data,
                structure_size=10,
                member_count=2,
                structure_handle=_HANDLE_NESTED,
            ),
        },
        tag_store={"NestedTag": (0x02A0, reply_payload)},
    )
    sim.start()
    session, transport, driver = _open(sim)
    try:
        driver.get_tag_list()
        tag = driver.read_tag("NestedTag")
        assert tag.type == "OuterUDT"
        assert tag.value["counter"] == counter_val
        assert tag.value["inner"]["x"] == 42
        assert abs(tag.value["inner"]["y"] - 1.5) < 1e-4
    finally:
        _close(session, transport)
        sim.stop()


# ---------------------------------------------------------------------------
# String struct (LEN + DATA SINT[82])
# ---------------------------------------------------------------------------


def test_e2e_string_udt() -> None:
    text = "hello"
    member_info = _make_member_info(0, DINT.code, 0) + _make_member_info(82, SINT.code, 4)
    tmpl_data = member_info + _make_names_blob("STRING;FF", ["LEN", "DATA"])

    len_bytes = DINT.encode(len(text))
    data_bytes = text.encode("ascii") + b"\x00" * (82 - len(text))
    member_data = len_bytes + data_bytes
    reply_payload = struct.pack("<H", _HANDLE_STRING) + member_data

    sim = CipSimServer(
        symbol_store={"controller": [_symbol_entry("StrTag", 5, _STRING_INST)]},
        template_store={
            _STRING_INST: TemplateEntry(
                template_data=tmpl_data,
                structure_size=86,
                member_count=2,
                structure_handle=_HANDLE_STRING,
            )
        },
        tag_store={"StrTag": (0x02A0, reply_payload)},
    )
    sim.start()
    session, transport, driver = _open(sim)
    try:
        driver.get_tag_list()
        tag = driver.read_tag("StrTag")
        assert tag.type == "STRING"
        assert tag.value == text
    finally:
        _close(session, transport)
        sim.stop()


# ---------------------------------------------------------------------------
# Large template forcing continuation (template_frag_threshold)
# ---------------------------------------------------------------------------


def test_e2e_large_template_continuation() -> None:
    """Template data larger than template_frag_threshold forces 0x06 continuation."""
    # Build a template with many DINT members so the template bytes exceed 50
    n_members = 20
    member_info = b"".join(_make_member_info(0, DINT.code, i * 4) for i in range(n_members))
    member_names = [f"f{i}" for i in range(n_members)]
    names = _make_names_blob("BigUDT;GG", member_names)
    tmpl_data = member_info + names

    structure_size = n_members * 4
    member_data = b"".join(DINT.encode(i) for i in range(n_members))
    reply_payload = struct.pack("<H", 0x0020) + member_data

    large_inst = 0x210
    sim = CipSimServer(
        symbol_store={"controller": [_symbol_entry("BigTag", 6, large_inst)]},
        template_store={
            large_inst: TemplateEntry(
                template_data=tmpl_data,
                structure_size=structure_size,
                member_count=n_members,
                structure_handle=0x0020,
            )
        },
        tag_store={"BigTag": (0x02A0, reply_payload)},
        template_frag_threshold=50,  # force continuation mid-template
    )
    sim.start()
    session, transport, driver = _open(sim)
    try:
        driver.get_tag_list()
        tag = driver.read_tag("BigTag")
        assert tag.type == "BigUDT"
        assert isinstance(tag.value, dict)
        assert len(tag.value) == n_members
        for i, key in enumerate(member_names):
            assert tag.value[key] == i
    finally:
        _close(session, transport)
        sim.stop()


# ---------------------------------------------------------------------------
# Graceful fallback without get_tag_list()
# ---------------------------------------------------------------------------


def test_e2e_struct_survives_without_tag_list() -> None:
    """read_tag on a struct without prior get_tag_list returns raw bytes, no crash."""
    reply_payload = struct.pack("<H", 0x1234) + b"\xde\xad\xbe\xef"
    sim = CipSimServer(tag_store={"MyTag": (0x02A0, reply_payload)})
    sim.start()
    session, transport, driver = _open(sim)
    try:
        tag = driver.read_tag("MyTag")
        # No template info → raw bytes fallback, no exception
        assert tag.type == "STRUCT"
        assert isinstance(tag.value, bytes)
    finally:
        _close(session, transport)
        sim.stop()


# ---------------------------------------------------------------------------
# MSP (read_tags) with struct tag
# ---------------------------------------------------------------------------


def test_e2e_read_tags_msp_with_struct() -> None:
    """read_tags decodes struct tags when template is cached."""
    member_info = _make_member_info(0, DINT.code, 0)
    names = _make_names_blob("MspUDT;HH", ["val"])
    tmpl_data = member_info + names

    member_data = DINT.encode(99)
    reply_payload = struct.pack("<H", 0x0030) + member_data

    msp_inst = 0x211
    sim = CipSimServer(
        symbol_store={"controller": [_symbol_entry("UdtTag", 7, msp_inst)]},
        template_store={
            msp_inst: TemplateEntry(
                template_data=tmpl_data,
                structure_size=4,
                member_count=1,
                structure_handle=0x0030,
            )
        },
        tag_store={
            "UdtTag": (0x02A0, reply_payload),
            "ScalarTag": (DINT.code, DINT.encode(77)),
        },
    )
    sim.start()
    session, transport, driver = _open(sim)
    try:
        driver.get_tag_list()
        tags = driver.read_tags(["UdtTag", "ScalarTag"])
        assert tags[0].type == "MspUDT"
        assert tags[0].value["val"] == 99
        assert tags[1].value == 77
    finally:
        _close(session, transport)
        sim.stop()


# ---------------------------------------------------------------------------
# array-of-struct raises DataError (not silent mis-decode)
# ---------------------------------------------------------------------------


def test_array_of_struct_raises() -> None:
    """element_count > 1 on a struct tag must raise DataError, not silently corrupt."""
    from daedalus.drivers._logix import _decode_read_reply
    from daedalus.exceptions import DataError

    reply_handle = b"\x01\x00"
    member_data = b"\xde\xad\xbe\xef"
    payload = struct.pack("<H", 0x02A0) + reply_handle + member_data
    with pytest.raises(DataError, match="array-of-struct"):
        _decode_read_reply("S", payload, element_count=2)
