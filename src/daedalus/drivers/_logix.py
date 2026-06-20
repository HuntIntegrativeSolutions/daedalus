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

from daedalus.cip.data_types import DATA_TYPES_BY_CODE, STRING, UDINT, UINT, Array
from daedalus.cip.object_library import ClassCode
from daedalus.cip.segments import PADDED_EPATH, DataSegment, LogicalSegment
from daedalus.cip.services import CIPService
from daedalus.cip.status import decode_status
from daedalus.exceptions import BufferEmptyError, DataError, ResponseError
from daedalus.packets.cip import (
    MSG_ROUTER_PATH,
    build_cip_request,
    build_send_unit_data,
    parse_cip_response,
    tag_request_path,
)
from daedalus.packets.encap import CPFTypeCode, parse_cpf
from daedalus.session import Session
from daedalus.tag import Tag, TagInfo

__all__ = ["LogixDriver"]

_SUCCESS: int = 0x00
_PARTIAL_TRANSFER: int = 0x06

# CIP type code returned for all UDT / struct reads before template is fetched.
_STRUCT_TYPE_CODE: int = 0x02A0

# ---------------------------------------------------------------------------
# Tag-list constants (Symbol Object class 0x6B, service 0x55)
# ---------------------------------------------------------------------------

# Attribute IDs for Get Instance Attribute List — no attr 10 (external access,
# firmware-gated; pycomm3 appends it only when revision_major >= 18; daedalus
# always uses this base-6 list and documents attr 10 as deferred).
_TAG_LIST_ATTRS: tuple[int, ...] = (1, 2, 3, 5, 6, 8)

_ST_STRUCT_FLAG: int = 0x8000
_ST_DIM_MASK: int = 0x6000
_ST_DIM_SHIFT: int = 13
_ST_SYSTEM_FLAG: int = 0x1000
_ST_TEMPLATE_MASK: int = 0x0FFF
_ST_ATOMIC_MASK: int = 0x00FF


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
# Tag-list module-level helpers (Phase 3 reuse point — no I/O, no state)
# ---------------------------------------------------------------------------


def _symbol_object_path(instance: int, program: str | None = None) -> bytes:
    """Build a PADDED_EPATH (with word-count prefix) for the Symbol Object.

    Controller scope: class 0x6B + instance N.
    Program scope: DataSegment("Program:X") + class 0x6B + instance N.
    """
    segments: list[DataSegment | LogicalSegment] = []
    if program is not None:
        segments.append(DataSegment(program))
    segments.append(LogicalSegment(int(ClassCode.SYMBOL_OBJECT), "class_id"))
    segments.append(LogicalSegment(instance, "instance_id"))
    return PADDED_EPATH.encode(segments, length=True)


def _build_tag_list_request(instance: int, program: str | None = None) -> bytes:
    """Build a Get Instance Attribute List (0x55) CIP request.

    Requests attrs 1,2,3,5,6,8 starting at *instance*.  Use *program* to
    target a program scope (e.g. ``"Program:Main"``); pass ``None`` for the
    controller scope.  Phase 3 reuse point: async driver calls this unchanged.
    """
    path = _symbol_object_path(instance, program)
    data = struct.pack("<H", len(_TAG_LIST_ATTRS)) + b"".join(
        struct.pack("<H", a) for a in _TAG_LIST_ATTRS
    )
    return build_cip_request(CIPService.GET_INSTANCE_ATTRIBUTE_LIST, path, data)


def _is_system_tag(name: str, symbol_type: int) -> bool:
    """Return True if this symbol entry should be excluded from the user tag list.

    Exact port of pycomm3 ``_isolate_user_tags`` filter logic.  I/O tags
    (containing ``:I``, ``:O``, ``:C``, ``:S``) are KEPT — only non-I/O
    colon-containing names are dropped by the catch-all colon rule.
    """
    io_tag = any(x in name for x in (":I", ":O", ":C", ":S"))
    return (
        name.startswith("Program:")
        or name.startswith("Routine:")
        or name.startswith("Task:")
        or "Map:" in name
        or "Cxn:" in name
        or (not io_tag and ":" in name)
        or name.startswith("__")
        or bool(symbol_type & _ST_SYSTEM_FLAG)
    )


def _decode_symbol_type(
    name: str,
    instance_id: int,
    symbol_type: int,
    dim1: int,
    dim2: int,
    dim3: int,
    scope: str,
) -> TagInfo:
    """Decode a symbol_type word + dimension fields into a TagInfo."""
    dim_count = (symbol_type & _ST_DIM_MASK) >> _ST_DIM_SHIFT
    dimensions: tuple[int, ...] = (dim1, dim2, dim3)[:dim_count]

    if symbol_type & _ST_STRUCT_FLAG:
        return TagInfo(
            tag_name=name,
            instance_id=instance_id,
            is_struct=True,
            data_type=None,
            template_instance_id=symbol_type & _ST_TEMPLATE_MASK,
            dimensions=dimensions,
            scope=scope,
        )
    else:
        type_code = symbol_type & _ST_ATOMIC_MASK
        dt = DATA_TYPES_BY_CODE.get(type_code)
        return TagInfo(
            tag_name=name,
            instance_id=instance_id,
            is_struct=False,
            data_type=dt.__name__ if dt is not None else None,
            template_instance_id=None,
            dimensions=dimensions,
            scope=scope,
        )


def _parse_tag_list_reply(payload: bytes, scope: str) -> tuple[list[TagInfo], list[str], int]:
    """Parse a Get Instance Attribute List reply payload.

    Returns ``(user_tags, discovered_programs, last_instance_id)``.

    Wire format per entry (no count word, no separator):
        UDINT  instance_id
        STRING name  (UINT count + bytes, NO word-alignment padding)
        UINT   symbol_type
        UDINT  symbol_address        (ignored)
        UDINT  symbol_object_address (ignored)
        UDINT  software_control      (ignored)
        UDINT  dim1
        UDINT  dim2
        UDINT  dim3

    The STRING encoding uses a UINT length prefix with no trailing pad byte —
    a 3-char name "abc" is exactly 5 bytes (03 00 61 62 63); the stream lands
    directly on symbol_type.  Any padding byte here would silently swallow a
    real byte on odd-length tag names.
    """
    stream = BytesIO(payload)
    user_tags: list[TagInfo] = []
    discovered_programs: list[str] = []
    last_instance: int = 0

    try:
        while True:
            # Peek: if we're at EOF this is the normal exit.
            if stream.read(1) == b"":
                break
            stream.seek(stream.tell() - 1)

            instance_id = UDINT.decode(stream)
            last_instance = instance_id
            name = STRING.decode(stream)  # UINT length + bytes, NO pad
            symbol_type = UINT.decode(stream)
            UDINT.decode(stream)  # symbol_address — ignored
            UDINT.decode(stream)  # symbol_object_address — ignored
            UDINT.decode(stream)  # software_control — ignored
            dim1 = UDINT.decode(stream)
            dim2 = UDINT.decode(stream)
            dim3 = UDINT.decode(stream)

            if name.startswith("Program:"):
                discovered_programs.append(name)
                continue

            if _is_system_tag(name, symbol_type):
                continue

            # Program-scope tag names are prefixed: "Program:Main.TagName"
            qualified = f"{scope}.{name}" if scope != "controller" else name
            user_tags.append(
                _decode_symbol_type(qualified, instance_id, symbol_type, dim1, dim2, dim3, scope)
            )

    except BufferEmptyError as exc:
        raise DataError("tag list reply truncated") from exc

    return user_tags, discovered_programs, last_instance


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

    def get_tag_list(self) -> list[TagInfo]:
        """Enumerate all user tags on the controller (controller + program scopes).

        Walks the Symbol Object (class 0x6B) via Get Instance Attribute List
        (service 0x55).  Controller-scope scan is performed first; any
        ``"Program:X"`` entries discovered there are iterated as separate
        program-scope scans.  System / private tags are filtered out; I/O
        module tags are retained.

        Returns:
            Flat list of TagInfo entries ordered controller-scope first,
            then program-scope in discovery order.

        Raises:
            ResponseError: The device returned a CIP error status.
            DataError: A reply payload is malformed or truncated.
        """
        result, programs = self._get_scope_tag_list(None)
        for prog in programs:
            prog_tags, _ = self._get_scope_tag_list(prog)
            result.extend(prog_tags)
        return result

    def _get_scope_tag_list(self, program: str | None) -> tuple[list[TagInfo], list[str]]:
        """Enumerate one scope via the continuation loop.

        Sends Get Instance Attribute List requests starting at instance 0,
        incrementing to ``last_seen + 1`` each time the device returns
        status 0x06 (partial transfer), until 0x00 (success / done).

        Returns ``(user_tags, discovered_programs)`` for this scope only.
        """
        all_tags: list[TagInfo] = []
        all_programs: list[str] = []
        instance = 0
        scope = program or "controller"

        while True:
            _, status, ext, payload = self._send_connected(
                _build_tag_list_request(instance, program)
            )
            if status not in (_SUCCESS, _PARTIAL_TRANSFER):
                raise ResponseError(f"get_tag_list({scope!r}) failed: {decode_status(status, ext)}")

            tags, programs, last_instance = _parse_tag_list_reply(payload, scope)
            all_tags.extend(tags)
            all_programs.extend(programs)

            if status == _SUCCESS:
                break
            instance = last_instance + 1  # start AFTER last seen instance

        return all_tags, all_programs
