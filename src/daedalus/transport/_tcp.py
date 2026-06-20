"""Synchronous TCP transport for EtherNet/IP.

This is the ONLY module in daedalus permitted to import ``socket``.  All
other layers (cip/, packets/, session/, drivers/) are I/O-FORBIDDEN.

Responsibility: move raw EtherNet/IP frames between caller and device.
This module does NOT parse CIP — it reads the 2-byte little-endian length
field at offset 2 of the 24-byte encapsulation header to know how many
payload bytes follow, and that is the full extent of its knowledge about
the frame format.
"""

from __future__ import annotations

import contextlib
import socket
import struct
from types import TracebackType

from daedalus.exceptions import CommError

__all__ = ["SyncTcpTransport"]

_HEADER_SIZE: int = 24
_LENGTH_OFFSET: int = 2  # little-endian UINT16 payload-length field in encap header


class SyncTcpTransport:
    """Synchronous TCP byte-mover for EtherNet/IP.

    Provides ``send_frame`` / ``recv_frame`` primitives plus context-manager
    support.  Connect before calling either frame method.

    Usage::

        with SyncTcpTransport("192.168.1.10") as t:
            t.send_frame(session.register_request())
            session.register_reply(t.recv_frame())
            t.send_frame(session.unregister_request())
            # no recv — device closes the connection
    """

    def __init__(
        self,
        host: str,
        port: int = 44818,
        timeout: float = 5.0,
    ) -> None:
        self._host = host
        self._port = port
        self._timeout = timeout
        self._sock: socket.socket | None = None

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def __enter__(self) -> SyncTcpTransport:
        self.connect()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def connect(self) -> None:
        """Open the TCP connection.

        Raises:
            CommError: if the connection cannot be established.
        """
        try:
            self._sock = socket.create_connection((self._host, self._port), timeout=self._timeout)
        except OSError as exc:
            raise CommError(f"Cannot connect to {self._host}:{self._port}: {exc}") from exc

    def close(self) -> None:
        """Close the TCP connection. Idempotent; silently ignores errors."""
        sock, self._sock = self._sock, None
        if sock is not None:
            with contextlib.suppress(OSError):
                sock.close()

    # ------------------------------------------------------------------
    # Frame I/O
    # ------------------------------------------------------------------

    def send_frame(self, data: bytes) -> None:
        """Send one encapsulation frame to the device.

        Raises:
            CommError: if not connected or if the send fails.
        """
        if self._sock is None:
            raise CommError("Not connected")
        try:
            self._sock.sendall(data)
        except OSError as exc:
            raise CommError(f"Send failed: {exc}") from exc

    def recv_frame(self) -> bytes:
        """Receive one encapsulation frame (24-byte header + declared payload).

        Reads exactly 24 bytes for the encapsulation header, extracts the
        payload length from the little-endian UINT16 at byte offset 2, then
        reads that many additional bytes.

        Raises:
            CommError: on I/O error, timeout, or unexpected connection close.
        """
        header = self._recv_exactly(_HEADER_SIZE)
        data_len = struct.unpack_from("<H", header, _LENGTH_OFFSET)[0]
        payload = self._recv_exactly(data_len) if data_len else b""
        return header + payload

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _recv_exactly(self, n: int) -> bytes:
        """Loop ``recv()`` until exactly *n* bytes are accumulated.

        Raises:
            CommError: if not connected, on I/O error, or if the peer closes
                the connection before *n* bytes arrive.
        """
        if self._sock is None:
            raise CommError("Not connected")
        buf = bytearray()
        while len(buf) < n:
            try:
                chunk = self._sock.recv(n - len(buf))
            except (OSError, TimeoutError) as exc:
                raise CommError(f"Recv failed: {exc}") from exc
            if not chunk:
                raise CommError("Connection closed by peer before full frame received")
            buf.extend(chunk)
        return bytes(buf)
