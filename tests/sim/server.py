"""Minimal in-process CIP sim server for tests.

Answers RegisterSession (assigns a handle) and UnregisterSession (closes the
connection per the ODVA spec).  Runs in a daemon thread; bind to port 0 so
the OS assigns an ephemeral port.

This is the foundation for later driver tests — keep it clean and extendable,
but only implement what Phase 2a needs.
"""

from __future__ import annotations

import contextlib
import secrets
import socket
import struct
import threading

from daedalus.packets.encap import EncapsulationHeader

__all__ = ["CipSimServer"]

_HEADER_SIZE: int = 24
_CMD_REGISTER_SESSION: int = 0x65
_CMD_UNREGISTER_SESSION: int = 0x66


class CipSimServer:
    """In-process TCP server that speaks the minimum EtherNet/IP for
    RegisterSession / UnregisterSession round-trips.

    The server binds to ``127.0.0.1`` on an ephemeral port chosen by the OS.
    Read :attr:`host` and :attr:`port` after :meth:`start` to connect.

    Protocol behaviour:
    - **RegisterSession** (0x65): assigns a cryptographically random non-zero
      session handle, sends the 28-byte reply, and stays connected.
    - **UnregisterSession** (0x66): closes the connection per ODVA (no reply).
    - Any other command: closes the connection.
    """

    def __init__(self) -> None:
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
                if data_len:
                    _recv_exactly(conn, data_len)  # consume payload (not needed for Phase 2a)

                if command == _CMD_REGISTER_SESSION:
                    self._reply_register(conn, raw_header)
                elif command == _CMD_UNREGISTER_SESSION:
                    break  # ODVA: device closes the connection, no reply
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
        sender_context = request_header[8:16]  # echo back the request's context
        reply_header = EncapsulationHeader(
            command=_CMD_REGISTER_SESSION,
            length=4,
            session_handle=handle,
            status=0,
            sender_context=sender_context,
            options=0,
        )
        reply = reply_header.encode() + b"\x01\x00\x00\x00"  # EIP version 1, options 0
        conn.sendall(reply)


def _recv_exactly(conn: socket.socket, n: int) -> bytes:
    """Read exactly *n* bytes from *conn*; return ``b""`` on EOF."""
    buf = bytearray()
    while len(buf) < n:
        chunk = conn.recv(n - len(buf))
        if not chunk:
            return b""
        buf.extend(chunk)
    return bytes(buf)
