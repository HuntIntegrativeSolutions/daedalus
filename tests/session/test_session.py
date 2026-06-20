"""Unit tests for the Session sans-I/O state machine.

No sockets here — all replies are built synthetically from L0 primitives.
"""

from __future__ import annotations

import pytest

from daedalus.cip.services import EncapsulationCommand
from daedalus.exceptions import DataError, RequestError, ResponseError
from daedalus.packets.encap import EncapsulationHeader
from daedalus.session import Session, SessionState

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_register_reply(
    session_handle: int = 0x1234,
    status: int = 0,
    command: int = int(EncapsulationCommand.REGISTER_SESSION),
) -> bytes:
    """Build a synthetic RegisterSession reply frame."""
    header = EncapsulationHeader(
        command=command,
        length=4,
        session_handle=session_handle,
        status=status,
        sender_context=b"\x00" * 8,
        options=0,
    )
    return header.encode() + b"\x01\x00\x00\x00"


# ---------------------------------------------------------------------------
# Initial state
# ---------------------------------------------------------------------------


def test_initial_state_is_idle() -> None:
    session = Session()
    assert session.state == SessionState.IDLE
    assert not session.registered
    assert session.session_handle == 0


# ---------------------------------------------------------------------------
# register_request
# ---------------------------------------------------------------------------


def test_register_request_bytes_length() -> None:
    session = Session()
    data = session.register_request()
    assert len(data) == 28  # 24-byte header + 4-byte payload (version + options)


def test_register_request_transitions_to_registering() -> None:
    session = Session()
    session.register_request()
    assert session.state == SessionState.REGISTERING


def test_register_request_from_idle_only() -> None:
    """register_request() is only valid from IDLE; a second call must raise."""
    session = Session()
    session.register_request()
    with pytest.raises(RequestError):
        session.register_request()


# ---------------------------------------------------------------------------
# register_reply — success path
# ---------------------------------------------------------------------------


def test_register_reply_sets_handle() -> None:
    session = Session()
    session.register_request()
    session.register_reply(_make_register_reply(session_handle=0xABCD))
    assert session.registered
    assert session.session_handle == 0xABCD
    assert session.state == SessionState.REGISTERED


# ---------------------------------------------------------------------------
# register_reply — error paths
# ---------------------------------------------------------------------------


def test_register_reply_bad_command() -> None:
    session = Session()
    session.register_request()
    bad_reply = _make_register_reply(command=0x6F)  # SEND_RR_DATA, not REGISTER_SESSION
    with pytest.raises(ResponseError, match="REGISTER_SESSION"):
        session.register_reply(bad_reply)


def test_register_reply_nonzero_status() -> None:
    session = Session()
    session.register_request()
    with pytest.raises(ResponseError, match="status"):
        session.register_reply(_make_register_reply(status=0x0001))


def test_register_reply_zero_handle() -> None:
    session = Session()
    session.register_request()
    with pytest.raises(ResponseError, match="session_handle=0"):
        session.register_reply(_make_register_reply(session_handle=0))


def test_register_reply_short_frame() -> None:
    """Frames shorter than 24 bytes must surface as DataError, not struct.error."""
    session = Session()
    session.register_request()
    with pytest.raises(DataError):
        session.register_reply(b"\x65\x00\x04\x00")  # 4 bytes — far too short


def test_register_reply_requires_registering_state() -> None:
    """register_reply() from IDLE (without a prior request) must raise."""
    session = Session()
    with pytest.raises(RequestError):
        session.register_reply(_make_register_reply())


# ---------------------------------------------------------------------------
# unregister_request
# ---------------------------------------------------------------------------


def test_unregister_request_bytes_length() -> None:
    session = Session()
    session.register_request()
    session.register_reply(_make_register_reply())
    data = session.unregister_request()
    assert len(data) == 24  # header only; UnregisterSession carries no payload


def test_unregister_resets_state() -> None:
    session = Session()
    session.register_request()
    session.register_reply(_make_register_reply())
    assert session.registered
    session.unregister_request()
    assert not session.registered
    assert session.session_handle == 0
    assert session.state == SessionState.IDLE


def test_unregister_requires_registered() -> None:
    session = Session()
    with pytest.raises(RequestError):
        session.unregister_request()


def test_full_lifecycle() -> None:
    """IDLE → REGISTERING → REGISTERED → IDLE round-trip."""
    session = Session()
    session.register_request()
    assert session.state == SessionState.REGISTERING
    session.register_reply(_make_register_reply(session_handle=42))
    assert session.registered  # REGISTERED state via bool property (avoids mypy narrowing)
    assert session.session_handle == 42
    session.unregister_request()
    assert not session.registered  # back to IDLE via bool property
    assert session.session_handle == 0
