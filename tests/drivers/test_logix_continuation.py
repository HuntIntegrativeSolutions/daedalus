"""Regression tests for unbounded continuation-loop DoS fixes.

Covers three loops that previously looped forever when a device sent
_PARTIAL_TRANSFER (0x06) with an empty payload or with no forward progress:

  - LogixDriver._read_tag_fragmented  (fragmented READ_TAG)
  - LogixDriver._get_scope_tag_list   (Symbol Object enumeration)
  - LogixDriver._fetch_template_data  (Template Object data fetch)

All tests are pure unit tests: send_recv is a stub, no sim server needed.
"""

from __future__ import annotations

import struct
from collections.abc import Callable, Iterator

import pytest

import daedalus.drivers._logix as _logix_mod
from daedalus.cip.data_types import DINT
from daedalus.cip.services import CIPService
from daedalus.drivers import LogixDriver
from daedalus.exceptions import DataError
from daedalus.packets.encap import CPFItem, CPFTypeCode, EncapsulationHeader, build_cpf
from daedalus.session import Session

# ---------------------------------------------------------------------------
# Frame helpers (mirrors test_logix_driver.py pattern)
# ---------------------------------------------------------------------------

_SESSION_HANDLE = 0xABCD
_OT_CONN_ID = 0x12345678


def _make_connected_reply(cip_payload: bytes, seq: int = 0) -> bytes:
    """Wrap *cip_payload* in a full SendUnitData reply frame."""
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


def _cip_reply(service_code: int, status: int, payload: bytes = b"") -> bytes:
    """Build a minimal CIP reply (4-byte header + payload)."""
    return bytes([service_code | 0x80, 0x00, status, 0x00]) + payload


def _read_tag_reply(type_code: int, data: bytes, status: int = 0x00) -> bytes:
    """CIP READ_TAG reply with type_code prefix (for initial partial replies)."""
    return _cip_reply(int(CIPService.READ_TAG), status, struct.pack("<H", type_code) + data)


def _frag_reply(data: bytes, status: int) -> bytes:
    """CIP READ_TAG_FRAGMENTED reply (no type prefix in continuation replies)."""
    return _cip_reply(int(CIPService.READ_TAG_FRAGMENTED), status, data)


def _taglist_reply(payload: bytes, status: int) -> bytes:
    """CIP GET_INSTANCE_ATTRIBUTE_LIST (0x55) reply."""
    return _cip_reply(0x55, status, payload)


def _template_data_reply(data: bytes, status: int) -> bytes:
    """CIP READ_TAG reply on Template Object (same service code 0x4C)."""
    return _cip_reply(int(CIPService.READ_TAG), status, data)


def _stub(frames: list[bytes]) -> Callable[[bytes], bytes]:
    """send_recv stub that returns frames in order; raises if exhausted."""
    it: Iterator[bytes] = iter(frames)

    def _inner(_sent: bytes) -> bytes:
        return next(it)

    return _inner


def _make_session() -> Session:
    """Return a Session in CONNECTED state using synthetic bytes."""
    from daedalus.cip.services import ConnectionManagerService

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


def _make_driver(frames: list[bytes]) -> LogixDriver:
    return LogixDriver(_make_session(), _stub(frames))


# ---------------------------------------------------------------------------
# _read_tag_fragmented: normal multi-fragment path
# ---------------------------------------------------------------------------


def test_fragmented_read_three_chunks_succeeds() -> None:
    """Normal fragmented read: partial, partial, success → correct Tag value."""
    # DINT 12345 = 0x00003039, bytes: 39 30 00 00
    chunk1 = b"\x39\x30"  # first 2 bytes (initial partial)
    chunk2 = b"\x00"  # third byte (continuation partial)
    chunk3 = b"\x00"  # fourth byte (continuation success)

    frames = [
        _make_connected_reply(_read_tag_reply(DINT.code, chunk1, status=0x06)),
        _make_connected_reply(_frag_reply(chunk2, status=0x06)),
        _make_connected_reply(_frag_reply(chunk3, status=0x00)),
    ]
    driver = _make_driver(frames)
    tag = driver.read_tag("MyDINT")
    assert tag.value == 12345
    assert tag.error is None


# ---------------------------------------------------------------------------
# _read_tag_fragmented: empty-partial raises immediately
# ---------------------------------------------------------------------------


def test_fragmented_read_empty_partial_raises() -> None:
    """_PARTIAL_TRANSFER with empty payload → DataError; no repeated request sent."""
    sent_frames: list[bytes] = []

    def _recording_stub(frame: bytes) -> bytes:
        sent_frames.append(frame)
        replies = [
            _make_connected_reply(_read_tag_reply(DINT.code, b"\x39\x30", status=0x06)),
            _make_connected_reply(_frag_reply(b"", status=0x06)),  # empty payload
        ]
        return replies[len(sent_frames) - 1]

    driver = LogixDriver(_make_session(), _recording_stub)
    with pytest.raises(DataError, match="empty payload"):
        driver.read_tag("MyDINT")

    # Only 2 frames were sent (initial + one empty-partial); loop did not continue
    assert len(sent_frames) == 2


# ---------------------------------------------------------------------------
# _read_tag_fragmented: fragment cap exceeded
# ---------------------------------------------------------------------------


def test_fragmented_read_cap_exceeded_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fragment cap exceeded → DataError; monkeypatched to cap=2 for speed."""
    monkeypatch.setattr(_logix_mod, "_MAX_CONTINUATION_FRAGMENTS", 2)

    # 3 continuation partials (> cap=2), each with a real byte
    frames = [
        _make_connected_reply(_read_tag_reply(DINT.code, b"\x01", status=0x06)),
        _make_connected_reply(_frag_reply(b"\x02", status=0x06)),
        _make_connected_reply(_frag_reply(b"\x03", status=0x06)),
        _make_connected_reply(_frag_reply(b"\x04", status=0x06)),
    ]
    driver = _make_driver(frames)
    with pytest.raises(DataError, match="continuation fragments"):
        driver.read_tag("MyDINT")


# ---------------------------------------------------------------------------
# _get_scope_tag_list: no-forward-progress raises
# ---------------------------------------------------------------------------


def test_tag_list_no_progress_raises() -> None:
    """_PARTIAL_TRANSFER where last_instance < requested instance → DataError."""
    # Empty payload → _parse_tag_list_reply returns last_instance=0.
    # First iteration: instance=0, last_instance=0 → advance to 1 (still valid).
    # Second iteration: instance=1, last_instance=0 < 1 → DataError.
    empty_partial = _make_connected_reply(_taglist_reply(b"", status=0x06))
    driver = _make_driver([empty_partial, empty_partial])
    with pytest.raises(DataError, match="no forward progress"):
        driver.get_tag_list()


def test_tag_list_cap_exceeded_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tag-list iteration cap → DataError when too many partials arrive."""
    monkeypatch.setattr(_logix_mod, "_MAX_CONTINUATION_FRAGMENTS", 2)

    # Build a minimal one-entry tag-list payload (instance_id=N, name, type, ...)
    # to ensure last_instance advances, so only the cap guard triggers.
    def _one_entry(instance_id: int) -> bytes:
        name = b"T"
        name_bytes = struct.pack("<H", len(name)) + name
        symbol_type = struct.pack("<H", 0x00C4)  # DINT
        dims = struct.pack("<III", 0, 0, 0)
        ignored = struct.pack("<III", 0, 0, 0)
        return struct.pack("<I", instance_id) + name_bytes + symbol_type + ignored + dims

    frames = [
        _make_connected_reply(_taglist_reply(_one_entry(1), status=0x06)),
        _make_connected_reply(_taglist_reply(_one_entry(2), status=0x06)),
        _make_connected_reply(_taglist_reply(_one_entry(3), status=0x06)),  # iteration 3 > cap=2
    ]
    driver = _make_driver(frames)
    with pytest.raises(DataError, match="continuation iterations"):
        driver.get_tag_list()


# ---------------------------------------------------------------------------
# _fetch_template_data: empty-partial raises immediately
# ---------------------------------------------------------------------------


def test_template_data_empty_partial_raises() -> None:
    """Template fetch: _PARTIAL_TRANSFER with empty payload → DataError."""
    frames = [
        _make_connected_reply(_template_data_reply(b"", status=0x06)),
    ]
    driver = _make_driver(frames)
    with pytest.raises(DataError, match="empty payload"):
        driver._fetch_template_data(instance_id=0x100, object_definition_size=10)


def test_template_data_normal_path_succeeds() -> None:
    """Template fetch: partial then success → accumulated bytes returned."""
    chunk1 = b"\xaa\xbb"
    chunk2 = b"\xcc\xdd"
    frames = [
        _make_connected_reply(_template_data_reply(chunk1, status=0x06)),
        _make_connected_reply(_template_data_reply(chunk2, status=0x00)),
    ]
    driver = _make_driver(frames)
    # object_definition_size=10 → bytes_to_read = (10*4)-21-offset; always ≥ 0 in this test
    result = driver._fetch_template_data(instance_id=0x100, object_definition_size=10)
    assert result == chunk1 + chunk2


def test_template_data_cap_exceeded_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """Template fetch: fragment cap → DataError."""
    monkeypatch.setattr(_logix_mod, "_MAX_CONTINUATION_FRAGMENTS", 1)

    frames = [
        _make_connected_reply(_template_data_reply(b"\x01", status=0x06)),
        _make_connected_reply(_template_data_reply(b"\x02", status=0x06)),  # fragment 2 > cap=1
        _make_connected_reply(_template_data_reply(b"\x03", status=0x00)),
    ]
    driver = _make_driver(frames)
    # object_definition_size=10 → bytes_to_read stays positive across both fragments
    with pytest.raises(DataError, match="continuation fragments"):
        driver._fetch_template_data(instance_id=0x100, object_definition_size=10)
