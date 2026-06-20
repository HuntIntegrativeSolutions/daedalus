"""Sans-I/O EtherNet/IP session state machine.

I/O-FORBIDDEN: this module must never import socket, ssl, asyncio, anyio,
selectors, socketserver, http, urllib, or requests. Pure state transitions only.
"""

from __future__ import annotations

from enum import Enum, auto

from daedalus.cip.services import EncapsulationCommand
from daedalus.exceptions import DataError, RequestError, ResponseError
from daedalus.packets.cip import build_register_session, build_unregister_session
from daedalus.packets.encap import EncapsulationHeader

__all__ = ["Session", "SessionState"]


class SessionState(Enum):
    """EtherNet/IP session lifecycle states."""

    IDLE = auto()
    REGISTERING = auto()
    REGISTERED = auto()


class Session:
    """Sans-I/O EtherNet/IP session state machine.

    Pattern (from h11): call an emit method to obtain bytes to send, then
    call the corresponding feed method to process the device's reply.
    State advances on *emit* (when the request is committed to the wire),
    not on receipt — this keeps the machine honest about what has been sent.

    Usage::

        session = Session()
        transport.send_frame(session.register_request())  # state → REGISTERING
        session.register_reply(transport.recv_frame())    # state → REGISTERED
        ...
        transport.send_frame(session.unregister_request())  # state → IDLE
        # no reply expected; device closes the connection

    Neither this class nor any module in ``session/`` may touch a socket.
    """

    def __init__(self) -> None:
        self._state: SessionState = SessionState.IDLE
        self._session_handle: int = 0

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def state(self) -> SessionState:
        """Current lifecycle state."""
        return self._state

    @property
    def session_handle(self) -> int:
        """Session handle assigned by the device; 0 until registered."""
        return self._session_handle

    @property
    def registered(self) -> bool:
        """``True`` when a session handle has been assigned and confirmed."""
        return self._state == SessionState.REGISTERED

    # ------------------------------------------------------------------
    # Emit methods — return bytes for the caller to send
    # ------------------------------------------------------------------

    def register_request(self) -> bytes:
        """Return a RegisterSession encapsulation frame to send.

        Transitions state IDLE → REGISTERING.  State will advance to
        REGISTERED only after a valid reply is fed via :meth:`register_reply`.

        Raises:
            RequestError: if not in IDLE state (session already pending or
                registered).
        """
        if self._state != SessionState.IDLE:
            raise RequestError(
                f"register_request() requires IDLE state, current state: {self._state.name}"
            )
        self._state = SessionState.REGISTERING
        return build_register_session()

    def unregister_request(self) -> bytes:
        """Return an UnregisterSession encapsulation frame to send.

        Resets local state to IDLE immediately — no reply is expected from
        the device; it closes the TCP connection on receipt.

        Raises:
            RequestError: if not in REGISTERED state.
        """
        if self._state != SessionState.REGISTERED:
            raise RequestError(
                f"unregister_request() requires REGISTERED state, current state: {self._state.name}"
            )
        handle = self._session_handle
        self._session_handle = 0
        self._state = SessionState.IDLE
        return build_unregister_session(handle)

    # ------------------------------------------------------------------
    # Feed methods — process raw reply bytes from the device
    # ------------------------------------------------------------------

    def register_reply(self, frame: bytes) -> None:
        """Feed the device's RegisterSession reply.

        Validates the encapsulation header fields and stores the assigned
        ``session_handle``.  Advances state REGISTERING → REGISTERED on success.

        Args:
            frame: raw bytes received from the device (24-byte header + payload).

        Raises:
            RequestError: if not in REGISTERING state.
            DataError / BufferEmptyError: if the frame is too short to decode.
            ResponseError: if the command code, encap status, or session handle
                is invalid.
        """
        if self._state != SessionState.REGISTERING:
            raise RequestError(
                f"register_reply() requires REGISTERING state, current state: {self._state.name}"
            )
        if len(frame) < 24:
            raise DataError(
                f"RegisterSession reply too short: {len(frame)} bytes (minimum 24)"
            )
        header = EncapsulationHeader.decode(frame)
        if header.command != int(EncapsulationCommand.REGISTER_SESSION):
            raise ResponseError(
                f"Expected REGISTER_SESSION reply (0x65), got 0x{header.command:02x}"
            )
        if header.status != 0:
            raise ResponseError(
                f"RegisterSession failed: encap status 0x{header.status:08x}"
            )
        if header.session_handle == 0:
            raise ResponseError("RegisterSession returned session_handle=0")
        self._session_handle = header.session_handle
        self._state = SessionState.REGISTERED
