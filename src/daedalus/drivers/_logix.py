"""LogixDriver — Layer 3 (sans-I/O) request builder for Allen-Bradley Logix controllers.

Architecture note: ALL byte-building and reply-parsing lives in module-level
pure helpers (_extract_connected_cip, _decode_read_reply, _parse_msp_reply).
The LogixDriver class is only the thin send_recv orchestration shell so that
Phase 3's AsyncLogixDriver can reuse every helper by re-implementing just the
await loop — no protocol logic needs to be duplicated.

I/O-FORBIDDEN: this module must never import socket, ssl, asyncio, anyio,
selectors, socketserver, http, urllib, or requests.
"""

from __future__ import annotations

import struct
from collections.abc import Callable, Sequence
from io import BytesIO
from typing import Any

from daedalus.cip.data_types import DATA_TYPES_BY_CODE, UDINT, UINT, Array
from daedalus.cip.services import CIPService
from daedalus.cip.status import decode_status
from daedalus.exceptions import DataError, ResponseError
from daedalus.packets.cip import (
    MSG_ROUTER_PATH,
    build_cip_request,
    build_send_unit_data,
    parse_cip_response,
    tag_request_path,
)
from daedalus.packets.encap import CPFTypeCode, parse_cpf
from daedalus.session import Session
from daedalus.tag import Tag

__all__ = ["LogixDriver"]

_SUCCESS: int = 0x00
_PARTIAL_TRANSFER: int = 0x06

# CIP type code returned for all UDT / struct reads before template is fetched.
_STRUCT_TYPE_CODE: int = 0x02A0


# ---------------------------------------------------------------------------
# Module-level pure helpers (Phase 3 reuse point — no I/O, no state)
# ---------------------------------------------------------------------------


def _extract_connected_cip(frame: bytes) -> tuple[int, int, int, bytes]:
    """Decode a SendUnitData reply frame → CONNECTED_DATA → parse_cip_response.

    Layout: 24-byte EIP header + 4-byte interface_handle + 2-byte timeout + CPF.
    CPF offset = 30. CONNECTED_DATA item contains 2-byte seq count then CIP reply.
    """
    items = parse_cpf(frame[30:])
    conn = next((it for it in items if it.type_code == int(CPFTypeCode.CONNECTED_DATA)), None)
    if conn is None:
        raise DataError("No CONNECTED_DATA item in SendUnitData reply")
    # Strip the 2-byte sequence count that prefixes the CIP payload.
    return parse_cip_response(conn.data[2:])


def _decode_read_reply(tag_name: str, payload: bytes, element_count: int) -> Tag:
    """Decode a READ_TAG payload (2-byte type code + data bytes) into a Tag.

    Args:
        tag_name: The tag name (for the Tag result and error messages).
        payload: Raw bytes starting with the 2-byte CIP type code.
        element_count: Number of elements requested (1 = scalar).

    Returns:
        A Tag with the decoded value.

    Raises:
        DataError: If the payload is too short or the type code is unknown.
    """
    if len(payload) < 2:
        raise DataError(f"READ_TAG reply for '{tag_name}' too short: {len(payload)} bytes")

    type_code = struct.unpack_from("<H", payload, 0)[0]
    data = payload[2:]

    # Struct path — check BEFORE the registry lookup; 0x02A0 is not in DATA_TYPES_BY_CODE.
    if type_code == _STRUCT_TYPE_CODE:
        # Strip the 2-byte structure handle that precedes the member data.
        # Named-dict decode requires the UDT template and is deferred to Phase 2d.
        value: Any = bytes(data[2:]) if len(data) >= 2 else bytes(data)
        return Tag(tag_name=tag_name, value=value, type_code=type_code)

    dt = DATA_TYPES_BY_CODE.get(type_code)
    if dt is None:
        raise DataError(f"Unknown CIP type code 0x{type_code:04X} for '{tag_name}'")

    if element_count > 1:
        value = Array(element_count, dt).decode(BytesIO(data))
    else:
        value = dt.decode(BytesIO(data))

    return Tag(tag_name=tag_name, value=value, type_code=type_code)


def _parse_msp_reply(tag_names: list[str], payload: bytes) -> list[Tag]:
    """Parse an MSP (Multiple Service Packet) reply payload into per-tag Tags.

    Per-tag errors are captured in Tag.error / Tag.status — never raised.
    Only an MSP-level failure (bad outer status) raises ResponseError.

    MSP reply layout: UINT(count) + count*UINT(offsets) + concatenated sub-replies.
    Offsets are measured from the count word (byte 0 of *payload*).
    """
    if len(payload) < 2:
        raise DataError("MSP reply payload too short")

    count = struct.unpack_from("<H", payload, 0)[0]
    tags: list[Tag] = []

    for i in range(count):
        offset_pos = 2 + 2 * i
        if len(payload) < offset_pos + 2:
            raise DataError(f"MSP reply: truncated offset table at index {i}")

        off = struct.unpack_from("<H", payload, offset_pos)[0]

        if i + 1 < count:
            next_off = struct.unpack_from("<H", payload, offset_pos + 2)[0]
            sub = payload[off:next_off]
        else:
            sub = payload[off:]

        name = tag_names[i]
        try:
            _, st, ext, sub_pl = parse_cip_response(sub)
            if st != _SUCCESS:
                tags.append(Tag(name, None, 0, st, decode_status(st, ext)))
            else:
                tags.append(_decode_read_reply(name, sub_pl, 1))
        except DataError as exc:
            tags.append(Tag(name, None, 0, 0xFF, str(exc)))

    return tags


# ---------------------------------------------------------------------------
# LogixDriver — thin orchestration shell
# ---------------------------------------------------------------------------


class LogixDriver:
    """Sans-I/O Logix tag reader for Class 3 connected messaging.

    The driver never touches a socket.  Callers inject a ``send_recv``
    callable that wires the L1 transport::

        def _send_recv(frame: bytes) -> bytes:
            transport.send_frame(frame)
            return transport.recv_frame()

        driver = LogixDriver(session, _send_recv)
        tag = driver.read_tag("Program:Main.Counter")

    Args:
        session: A Session in the CONNECTED state (Forward_Open completed).
        send_recv: Callable that accepts a frame, sends it, waits for the
            reply, and returns the reply bytes.  Must be provided by the L1
            transport layer — LogixDriver never creates one itself.
    """

    def __init__(
        self,
        session: Session,
        send_recv: Callable[[bytes], bytes],
    ) -> None:
        self._session = session
        self._send_recv = send_recv

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _send_connected(self, cip_msg: bytes) -> tuple[int, int, int, bytes]:
        """Wrap *cip_msg* in SendUnitData, send it, return parse_cip_response tuple."""
        seq = self._session.next_sequence_count()
        frame = build_send_unit_data(
            session_handle=self._session.session_handle,
            connection_id=self._session.ot_connection_id,
            sequence_count=seq,
            message=cip_msg,
        )
        return _extract_connected_cip(self._send_recv(frame))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def read_tag(self, tag_name: str, *, element_count: int = 1) -> Tag:
        """Read a tag over a Class 3 connected session.

        Args:
            tag_name: Logix tag name (supports dotted paths and array indices).
            element_count: Number of elements to read (1 = scalar; >1 = array slice).

        Returns:
            A Tag with the decoded value.

        Raises:
            ResponseError: Device returned a CIP error status.
            DataError: Reply too short or contains an unknown type code.
        """
        path = tag_request_path(tag_name)
        cip_msg = build_cip_request(CIPService.READ_TAG, path, UINT.encode(element_count))
        _, status, ext, payload = self._send_connected(cip_msg)

        if status == _PARTIAL_TRANSFER:
            return self._read_tag_fragmented(tag_name, element_count, payload)
        if status != _SUCCESS:
            raise ResponseError(f"READ_TAG '{tag_name}' failed: {decode_status(status, ext)}")
        return _decode_read_reply(tag_name, payload, element_count)

    def _read_tag_fragmented(
        self,
        tag_name: str,
        element_count: int,
        first_payload: bytes,
    ) -> Tag:
        """Accumulate READ_TAG_FRAGMENTED replies for a tag that exceeded the connection size.

        The first partial reply (status 0x06 from the initial READ_TAG request) includes
        the 2-byte type code prefix.  Continuation replies do NOT repeat the prefix.
        The byte offset in subsequent READ_TAG_FRAGMENTED requests is a UDINT (4 bytes).
        """
        if len(first_payload) < 2:
            raise DataError(
                f"Fragmented read '{tag_name}': first payload too short"
                f" ({len(first_payload)} bytes)"
            )

        type_bytes = first_payload[:2]
        accumulated = bytearray(first_payload[2:])
        byte_offset = len(accumulated)
        path = tag_request_path(tag_name)

        while True:
            # READ_TAG_FRAGMENTED request data: UINT(element_count) + UDINT(byte_offset)
            data = UINT.encode(element_count) + UDINT.encode(byte_offset)
            _, status, ext, payload = self._send_connected(
                build_cip_request(CIPService.READ_TAG_FRAGMENTED, path, data)
            )

            if status == _PARTIAL_TRANSFER:
                accumulated.extend(payload)
                byte_offset += len(payload)
            elif status == _SUCCESS:
                accumulated.extend(payload)
                break
            else:
                raise ResponseError(
                    f"READ_TAG_FRAGMENTED '{tag_name}' failed: {decode_status(status, ext)}"
                )

        return _decode_read_reply(tag_name, type_bytes + bytes(accumulated), element_count)

    def read_tags(self, tag_names: Sequence[str]) -> list[Tag]:
        """Read multiple tags in one Multiple Service Packet (MSP, service 0x0A).

        Scalar reads only — one element per tag.  For array reads use
        :meth:`read_tag` with ``element_count > 1``.

        Per-tag errors are captured in the returned Tag (Tag.error / Tag.status)
        and do not raise.  An MSP-level failure raises ResponseError.

        Args:
            tag_names: Sequence of Logix tag names to read.

        Returns:
            List of Tags in the same order as *tag_names*.

        Raises:
            ResponseError: The MSP outer request itself failed.
            DataError: The MSP reply is malformed.
        """
        if not tag_names:
            return []

        # Build individual READ_TAG sub-requests (scalar: element_count=1)
        sub_reqs = [
            build_cip_request(CIPService.READ_TAG, tag_request_path(n), UINT.encode(1))
            for n in tag_names
        ]

        # MSP data layout:
        #   UINT(count)
        #   count x UINT(offset)  -- offsets from the count word (byte 0)
        #   concatenated sub-requests
        count = len(sub_reqs)
        base = 2 + 2 * count  # count word (2) + offset table (2*count)
        pos = 0
        offsets: list[int] = []
        for req in sub_reqs:
            offsets.append(base + pos)
            pos += len(req)

        msp_data = (
            struct.pack("<H", count)
            + b"".join(struct.pack("<H", o) for o in offsets)
            + b"".join(sub_reqs)
        )
        cip_msg = build_cip_request(CIPService.MULTIPLE_SERVICE_REQUEST, MSG_ROUTER_PATH, msp_data)

        _, status, ext, payload = self._send_connected(cip_msg)
        if status != _SUCCESS:
            raise ResponseError(f"MSP failed: {decode_status(status, ext)}")

        return _parse_msp_reply(list(tag_names), payload)
