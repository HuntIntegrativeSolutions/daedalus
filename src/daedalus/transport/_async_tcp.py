"""Asynchronous TCP transport for EtherNet/IP (anyio-based).

This is the ONLY async-socket module in daedalus. anyio is permitted here
(L1 transport) and nowhere else. All other layers (cip/, packets/, session/,
drivers/) are I/O-FORBIDDEN.

Responsibility: move raw EtherNet/IP frames between caller and device.
This module does NOT parse CIP — it reads the 2-byte little-endian length
field at offset 2 of the 24-byte encapsulation header to know how many
payload bytes follow, and that is the full extent of its knowledge about
the frame format.
"""

from __future__ import annotations

import struct
from types import TracebackType

import anyio
import anyio.abc
from anyio.streams.buffered import BufferedByteReceiveStream

from daedalus.exceptions import CommError

__all__ = ["AsyncTcpTransport"]

_HEADER_SIZE: int = 24
_LENGTH_OFFSET: int = 2  # little-endian UINT16 payload-length field in encap header


class AsyncTcpTransport:
    """Async TCP byte-mover for EtherNet/IP (anyio backend).

    Mirrors SyncTcpTransport's contract. Connect before calling either frame
    method. The ``send_recv`` method is the callable injected into
    ``_run_async`` on AsyncLogixDriver.

    Usage::

        async with AsyncTcpTransport("192.168.1.10") as t:
            await t.send_frame(session.register_request())
            session.register_reply(await t.recv_frame())
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
        self._stream: anyio.abc.SocketStream | None = None
        self._buffered: BufferedByteReceiveStream | None = None

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    async def __aenter__(self) -> AsyncTcpTransport:
        await self.connect()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        await self.close()

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Open the TCP connection.

        Raises:
            CommError: if the connection cannot be established.
        """
        try:
            stream = await anyio.connect_tcp(self._host, self._port)
        except OSError as exc:
            raise CommError(f"Cannot connect to {self._host}:{self._port}: {exc}") from exc
        self._stream = stream
        self._buffered = BufferedByteReceiveStream(stream)

    async def close(self) -> None:
        """Close the TCP connection. Idempotent; silently ignores errors."""
        stream, self._stream = self._stream, None
        self._buffered = None
        if stream is not None:
            with anyio.CancelScope(shield=True):
                await stream.aclose()

    # ------------------------------------------------------------------
    # Frame I/O
    # ------------------------------------------------------------------

    async def send_frame(self, data: bytes) -> None:
        """Send one encapsulation frame to the device.

        Raises:
            CommError: if not connected or if the send fails.
        """
        if self._stream is None:
            raise CommError("Not connected")
        try:
            await self._stream.send(data)
        except (anyio.BrokenResourceError, anyio.ClosedResourceError, OSError) as exc:
            raise CommError(f"Send failed: {exc}") from exc

    async def recv_frame(self) -> bytes:
        """Receive one encapsulation frame (24-byte header + declared payload).

        Reads exactly 24 bytes for the encapsulation header, extracts the
        payload length from the little-endian UINT16 at byte offset 2, then
        reads that many additional bytes.

        Raises:
            CommError: on I/O error, timeout, or unexpected connection close.
        """
        if self._buffered is None:
            raise CommError("Not connected")
        try:
            with anyio.fail_after(self._timeout):
                header = await self._buffered.receive_exactly(_HEADER_SIZE)
                data_len = struct.unpack_from("<H", header, _LENGTH_OFFSET)[0]
                payload = await self._buffered.receive_exactly(data_len) if data_len else b""
        except TimeoutError as exc:
            raise CommError(f"Recv timed out after {self._timeout}s") from exc
        except anyio.IncompleteRead as exc:
            raise CommError("Connection closed by peer before full frame received") from exc
        except anyio.EndOfStream as exc:
            raise CommError("Connection closed by peer before full frame received") from exc
        except (anyio.BrokenResourceError, anyio.ClosedResourceError, OSError) as exc:
            raise CommError(f"Recv failed: {exc}") from exc
        return header + payload

    # ------------------------------------------------------------------
    # Convenience method — the callable signature expected by _run_async
    # ------------------------------------------------------------------

    async def send_recv(self, frame: bytes) -> bytes:
        """Send a frame and return the reply."""
        await self.send_frame(frame)
        return await self.recv_frame()
