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

import re
import struct
from collections.abc import Callable, Generator, Sequence
from contextlib import contextmanager
from io import BytesIO
from typing import Any

from daedalus.cip.data_types import (
    BOOL,
    DATA_TYPES_BY_CODE,
    DATA_TYPES_BY_NAME,
    DINT,
    STRING,
    UDINT,
    UINT,
    Array,
    DataType,
)
from daedalus.cip.object_library import ClassCode
from daedalus.cip.segments import PADDED_EPATH, DataSegment, LogicalSegment
from daedalus.cip.services import CIPService
from daedalus.cip.status import decode_status
from daedalus.cip.templates import (
    RawMember,
    ResolvedMember,
    ResolvedTemplate,
    TemplateAttributes,
    decode_struct,
    parse_template_attr_reply,
    parse_template_data,
)
from daedalus.exceptions import BufferEmptyError, DataError, ResponseError
from daedalus.packets.cip import (
    MSG_ROUTER_PATH,
    build_cip_request,
    build_send_unit_data,
    parse_cip_response,
    tag_request_path,
)
from daedalus.packets.encap import CPFTypeCode, parse_cpf
from daedalus.runtime.write_policy import WriteMode, WritePolicy
from daedalus.session import Session
from daedalus.tag import Tag, TagInfo

__all__ = ["LogixDriver"]

_SUCCESS: int = 0x00
_PARTIAL_TRANSFER: int = 0x06

# CIP type code returned for all UDT / struct reads before template is fetched.
_STRUCT_TYPE_CODE: int = 0x02A0

# Template Object (class 0x6C) — used for UDT template fetching.
_TEMPLATE_CLASS: int = int(ClassCode.TEMPLATE_OBJECT)

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
        if element_count > 1:
            # array-of-struct: reply would be reply_handle + N*structure_size chunks;
            # chunking is not yet supported -- raise explicitly rather than mis-decode.
            raise DataError(
                f"READ_TAG '{tag_name}': array-of-struct (element_count={element_count}) "
                "is not supported in Phase 2e; read elements individually."
            )
        # Keep full payload (reply_handle prefix + member data).
        # _maybe_resolve_struct extracts the handle and decodes members.
        return Tag(tag_name=tag_name, value=bytes(data), type_code=type_code)

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
# Template Object module-level pure helpers (Phase 3 reuse point — no I/O)
# ---------------------------------------------------------------------------


def _template_object_path(instance_id: int) -> bytes:
    """PADDED_EPATH (with word-count prefix) for Template Object (class 0x6C, instance N)."""
    return PADDED_EPATH.encode(
        [
            LogicalSegment(int(ClassCode.TEMPLATE_OBJECT), "class_id"),
            LogicalSegment(instance_id, "instance_id"),
        ],
        length=True,
    )


def _build_template_attr_request(instance_id: int) -> bytes:
    """GET_ATTRIBUTE_LIST (0x03) CIP request for template makeup attrs 4, 5, 2, 1."""
    path = _template_object_path(instance_id)
    # UINT(count=4) then four UINT attribute numbers in order: 4, 5, 2, 1
    data = struct.pack("<H", 4) + struct.pack("<HHHH", 4, 5, 2, 1)
    return build_cip_request(CIPService.GET_ATTRIBUTE_LIST, path, data)


def _build_template_read_request(
    instance_id: int,
    object_definition_size: int,
    offset: int,
) -> bytes:
    """READ_TAG (0x4C) CIP request for template data at *offset*.

    Length formula (verbatim from pycomm3):
        bytes_to_read = (object_definition_size * 4) - 21 - offset
    Request data: DINT(offset) + UINT(bytes_to_read).
    """
    path = _template_object_path(instance_id)
    bytes_to_read = (object_definition_size * 4) - 21 - offset
    data = DINT.encode(offset) + UINT.encode(bytes_to_read)
    return build_cip_request(CIPService.READ_TAG, path, data)


def _build_atomic_member(
    name: str,
    raw: RawMember,
    dt: type,
    is_private: bool,
) -> ResolvedMember:
    """Create a ResolvedMember for an atomic/array/bool member."""
    if dt is BOOL:
        return ResolvedMember(
            name=name,
            offset=raw.offset,
            is_private=is_private,
            is_bool=True,
            bit_number=raw.type_info,
            is_array=False,
            array_length=0,
            atomic_type=dt,
            nested_template=None,
        )
    is_array = raw.type_info > 0
    return ResolvedMember(
        name=name,
        offset=raw.offset,
        is_private=is_private,
        is_bool=False,
        bit_number=0,
        is_array=is_array,
        array_length=raw.type_info if is_array else 0,
        atomic_type=dt,
        nested_template=None,
    )


def _base_names_for(tag_name: str) -> list[str]:
    """Return candidate base names (longest first) for _tag_info_cache lookup.

    Strips array subscripts ``[N]``, then walks up dotted path segments.
    Stops before eating into a ``"Program:X"`` scope prefix.
    """
    name = re.sub(r"\[\d+\]", "", tag_name)  # strip e.g. [0], [2]
    candidates = [name]
    while "." in name:
        last_dot = name.rfind(".")
        # Don't eat "Program:X" — check there's no colon-only-in-scope-prefix
        before_dot = name[:last_dot]
        if ":" in before_dot and "." not in before_dot.split(":", 1)[1]:
            break  # would eat the tag name from "Program:X.TagName"
        name = before_dot
        candidates.append(name)
    return candidates


# ---------------------------------------------------------------------------
# Write-tag module-level pure helpers (Phase 3 reuse point — no I/O, no state)
# ---------------------------------------------------------------------------

# Matches "MyDINT.3" (bit-of-word index) — trailing dot followed by ONLY digits.
# "MyStruct.FieldName" does NOT match (FieldName has letters).
_BIT_INDEX_RE: re.Pattern[str] = re.compile(r"\.\d+$")


def _is_bit_of_word(tag_name: str) -> bool:
    """Return True if the tag name targets a specific bit of a word.

    WRITE_TAG on a bit-index path (e.g. "MyDINT.3") is semantically wrong:
    the controller interprets the path as a member_id segment and may silently
    corrupt the rest of the word.  Bit writes require Read-Modify-Write
    (CIP 0x4E), deferred to Phase 2g+.
    """
    return bool(_BIT_INDEX_RE.search(tag_name))


def _build_write_request(
    tag_name: str,
    type_code: int,
    element_count: int,
    value_bytes: bytes,
) -> bytes:
    """Build a WRITE_TAG (0x4D) CIP request — pure, no I/O.

    Wire format:
        path   = tag_request_path(tag_name)
        data   = UINT(type_code) + UINT(element_count) + value_bytes
    """
    path = tag_request_path(tag_name)
    data = UINT.encode(type_code) + UINT.encode(element_count) + value_bytes
    return build_cip_request(CIPService.WRITE_TAG, path, data)


def _encode_value(dt: type[DataType[Any]], value: Any, element_count: int) -> bytes:
    """Encode a scalar or array value to bytes for WRITE_TAG.

    For arrays: each element is encoded individually (NOT via ``Array(n, dt).encode``
    which would add a length-prefix for variable-length types).
    """
    if element_count == 1:
        return dt.encode(value)
    return b"".join(dt.encode(v) for v in value)


def _resolve_write_type(
    tag_name: str,
    data_type: str | type[DataType[Any]] | None,
    tag_info_cache: dict[str, TagInfo],
) -> tuple[type[DataType[Any]], int, str]:
    """Resolve (DataType, type_code, type_name) for a write.

    Priority: explicit ``data_type`` kwarg > tag_info_cache lookup.

    Raises:
        DataError: type unknown, struct (Phase 2g), or not in cache.
    """
    if data_type is not None:
        if isinstance(data_type, str):
            dt = DATA_TYPES_BY_NAME.get(data_type.lower())
            if dt is None:
                raise DataError(f"Unknown data_type {data_type!r} for '{tag_name}'")
        else:
            dt = data_type
        return dt, dt.code, dt.__name__

    # No explicit type — walk the tag_info_cache
    for base in _base_names_for(tag_name):
        ti = tag_info_cache.get(base)
        if ti is None:
            continue
        if ti.is_struct:
            raise DataError(f"Struct writes not yet supported (Phase 2g): {tag_name!r}")
        if ti.data_type is None:
            raise DataError(
                f"Cannot resolve write type for '{tag_name}': TagInfo.data_type is None"
            )
        dt = DATA_TYPES_BY_NAME.get(ti.data_type.lower())
        if dt is None:
            raise DataError(f"Unknown type name {ti.data_type!r} for '{tag_name}'")
        return dt, dt.code, dt.__name__

    raise DataError(
        f"Cannot infer type for '{tag_name}': not in tag_info_cache. "
        "Provide data_type= kwarg or call get_tag_list() first."
    )


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
        policy: WritePolicy | None = None,
    ) -> None:
        self._session = session
        self._send_recv = send_recv
        # Each driver gets its own default WritePolicy (READ_ONLY) so arming
        # one driver never affects another.
        self._policy: WritePolicy = policy if policy is not None else WritePolicy()
        # Template caches — populated lazily on first struct read.
        # _handle_to_instance maps the REPLY handle (observed in a struct read
        # reply) to the template instance_id.  NOT the makeup structure_handle —
        # these may differ; name-based resolution is the source of truth.
        self._template_cache: dict[int, ResolvedTemplate] = {}
        self._handle_to_instance: dict[int, int] = {}  # reply_handle → instance_id
        self._tag_info_cache: dict[str, TagInfo] = {}  # tag_name → TagInfo

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
            tag = self._read_tag_fragmented(tag_name, element_count, payload)
            return self._maybe_resolve_struct(tag)
        if status != _SUCCESS:
            raise ResponseError(f"READ_TAG '{tag_name}' failed: {decode_status(status, ext)}")
        tag = _decode_read_reply(tag_name, payload, element_count)
        return self._maybe_resolve_struct(tag)

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

        tags = _parse_msp_reply(list(tag_names), payload)
        return [self._maybe_resolve_struct(t) for t in tags]

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
        # Populate name→TagInfo cache so _maybe_resolve_struct can bootstrap
        # struct reads by tag name without a pre-cached reply handle.
        self._tag_info_cache = {t.tag_name: t for t in result}
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

    # ------------------------------------------------------------------
    # Write API
    # ------------------------------------------------------------------

    @contextmanager
    def armed(self) -> Generator[WritePolicy, None, None]:
        """Arm writes for the duration of this block; guarantee disarm on exit.

        The context manager sets ``self._policy.mode = ARMED`` on enter and
        reverts it (to whatever it was before) on exit — even when the body
        raises.  ``write_tag`` never self-disarms, so all tags in the block
        share the same armed state.

        Usage::

            with driver.armed():
                driver.write_tag("ScratchDINT", 42)
            # policy is READ_ONLY again — even if write_tag raised

        Yields:
            The WritePolicy so callers can inspect audit records::

                with driver.armed() as policy:
                    driver.write_tag("Tag", 1)
                    print(policy.get_records())
        """
        policy = self._policy
        old_mode = policy.mode
        policy.mode = WriteMode.ARMED
        try:
            yield policy
        finally:
            policy.mode = old_mode

    def write_tag(
        self,
        tag_name: str,
        value: Any,
        *,
        data_type: str | type[DataType[Any]] | None = None,
        element_count: int = 1,
    ) -> Tag:
        """Write one atomic scalar or array tag.

        Must be called inside ``with driver.armed():`` — the policy's mode must
        be ARMED or DRY_RUN.  Does NOT self-disarm; arming is context-scoped.

        Struct writes and fragmented writes are Phase 2g — refused cleanly with
        an audited DataError denial.

        Bit-of-word writes (e.g. ``"MyDINT.3"``) require Read-Modify-Write and
        are refused — an audited denial is recorded, no bytes are sent.

        The write pipeline (all failures return a Tag with error set):
            1. Bit-of-word guard (structural; no I/O)
            2. Policy gate: mode / safety / allow-deny (no I/O)
            3. Type resolution (DataError on struct or unknown type)
            4. Element-count validation for arrays
            5. Value encoding (DataError on encode failure)
            6. Dry-run exit: build bytes, audit, return (no send)
            7. Stage: read old value for audit record (failure is non-fatal)
            8. Commit: send WRITE_TAG
            9. Read-back verify in encoded domain (avoids REAL float32 trap)
            10. Audit and return

        Args:
            tag_name:      Logix tag name (dotted paths supported for struct members).
            value:         Value to write (scalar or list for arrays).
            data_type:     CIP type override (string like ``"DINT"`` or DataType
                           subclass).  Required when the tag is not in the cache.
            element_count: 1 for scalar; must equal len(value) for arrays.

        Returns:
            Tag with status=0 on success; Tag.error describes the failure mode
            on any error (policy denial, CIP error, or verify mismatch).
        """
        policy = self._policy
        reason: str | None

        # 1. BIT-OF-WORD GUARD
        if _is_bit_of_word(tag_name):
            reason = (
                f"Bit-of-word write requires Read-Modify-Write (Phase 2g+): {tag_name!r}. "
                "Use WRITE_TAG on the base word, not a bit index."
            )
            policy.deny(tag_name, reason)
            return Tag(tag_name, None, 0, 0xFF, reason)

        # 2. CHEAP PRE-I/O POLICY CHECKS
        allowed, reason = policy.evaluate(tag_name)
        if not allowed:
            assert reason is not None
            policy.deny(tag_name, reason)
            return Tag(tag_name, None, 0, 0xFF, reason)

        # 3. RESOLVE TYPE (no I/O — fail fast before stage read)
        try:
            dt, type_code, _ = _resolve_write_type(tag_name, data_type, self._tag_info_cache)
        except DataError as exc:
            reason = str(exc)
            policy.deny(tag_name, reason)
            return Tag(tag_name, None, 0, 0xFF, reason)

        # 4. ELEMENT COUNT VALIDATION
        if element_count > 1:
            n: int = -1
            if hasattr(value, "__len__"):
                n = len(value)
            if n != element_count:
                reason = f"element_count={element_count} but value has length {n} for '{tag_name}'"
                policy.deny(tag_name, reason)
                return Tag(tag_name, None, type_code, 0xFF, reason)

        # 5. BUILD VALUE BYTES (validate encoding before any I/O)
        try:
            value_bytes = _encode_value(dt, value, element_count)
        except DataError as exc:
            reason = str(exc)
            policy.deny(tag_name, reason)
            return Tag(tag_name, None, type_code, 0xFF, reason)

        # 6. DRY-RUN: validate request bytes, audit, return without sending
        if policy.mode == WriteMode.DRY_RUN:
            _build_write_request(tag_name, type_code, element_count, value_bytes)
            policy.audit(policy._make_record("dry_run", tag_name, value_bytes, None, None))
            return Tag(tag_name, value, type_code, 0, None)

        # 7. STAGE: read old value for audit record (non-fatal on failure)
        old_bytes: bytes | None
        try:
            old_tag = self.read_tag(tag_name, element_count=element_count)
            if old_tag.error is None and old_tag.value is not None:
                old_bytes = _encode_value(dt, old_tag.value, element_count)
            else:
                old_bytes = b""
        except Exception:
            old_bytes = b""

        # 8. COMMIT: send WRITE_TAG
        cip_msg = _build_write_request(tag_name, type_code, element_count, value_bytes)
        _, status, ext, _ = self._send_connected(cip_msg)
        if status != _SUCCESS:
            error_msg = f"WRITE_TAG '{tag_name}' failed: {decode_status(status, ext)}"
            policy.audit(policy._make_record("denied", tag_name, value_bytes, old_bytes, error_msg))
            return Tag(tag_name, None, type_code, status, error_msg)

        # 9. READ-BACK VERIFY in encoded domain (avoids REAL float32 precision trap:
        #    3.14 → float32 → decodes to 3.1400001 → Python "==" fails)
        readback_bytes: bytes | None
        try:
            verify_tag = self.read_tag(tag_name, element_count=element_count)
            if verify_tag.error is None and verify_tag.value is not None:
                readback_bytes = _encode_value(dt, verify_tag.value, element_count)
            else:
                readback_bytes = None
        except Exception:
            readback_bytes = None

        if readback_bytes != value_bytes:
            reason = (
                f"Write verify failed for '{tag_name}': "
                f"readback {readback_bytes!r} != intended {value_bytes!r}"
            )
            policy.audit(
                policy._make_record("verify_failed", tag_name, value_bytes, old_bytes, reason)
            )
            return Tag(tag_name, value, type_code, 0xFF, reason)

        # 10. AUDIT SUCCESS
        policy.audit(policy._make_record("committed", tag_name, value_bytes, old_bytes, None))
        return Tag(tag_name, value, type_code, 0, None)

    def write_tags(
        self,
        tags: list[tuple[str, Any]],
        *,
        data_type: str | type[DataType[Any]] | None = None,
        element_count: int = 1,
    ) -> list[Tag]:
        """Write multiple atomic tags with batch critic approval.

        Runs one ``critic(all_tag_names)`` before any commit — the critic sees
        the full batch and may veto all of it (all-or-nothing at the critic
        gate).

        After critic approval, each tag is committed individually; CIP errors
        on individual tags are captured in ``Tag.error`` and do not block
        subsequent tags.

        Arming/disarming is caller-managed (``with driver.armed():``).

        Args:
            tags:          List of ``(tag_name, value)`` pairs.
            data_type:     CIP type override applied to every tag in the batch.
            element_count: Element count applied to every tag.

        Returns:
            List of Tags in the same order as *tags*.
        """
        if not tags:
            return []

        policy = self._policy
        tag_names = [n for n, _ in tags]

        # 1. BATCH PRE-CHECKS: any refusal denies the entire batch
        first_refusal: str | None = None
        for name, _ in tags:
            if _is_bit_of_word(name):
                first_refusal = (
                    f"Bit-of-word write in batch: {name!r} requires Read-Modify-Write (Phase 2g+)"
                )
                break
            allowed, reason = policy.evaluate(name)
            if not allowed:
                first_refusal = reason
                break

        if first_refusal is not None:
            policy._deny_all(tag_names, first_refusal)
            return [Tag(n, None, 0, 0xFF, first_refusal) for n in tag_names]

        # 2. CRITIC: one call, sees full batch — veto blocks all before any commit
        if policy.critic is not None:
            result = policy.critic(tag_names)
            if result is not True:
                critic_reason = result if isinstance(result, str) else "Critic denied batch"
                policy._deny_all(tag_names, critic_reason)
                return [Tag(n, None, 0, 0xFF, critic_reason) for n in tag_names]

        # 3. PER-TAG COMMIT: individual CIP errors captured, not raised
        return [
            self.write_tag(name, val, data_type=data_type, element_count=element_count)
            for name, val in tags
        ]

    # ------------------------------------------------------------------
    # Template pipeline — fetch, parse, cache, decode
    # ------------------------------------------------------------------

    def _fetch_template_attrs(self, instance_id: int) -> TemplateAttributes:
        """Round-trip GET_ATTRIBUTE_LIST on Template Object → TemplateAttributes."""
        cip_msg = _build_template_attr_request(instance_id)
        _, status, ext, payload = self._send_connected(cip_msg)
        if status != _SUCCESS:
            raise ResponseError(
                f"GET_ATTRIBUTE_LIST template {instance_id:#x}: {decode_status(status, ext)}"
            )
        return parse_template_attr_reply(payload)

    def _fetch_template_data(self, instance_id: int, object_definition_size: int) -> bytes:
        """Round-trip READ_TAG on Template Object with 0x06 continuation."""
        offset = 0
        accumulated = bytearray()
        while True:
            cip_msg = _build_template_read_request(instance_id, object_definition_size, offset)
            _, status, ext, payload = self._send_connected(cip_msg)
            if status == _PARTIAL_TRANSFER:
                accumulated.extend(payload)
                offset += len(payload)
            elif status == _SUCCESS:
                accumulated.extend(payload)
                break
            else:
                raise ResponseError(
                    f"READ_TAG template {instance_id:#x}: {decode_status(status, ext)}"
                )
        return bytes(accumulated)

    def _get_template(self, instance_id: int) -> ResolvedTemplate:
        """Lazily fetch, parse, and cache a ResolvedTemplate.

        Recursive for nested UDTs (Logix UDTs have no cycles so depth-first
        terminates; the cache deduplicates diamond-shaped nesting).

        Note: does NOT populate _handle_to_instance — that is done by
        _maybe_resolve_struct using the reply handle observed in a struct read.
        """
        if instance_id in self._template_cache:
            return self._template_cache[instance_id]

        attrs = self._fetch_template_attrs(instance_id)
        data = self._fetch_template_data(instance_id, attrs.object_definition_size)
        template_name, member_pairs = parse_template_data(data, attrs, instance_id)
        is_predefined = instance_id < 0x100 or instance_id > 0xEFF

        resolved_members: list[ResolvedMember] = []
        for name, raw in member_pairs:
            resolved_members.append(self._resolve_member(name, raw, is_predefined))

        non_private = [m for m in resolved_members if not m.is_private]
        is_string = (
            len(non_private) == 2
            and [m.name for m in non_private] == ["LEN", "DATA"]
            and non_private[1].is_array
            and non_private[1].atomic_type is not None
            and non_private[1].atomic_type.__name__ == "SINT"
        )
        string_length = non_private[1].array_length if is_string else None

        template = ResolvedTemplate(
            name=template_name,
            structure_size=attrs.structure_size,
            structure_handle=attrs.structure_handle,
            members=resolved_members,
            is_string=is_string,
            string_length=string_length,
        )
        self._template_cache[instance_id] = template
        return template

    def _resolve_member(
        self,
        name: str,
        raw: RawMember,
        is_predefined: bool,
    ) -> ResolvedMember:
        """Resolve one RawMember — may call _get_template recursively for struct members."""
        is_private = (
            name.startswith("ZZZZZZZZZZ")
            or name.startswith("__")
            or (is_predefined and name in {"CTL", "Control"})
        )
        typ = raw.typ
        # Branch 1: full typ is a known atomic type code
        dt = DATA_TYPES_BY_CODE.get(typ)
        if dt is not None:
            return _build_atomic_member(name, raw, dt, is_private)
        # Branch 2: masked typ (low 12 bits) is a known atomic type code
        dt = DATA_TYPES_BY_CODE.get(typ & 0x0FFF)
        if dt is not None:
            return _build_atomic_member(name, raw, dt, is_private)
        # Branch 3: nested struct — recurse into driver to fetch that template
        nested_id = typ & 0x0FFF
        nested = self._get_template(nested_id)
        return ResolvedMember(
            name=name,
            offset=raw.offset,
            is_private=is_private,
            is_bool=False,
            bit_number=0,
            is_array=False,
            array_length=0,
            atomic_type=None,
            nested_template=nested,
        )

    def _maybe_resolve_struct(self, tag: Tag) -> Tag:
        """Post-process a 0x02A0 Tag: extract reply handle, look up template, decode.

        Resolution is name-based (same approach as pycomm3). The 2-byte reply handle
        carried in the struct read response is opaque — it is NOT assumed to equal
        the structure_handle returned by GET_ATTRIBUTE_LIST makeup. After the first
        name-based resolution the reply handle is cached so subsequent reads of the
        same UDT type skip the I/O bootstrap.

        Scoped to whole-struct (top-level) reads for Phase 2e.
        Nested-member-path reads (read_tag("MyTag.Inner")) fall back to raw bytes
        gracefully — no crash.
        """
        if tag.type_code != _STRUCT_TYPE_CODE or tag.error is not None:
            return tag
        raw = tag.value
        if not isinstance(raw, (bytes, bytearray)) or len(raw) < 2:
            return tag
        reply_handle = struct.unpack_from("<H", raw, 0)[0]
        member_data = bytes(raw[2:])

        # Fast-path: reply handle was seen before and mapped to an instance_id.
        instance_id = self._handle_to_instance.get(reply_handle)

        if instance_id is None:
            # Name-based bootstrap: strip subscripts, walk up dotted path
            # segments to find a TagInfo with template_instance_id set.
            for base in _base_names_for(tag.tag_name):
                ti = self._tag_info_cache.get(base)
                if ti is not None and ti.template_instance_id is not None:
                    self._get_template(ti.template_instance_id)  # populate _template_cache
                    # Map the REPLY handle we just observed to this instance_id.
                    instance_id = ti.template_instance_id
                    self._handle_to_instance[reply_handle] = instance_id
                    break

        if instance_id is None:
            # Can't resolve without tag-list info — return raw bytes, no crash.
            return tag

        template = self._template_cache[instance_id]
        try:
            decoded = decode_struct(member_data, template)
        except Exception as exc:
            return Tag(
                tag_name=tag.tag_name,
                value=None,
                type_code=tag.type_code,
                status=0xFF,
                error=str(exc),
            )
        return Tag(
            tag_name=tag.tag_name,
            value=decoded,
            type_code=tag.type_code,
            status=tag.status,
            error=tag.error,
            udt_name=template.name,
        )
