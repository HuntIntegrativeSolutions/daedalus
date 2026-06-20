"""Unit tests for LogixDriver and helpers — no sockets.

All replies are built synthetically via helpers that wrap CIP bytes in a full
SendUnitData frame.  The send_recv callable is a stub that returns pre-baked
reply frames so the driver exercises the full parse path without a sim server.
"""

from __future__ import annotations

import struct
from collections.abc import Callable

import pytest

from daedalus.cip.data_types import DINT, REAL, SINT
from daedalus.drivers import LogixDriver
from daedalus.drivers._logix import _decode_read_reply, _parse_msp_reply
from daedalus.exceptions import DataError, ResponseError
from daedalus.packets.encap import CPFItem, CPFTypeCode, EncapsulationHeader, build_cpf
from daedalus.session import Session
from daedalus.tag import Tag

# ---------------------------------------------------------------------------
# Frame / reply helpers
# ---------------------------------------------------------------------------

_SESSION_HANDLE = 0x1234
_OT_CONN_ID = 0xDEADBEEF


def _make_connected_reply(cip_payload: bytes, seq: int = 0) -> bytes:
    """Wrap a CIP payload in a full SendUnitData reply frame."""
    connected_data = struct.pack("<H", seq) + cip_payload
    cpf = (
        b"\x00\x00\x00\x00"  # interface handle
        + b"\x00\x00"  # timeout
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


def _make_read_reply(type_code: int, data: bytes, status: int = 0x00) -> bytes:
    """Build a CIP READ_TAG reply payload (status header + type_code + data)."""
    return bytes([0x4C | 0x80, 0x00, status, 0x00]) + struct.pack("<H", type_code) + data


def _stub(frames: list[bytes]) -> Callable[[bytes], bytes]:
    """Return a send_recv stub that pops from *frames* in order."""
    it = iter(frames)

    def _inner(_sent: bytes) -> bytes:
        return next(it)

    return _inner


def _make_session() -> Session:
    """Return a Session wired into CONNECTED state using synthetic bytes."""
    from daedalus.cip.services import ConnectionManagerService
    from daedalus.packets.encap import CPFItem, CPFTypeCode, EncapsulationHeader, build_cpf

    s = Session()
    # Register
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

    # Forward_Open
    s.forward_open_request(large=False)
    fo_payload = struct.pack(
        "<IIHHIIIBB",
        _OT_CONN_ID,  # ot_connection_id
        0x71190427,  # to_connection_id
        0x0427,  # connection_serial
        0x1009,  # originator_vendor_id
        0x71191009,  # originator_serial
        0x00204001,  # ot_actual_api
        0x00204001,  # to_actual_api
        0,  # app_reply_size
        0,  # reserved
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
# Tag result type tests
# ---------------------------------------------------------------------------


def test_tag_type_dint() -> None:
    tag = Tag("x", 1, DINT.code)
    assert tag.type == "DINT"


def test_tag_type_real() -> None:
    tag = Tag("x", 1.0, REAL.code)
    assert tag.type == "REAL"


def test_tag_type_struct() -> None:
    tag = Tag("x", b"\x00", 0x02A0)
    assert tag.type == "STRUCT"


def test_tag_type_unknown() -> None:
    tag = Tag("x", None, 0x9999)
    assert tag.type is None


def test_tag_pycomm3_props() -> None:
    tag = Tag("MyTag", 42, DINT.code)
    assert tag.value == 42
    assert tag.error is None
    assert tag.type == "DINT"


def test_tag_pylogix_props() -> None:
    tag = Tag("MyTag", 42, DINT.code, status=0)
    assert tag.TagName == "MyTag"
    assert tag.Value == 42
    assert tag.Status == 0


def test_tag_error_status() -> None:
    tag = Tag("X", None, 0, status=0x08, error="bad")
    assert tag.error == "bad"
    assert tag.Status == 0x08


# ---------------------------------------------------------------------------
# _decode_read_reply unit tests
# ---------------------------------------------------------------------------


def test_decode_dint() -> None:
    payload = struct.pack("<H", DINT.code) + DINT.encode(12345)
    tag = _decode_read_reply("T", payload, 1)
    assert tag.value == 12345
    assert tag.type_code == DINT.code


def test_decode_real() -> None:
    payload = struct.pack("<H", REAL.code) + REAL.encode(3.14)
    tag = _decode_read_reply("T", payload, 1)
    assert abs(tag.value - 3.14) < 1e-5


def test_decode_array() -> None:
    data = b"".join(DINT.encode(i) for i in range(3))
    payload = struct.pack("<H", DINT.code) + data
    tag = _decode_read_reply("T", payload, 3)
    assert tag.value == [0, 1, 2]


def test_decode_struct() -> None:
    # 2-byte struct handle + payload data
    payload = struct.pack("<H", 0x02A0) + b"\x01\x00" + b"\xde\xad\xbe\xef"
    tag = _decode_read_reply("S", payload, 1)
    assert isinstance(tag.value, bytes)
    assert tag.value == b"\xde\xad\xbe\xef"
    assert tag.type == "STRUCT"


def test_decode_unknown_type_raises() -> None:
    payload = struct.pack("<H", 0x9999) + b"\x00\x00\x00\x00"
    with pytest.raises(DataError, match="Unknown CIP type code"):
        _decode_read_reply("T", payload, 1)


def test_decode_too_short_raises() -> None:
    with pytest.raises(DataError, match="too short"):
        _decode_read_reply("T", b"\x00", 1)


# ---------------------------------------------------------------------------
# _parse_msp_reply unit tests
# ---------------------------------------------------------------------------


def _make_msp_reply_payload(sub_replies: list[bytes]) -> bytes:
    """Build an MSP reply payload from a list of sub-reply byte strings."""
    count = len(sub_replies)
    base = 2 + 2 * count
    pos = 0
    offsets: list[int] = []
    for sub in sub_replies:
        offsets.append(base + pos)
        pos += len(sub)
    return (
        struct.pack("<H", count)
        + b"".join(struct.pack("<H", o) for o in offsets)
        + b"".join(sub_replies)
    )


def test_parse_msp_two_success() -> None:
    sub1 = bytes([0x4C | 0x80, 0x00, 0x00, 0x00]) + struct.pack("<H", DINT.code) + DINT.encode(1)
    sub2 = bytes([0x4C | 0x80, 0x00, 0x00, 0x00]) + struct.pack("<H", REAL.code) + REAL.encode(2.0)
    payload = _make_msp_reply_payload([sub1, sub2])
    tags = _parse_msp_reply(["A", "B"], payload)
    assert len(tags) == 2
    assert tags[0].value == 1
    assert abs(tags[1].value - 2.0) < 1e-5


def test_parse_msp_partial_failure() -> None:
    sub1 = bytes([0x4C | 0x80, 0x00, 0x08, 0x00])  # error
    sub2 = bytes([0x4C | 0x80, 0x00, 0x00, 0x00]) + struct.pack("<H", DINT.code) + DINT.encode(99)
    payload = _make_msp_reply_payload([sub1, sub2])
    tags = _parse_msp_reply(["A", "B"], payload)
    assert tags[0].error is not None
    assert tags[0].value is None
    assert tags[1].value == 99


def test_parse_msp_too_short_raises() -> None:
    with pytest.raises(DataError, match="too short"):
        _parse_msp_reply(["X"], b"\x00")


# ---------------------------------------------------------------------------
# LogixDriver.read_tag unit tests
# ---------------------------------------------------------------------------


def test_driver_read_dint_success() -> None:
    session = _make_session()
    cip_payload = _make_read_reply(DINT.code, DINT.encode(42))
    stub = _stub([_make_connected_reply(cip_payload, seq=1)])
    driver = LogixDriver(session, stub)
    tag = driver.read_tag("MyTag")
    assert tag.value == 42
    assert tag.type == "DINT"
    assert tag.error is None


def test_driver_read_real_success() -> None:
    session = _make_session()
    cip_payload = _make_read_reply(REAL.code, REAL.encode(1.5))
    stub = _stub([_make_connected_reply(cip_payload, seq=1)])
    driver = LogixDriver(session, stub)
    tag = driver.read_tag("R")
    assert abs(tag.value - 1.5) < 1e-5


def test_driver_read_array_success() -> None:
    session = _make_session()
    data = b"".join(SINT.encode(i) for i in range(5))
    cip_payload = _make_read_reply(SINT.code, data)
    stub = _stub([_make_connected_reply(cip_payload, seq=1)])
    driver = LogixDriver(session, stub)
    tag = driver.read_tag("Arr", element_count=5)
    assert tag.value == list(range(5))


def test_driver_read_struct_success() -> None:
    session = _make_session()
    cip_payload = _make_read_reply(0x02A0, b"\x00\x00" + b"\xab\xcd")
    stub = _stub([_make_connected_reply(cip_payload, seq=1)])
    driver = LogixDriver(session, stub)
    tag = driver.read_tag("S")
    assert isinstance(tag.value, bytes)
    assert tag.type == "STRUCT"


def test_driver_read_error_status_raises() -> None:
    session = _make_session()
    cip_payload = bytes([0x4C | 0x80, 0x00, 0x08, 0x00])
    stub = _stub([_make_connected_reply(cip_payload, seq=1)])
    driver = LogixDriver(session, stub)
    with pytest.raises(ResponseError, match="READ_TAG"):
        driver.read_tag("X")


def test_driver_read_unknown_type_raises() -> None:
    session = _make_session()
    cip_payload = _make_read_reply(0x9999, b"\x00\x00\x00\x00")
    stub = _stub([_make_connected_reply(cip_payload, seq=1)])
    driver = LogixDriver(session, stub)
    with pytest.raises(DataError, match="Unknown CIP type code"):
        driver.read_tag("X")


def test_driver_read_fragmented_accumulates() -> None:
    """First reply is status=0x06 (partial), second and third are also 0x06, fourth is 0x00."""
    session = _make_session()
    # value = 6 DINTs = 24 bytes; split into chunks of 8
    full_value = b"".join(DINT.encode(i) for i in range(6))
    type_code = DINT.code
    type_prefix = struct.pack("<H", type_code)

    # First reply: status 0x06 + type_prefix + first 8 bytes
    r1 = _make_connected_reply(
        bytes([0x4C | 0x80, 0x00, 0x06, 0x00]) + type_prefix + full_value[:8],
        seq=1,
    )
    # Continuation reply: status 0x06 + next 8 bytes (no type prefix)
    r2 = _make_connected_reply(
        bytes([0x52 | 0x80, 0x00, 0x06, 0x00]) + full_value[8:16],
        seq=2,
    )
    # Final reply: status 0x00 + last 8 bytes (no type prefix)
    r3 = _make_connected_reply(
        bytes([0x52 | 0x80, 0x00, 0x00, 0x00]) + full_value[16:],
        seq=3,
    )
    stub = _stub([r1, r2, r3])
    driver = LogixDriver(session, stub)
    tag = driver.read_tag("BigTag", element_count=6)
    assert tag.value == list(range(6))


# ---------------------------------------------------------------------------
# LogixDriver.read_tags (MSP) unit tests
# ---------------------------------------------------------------------------


def test_driver_read_tags_success() -> None:
    session = _make_session()
    sub1 = bytes([0x4C | 0x80, 0x00, 0x00, 0x00]) + struct.pack("<H", DINT.code) + DINT.encode(10)
    sub2 = bytes([0x4C | 0x80, 0x00, 0x00, 0x00]) + struct.pack("<H", REAL.code) + REAL.encode(2.5)
    msp_payload = _make_msp_reply_payload([sub1, sub2])
    cip_payload = bytes([0x0A | 0x80, 0x00, 0x00, 0x00]) + msp_payload
    stub = _stub([_make_connected_reply(cip_payload, seq=1)])
    driver = LogixDriver(session, stub)
    tags = driver.read_tags(["A", "B"])
    assert len(tags) == 2
    assert tags[0].value == 10
    assert abs(tags[1].value - 2.5) < 1e-5


def test_driver_read_tags_empty() -> None:
    session = _make_session()
    driver = LogixDriver(session, lambda _: b"")
    assert driver.read_tags([]) == []


def test_driver_read_tags_msp_outer_error_raises() -> None:
    session = _make_session()
    cip_payload = bytes([0x0A | 0x80, 0x00, 0x08, 0x00])  # outer MSP failure
    stub = _stub([_make_connected_reply(cip_payload, seq=1)])
    driver = LogixDriver(session, stub)
    with pytest.raises(ResponseError, match="MSP failed"):
        driver.read_tags(["X"])
