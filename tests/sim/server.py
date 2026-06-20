"""Minimal in-process CIP sim server for tests.

Answers RegisterSession, UnregisterSession, Forward_Open (standard and Large),
and Forward_Close.  Runs in a daemon thread; bind to port 0 so the OS assigns
an ephemeral port.

Pass ``reject_large_fo=True`` to make the sim reject Large_Forward_Open with
CIP status 0x08 (Service Not Supported) — used to exercise the fallback path.
"""

from __future__ import annotations

import contextlib
import secrets
import socket
import struct
import threading

from daedalus.packets.encap import CPFItem, CPFTypeCode, EncapsulationHeader, build_cpf

__all__ = ["CipSimServer"]

_HEADER_SIZE: int = 24
_CMD_REGISTER_SESSION: int = 0x65
_CMD_UNREGISTER_SESSION: int = 0x66
_CMD_SEND_RR_DATA: int = 0x6F

_SVC_FORWARD_OPEN: int = 0x54
_SVC_LARGE_FORWARD_OPEN: int = 0x5B
_SVC_FORWARD_CLOSE: int = 0x4E


class CipSimServer:
    """In-process TCP server that speaks the minimum EtherNet/IP for
    RegisterSession / UnregisterSession / Forward_Open / Forward_Close.

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
    - Any unrecognised command or service: closes the connection.
    """

    def __init__(self, *, reject_large_fo: bool = False) -> None:
        self._reject_large_fo = reject_large_fo
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
                    if not self._dispatch_send_rr_data(conn, raw_header, payload):
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

    def _dispatch_send_rr_data(
        self, conn: socket.socket, raw_header: bytes, payload: bytes
    ) -> bool:
        """Dispatch a SendRRData payload; return False to close the connection."""
        # payload layout: 4-byte interface_handle + 2-byte timeout + CPF
        if len(payload) < 6:
            return False
        # CPF data starts at offset 6
        cpf_data = payload[6:]
        if len(cpf_data) < 2:
            return False
        item_count = struct.unpack_from("<H", cpf_data, 0)[0]
        if item_count < 2:
            return False

        # Parse CPF items manually to avoid import of parse_cpf in test infra
        # (parse_cpf is available from daedalus.packets.encap but we can use it directly)
        from daedalus.packets.encap import parse_cpf

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
                    conn, service, session_handle, sender_context, unconnected.data
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
    ) -> None:
        """Build and send a Forward_Open success reply."""
        # Extract fields from FO request data (after service byte + path).
        # Layout: [0]=service, [1]=path_word_count, [2..2+path_len-1]=path,
        #         [2+path_len..] = FO data fields.
        path_word_count = cip_request[1]
        fo_data_offset = 2 + path_word_count * 2
        fo_data = cip_request[fo_data_offset:]
        # FO data fields (same layout for standard and large up to offset 22):
        # [2-5]   O→T conn ID (we ignore, it's 0)
        # [6-9]   T→O conn ID
        # [10-11] connection serial
        # [12-13] originator vendor ID
        # [14-17] originator serial
        # [18]    timeout multiplier (skip)
        # [19-21] reserved (skip)
        # [22-25] O→T RPI
        if len(fo_data) < 26:
            return
        to_conn_id = struct.unpack_from("<I", fo_data, 6)[0]
        conn_serial = struct.unpack_from("<H", fo_data, 10)[0]
        orig_vendor_id = struct.unpack_from("<H", fo_data, 12)[0]
        orig_serial = struct.unpack_from("<I", fo_data, 14)[0]
        ot_rpi = struct.unpack_from("<I", fo_data, 22)[0]

        ot_conn_id = secrets.randbelow(0xFFFF_FFFF) + 1  # device assigns, never 0
        # CIP reply payload: 4-byte header + 26-byte FO success data = 30 bytes
        cip_reply = b"".join(
            [
                bytes([service | 0x80, 0x00, 0x00, 0x00]),  # svc|reply, rsvd, status, ext_size
                struct.pack("<I", ot_conn_id),
                struct.pack("<I", to_conn_id),
                struct.pack("<H", conn_serial),
                struct.pack("<H", orig_vendor_id),
                struct.pack("<I", orig_serial),
                struct.pack("<I", ot_rpi),  # O→T actual API = echo RPI
                struct.pack("<I", ot_rpi),  # T→O actual API = echo RPI
                b"\x00\x00",  # app_reply_size=0, reserved=0
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
        """Build and send a Forward_Open error reply."""
        cip_reply = bytes([service | 0x80, 0x00, status, 0x00])
        self._send_send_rr_data_reply(conn, session_handle, sender_context, cip_reply)

    def _reply_fc_success(
        self,
        conn: socket.socket,
        service: int,
        session_handle: int,
        sender_context: bytes,
    ) -> None:
        """Build and send a Forward_Close success reply (minimal)."""
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


def _recv_exactly(conn: socket.socket, n: int) -> bytes:
    """Read exactly *n* bytes from *conn*; return ``b""`` on EOF."""
    buf = bytearray()
    while len(buf) < n:
        chunk = conn.recv(n - len(buf))
        if not chunk:
            return b""
        buf.extend(chunk)
    return bytes(buf)
