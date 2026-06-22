"""Real async end-to-end tests for AsyncLogixDriver.

Tests cover:
  1. Full async round-trip against the CipSimServer.
  2. Write-policy gate — unarmed writes are refused and no WRITE_TAG frame is sent.
  3. Async cancellation ownership-release — exception during commit restores READ_ONLY.
"""

from __future__ import annotations

import struct
from typing import Any

import pytest

from daedalus.cip.data_types import DINT
from daedalus.cip.services import ConnectionManagerService
from daedalus.drivers import AsyncLogixDriver
from daedalus.packets.encap import CPFItem, CPFTypeCode, EncapsulationHeader, build_cpf
from daedalus.runtime.write_policy import WriteMode, WritePolicy
from daedalus.session import Session
from daedalus.transport import AsyncTcpTransport
from sim.server import CipSimServer

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SESSION_HANDLE = 0x1234
_OT_CONN_ID = 0xDEADBEEF

# CIP WRITE_TAG service byte (request direction: 0x4D, not reply-bit set)
_WRITE_TAG_SERVICE = 0x4D


def _is_write_tag_frame(frame: bytes) -> bool:
    """True if the frame carries a CIP WRITE_TAG request (service 0x4D).

    SendUnitData frame layout:
      24-byte EncapsulationHeader
      4-byte interface handle
      2-byte timeout
      CPF items — the connected data item payload starts with a 2-byte seq count
      followed by the CIP message.  The CIP service byte is the very first byte
      of the message.
    """
    if len(frame) < 50:
        return False
    # Skip header (24) + interface_handle (4) + timeout (2) + CPF header (4)
    # + first CPF item type+len (4) + connection_id (4)
    # + second CPF item type+len (4) + seq_count (2) = offset 48
    # The CIP service byte is at offset 48 in the typical connected message layout.
    # Use a safe search: look for 0x4D in the CIP payload region (bytes 48 onward).
    return frame[48:49] == bytes([_WRITE_TAG_SERVICE])


def _make_connected_reply(cip_payload: bytes, seq: int = 0) -> bytes:
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


def _make_session() -> Session:
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
    cip_reply_bytes = bytes([svc | 0x80, 0x00, 0x00, 0x00]) + fo_payload
    fo_cpf = (
        b"\x00\x00\x00\x00"
        + b"\x00\x00"
        + build_cpf(
            [
                CPFItem(CPFTypeCode.NULL_ADDRESS),
                CPFItem(CPFTypeCode.UNCONNECTED_DATA, cip_reply_bytes),
            ]
        )
    )
    fo_header = EncapsulationHeader.for_command(
        0x6F, data_length=len(fo_cpf), session_handle=_SESSION_HANDLE
    )
    s.forward_open_reply(fo_header.encode() + fo_cpf)
    return s


# ---------------------------------------------------------------------------
# 1. Full async round-trip against sim server
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_async_e2e_read_dint(make_tag_server: Any) -> None:
    """AsyncLogixDriver reads a DINT tag from the CipSimServer over AsyncTcpTransport."""
    srv: CipSimServer = make_tag_server({"AsyncTag": (DINT.code, DINT.encode(99))})

    session = Session()
    async with AsyncTcpTransport(srv.host, srv.port) as t:
        await t.send_frame(session.register_request())
        session.register_reply(await t.recv_frame())

        await t.send_frame(session.forward_open_request(large=False))
        session.forward_open_reply(await t.recv_frame())

        driver = AsyncLogixDriver(session, t.send_recv)
        tag = await driver.read_tag("AsyncTag")

        assert tag.value == 99
        assert tag.error is None

        # teardown
        await t.send_frame(session.forward_close_request())
        session.forward_close_reply(await t.recv_frame())
        await t.send_frame(session.unregister_request())


@pytest.mark.anyio
async def test_async_e2e_write_dint(make_tag_server: Any) -> None:
    """AsyncLogixDriver writes and reads back a DINT tag."""
    srv: CipSimServer = make_tag_server({"ScratchDINT": (DINT.code, DINT.encode(0))})

    session = Session()
    async with AsyncTcpTransport(srv.host, srv.port) as t:
        await t.send_frame(session.register_request())
        session.register_reply(await t.recv_frame())

        await t.send_frame(session.forward_open_request(large=False))
        session.forward_open_reply(await t.recv_frame())

        driver = AsyncLogixDriver(session, t.send_recv)

        with driver.armed():
            result = await driver.write_tag("ScratchDINT", 77, data_type="DINT")

        assert result.error is None

        # Verify the written value reads back correctly.
        tag = await driver.read_tag("ScratchDINT")
        assert tag.value == 77

        await t.send_frame(session.forward_close_request())
        session.forward_close_reply(await t.recv_frame())
        await t.send_frame(session.unregister_request())


# ---------------------------------------------------------------------------
# 2. Write-policy gate — unarmed write is refused, no WRITE_TAG frame sent
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_async_write_gate_refuses_unarmed(make_tag_server: Any) -> None:
    """Default READ_ONLY policy refuses writes — no WRITE_TAG frame sent."""
    srv: CipSimServer = make_tag_server({"ScratchDINT": (DINT.code, DINT.encode(0))})

    sent_frames: list[bytes] = []

    session = Session()
    async with AsyncTcpTransport(srv.host, srv.port) as t:
        await t.send_frame(session.register_request())
        session.register_reply(await t.recv_frame())

        await t.send_frame(session.forward_open_request(large=False))
        session.forward_open_reply(await t.recv_frame())

        async def _recording_send_recv(frame: bytes) -> bytes:
            sent_frames.append(frame)
            return await t.send_recv(frame)

        driver = AsyncLogixDriver(session, _recording_send_recv)

        # Write without arming — should be denied
        result = await driver.write_tag("ScratchDINT", 1, data_type="DINT")

        assert result.error is not None, "unarmed write must return an error Tag"
        assert not any(_is_write_tag_frame(f) for f in sent_frames), (
            "no WRITE_TAG frame must reach the transport"
        )

        await t.send_frame(session.forward_close_request())
        session.forward_close_reply(await t.recv_frame())
        await t.send_frame(session.unregister_request())


# ---------------------------------------------------------------------------
# 3. Async cancellation — exception during commit restores mode to READ_ONLY
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_async_cancellation_restores_mode() -> None:
    """Exception raised during the commit round-trip must restore mode to READ_ONLY.

    The write pipeline calls send_recv twice before completing:
      call 1: stage read (step 7) — returns the old value
      call 2: commit WRITE_TAG (step 8) — raises RuntimeError here

    _run_async's `except BaseException: gen.close(); raise` fires at the await,
    then the `with driver.armed():` finally block restores policy.mode.
    """
    call_count = 0
    stage_reply = _make_connected_reply(_make_read_reply(DINT.code, DINT.encode(0)), seq=1)

    async def _cancel_on_commit(frame: bytes) -> bytes:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return stage_reply
        raise RuntimeError("simulated cancellation during commit")

    session = _make_session()
    policy = WritePolicy()
    driver = AsyncLogixDriver(session, _cancel_on_commit, policy)

    with pytest.raises(RuntimeError, match="simulated cancellation"), driver.armed():
        await driver.write_tag("ScratchDINT", 42, data_type="DINT")

    assert policy.mode == WriteMode.READ_ONLY, (
        f"mode must be READ_ONLY after exception; got {policy.mode}"
    )
    assert call_count == 2, (
        f"expected 2 transport calls (stage-read + commit-fail); got {call_count}"
    )
