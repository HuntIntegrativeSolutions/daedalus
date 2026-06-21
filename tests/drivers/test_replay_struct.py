"""Decode-replay tests for struct member reads — real wire bytes from the emulator.

Captures stored under tests/fixtures/replay/ are one-JSON-per-line with fields
{"request": "<hex>", "reply": "<hex>"}.  Each test replays the reply bytes through
the driver's full parse path (sans-I/O stub) and asserts the expected output.

Note: the replay harness here is intentionally minimal — a full request/response
decode harness is tracked as Phase 2+ work (see tests/fixtures/replay/README.md).

Phase 2g follow-up: after member-template resolution is implemented, these tests
should assert full decode (e.g. STRING_40 Desc → '' not raw bytes).
"""

from __future__ import annotations

import json
import struct
from collections.abc import Callable
from pathlib import Path

import pytest

from daedalus.cip.data_types import DINT
from daedalus.cip.services import ConnectionManagerService
from daedalus.cip.templates import ResolvedMember, ResolvedTemplate
from daedalus.drivers import LogixDriver
from daedalus.packets.encap import CPFItem, CPFTypeCode, EncapsulationHeader, build_cpf
from daedalus.session import Session
from daedalus.tag import TagInfo

REPLAY_DIR = Path(__file__).parent.parent / "fixtures" / "replay"

_SESSION_HANDLE = 0x1234
_OT_CONN_ID = 0xDEADBEEF
_STRUCT_TMPL_ID = 0x0A5B


# ---------------------------------------------------------------------------
# Shared helpers (same pattern as test_logix_driver.py)
# ---------------------------------------------------------------------------


def _stub_frames(frames: list[bytes]) -> Callable[[bytes], bytes]:
    it = iter(frames)

    def _inner(_sent: bytes) -> bytes:
        return next(it)

    return _inner


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


def _make_warm_driver(reply_bytes: list[bytes]) -> LogixDriver:
    """Driver with template + tag cache pre-populated; stubs return captured reply bytes.

    _get_template() checks _template_cache first (line 1050-1051 of _logix.py) so no
    I/O occurs.  The fixture only contains the read_tag reply, not the get_tag_list
    or template-fetch frames — pre-populating the cache avoids replaying all of those.
    """
    session = _make_session()
    tmpl = ResolvedTemplate(
        name="P_DescList",
        structure_size=4,
        structure_handle=_STRUCT_TMPL_ID,
        members=[
            ResolvedMember(
                name="Code",
                offset=0,
                is_private=False,
                is_bool=False,
                bit_number=0,
                is_array=False,
                array_length=0,
                atomic_type=DINT,
                nested_template=None,
            )
        ],
        is_string=False,
        string_length=0,
    )
    driver = LogixDriver(session, _stub_frames(reply_bytes))
    driver._template_cache[_STRUCT_TMPL_ID] = tmpl
    driver._tag_info_cache["VFD_101_Fault"] = TagInfo(
        tag_name="VFD_101_Fault",
        instance_id=1,
        is_struct=True,
        data_type=None,
        template_instance_id=_STRUCT_TMPL_ID,
        dimensions=(1,),
        scope="controller",
    )
    return driver


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_replay_member_desc_read_returns_raw_bytes_not_parent_dict() -> None:
    """Real wire bytes from emulator: VFD_101_Fault[0].Desc must not decode as parent struct.

    Before the fix, _maybe_resolve_struct applied P_DescList template to the STRING_40
    member reply and returned {'Code': 0, 'Desc': ''}.  After the fix it returns raw
    bytes (type_code=0x02A0).

    Phase 2g follow-up: with member-template resolution, this should return '' (str).
    """
    fixture = REPLAY_DIR / "VFD_101_Fault_member_desc_read.jsonl"
    if not fixture.exists():
        pytest.skip("replay fixture not yet captured")

    lines = [ln for ln in fixture.read_text().splitlines() if ln.strip()]
    # Re-wrap the raw captured reply bytes in a connected reply with our synthetic
    # session IDs so the driver's CPF parser accepts them.  Only the CIP payload
    # (type code + data) matters for the decode test; the connection ID differs.
    raw_replies = [bytes.fromhex(json.loads(line)["reply"]) for line in lines]

    # Extract the CIP payload from the captured reply (everything after the
    # connected-data CPF item header — skip: 24-byte encap, 4+2 interface/timeout,
    # CPF count(2)+items(4+2+len+4+2+len pattern)).  Use _make_connected_reply
    # to re-wrap so the session's OT connection ID matches.
    #
    # Simplest approach: parse just the CIP portion from the captured frame.
    # Layout: encap(24) | interface_handle(4) | timeout(2) | CPF
    # CPF: count(2) | [item0: type(2)+len(2)] | [item1: type(2)+len(2)+seq(2)+cip_data]
    def _extract_cip(frame: bytes) -> bytes:
        offset = 24 + 6  # skip encap header + interface_handle + timeout
        # item count (skip)
        offset += 2
        # item 0: Connected Address (type=0xA1, len=4, data=4 bytes) → skip 4+4=8 bytes
        offset += 2 + 2 + 4
        # item 1: Connected Data (type=0xB1, len=N, data starts with seq(2) then CIP)
        offset += 2  # type
        item_len = struct.unpack_from("<H", frame, offset)[0]
        offset += 2  # len field
        offset += 2  # sequence count
        return frame[offset : offset + item_len - 2]

    cip_payloads = [_extract_cip(r) for r in raw_replies]
    rewrapped = [_make_connected_reply(cip, seq=i + 1) for i, cip in enumerate(cip_payloads)]

    driver = _make_warm_driver(rewrapped)
    tag = driver.read_tag("VFD_101_Fault[0].Desc")

    assert not isinstance(tag.value, dict), (
        f"member-path read must not return parent struct dict — got {tag.value!r}"
    )
    assert isinstance(tag.value, (bytes, bytearray)), (
        f"expected raw bytes, got {type(tag.value).__name__!r}"
    )
    assert tag.type_code == 0x02A0
