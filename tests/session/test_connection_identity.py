"""Tests verifying that Session generates fresh connection identifiers per Forward_Open.

These tests prove:
- Seeded Sessions are byte-for-byte reproducible (RNG injection works).
- Different seeds → different connection_serial and to_connection_id (reconnect safety).
- Explicit caller-supplied values are honored exactly (parity oracle guard).
- Generated values are in valid CIP range and non-zero.
"""

from __future__ import annotations

import random
import struct

from daedalus.packets.encap import EncapsulationHeader
from daedalus.session import Session, SessionState

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SESSION_HANDLE = 0xABCD


def _registered_session(seed: int | None = None) -> Session:
    """Return a Session in REGISTERED state; optionally inject a seeded RNG."""
    rng = random.Random(seed) if seed is not None else None
    s = Session(rng=rng)
    s.register_request()
    header = EncapsulationHeader(
        command=0x65,
        length=4,
        session_handle=_SESSION_HANDLE,
        status=0,
        sender_context=b"\x00" * 8,
        options=0,
    )
    s.register_reply(header.encode() + b"\x01\x00\x00\x00")
    return s


# ---------------------------------------------------------------------------
# Reproducibility
# ---------------------------------------------------------------------------


def test_seeded_session_reproducible() -> None:
    """Two Sessions with the same seed produce byte-for-byte identical FO frames."""
    s1 = _registered_session(seed=42)
    s2 = _registered_session(seed=42)
    frame1 = s1.forward_open_request(large=False)
    frame2 = s2.forward_open_request(large=False)
    assert frame1 == frame2


def test_same_seed_same_connection_serial() -> None:
    """Same seed → same connection_serial stored on the Session after FO."""
    s1 = _registered_session(seed=99)
    s2 = _registered_session(seed=99)
    s1.forward_open_request(large=False)
    s2.forward_open_request(large=False)
    assert s1.connection_serial == s2.connection_serial


# ---------------------------------------------------------------------------
# Distinctness
# ---------------------------------------------------------------------------


def test_distinct_seeds_produce_distinct_serials() -> None:
    """Different seeds produce different connection_serial values (deterministic proof)."""
    s1 = _registered_session(seed=1)
    s2 = _registered_session(seed=2)
    s1.forward_open_request(large=False)
    s2.forward_open_request(large=False)
    # Seeds 1 and 2 are chosen to produce distinct 16-bit serials; if this ever fails
    # due to a Python version change, pick different seeds — do not weaken to a warning.
    assert s1.connection_serial != s2.connection_serial


def test_distinct_seeds_produce_distinct_to_connection_ids() -> None:
    """Different seeds produce different to_connection_id values in the FO frame."""
    s1 = _registered_session(seed=3)
    s2 = _registered_session(seed=4)
    f1 = s1.forward_open_request(large=False)
    f2 = s2.forward_open_request(large=False)
    # Frames must differ (connection IDs are in the payload, so if IDs differ, frames differ)
    assert f1 != f2


# ---------------------------------------------------------------------------
# Explicit overrides are honored
# ---------------------------------------------------------------------------


def test_explicit_connection_serial_in_frame() -> None:
    """Explicit connection_serial appears verbatim in the encoded FO frame."""
    s = _registered_session(seed=0)
    frame = s.forward_open_request(large=False, connection_serial=0x1234)
    assert s.connection_serial == 0x1234
    assert struct.pack("<H", 0x1234) in frame


def test_explicit_to_connection_id_in_frame() -> None:
    """Explicit to_connection_id appears verbatim in the encoded FO frame."""
    s = _registered_session(seed=0)
    frame = s.forward_open_request(large=False, to_connection_id=0xABCD1234)
    assert struct.pack("<I", 0xABCD1234) in frame


def test_explicit_originator_serial_in_frame() -> None:
    """Explicit originator_serial appears verbatim in the encoded FO frame."""
    s = _registered_session(seed=0)
    frame = s.forward_open_request(large=False, originator_serial=0xDEADBEEF)
    assert struct.pack("<I", 0xDEADBEEF) in frame


def test_all_explicit_overrides_honored() -> None:
    """All three IDs can be pinned simultaneously; bytes must match exactly."""
    s = _registered_session(seed=0)
    frame = s.forward_open_request(
        large=False,
        connection_serial=0x5A5A,
        to_connection_id=0x12345678,
        originator_serial=0xCAFEBABE,
    )
    assert s.connection_serial == 0x5A5A
    assert struct.pack("<H", 0x5A5A) in frame
    assert struct.pack("<I", 0x12345678) in frame
    assert struct.pack("<I", 0xCAFEBABE) in frame


# ---------------------------------------------------------------------------
# Valid range and non-zero
# ---------------------------------------------------------------------------


def test_generated_connection_serial_in_valid_range() -> None:
    """Default connection_serial must be 1-0xFFFF (16-bit, non-zero)."""
    for i in range(20):
        s = _registered_session(seed=i)
        s.forward_open_request(large=False)
        assert 1 <= s.connection_serial <= 0xFFFF, (
            f"seed={i}: connection_serial=0x{s.connection_serial:x} out of range"
        )


def test_originator_serial_stable_across_reconnects() -> None:
    """The per-Session originator_serial is the same on two successive FO calls."""
    s = _registered_session(seed=11)

    # First connection
    s.forward_open_request(large=False)
    first_orig_serial = s._originator_serial

    # Simulate reconnect: reset to REGISTERED without a real FC reply
    s._state = SessionState.REGISTERED  # bypass FC handshake for isolation

    # Second connection
    s.forward_open_request(large=False)
    second_orig_serial = s._originator_serial

    assert first_orig_serial == second_orig_serial, (
        "originator_serial must be stable across reconnects within the same Session"
    )


def test_originator_serial_assigned_nonzero() -> None:
    """The Session-assigned originator_serial is never zero."""
    for seed in range(50):
        s = Session(rng=random.Random(seed))
        assert s._originator_serial_assigned != 0, f"seed={seed}: originator_serial_assigned=0"
