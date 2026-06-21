"""End-to-end write tests through SyncTcpTransport + CipSimServer.

Safety invariants are the review priority:
  - read-only-by-default refuses writes outside armed()
  - dry-run builds bytes but never sends
  - bit-of-word writes are refused before any I/O
  - critic veto blocks the whole batch before any commit
  - verify-failed is surfaced when read-back diverges
  - armed() context manager reverts mode on exit (including on raise)

Functional tests confirm DINT/REAL/BOOL/array round-trips work.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from daedalus.cip.data_types import BOOL, DINT, REAL
from daedalus.drivers import LogixDriver
from daedalus.runtime.write_policy import WriteMode, WritePolicy
from daedalus.session import Session
from daedalus.transport import SyncTcpTransport
from sim.server import CipSimServer

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_send_recv(transport: SyncTcpTransport) -> Callable[[bytes], bytes]:
    def _inner(frame: bytes) -> bytes:
        transport.send_frame(frame)
        return transport.recv_frame()

    return _inner


def _open(
    sim: CipSimServer,
    policy: WritePolicy | None = None,
) -> tuple[Session, SyncTcpTransport, LogixDriver]:
    session = Session()
    transport = SyncTcpTransport(sim.host, sim.port)
    transport.connect()
    transport.send_frame(session.register_request())
    session.register_reply(transport.recv_frame())
    transport.send_frame(session.forward_open_request(large=False))
    session.forward_open_reply(transport.recv_frame())
    driver = LogixDriver(session, _make_send_recv(transport), policy=policy)
    return session, transport, driver


def _close(session: Session, transport: SyncTcpTransport) -> None:
    transport.send_frame(session.forward_close_request())
    session.forward_close_reply(transport.recv_frame())
    transport.send_frame(session.unregister_request())
    transport.close()


def _make_sim(tag_store: dict[str, tuple[int, bytes]], **kwargs: Any) -> CipSimServer:
    srv = CipSimServer(tag_store=tag_store, **kwargs)
    srv.start()
    return srv


# ---------------------------------------------------------------------------
# Safety: read-only-by-default
# ---------------------------------------------------------------------------


def test_e2e_read_only_refuses_without_armed() -> None:
    """Default policy must refuse writes — no armed() context."""
    srv = _make_sim({"ScratchDINT": (DINT.code, DINT.encode(0))})
    try:
        session, transport, driver = _open(srv)
        result = driver.write_tag("ScratchDINT", 42, data_type="DINT")
        assert result.error is not None
        assert "READ_ONLY" in result.error
        records = driver._policy.get_records()
        assert any(r.outcome == "denied" for r in records)
    finally:
        _close(session, transport)
        srv.stop()


def test_e2e_read_only_denies_without_altering_tag_store() -> None:
    """Refused write must NOT touch the controller — verify tag unchanged."""
    srv = _make_sim({"ScratchDINT": (DINT.code, DINT.encode(0))})
    try:
        session, transport, driver = _open(srv)
        driver.write_tag("ScratchDINT", 99, data_type="DINT")
        # Tag in sim should still be 0 (write never sent)
        assert srv._tag_store["ScratchDINT"] == (DINT.code, DINT.encode(0))
    finally:
        _close(session, transport)
        srv.stop()


# ---------------------------------------------------------------------------
# Safety: armed() context manager
# ---------------------------------------------------------------------------


def test_e2e_armed_context_manager_allows_write() -> None:
    """write_tag inside armed() block succeeds."""
    srv = _make_sim({"ScratchDINT": (DINT.code, DINT.encode(0))})
    try:
        session, transport, driver = _open(srv)
        with driver.armed():
            result = driver.write_tag("ScratchDINT", 42, data_type="DINT")
        assert result.error is None
        assert result.value == 42
    finally:
        _close(session, transport)
        srv.stop()


def test_e2e_armed_context_manager_reverts_mode_on_exit() -> None:
    """Mode must revert to READ_ONLY after armed() block exits normally."""
    srv = _make_sim({"ScratchDINT": (DINT.code, DINT.encode(0))})
    try:
        session, transport, driver = _open(srv)
        with driver.armed():
            assert driver._policy.mode == WriteMode.ARMED
        assert driver._policy.mode == WriteMode.READ_ONLY  # type: ignore[comparison-overlap]
    finally:
        _close(session, transport)
        srv.stop()


def test_e2e_armed_context_manager_reverts_mode_on_exception() -> None:
    """Mode must revert even when the block body raises."""
    srv = _make_sim({"ScratchDINT": (DINT.code, DINT.encode(0))})
    try:
        session, transport, driver = _open(srv)
        with pytest.raises(ValueError), driver.armed():
            raise ValueError("deliberate error")
        assert driver._policy.mode == WriteMode.READ_ONLY
    finally:
        _close(session, transport)
        srv.stop()


def test_e2e_second_write_in_armed_block_not_disarmed() -> None:
    """Two write_tag calls in one armed() block must both succeed."""
    srv = _make_sim(
        {
            "Tag1": (DINT.code, DINT.encode(0)),
            "Tag2": (DINT.code, DINT.encode(0)),
        }
    )
    try:
        session, transport, driver = _open(srv)
        with driver.armed():
            r1 = driver.write_tag("Tag1", 1, data_type="DINT")
            r2 = driver.write_tag("Tag2", 2, data_type="DINT")
        assert r1.error is None
        assert r2.error is None
    finally:
        _close(session, transport)
        srv.stop()


# ---------------------------------------------------------------------------
# Safety: dry-run never sends
# ---------------------------------------------------------------------------


def test_e2e_dry_run_never_calls_send_recv() -> None:
    """DRY_RUN mode must never send a WRITE_TAG to the controller."""
    srv = _make_sim({"ScratchDINT": (DINT.code, DINT.encode(0))})
    sent_frames: list[bytes] = []

    try:
        policy = WritePolicy(mode=WriteMode.DRY_RUN)
        session, transport, driver = _open(srv, policy=policy)

        # Patch send_recv to record frames
        real_sr = driver._send_recv

        def _recording(frame: bytes) -> bytes:
            sent_frames.append(frame)
            return real_sr(frame)

        driver._send_recv = _recording

        result = driver.write_tag("ScratchDINT", 99, data_type="DINT")
        assert result.error is None
        assert result.value == 99

        # No WRITE_TAG (0x4D) should appear in any sent frame
        write_tag_service = 0x4D
        for frame in sent_frames:
            # Frames contain EIP encapsulation; look for the CIP service byte
            # in the connected payload. The service appears after seq_count (2B).
            # We just check for absence of 0x4D in all frames — good enough for
            # a sim where no other service is 0x4D.
            assert write_tag_service not in frame[30:], (
                "WRITE_TAG service 0x4D found in frame — dry-run sent a write"
            )
    finally:
        _close(session, transport)
        srv.stop()


def test_e2e_dry_run_records_dry_run_outcome() -> None:
    """DRY_RUN write must be recorded with outcome="dry_run"."""
    srv = _make_sim({"ScratchDINT": (DINT.code, DINT.encode(0))})
    try:
        policy = WritePolicy(mode=WriteMode.DRY_RUN)
        session, transport, driver = _open(srv, policy=policy)
        driver.write_tag("ScratchDINT", 7, data_type="DINT")
        records = policy.get_records()
        assert any(r.outcome == "dry_run" for r in records)
    finally:
        _close(session, transport)
        srv.stop()


# ---------------------------------------------------------------------------
# Safety: bit-of-word refused
# ---------------------------------------------------------------------------


def test_e2e_bit_of_word_refused() -> None:
    """Tags like 'MyDINT.3' must be refused (requires RMW, not WRITE_TAG)."""
    srv = _make_sim({"MyDINT": (DINT.code, DINT.encode(0))})
    try:
        session, transport, driver = _open(srv)
        with driver.armed():
            result = driver.write_tag("MyDINT.3", True, data_type="BOOL")
        assert result.error is not None
        assert "bit" in result.error.lower() or "Bit" in result.error
        # Must be audited as denial
        records = driver._policy.get_records()
        assert any(r.outcome == "denied" and r.tag_name == "MyDINT.3" for r in records)
        # Tag store must be untouched
        assert srv._tag_store["MyDINT"] == (DINT.code, DINT.encode(0))
    finally:
        _close(session, transport)
        srv.stop()


def test_e2e_bit_of_word_refused_no_i_o_sent() -> None:
    """Bit-of-word refusal must happen before any I/O."""
    srv = _make_sim({"MyDINT": (DINT.code, DINT.encode(0))})
    sent: list[bytes] = []

    try:
        session, transport, driver = _open(srv)
        real_sr = driver._send_recv

        def _track(frame: bytes) -> bytes:
            sent.append(frame)
            return real_sr(frame)

        driver._send_recv = _track
        sent.clear()  # ignore setup frames

        with driver.armed():
            driver.write_tag("MyDINT.0", True, data_type="BOOL")

        # No frames should have been sent after arming (the refusal is pre-I/O)
        assert len(sent) == 0
    finally:
        _close(session, transport)
        srv.stop()


# ---------------------------------------------------------------------------
# Safety: verify-failed (mismatch) path
# ---------------------------------------------------------------------------


def test_e2e_verify_failed_on_mismatch() -> None:
    """When read-back differs from written value, outcome must be verify_failed."""
    srv = _make_sim(
        {"ScratchDINT": (DINT.code, DINT.encode(0))},
        mismatch_tags={"ScratchDINT"},
    )
    try:
        session, transport, driver = _open(srv)
        with driver.armed():
            result = driver.write_tag("ScratchDINT", 99, data_type="DINT")
        assert result.error is not None
        assert "verify" in result.error.lower() or "Verify" in result.error
        records = driver._policy.get_records()
        assert any(r.outcome == "verify_failed" for r in records)
    finally:
        _close(session, transport)
        srv.stop()


# ---------------------------------------------------------------------------
# Safety: batch critic
# ---------------------------------------------------------------------------


def test_e2e_critic_veto_blocks_entire_batch() -> None:
    """Critic veto must prevent ALL commits in the batch."""
    srv = _make_sim(
        {
            "Tag1": (DINT.code, DINT.encode(0)),
            "Tag2": (DINT.code, DINT.encode(0)),
        }
    )
    try:
        policy = WritePolicy(critic=lambda names: "Batch rejected by policy")
        session, transport, driver = _open(srv, policy=policy)
        with driver.armed():
            results = driver.write_tags([("Tag1", 1), ("Tag2", 2)], data_type="DINT")
        for r in results:
            assert r.error is not None
        # Tag store must be untouched
        assert srv._tag_store["Tag1"] == (DINT.code, DINT.encode(0))
        assert srv._tag_store["Tag2"] == (DINT.code, DINT.encode(0))
    finally:
        _close(session, transport)
        srv.stop()


def test_e2e_critic_approval_allows_batch() -> None:
    """Critic returning True must permit all writes."""
    srv = _make_sim(
        {
            "Tag1": (DINT.code, DINT.encode(0)),
            "Tag2": (DINT.code, DINT.encode(0)),
        }
    )
    try:
        policy = WritePolicy(critic=lambda names: True)
        session, transport, driver = _open(srv, policy=policy)
        with driver.armed():
            results = driver.write_tags([("Tag1", 1), ("Tag2", 2)], data_type="DINT")
        for r in results:
            assert r.error is None
    finally:
        _close(session, transport)
        srv.stop()


def test_e2e_critic_sees_full_batch_before_commit() -> None:
    """Confirm critic receives all tag names at once (not per-tag)."""
    seen_batches: list[list[str]] = []

    def _critic(names: list[str]) -> bool:
        seen_batches.append(names)
        return True

    srv = _make_sim(
        {
            "A": (DINT.code, DINT.encode(0)),
            "B": (DINT.code, DINT.encode(0)),
            "C": (DINT.code, DINT.encode(0)),
        }
    )
    try:
        policy = WritePolicy(critic=_critic)
        session, transport, driver = _open(srv, policy=policy)
        with driver.armed():
            driver.write_tags([("A", 1), ("B", 2), ("C", 3)], data_type="DINT")
        assert len(seen_batches) == 1
        assert set(seen_batches[0]) == {"A", "B", "C"}
    finally:
        _close(session, transport)
        srv.stop()


# ---------------------------------------------------------------------------
# Functional: scalar writes
# ---------------------------------------------------------------------------


def test_e2e_write_dint_scalar() -> None:
    srv = _make_sim({"ScratchDINT": (DINT.code, DINT.encode(0))})
    try:
        session, transport, driver = _open(srv)
        with driver.armed():
            result = driver.write_tag("ScratchDINT", 12345, data_type="DINT")
        assert result.error is None
        assert result.value == 12345
        assert srv._tag_store["ScratchDINT"] == (DINT.code, DINT.encode(12345))
    finally:
        _close(session, transport)
        srv.stop()


def test_e2e_write_bool_scalar() -> None:
    srv = _make_sim({"ScratchBOOL": (BOOL.code, BOOL.encode(False))})
    try:
        session, transport, driver = _open(srv)
        with driver.armed():
            result = driver.write_tag("ScratchBOOL", True, data_type="BOOL")
        assert result.error is None
        # Read-back verify: encoded True is 0xFF
        assert srv._tag_store["ScratchBOOL"] == (BOOL.code, b"\xff")
    finally:
        _close(session, transport)
        srv.stop()


def test_e2e_write_real_scalar_byte_domain_verify() -> None:
    """REAL verify must compare in encoded domain, not Python float ==.

    3.14 encoded as float32 decodes to ~3.1400001 in Python.  If verify
    compared Python floats, it would always fail for REAL writes.
    """
    import struct as _struct

    float_val = 3.14
    encoded = _struct.pack("<f", float_val)
    # Confirm that decoded != original (this is the trap we're avoiding)
    decoded = _struct.unpack("<f", encoded)[0]
    assert decoded != float_val, "float32 precision trap not present — test needs update"

    srv = _make_sim({"ScratchREAL": (REAL.code, REAL.encode(0.0))})
    try:
        session, transport, driver = _open(srv)
        with driver.armed():
            result = driver.write_tag("ScratchREAL", float_val, data_type="REAL")
        # Must succeed despite float32 precision trap
        assert result.error is None, f"Unexpected error: {result.error}"
    finally:
        _close(session, transport)
        srv.stop()


# ---------------------------------------------------------------------------
# Functional: array writes
# ---------------------------------------------------------------------------


def test_e2e_write_dint_array() -> None:
    values = [1, 2, 3]
    initial = b"".join(DINT.encode(0) for _ in values)
    expected = b"".join(DINT.encode(v) for v in values)
    srv = _make_sim({"ArrayTag": (DINT.code, initial)})
    try:
        session, transport, driver = _open(srv)
        with driver.armed():
            result = driver.write_tag("ArrayTag", values, data_type="DINT", element_count=3)
        assert result.error is None
        assert srv._tag_store["ArrayTag"] == (DINT.code, expected)
    finally:
        _close(session, transport)
        srv.stop()


def test_e2e_array_element_count_mismatch_refused() -> None:
    srv = _make_sim({"ArrayTag": (DINT.code, b"\x00" * 12)})
    try:
        session, transport, driver = _open(srv)
        with driver.armed():
            result = driver.write_tag("ArrayTag", [1, 2], data_type="DINT", element_count=3)
        assert result.error is not None
        assert "element_count" in result.error or "length" in result.error
    finally:
        _close(session, transport)
        srv.stop()


# ---------------------------------------------------------------------------
# Functional: explicit data_type kwarg (no tag list needed)
# ---------------------------------------------------------------------------


def test_e2e_write_without_tag_list() -> None:
    """Write should work with explicit data_type even when tag list not fetched."""
    srv = _make_sim({"ScratchDINT": (DINT.code, DINT.encode(0))})
    try:
        session, transport, driver = _open(srv)
        # No get_tag_list() call — use explicit data_type
        with driver.armed():
            result = driver.write_tag("ScratchDINT", 77, data_type="DINT")
        assert result.error is None
    finally:
        _close(session, transport)
        srv.stop()


# ---------------------------------------------------------------------------
# Functional: CIP error path
# ---------------------------------------------------------------------------


def test_e2e_write_cip_error_captured_in_tag() -> None:
    """CIP error from controller must be captured in Tag.error, not raised."""
    srv = _make_sim({})  # empty tag store — all writes will get NOT_SUPPORTED
    try:
        session, transport, driver = _open(srv)
        with driver.armed():
            result = driver.write_tag("MissingTag", 1, data_type="DINT")
        assert result.error is not None
        # Audit must record a denial
        records = driver._policy.get_records()
        assert any(r.outcome == "denied" for r in records)
    finally:
        _close(session, transport)
        srv.stop()


# ---------------------------------------------------------------------------
# Functional: batch write
# ---------------------------------------------------------------------------


def test_e2e_write_tags_batch() -> None:
    srv = _make_sim(
        {
            "A": (DINT.code, DINT.encode(0)),
            "B": (DINT.code, DINT.encode(0)),
        }
    )
    try:
        session, transport, driver = _open(srv)
        with driver.armed():
            results = driver.write_tags([("A", 10), ("B", 20)], data_type="DINT")
        assert all(r.error is None for r in results)
        assert srv._tag_store["A"] == (DINT.code, DINT.encode(10))
        assert srv._tag_store["B"] == (DINT.code, DINT.encode(20))
    finally:
        _close(session, transport)
        srv.stop()


def test_e2e_write_tags_empty_list() -> None:
    srv = _make_sim({})
    try:
        session, transport, driver = _open(srv)
        with driver.armed():
            results = driver.write_tags([])
        assert results == []
    finally:
        _close(session, transport)
        srv.stop()


# ---------------------------------------------------------------------------
# Functional: struct write refused cleanly
# ---------------------------------------------------------------------------


def test_e2e_struct_write_refused_with_audit() -> None:
    """write_tag on a struct tag (after get_tag_list) must refuse with DataError denial."""

    _STRUCT_TYPE = 0x8001  # struct flag set, template instance 1
    srv = _make_sim(
        {},
        symbol_store={
            "controller": [
                {
                    "name": "MyUDT",
                    "instance_id": 1,
                    "symbol_type": _STRUCT_TYPE,
                    "dims": (0, 0, 0),
                }
            ]
        },
    )
    try:
        session, transport, driver = _open(srv)
        driver.get_tag_list()  # populates _tag_info_cache with is_struct=True
        with driver.armed():
            result = driver.write_tag("MyUDT", {"x": 1})
        assert result.error is not None
        assert "struct" in result.error.lower() or "Phase 2g" in result.error
        records = driver._policy.get_records()
        assert any(r.outcome == "denied" and r.tag_name == "MyUDT" for r in records)
    finally:
        _close(session, transport)
        srv.stop()


# ---------------------------------------------------------------------------
# Audit trail
# ---------------------------------------------------------------------------


def test_e2e_committed_write_creates_audit_record() -> None:
    srv = _make_sim({"ScratchDINT": (DINT.code, DINT.encode(0))})
    try:
        session, transport, driver = _open(srv)
        with driver.armed():
            driver.write_tag("ScratchDINT", 5, data_type="DINT")
        records = driver._policy.get_records()
        committed = [r for r in records if r.outcome == "committed"]
        assert len(committed) == 1
        assert committed[0].tag_name == "ScratchDINT"
        assert committed[0].intended_bytes == DINT.encode(5)
        assert committed[0].reason is None
    finally:
        _close(session, transport)
        srv.stop()
