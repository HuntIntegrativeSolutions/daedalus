"""Minimal in-process CIP sim server for tests.

Answers RegisterSession, UnregisterSession, Forward_Open (standard and Large),
Forward_Close, and (when tag_store is provided) SendUnitData with Read Tag /
Read Tag Fragmented / Multiple Service Packet service handlers.

Runs in a daemon thread; binds to port 0 so the OS assigns an ephemeral port.

Pass ``reject_large_fo=True`` to make the sim reject Large_Forward_Open with
CIP status 0x08 (Service Not Supported) — used to exercise the fallback path.

Pass ``tag_store`` to enable connected reads::

    store = {"MyDINT": (0xC4, DINT.encode(42))}
    srv = CipSimServer(tag_store=store)

Tags whose value bytes exceed ``frag_threshold`` trigger a fragmented response
(initial READ_TAG reply carries CIP status 0x06; the driver must follow up with
READ_TAG_FRAGMENTED requests at increasing byte offsets).
"""

from __future__ import annotations

import contextlib
import math
import secrets
import socket
import struct
import threading
from dataclasses import dataclass
from typing import Any

from daedalus.cip.data_types import STRING
from daedalus.cip.segments import PADDED_EPATH, DataSegment, LogicalSegment
from daedalus.packets.encap import CPFItem, CPFTypeCode, EncapsulationHeader, build_cpf, parse_cpf

__all__ = ["CipSimServer", "TemplateEntry"]


@dataclass
class TemplateEntry:
    """Describes a single UDT template served by the sim's template store.

    The ``structure_handle`` is what the sim echoes back in GET_ATTRIBUTE_LIST
    replies AND in struct read replies (as the 2-byte reply_handle prefix).
    ``object_definition_size`` is derived automatically from ``template_data``
    using the inverse of the driver's read-length formula so the driver can
    read all bytes in one or more continuations.
    """

    template_data: bytes  # raw bytes: member-info array + null-separated names blob
    structure_size: int  # struct size in bytes (reported in GET_ATTRIBUTE_LIST attr 5)
    member_count: int  # number of members (reported in attr 2)
    structure_handle: int  # echoed in attr 1 AND as reply_handle in struct read replies

    @property
    def object_definition_size(self) -> int:
        """Number of 32-bit words covering all template_data bytes.

        Inverse of: bytes_to_read = (object_definition_size * 4) - 21 - offset
        At offset=0, bytes_to_read >= len(template_data), so:
            object_definition_size = ceil((len(template_data) + 21) / 4)
        """
        return math.ceil((len(self.template_data) + 21) / 4)


_HEADER_SIZE: int = 24
_CMD_REGISTER_SESSION: int = 0x65
_CMD_UNREGISTER_SESSION: int = 0x66
_CMD_SEND_RR_DATA: int = 0x6F
_CMD_SEND_UNIT_DATA: int = 0x70

_SVC_FORWARD_OPEN: int = 0x54
_SVC_LARGE_FORWARD_OPEN: int = 0x5B
_SVC_FORWARD_CLOSE: int = 0x4E

_SVC_GET_ATTRIBUTE_LIST: int = 0x03
_SVC_READ_TAG: int = 0x4C
_SVC_READ_TAG_FRAGMENTED: int = 0x52
_SVC_MULTIPLE_SERVICE_REQUEST: int = 0x0A
_SVC_GET_INSTANCE_ATTR_LIST: int = 0x55

_TEMPLATE_CLASS: int = 0x6C

_CIP_SUCCESS: int = 0x00
_CIP_PARTIAL: int = 0x06
_CIP_ATTR_NOT_SUPPORTED: int = 0x08


class CipSimServer:
    """In-process TCP server that speaks the minimum EtherNet/IP for
    RegisterSession / UnregisterSession / Forward_Open / Forward_Close
    and (optionally) connected tag reads via SendUnitData.

    The server binds to ``127.0.0.1`` on an ephemeral port chosen by the OS.
    Read :attr:`host` and :attr:`port` after :meth:`start` to connect.

    Protocol behaviour:
    - **RegisterSession** (0x65): assigns a cryptographically random non-zero
      session handle, sends the 28-byte reply, and stays connected.
    - **UnregisterSession** (0x66): closes the connection per ODVA (no reply).
    - **SendRRData** (0x6F): dispatches on CIP service byte:
        - 0x54 / 0x5B (FORWARD_OPEN / LARGE_FORWARD_OPEN): replies with success
          (or CIP status 0x08 if ``reject_large_fo=True`` and service is 0x5B).
        - 0x4E (FORWARD_CLOSE): replies with success.
    - **SendUnitData** (0x70): dispatches on CIP service byte (requires
      ``tag_store`` to be populated; otherwise closes the connection):
        - 0x4C (READ_TAG): replies with type+value bytes from tag_store, or
          status 0x06 if the value exceeds frag_threshold.
        - 0x52 (READ_TAG_FRAGMENTED): serves a chunk at the requested byte offset.
        - 0x0A (MULTIPLE_SERVICE_REQUEST): routes each sub-request individually.
    - Any unrecognised command or service: closes the connection.
    """

    def __init__(
        self,
        *,
        reject_large_fo: bool = False,
        tag_store: dict[str, tuple[int, bytes]] | None = None,
        frag_threshold: int = 480,
        symbol_store: dict[str, list[dict[str, Any]]] | None = None,
        tag_list_frag_size: int = 300,
        template_store: dict[int, TemplateEntry] | None = None,
        template_frag_threshold: int = 480,
    ) -> None:
        self._reject_large_fo = reject_large_fo
        self._tag_store: dict[str, tuple[int, bytes]] = tag_store or {}
        self._frag_threshold = frag_threshold
        # symbol_store: dict keyed by scope ("controller", "Program:Main", etc.)
        # Each value is a list of dicts with keys: name, instance_id, symbol_type, dims
        self._symbol_store: dict[str, list[dict[str, Any]]] = symbol_store or {}
        self._tag_list_frag_size = tag_list_frag_size
        # template_store: dict keyed by instance_id (int) → TemplateEntry
        self._template_store: dict[int, TemplateEntry] = template_store or {}
        self._template_frag_threshold = template_frag_threshold

        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        addr: tuple[str, int] = self._sock.getsockname()
        self._host: str = addr[0]
        self._port: int = addr[1]
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def host(self) -> str:
        """Bind address (always ``"127.0.0.1"``)."""
        return self._host

    @property
    def port(self) -> int:
        """Ephemeral port assigned by the OS."""
        return self._port

    def start(self) -> None:
        """Start accepting connections in a background daemon thread."""
        self._sock.listen(8)
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Signal the server to stop and close the listener socket."""
        self._stop.set()
        with contextlib.suppress(OSError):
            self._sock.close()

    # ------------------------------------------------------------------
    # Internal — accept / dispatch loop
    # ------------------------------------------------------------------

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except OSError:
                break
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn: socket.socket) -> None:
        # Per-connection state shared across handlers (e.g. the O→T conn ID assigned
        # during Forward_Open that must be echoed back in SendUnitData replies).
        conn_state: dict[str, Any] = {"ot_connection_id": 0}
        try:
            while True:
                raw_header = _recv_exactly(conn, _HEADER_SIZE)
                if not raw_header:
                    break
                command = struct.unpack_from("<H", raw_header, 0)[0]
                data_len = struct.unpack_from("<H", raw_header, 2)[0]
                payload = _recv_exactly(conn, data_len) if data_len else b""

                if command == _CMD_REGISTER_SESSION:
                    self._reply_register(conn, raw_header)
                elif command == _CMD_UNREGISTER_SESSION:
                    break  # ODVA: device closes the connection, no reply
                elif command == _CMD_SEND_RR_DATA:
                    if not self._dispatch_send_rr_data(conn, raw_header, payload, conn_state):
                        break
                elif command == _CMD_SEND_UNIT_DATA:
                    if not self._dispatch_send_unit_data(conn, raw_header, payload, conn_state):
                        break
                else:
                    break  # unexpected — close
        except OSError:
            pass
        finally:
            with contextlib.suppress(OSError):
                conn.close()

    def _reply_register(self, conn: socket.socket, request_header: bytes) -> None:
        """Send a RegisterSession reply with a fresh random session handle."""
        handle = secrets.randbelow(0xFFFF_FFFF) + 1  # never 0
        sender_context = request_header[8:16]
        reply_header = EncapsulationHeader(
            command=_CMD_REGISTER_SESSION,
            length=4,
            session_handle=handle,
            status=0,
            sender_context=sender_context,
            options=0,
        )
        reply = reply_header.encode() + b"\x01\x00\x00\x00"
        conn.sendall(reply)

    # ------------------------------------------------------------------
    # SendRRData (unconnected) dispatch
    # ------------------------------------------------------------------

    def _dispatch_send_rr_data(
        self,
        conn: socket.socket,
        raw_header: bytes,
        payload: bytes,
        conn_state: dict[str, Any],
    ) -> bool:
        """Dispatch a SendRRData payload; return False to close the connection."""
        # payload layout: 4-byte interface_handle + 2-byte timeout + CPF
        if len(payload) < 6:
            return False
        cpf_data = payload[6:]
        if len(cpf_data) < 2:
            return False
        item_count = struct.unpack_from("<H", cpf_data, 0)[0]
        if item_count < 2:
            return False

        items = parse_cpf(cpf_data)
        unconnected = next(
            (it for it in items if it.type_code == int(CPFTypeCode.UNCONNECTED_DATA)), None
        )
        if unconnected is None or len(unconnected.data) < 1:
            return False

        service = unconnected.data[0]
        session_handle = struct.unpack_from("<I", raw_header, 4)[0]
        sender_context = raw_header[8:16]

        if service in (_SVC_FORWARD_OPEN, _SVC_LARGE_FORWARD_OPEN):
            if service == _SVC_LARGE_FORWARD_OPEN and self._reject_large_fo:
                self._reply_fo_error(conn, service, session_handle, sender_context, status=0x08)
            else:
                self._reply_fo_success(
                    conn, service, session_handle, sender_context, unconnected.data, conn_state
                )
        elif service == _SVC_FORWARD_CLOSE:
            self._reply_fc_success(conn, service, session_handle, sender_context)
        else:
            return False
        return True

    def _reply_fo_success(
        self,
        conn: socket.socket,
        service: int,
        session_handle: int,
        sender_context: bytes,
        cip_request: bytes,
        conn_state: dict[str, Any],
    ) -> None:
        """Build and send a Forward_Open success reply; save O→T conn ID in conn_state."""
        path_word_count = cip_request[1]
        fo_data_offset = 2 + path_word_count * 2
        fo_data = cip_request[fo_data_offset:]
        if len(fo_data) < 26:
            return
        to_conn_id = struct.unpack_from("<I", fo_data, 6)[0]
        conn_serial = struct.unpack_from("<H", fo_data, 10)[0]
        orig_vendor_id = struct.unpack_from("<H", fo_data, 12)[0]
        orig_serial = struct.unpack_from("<I", fo_data, 14)[0]
        ot_rpi = struct.unpack_from("<I", fo_data, 22)[0]

        ot_conn_id = secrets.randbelow(0xFFFF_FFFF) + 1  # device assigns, never 0
        conn_state["ot_connection_id"] = ot_conn_id  # save for SendUnitData replies

        cip_reply = b"".join(
            [
                bytes([service | 0x80, 0x00, 0x00, 0x00]),
                struct.pack("<I", ot_conn_id),
                struct.pack("<I", to_conn_id),
                struct.pack("<H", conn_serial),
                struct.pack("<H", orig_vendor_id),
                struct.pack("<I", orig_serial),
                struct.pack("<I", ot_rpi),
                struct.pack("<I", ot_rpi),
                b"\x00\x00",
            ]
        )
        self._send_send_rr_data_reply(conn, session_handle, sender_context, cip_reply)

    def _reply_fo_error(
        self,
        conn: socket.socket,
        service: int,
        session_handle: int,
        sender_context: bytes,
        status: int,
    ) -> None:
        cip_reply = bytes([service | 0x80, 0x00, status, 0x00])
        self._send_send_rr_data_reply(conn, session_handle, sender_context, cip_reply)

    def _reply_fc_success(
        self,
        conn: socket.socket,
        service: int,
        session_handle: int,
        sender_context: bytes,
    ) -> None:
        cip_reply = bytes([service | 0x80, 0x00, 0x00, 0x00])
        self._send_send_rr_data_reply(conn, session_handle, sender_context, cip_reply)

    def _send_send_rr_data_reply(
        self,
        conn: socket.socket,
        session_handle: int,
        sender_context: bytes,
        cip_reply: bytes,
    ) -> None:
        """Wrap a CIP reply in SendRRData and send it."""
        cpf = (
            b"\x00\x00\x00\x00"  # interface handle
            + b"\x00\x00"  # timeout
            + build_cpf(
                [
                    CPFItem(CPFTypeCode.NULL_ADDRESS),
                    CPFItem(CPFTypeCode.UNCONNECTED_DATA, cip_reply),
                ]
            )
        )
        reply_header = EncapsulationHeader(
            command=_CMD_SEND_RR_DATA,
            length=len(cpf),
            session_handle=session_handle,
            status=0,
            sender_context=sender_context,
            options=0,
        )
        conn.sendall(reply_header.encode() + cpf)

    # ------------------------------------------------------------------
    # SendUnitData (connected) dispatch
    # ------------------------------------------------------------------

    def _dispatch_send_unit_data(
        self,
        conn: socket.socket,
        raw_header: bytes,
        payload: bytes,
        conn_state: dict[str, Any],
    ) -> bool:
        """Dispatch a SendUnitData payload; return False to close the connection."""
        # payload: interface_handle(4) + timeout(2) + CPF
        if len(payload) < 6:
            return False

        items = parse_cpf(payload[6:])
        connected_data = next(
            (it for it in items if it.type_code == int(CPFTypeCode.CONNECTED_DATA)), None
        )
        if connected_data is None or len(connected_data.data) < 3:
            return False

        # First 2 bytes of connected_data.data = sequence_count (UINT)
        seq_count = struct.unpack_from("<H", connected_data.data, 0)[0]
        cip_data = connected_data.data[2:]
        service = cip_data[0]

        session_handle = struct.unpack_from("<I", raw_header, 4)[0]
        sender_context = raw_header[8:16]

        if service == _SVC_GET_ATTRIBUTE_LIST:
            return self._handle_template_attr(
                conn, session_handle, sender_context, seq_count, cip_data, conn_state
            )
        elif service == _SVC_READ_TAG:
            return self._handle_read_tag(
                conn, session_handle, sender_context, seq_count, cip_data, conn_state
            )
        elif service == _SVC_READ_TAG_FRAGMENTED:
            return self._handle_read_tag_fragmented(
                conn, session_handle, sender_context, seq_count, cip_data, conn_state
            )
        elif service == _SVC_MULTIPLE_SERVICE_REQUEST:
            return self._handle_msp(
                conn, session_handle, sender_context, seq_count, cip_data, conn_state
            )
        elif service == _SVC_GET_INSTANCE_ATTR_LIST:
            return self._handle_tag_list(
                conn, session_handle, sender_context, seq_count, cip_data, conn_state
            )
        else:
            return False

    def _extract_tag_name(self, cip_data: bytes) -> str | None:
        """Extract the tag name from a CIP READ_TAG request's PADDED_EPATH.

        PADDED_EPATH layout: [0]=service, [1]=path_word_count, [2..]=path.
        Scan for 0x91 (ANSI extended symbol) segments; join with '.'.
        """
        if len(cip_data) < 4:
            return None
        path_word_count = cip_data[1]
        path = cip_data[2 : 2 + path_word_count * 2]
        name_parts: list[str] = []
        i = 0
        while i < len(path):
            seg_type = path[i]
            if seg_type == 0x91:  # ANSI extended symbol
                if i + 1 >= len(path):
                    break
                name_len = path[i + 1]
                name = path[i + 2 : i + 2 + name_len].decode("ascii", errors="replace")
                name_parts.append(name)
                # Pad to even word boundary
                total = 2 + name_len + (name_len % 2)
                i += total
            else:
                # Non-symbol segment (e.g. logical member_id for array index) — stop
                break
        return ".".join(name_parts) if name_parts else None

    def _extract_class_instance(self, cip_data: bytes) -> tuple[int | None, int | None]:
        """Extract (class_code, instance_id) from a logical-segment CIP path.

        Returns (None, None) if the path is a tag-name (DataSegment) path.
        Reuses PADDED_EPATH.decode — same approach as _extract_tag_list_path.
        """
        if len(cip_data) < 2:
            return None, None
        path_word_count = cip_data[1]
        path_bytes = cip_data[2 : 2 + path_word_count * 2]
        try:
            segments = PADDED_EPATH.decode(path_bytes)
        except Exception:
            return None, None
        class_code: int | None = None
        instance_id: int | None = None
        for seg in segments:
            if isinstance(seg, LogicalSegment):
                if seg.logical_type == "class_id":
                    lv = seg.logical_value
                    class_code = lv if isinstance(lv, int) else int.from_bytes(lv, "little")
                elif seg.logical_type == "instance_id":
                    lv = seg.logical_value
                    instance_id = lv if isinstance(lv, int) else int.from_bytes(lv, "little")
        return class_code, instance_id

    def _handle_read_tag(
        self,
        conn: socket.socket,
        session_handle: int,
        sender_context: bytes,
        seq_count: int,
        cip_data: bytes,
        conn_state: dict[str, Any],
    ) -> bool:
        """Serve a READ_TAG (0x4C) request — tag store or Template Object."""
        # Check whether this is a Template Object read (class 0x6C path)
        class_code, instance_id = self._extract_class_instance(cip_data)
        if class_code == _TEMPLATE_CLASS and instance_id is not None:
            return self._handle_template_read(
                conn, session_handle, sender_context, seq_count, cip_data, instance_id, conn_state
            )

        tag_name = self._extract_tag_name(cip_data)
        if tag_name is None or tag_name not in self._tag_store:
            cip_reply = bytes([_SVC_READ_TAG | 0x80, 0x00, _CIP_ATTR_NOT_SUPPORTED, 0x00])
            self._send_unit_data_reply(
                conn, session_handle, sender_context, seq_count, cip_reply, conn_state
            )
            return True

        type_code, value_bytes = self._tag_store[tag_name]
        type_prefix = struct.pack("<H", type_code)

        if len(value_bytes) > self._frag_threshold:
            # Partial transfer: include type_code + first frag_threshold value bytes
            partial = value_bytes[: self._frag_threshold]
            cip_reply = (
                bytes([_SVC_READ_TAG | 0x80, 0x00, _CIP_PARTIAL, 0x00]) + type_prefix + partial
            )
        else:
            cip_reply = (
                bytes([_SVC_READ_TAG | 0x80, 0x00, _CIP_SUCCESS, 0x00]) + type_prefix + value_bytes
            )

        self._send_unit_data_reply(
            conn, session_handle, sender_context, seq_count, cip_reply, conn_state
        )
        return True

    def _handle_read_tag_fragmented(
        self,
        conn: socket.socket,
        session_handle: int,
        sender_context: bytes,
        seq_count: int,
        cip_data: bytes,
        conn_state: dict[str, Any],
    ) -> bool:
        """Serve a READ_TAG_FRAGMENTED (0x52) continuation request."""
        tag_name = self._extract_tag_name(cip_data)
        if tag_name is None or tag_name not in self._tag_store:
            cip_reply = bytes(
                [_SVC_READ_TAG_FRAGMENTED | 0x80, 0x00, _CIP_ATTR_NOT_SUPPORTED, 0x00]
            )
            self._send_unit_data_reply(
                conn, session_handle, sender_context, seq_count, cip_reply, conn_state
            )
            return True

        _, value_bytes = self._tag_store[tag_name]

        # Request data: UINT(element_count) + UDINT(byte_offset)
        path_word_count = cip_data[1]
        data_offset = 2 + path_word_count * 2
        if len(cip_data) < data_offset + 6:  # 2 (UINT) + 4 (UDINT)
            return False
        byte_offset = struct.unpack_from("<I", cip_data, data_offset + 2)[0]

        remaining = value_bytes[byte_offset:]
        if len(remaining) > self._frag_threshold:
            partial = remaining[: self._frag_threshold]
            cip_reply = bytes([_SVC_READ_TAG_FRAGMENTED | 0x80, 0x00, _CIP_PARTIAL, 0x00]) + partial
        else:
            cip_reply = (
                bytes([_SVC_READ_TAG_FRAGMENTED | 0x80, 0x00, _CIP_SUCCESS, 0x00]) + remaining
            )

        self._send_unit_data_reply(
            conn, session_handle, sender_context, seq_count, cip_reply, conn_state
        )
        return True

    def _handle_msp(
        self,
        conn: socket.socket,
        session_handle: int,
        sender_context: bytes,
        seq_count: int,
        cip_data: bytes,
        conn_state: dict[str, Any],
    ) -> bool:
        """Serve a Multiple Service Packet (0x0A) by routing each sub-request individually."""
        # cip_data[0] = 0x0A, cip_data[1] = path_word_count
        path_word_count = cip_data[1]
        msp_payload = cip_data[2 + path_word_count * 2 :]

        if len(msp_payload) < 2:
            return False

        count = struct.unpack_from("<H", msp_payload, 0)[0]
        sub_replies: list[bytes] = []

        for i in range(count):
            offset_idx = 2 + i * 2
            if len(msp_payload) < offset_idx + 2:
                return False
            sub_offset = struct.unpack_from("<H", msp_payload, offset_idx)[0]

            if i + 1 < count:
                next_offset = struct.unpack_from("<H", msp_payload, offset_idx + 2)[0]
                sub_req = msp_payload[sub_offset:next_offset]
            else:
                sub_req = msp_payload[sub_offset:]

            sub_svc = sub_req[0] if sub_req else 0xFF
            if sub_svc != _SVC_READ_TAG:
                sub_replies.append(bytes([sub_svc | 0x80, 0x00, _CIP_ATTR_NOT_SUPPORTED, 0x00]))
                continue

            tag_name = self._extract_tag_name(sub_req)
            if tag_name is None or tag_name not in self._tag_store:
                sub_replies.append(
                    bytes([_SVC_READ_TAG | 0x80, 0x00, _CIP_ATTR_NOT_SUPPORTED, 0x00])
                )
                continue

            type_code, value_bytes = self._tag_store[tag_name]
            type_prefix = struct.pack("<H", type_code)
            sub_replies.append(
                bytes([_SVC_READ_TAG | 0x80, 0x00, _CIP_SUCCESS, 0x00]) + type_prefix + value_bytes
            )

        # Build MSP reply payload: UINT(count) + count x UINT(offsets) + sub_replies
        reply_base = 2 + 2 * count
        pos = 0
        reply_offsets: list[int] = []
        for sub in sub_replies:
            reply_offsets.append(reply_base + pos)
            pos += len(sub)

        msp_reply_payload = (
            struct.pack("<H", count)
            + b"".join(struct.pack("<H", o) for o in reply_offsets)
            + b"".join(sub_replies)
        )
        cip_reply = (
            bytes([_SVC_MULTIPLE_SERVICE_REQUEST | 0x80, 0x00, _CIP_SUCCESS, 0x00])
            + msp_reply_payload
        )
        self._send_unit_data_reply(
            conn, session_handle, sender_context, seq_count, cip_reply, conn_state
        )
        return True

    def _serialize_symbol_entry(self, entry: dict[str, Any]) -> bytes:
        """Serialize one symbol store entry to wire bytes.

        Wire layout (no separators, no count word):
            UDINT  instance_id
            STRING name  (UINT length + bytes, NO pad byte)
            UINT   symbol_type
            UDINT  symbol_address        (0)
            UDINT  symbol_object_address (0)
            UDINT  software_control      (0)
            UDINT  dim1, dim2, dim3
        """
        dims: tuple[int, int, int] = entry.get("dims", (0, 0, 0))
        return (
            struct.pack("<I", entry["instance_id"])
            + STRING.encode(entry["name"])
            + struct.pack("<H", entry["symbol_type"])
            + struct.pack("<I", 0)  # symbol_address
            + struct.pack("<I", 0)  # symbol_object_address
            + struct.pack("<I", 0)  # software_control
            + struct.pack("<I", dims[0])
            + struct.pack("<I", dims[1])
            + struct.pack("<I", dims[2])
        )

    def _extract_tag_list_path(self, cip_data: bytes) -> tuple[str | None, int]:
        """Parse scope and start instance from a Get Instance Attribute List request.

        cip_data layout: [0]=service(0x55), [1]=path_word_count, [2..]=path bytes.
        Returns (program, start_instance).
        """
        if len(cip_data) < 2:
            return None, 0
        path_word_count = cip_data[1]
        path_bytes = cip_data[2 : 2 + path_word_count * 2]
        try:
            segments = PADDED_EPATH.decode(path_bytes)
        except Exception:
            return None, 0

        program: str | None = None
        for seg in segments:
            if (
                isinstance(seg, DataSegment)
                and isinstance(seg.data, str)
                and seg.data.startswith("Program:")
            ):
                program = seg.data
                break

        instance: int = 0
        for seg in segments:
            if isinstance(seg, LogicalSegment) and seg.logical_type == "instance_id":
                lv = seg.logical_value
                instance = lv if isinstance(lv, int) else int.from_bytes(lv, "little")
                break

        return program, instance

    def _handle_tag_list(
        self,
        conn: socket.socket,
        session_handle: int,
        sender_context: bytes,
        seq_count: int,
        cip_data: bytes,
        conn_state: dict[str, Any],
    ) -> bool:
        """Serve a Get Instance Attribute List (0x55) request from the symbol store."""
        program, start_instance = self._extract_tag_list_path(cip_data)
        scope_key = program or "controller"
        all_entries = self._symbol_store.get(scope_key, [])
        eligible = [e for e in all_entries if e["instance_id"] >= start_instance]

        buf = b""
        last_idx = len(eligible)
        for i, entry in enumerate(eligible):
            serialized = self._serialize_symbol_entry(entry)
            if buf and len(buf) + len(serialized) > self._tag_list_frag_size:
                last_idx = i  # remaining entries start here
                break
            buf += serialized
        else:
            last_idx = len(eligible)  # all entries fit

        status = _CIP_PARTIAL if last_idx < len(eligible) else _CIP_SUCCESS
        cip_reply = bytes([_SVC_GET_INSTANCE_ATTR_LIST | 0x80, 0x00, status, 0x00]) + buf
        self._send_unit_data_reply(
            conn, session_handle, sender_context, seq_count, cip_reply, conn_state
        )
        return True

    def _handle_template_attr(
        self,
        conn: socket.socket,
        session_handle: int,
        sender_context: bytes,
        seq_count: int,
        cip_data: bytes,
        conn_state: dict[str, Any],
    ) -> bool:
        """Serve GET_ATTRIBUTE_LIST (0x03) on Template Object — return makeup attributes."""
        _, instance_id = self._extract_class_instance(cip_data)
        if instance_id is None or instance_id not in self._template_store:
            cip_reply = bytes([_SVC_GET_ATTRIBUTE_LIST | 0x80, 0x00, _CIP_ATTR_NOT_SUPPORTED, 0x00])
            self._send_unit_data_reply(
                conn, session_handle, sender_context, seq_count, cip_reply, conn_state
            )
            return True

        entry = self._template_store[instance_id]
        obj_def = entry.object_definition_size
        payload = (
            struct.pack("<H", 4)  # attr count = 4
            + struct.pack("<H", 4)
            + struct.pack("<H", 0)
            + struct.pack("<I", obj_def)
            + struct.pack("<H", 5)
            + struct.pack("<H", 0)
            + struct.pack("<I", entry.structure_size)
            + struct.pack("<H", 2)
            + struct.pack("<H", 0)
            + struct.pack("<H", entry.member_count)
            + struct.pack("<H", 1)
            + struct.pack("<H", 0)
            + struct.pack("<H", entry.structure_handle)
        )
        cip_reply = bytes([_SVC_GET_ATTRIBUTE_LIST | 0x80, 0x00, _CIP_SUCCESS, 0x00]) + payload
        self._send_unit_data_reply(
            conn, session_handle, sender_context, seq_count, cip_reply, conn_state
        )
        return True

    def _handle_template_read(
        self,
        conn: socket.socket,
        session_handle: int,
        sender_context: bytes,
        seq_count: int,
        cip_data: bytes,
        instance_id: int,
        conn_state: dict[str, Any],
    ) -> bool:
        """Serve READ_TAG (0x4C) on Template Object — stream template data with continuation."""
        if instance_id not in self._template_store:
            cip_reply = bytes([_SVC_READ_TAG | 0x80, 0x00, _CIP_ATTR_NOT_SUPPORTED, 0x00])
            self._send_unit_data_reply(
                conn, session_handle, sender_context, seq_count, cip_reply, conn_state
            )
            return True

        entry = self._template_store[instance_id]
        # Parse request data: DINT(offset) + UINT(bytes_to_read)
        path_word_count = cip_data[1]
        data_start = 2 + path_word_count * 2
        if len(cip_data) < data_start + 6:
            return False
        offset = struct.unpack_from("<i", cip_data, data_start)[0]  # DINT (signed)
        bytes_requested = struct.unpack_from("<H", cip_data, data_start + 4)[0]

        remaining = entry.template_data[offset:]
        chunk_size = min(bytes_requested, self._template_frag_threshold)
        chunk = remaining[:chunk_size]
        more_data = len(remaining) > chunk_size

        status = _CIP_PARTIAL if more_data else _CIP_SUCCESS
        cip_reply = bytes([_SVC_READ_TAG | 0x80, 0x00, status, 0x00]) + chunk
        self._send_unit_data_reply(
            conn, session_handle, sender_context, seq_count, cip_reply, conn_state
        )
        return True

    def _send_unit_data_reply(
        self,
        conn: socket.socket,
        session_handle: int,
        sender_context: bytes,
        seq_count: int,
        cip_reply: bytes,
        conn_state: dict[str, Any],
    ) -> None:
        """Wrap a CIP reply in SendUnitData (command 0x70) and send it."""
        ot_id = conn_state["ot_connection_id"]
        connected_data = struct.pack("<H", seq_count) + cip_reply
        cpf = (
            b"\x00\x00\x00\x00"  # interface handle
            + b"\x00\x00"  # timeout
            + build_cpf(
                [
                    CPFItem(CPFTypeCode.CONNECTED_ADDRESS, struct.pack("<I", ot_id)),
                    CPFItem(CPFTypeCode.CONNECTED_DATA, connected_data),
                ]
            )
        )
        reply_header = EncapsulationHeader(
            command=_CMD_SEND_UNIT_DATA,
            length=len(cpf),
            session_handle=session_handle,
            status=0,
            sender_context=sender_context,
            options=0,
        )
        conn.sendall(reply_header.encode() + cpf)


def _recv_exactly(conn: socket.socket, n: int) -> bytes:
    """Read exactly *n* bytes from *conn*; return ``b""`` on EOF."""
    buf = bytearray()
    while len(buf) < n:
        chunk = conn.recv(n - len(buf))
        if not chunk:
            return b""
        buf.extend(chunk)
    return bytes(buf)
