"""Async↔Sync parity suite — proves one protocol stack.

For each operation, two Sessions with identical state (same fixed session_handle
and ot_connection_id from synthetic replies), recording send_recv wrappers, and
the same canned reply sequence are fed to LogixDriver (sync) and AsyncLogixDriver
(async).

Assertions per test:
  1. The recorded request-frame lists are BYTE-IDENTICAL.
  2. The returned Tag(s) are equal.

Byte-identical frames prove the same generators produced the same wire bytes —
there is one protocol implementation.  Equal Tag results are a sanity check on
top.
"""

from __future__ import annotations

import functools
import struct
from collections.abc import Awaitable, Callable
from typing import Any

import anyio
import pytest

from daedalus.cip.data_types import DINT, REAL, STRING
from daedalus.cip.services import ConnectionManagerService
from daedalus.drivers import AsyncLogixDriver, LogixDriver
from daedalus.packets.encap import CPFItem, CPFTypeCode, EncapsulationHeader, build_cpf
from daedalus.runtime.write_policy import WriteMode, WritePolicy
from daedalus.session import Session
from daedalus.tag import Tag

# ---------------------------------------------------------------------------
# Shared session constants — MUST be identical on both sides
# ---------------------------------------------------------------------------

_SESSION_HANDLE = 0x1234
_OT_CONN_ID = 0xDEADBEEF


# ---------------------------------------------------------------------------
# Session setup
# ---------------------------------------------------------------------------


def _make_session() -> Session:
    """Return a Session in CONNECTED state using the fixed constants above."""
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
# Reply frame builders  (mirrors test_logix_driver.py helpers)
# ---------------------------------------------------------------------------


def _make_connected_reply(cip_payload: bytes, seq: int = 0) -> bytes:
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


def _make_read_reply(type_code: int, data: bytes, status: int = 0x00) -> bytes:
    return bytes([0x4C | 0x80, 0x00, status, 0x00]) + struct.pack("<H", type_code) + data


def _make_msp_reply(sub_replies: list[bytes]) -> bytes:
    """Build an MSP (service 0x0A) outer reply with sub-reply list."""
    count = len(sub_replies)
    base = 2 + 2 * count
    pos = 0
    offsets: list[int] = []
    for sub in sub_replies:
        offsets.append(base + pos)
        pos += len(sub)
    msp_payload = (
        struct.pack("<H", count)
        + b"".join(struct.pack("<H", o) for o in offsets)
        + b"".join(sub_replies)
    )
    return bytes([0x0A | 0x80, 0x00, 0x00, 0x00]) + msp_payload


def _make_tag_list_cip_reply(entries: list[dict[str, Any]], status: int = 0x00) -> bytes:
    """Build a CIP Get Instance Attribute List reply for the given tag entries."""
    buf = b""
    for e in entries:
        dims: tuple[int, int, int] = e.get("dims", (0, 0, 0))
        buf += struct.pack("<I", e["instance_id"])
        buf += STRING.encode(e["name"])
        buf += struct.pack("<H", e["symbol_type"])
        buf += struct.pack("<III", 0, 0, 0)
        buf += struct.pack("<III", dims[0], dims[1], dims[2])
    return bytes([0xD5, 0x00, status, 0x00]) + buf


# ---------------------------------------------------------------------------
# Recording transports
# ---------------------------------------------------------------------------


def _recording_sync(
    replies: list[bytes],
) -> tuple[Callable[[bytes], bytes], list[bytes]]:
    """Return (send_recv, recorded_frames) where send_recv pops from replies."""
    sent: list[bytes] = []
    it = iter(replies)

    def _inner(frame: bytes) -> bytes:
        sent.append(frame)
        return next(it)

    return _inner, sent


def _recording_async(
    replies: list[bytes],
) -> tuple[Callable[[bytes], Awaitable[bytes]], list[bytes]]:
    """Async variant: same pop semantics, awaitable."""
    sent: list[bytes] = []
    it = iter(replies)

    async def _inner(frame: bytes) -> bytes:
        sent.append(frame)
        return next(it)

    return _inner, sent


# ---------------------------------------------------------------------------
# Parity assertion helper
# ---------------------------------------------------------------------------


def _assert_parity(
    replies: list[bytes],
    sync_op: Callable[[LogixDriver], Any],
    async_op: Callable[[AsyncLogixDriver], Any],
    *,
    policy_factory: Callable[[], WritePolicy] | None = None,
) -> tuple[Any, Any]:
    """Run sync and async drivers with identical replies, assert frame parity.

    Returns (sync_result, async_result) for additional assertions by the caller.
    """
    sync_sr, sync_sent = _recording_sync(list(replies))
    async_sr, async_sent = _recording_async(list(replies))

    policy_s = policy_factory() if policy_factory else WritePolicy()
    policy_a = policy_factory() if policy_factory else WritePolicy()

    sync_driver = LogixDriver(_make_session(), sync_sr, policy=policy_s)
    async_driver = AsyncLogixDriver(_make_session(), async_sr, policy=policy_a)

    sync_result = sync_op(sync_driver)
    async_result = async_op(async_driver)

    assert sync_sent == async_sent, (
        f"request frames differ between sync and async drivers:\n"
        f" sync ({len(sync_sent)} frames): {[f.hex() for f in sync_sent]}\n"
        f"async ({len(async_sent)} frames): {[f.hex() for f in async_sent]}"
    )
    return sync_result, async_result


# ---------------------------------------------------------------------------
# 1. Scalar read
# ---------------------------------------------------------------------------


def test_parity_scalar_read() -> None:
    replies = [_make_connected_reply(_make_read_reply(DINT.code, DINT.encode(42)), seq=1)]

    sync_r, async_r = _assert_parity(
        replies,
        lambda d: d.read_tag("MyDINT"),
        lambda d: anyio.run(d.read_tag, "MyDINT"),
    )

    assert sync_r == async_r
    assert sync_r.value == 42


# ---------------------------------------------------------------------------
# 2. Array read
# ---------------------------------------------------------------------------


def test_parity_array_read() -> None:
    data = b"".join(DINT.encode(i) for i in range(3))
    replies = [_make_connected_reply(_make_read_reply(DINT.code, data), seq=1)]

    sync_r, async_r = _assert_parity(
        replies,
        lambda d: d.read_tag("Arr", element_count=3),
        lambda d: anyio.run(functools.partial(d.read_tag, "Arr", element_count=3)),
    )

    assert sync_r == async_r
    assert sync_r.value == [0, 1, 2]


# ---------------------------------------------------------------------------
# 3. Fragmented read (multi-frame)
# ---------------------------------------------------------------------------


def test_parity_fragmented_read() -> None:
    full_value = b"".join(DINT.encode(i) for i in range(6))
    type_prefix = struct.pack("<H", DINT.code)

    r1 = _make_connected_reply(
        bytes([0x4C | 0x80, 0x00, 0x06, 0x00]) + type_prefix + full_value[:8], seq=1
    )
    r2 = _make_connected_reply(bytes([0x52 | 0x80, 0x00, 0x06, 0x00]) + full_value[8:16], seq=2)
    r3 = _make_connected_reply(bytes([0x52 | 0x80, 0x00, 0x00, 0x00]) + full_value[16:], seq=3)
    replies = [r1, r2, r3]

    sync_r, async_r = _assert_parity(
        replies,
        lambda d: d.read_tag("BigTag", element_count=6),
        lambda d: anyio.run(functools.partial(d.read_tag, "BigTag", element_count=6)),
    )

    assert sync_r == async_r
    assert sync_r.value == list(range(6))


# ---------------------------------------------------------------------------
# 4. MSP multi-read (read_tags)
# ---------------------------------------------------------------------------


def test_parity_msp_read() -> None:
    sub1 = bytes([0x4C | 0x80, 0x00, 0x00, 0x00]) + struct.pack("<H", DINT.code) + DINT.encode(10)
    sub2 = bytes([0x4C | 0x80, 0x00, 0x00, 0x00]) + struct.pack("<H", REAL.code) + REAL.encode(2.5)
    replies = [_make_connected_reply(_make_msp_reply([sub1, sub2]), seq=1)]

    sync_r, async_r = _assert_parity(
        replies,
        lambda d: d.read_tags(["A", "B"]),
        lambda d: anyio.run(functools.partial(d.read_tags, ["A", "B"])),
    )

    assert sync_r == async_r
    assert sync_r[0].value == 10
    assert abs(sync_r[1].value - 2.5) < 1e-5


# ---------------------------------------------------------------------------
# 5. Tag list (single controller scope, single chunk)
# ---------------------------------------------------------------------------


def test_parity_tag_list() -> None:
    entries = [
        {"instance_id": 1, "name": "TagA", "symbol_type": 0x00C4},
        {"instance_id": 2, "name": "TagB", "symbol_type": 0x00C4},
    ]
    cip_reply = _make_tag_list_cip_reply(entries, status=0x00)
    replies = [_make_connected_reply(cip_reply, seq=1)]

    sync_r, async_r = _assert_parity(
        replies,
        lambda d: d.get_tag_list(),
        lambda d: anyio.run(d.get_tag_list),
    )

    assert [t.tag_name for t in sync_r] == [t.tag_name for t in async_r]
    names = [t.tag_name for t in sync_r]
    assert "TagA" in names
    assert "TagB" in names


# ---------------------------------------------------------------------------
# 6. Struct read (0x02A0 — graceful fallback to raw bytes)
# ---------------------------------------------------------------------------


def test_parity_struct_read() -> None:
    # When no template info is cached, the driver returns raw bytes.
    # _maybe_resolve_struct_gen tries to look up by handle but falls back cleanly.
    cip_payload = _make_read_reply(0x02A0, b"\x01\x00" + b"\xab\xcd")
    replies = [_make_connected_reply(cip_payload, seq=1)]

    sync_r, async_r = _assert_parity(
        replies,
        lambda d: d.read_tag("S"),
        lambda d: anyio.run(d.read_tag, "S"),
    )

    assert sync_r == async_r
    assert sync_r.type == "STRUCT"
    assert isinstance(sync_r.value, bytes)


# ---------------------------------------------------------------------------
# 7. Error-status read  (CIP error, not a success)
# ---------------------------------------------------------------------------


def test_parity_error_status_read() -> None:
    cip_payload = bytes([0x4C | 0x80, 0x00, 0x08, 0x00])  # service 0x08 = device failure
    replies = [_make_connected_reply(cip_payload, seq=1)]

    from daedalus.exceptions import ResponseError

    sync_sr, sync_sent = _recording_sync(list(replies))
    async_sr, async_sent = _recording_async(list(replies))

    sync_driver = LogixDriver(_make_session(), sync_sr)
    async_driver = AsyncLogixDriver(_make_session(), async_sr)

    with pytest.raises(ResponseError):
        sync_driver.read_tag("X")
    with pytest.raises(ResponseError):
        anyio.run(async_driver.read_tag, "X")

    assert sync_sent == async_sent, "request frames must be byte-identical even on error"


# ---------------------------------------------------------------------------
# 8. Denied write (READ_ONLY — policy fires pre-I/O, zero frames sent)
# ---------------------------------------------------------------------------


def test_parity_denied_write_no_frames() -> None:
    sync_sr, sync_sent = _recording_sync([])
    async_sr, async_sent = _recording_async([])

    sync_driver = LogixDriver(_make_session(), sync_sr)
    async_driver = AsyncLogixDriver(_make_session(), async_sr)

    # Default policy is READ_ONLY — no armed() context
    sync_r = sync_driver.write_tag("ScratchDINT", 42, data_type="DINT")
    async_r = anyio.run(
        functools.partial(async_driver.write_tag, "ScratchDINT", 42, data_type="DINT")
    )

    assert sync_sent == async_sent == [], "no frames must be sent for a policy denial"
    assert sync_r == async_r
    assert sync_r.error is not None


# ---------------------------------------------------------------------------
# 9. Dry-run write (DRY_RUN — zero frames sent, audit record created)
# ---------------------------------------------------------------------------


def test_parity_dry_run_no_frames() -> None:
    sync_sr, sync_sent = _recording_sync([])
    async_sr, async_sent = _recording_async([])

    def _dry_policy() -> WritePolicy:
        return WritePolicy(mode=WriteMode.DRY_RUN)

    sync_driver = LogixDriver(_make_session(), sync_sr, policy=_dry_policy())
    async_driver = AsyncLogixDriver(_make_session(), async_sr, policy=_dry_policy())

    sync_r = sync_driver.write_tag("ScratchDINT", 42, data_type="DINT")
    async_r = anyio.run(
        functools.partial(async_driver.write_tag, "ScratchDINT", 42, data_type="DINT")
    )

    assert sync_sent == async_sent == [], "dry-run must send no frames"
    assert sync_r == async_r


# ---------------------------------------------------------------------------
# 10. Scalar write (armed — stage-read + commit + verify-read = 3 frames)
# ---------------------------------------------------------------------------


def test_parity_scalar_write() -> None:
    old_value_reply = _make_connected_reply(_make_read_reply(DINT.code, DINT.encode(0)), seq=1)
    write_ack_reply = _make_connected_reply(bytes([0x4D | 0x80, 0x00, 0x00, 0x00]), seq=2)
    verify_reply = _make_connected_reply(_make_read_reply(DINT.code, DINT.encode(42)), seq=3)
    replies = [old_value_reply, write_ack_reply, verify_reply]

    def _run_sync(d: LogixDriver) -> Tag:
        with d.armed():
            return d.write_tag("ScratchDINT", 42, data_type="DINT")

    async def _run_async_op(d: AsyncLogixDriver) -> Tag:
        with d.armed():
            return await d.write_tag("ScratchDINT", 42, data_type="DINT")

    def _async_op(d: AsyncLogixDriver) -> Tag:
        return anyio.run(functools.partial(_run_async_op, d))

    sync_r, async_r = _assert_parity(replies, _run_sync, _async_op)

    assert sync_r == async_r
    assert sync_r.error is None


# ---------------------------------------------------------------------------
# 11. Array write (armed — same 3-frame pipeline)
# ---------------------------------------------------------------------------


def test_parity_array_write() -> None:
    old_data = b"".join(DINT.encode(0) for _ in range(3))
    new_data = b"".join(DINT.encode(i + 1) for i in range(3))

    old_reply = _make_connected_reply(_make_read_reply(DINT.code, old_data), seq=1)
    ack_reply = _make_connected_reply(bytes([0x4D | 0x80, 0x00, 0x00, 0x00]), seq=2)
    verify_reply = _make_connected_reply(_make_read_reply(DINT.code, new_data), seq=3)
    replies = [old_reply, ack_reply, verify_reply]

    def _run_sync(d: LogixDriver) -> Tag:
        with d.armed():
            return d.write_tag("ArrTag", [1, 2, 3], data_type="DINT", element_count=3)

    async def _run_async_op(d: AsyncLogixDriver) -> Tag:
        with d.armed():
            return await d.write_tag("ArrTag", [1, 2, 3], data_type="DINT", element_count=3)

    def _async_op(d: AsyncLogixDriver) -> Tag:
        return anyio.run(functools.partial(_run_async_op, d))

    sync_r, async_r = _assert_parity(replies, _run_sync, _async_op)

    assert sync_r == async_r
    assert sync_r.error is None
