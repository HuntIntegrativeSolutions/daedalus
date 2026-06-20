"""Sans-I/O EtherNet/IP session state machine.

I/O-FORBIDDEN: this module must never import socket, ssl, asyncio, anyio,
selectors, socketserver, http, urllib, or requests. Pure state transitions only.
"""

from __future__ import annotations

from enum import Enum, auto

from daedalus.cip.services import EncapsulationCommand
from daedalus.exceptions import (
    DataError,
    ForwardOpenError,
    RequestError,
    ResponseError,
)
from daedalus.packets.cip import MSG_ROUTER_PATH, build_register_session, build_unregister_session
from daedalus.packets.encap import EncapsulationHeader
from daedalus.packets.forward_open import (
    _DEFAULT_CONN_SERIAL,
    _DEFAULT_LARGE_CONN_SIZE,
    _DEFAULT_ORIG_SERIAL,
    _DEFAULT_ORIG_VENDOR_ID,
    _DEFAULT_RPI_US,
    _DEFAULT_STD_CONN_SIZE,
    _DEFAULT_TO_CONN_ID,
    build_forward_close,
    build_forward_open,
    parse_forward_close_reply,
    parse_forward_open_reply,
)

__all__ = ["Session", "SessionState"]


class SessionState(Enum):
    """EtherNet/IP session lifecycle states."""

    IDLE = auto()
    REGISTERING = auto()
    REGISTERED = auto()
    CONNECTING = auto()
    CONNECTED = auto()
    CLOSING = auto()


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
        # Forward_Open state — zeroed until CONNECTED
        self._ot_connection_id: int = 0
        self._to_connection_id: int = 0
        self._connection_serial: int = 0
        self._originator_vendor_id: int = 0
        self._originator_serial: int = 0
        self._connection_path: bytes = b""
        self._last_fo_was_large: bool = False

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

    @property
    def connecting(self) -> bool:
        """``True`` while waiting for a Forward_Open reply."""
        return self._state == SessionState.CONNECTING

    @property
    def connected(self) -> bool:
        """``True`` when a CIP connection is open (Forward_Open succeeded)."""
        return self._state == SessionState.CONNECTED

    @property
    def ot_connection_id(self) -> int:
        """Device-assigned O→T connection ID; 0 until CONNECTED."""
        return self._ot_connection_id

    @property
    def connection_serial(self) -> int:
        """Originator-assigned connection serial number; 0 until CONNECTED."""
        return self._connection_serial

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

    # ------------------------------------------------------------------
    # Forward_Open / Large_Forward_Open
    # ------------------------------------------------------------------

    def forward_open_request(
        self,
        *,
        large: bool = True,
        connection_size: int | None = None,
        rpi: int = _DEFAULT_RPI_US,
        connection_path: bytes = MSG_ROUTER_PATH,
        to_connection_id: int = _DEFAULT_TO_CONN_ID,
        connection_serial: int = _DEFAULT_CONN_SERIAL,
        originator_vendor_id: int = _DEFAULT_ORIG_VENDOR_ID,
        originator_serial: int = _DEFAULT_ORIG_SERIAL,
    ) -> bytes:
        """Return a Forward_Open or Large_Forward_Open SendRRData frame to send.

        Transitions state REGISTERED → CONNECTING.

        Args:
            large: True (default) → Large_Forward_Open (0x5B); False → standard (0x54).
            connection_size: Max payload bytes; defaults to 4000 (large) or 500 (std).
            rpi: Requested Packet Interval in µs.
            connection_path: Encoded PADDED_EPATH for the connection target.
            to_connection_id: Originator-assigned T→O connection ID.
            connection_serial: Connection serial (echoed in Forward_Close).
            originator_vendor_id: Our vendor ID.
            originator_serial: Our serial number.

        Raises:
            RequestError: if not in REGISTERED state.
        """
        if self._state != SessionState.REGISTERED:
            raise RequestError(
                f"forward_open_request() requires REGISTERED state, current: {self._state.name}"
            )
        if connection_size is None:
            connection_size = _DEFAULT_LARGE_CONN_SIZE if large else _DEFAULT_STD_CONN_SIZE

        # Store params for use in forward_close_request
        self._to_connection_id = to_connection_id
        self._connection_serial = connection_serial
        self._originator_vendor_id = originator_vendor_id
        self._originator_serial = originator_serial
        self._connection_path = connection_path
        self._last_fo_was_large = large

        self._state = SessionState.CONNECTING
        return build_forward_open(
            session_handle=self._session_handle,
            large=large,
            connection_size=connection_size,
            rpi=rpi,
            to_connection_id=to_connection_id,
            connection_serial=connection_serial,
            originator_vendor_id=originator_vendor_id,
            originator_serial=originator_serial,
            connection_path=connection_path,
        )

    def forward_open_reply(self, frame: bytes) -> None:
        """Feed the device's Forward_Open reply.

        Advances state CONNECTING → CONNECTED on success.  On failure, resets
        to REGISTERED before re-raising so the caller may retry.

        Raises:
            RequestError: if not in CONNECTING state.
            DataError: if the frame is malformed.
            LargeForwardOpenRejected: CIP status 0x08 on a Large_Forward_Open attempt.
                State is reset to REGISTERED; caller may retry with large=False.
            ForwardOpenError: any other non-zero CIP status.
                State is reset to REGISTERED.
        """
        if self._state != SessionState.CONNECTING:
            raise RequestError(
                f"forward_open_reply() requires CONNECTING state, current: {self._state.name}"
            )
        try:
            reply = parse_forward_open_reply(frame, was_large=self._last_fo_was_large)
        except (ForwardOpenError, DataError):
            self._state = SessionState.REGISTERED
            raise

        self._ot_connection_id = reply.ot_connection_id
        self._state = SessionState.CONNECTED

    # ------------------------------------------------------------------
    # Forward_Close
    # ------------------------------------------------------------------

    def forward_close_request(self) -> bytes:
        """Return a Forward_Close SendRRData frame to send.

        Transitions state CONNECTED → CLOSING.

        Raises:
            RequestError: if not in CONNECTED state.
        """
        if self._state != SessionState.CONNECTED:
            raise RequestError(
                f"forward_close_request() requires CONNECTED state, current: {self._state.name}"
            )
        self._state = SessionState.CLOSING
        return build_forward_close(
            session_handle=self._session_handle,
            connection_serial=self._connection_serial,
            originator_vendor_id=self._originator_vendor_id,
            originator_serial=self._originator_serial,
            connection_path=self._connection_path,
        )

    def forward_close_reply(self, frame: bytes) -> None:
        """Feed the device's Forward_Close reply.

        Always resets to REGISTERED (connection state is indeterminate after
        a FC, whether the reply succeeds or fails).

        Raises:
            RequestError: if not in CLOSING state.
            DataError: if the frame is malformed.
            ResponseError: if the device returned a non-zero CIP status.
        """
        if self._state != SessionState.CLOSING:
            raise RequestError(
                f"forward_close_reply() requires CLOSING state, current: {self._state.name}"
            )
        try:
            parse_forward_close_reply(frame)
        finally:
            self._ot_connection_id = 0
            self._state = SessionState.REGISTERED

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
            raise DataError(f"RegisterSession reply too short: {len(frame)} bytes (minimum 24)")
        header = EncapsulationHeader.decode(frame)
        if header.command != int(EncapsulationCommand.REGISTER_SESSION):
            raise ResponseError(
                f"Expected REGISTER_SESSION reply (0x65), got 0x{header.command:02x}"
            )
        if header.status != 0:
            raise ResponseError(f"RegisterSession failed: encap status 0x{header.status:08x}")
        if header.session_handle == 0:
            raise ResponseError("RegisterSession returned session_handle=0")
        self._session_handle = header.session_handle
        self._state = SessionState.REGISTERED
