"""Live write tests against a real PLC.

DOUBLE-GATED: Both env vars must be set or the entire module is skipped.
DAEDALUS_TEST_PLC must be an IP address or hostname.
DAEDALUS_TEST_WRITE_TAG must be the name of a safe scratch tag that the
tests may write freely (e.g. "ScratchDINT" — an INT/DINT tag you've reserved
for automated testing).

Never sets DAEDALUS_TEST_PLC alone to guard against triggering write tests
when only the read gate is set.
"""

from __future__ import annotations

import os
from typing import Any

import pytest

from daedalus.cip.data_types import DINT

pytestmark = pytest.mark.skipif(
    not (os.getenv("DAEDALUS_TEST_PLC") and os.getenv("DAEDALUS_TEST_WRITE_TAG")),
    reason=("Both DAEDALUS_TEST_PLC and DAEDALUS_TEST_WRITE_TAG must be set for live write tests"),
)


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest.fixture()
def plc() -> Any:
    """Open a connected LogixDriver to the live PLC and yield (driver, scratch_tag).

    Closes the connection cleanly on teardown.
    """

    from daedalus.drivers import LogixDriver
    from daedalus.session import Session
    from daedalus.transport import SyncTcpTransport

    host = os.environ["DAEDALUS_TEST_PLC"]
    scratch_tag = os.environ["DAEDALUS_TEST_WRITE_TAG"]

    session = Session()
    transport = SyncTcpTransport(host, 44818)
    transport.connect()
    transport.send_frame(session.register_request())
    session.register_reply(transport.recv_frame())
    transport.send_frame(session.forward_open_request(large=False))
    session.forward_open_reply(transport.recv_frame())

    def _send_recv(frame: bytes) -> bytes:
        transport.send_frame(frame)
        return transport.recv_frame()

    driver = LogixDriver(session, _send_recv)

    try:
        yield driver, scratch_tag
    finally:
        transport.send_frame(session.forward_close_request())
        session.forward_close_reply(transport.recv_frame())
        transport.send_frame(session.unregister_request())
        transport.close()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_live_write_dint_roundtrip(plc: Any) -> None:
    """Write a DINT value; read it back; verify; restore original value."""
    driver, scratch_tag = plc

    # Read original value for restoration
    original = driver.read_tag(scratch_tag)
    assert original.error is None, f"Pre-read failed: {original.error}"

    test_value = 12345

    with driver.armed():
        result = driver.write_tag(scratch_tag, test_value, data_type="DINT")

    assert result.error is None, f"Write failed: {result.error}"
    assert result.value == test_value

    # Read back to confirm
    readback = driver.read_tag(scratch_tag)
    assert readback.error is None
    assert readback.value == test_value

    # Restore original
    with driver.armed():
        restore = driver.write_tag(scratch_tag, original.value, data_type="DINT")
    assert restore.error is None, f"Restore failed: {restore.error}"


def test_live_write_requires_armed_context(plc: Any) -> None:
    """write_tag without armed() must be refused (read-only default)."""
    driver, scratch_tag = plc

    result = driver.write_tag(scratch_tag, 0, data_type="DINT")
    assert result.error is not None
    assert "READ_ONLY" in result.error


def test_live_armed_context_manager_reverts_mode(plc: Any) -> None:
    """After armed() block exits, driver must be read-only again."""
    driver, scratch_tag = plc

    from daedalus.runtime.write_policy import WriteMode

    with driver.armed():
        assert driver._policy.mode == WriteMode.ARMED

    assert driver._policy.mode == WriteMode.READ_ONLY

    # Write outside armed block must be refused
    result = driver.write_tag(scratch_tag, 0, data_type="DINT")
    assert result.error is not None


def test_live_bit_of_word_refused(plc: Any) -> None:
    """Bit-of-word writes must be refused at the gate — no PLC I/O."""
    driver, scratch_tag = plc

    with driver.armed():
        result = driver.write_tag(f"{scratch_tag}.0", True, data_type="BOOL")

    assert result.error is not None
    assert "bit" in result.error.lower() or "Bit" in result.error


def test_live_write_audit_record(plc: Any) -> None:
    """Committed write must produce an audit record with correct fields."""
    driver, scratch_tag = plc

    # Read original for restoration
    original = driver.read_tag(scratch_tag)

    with driver.armed():
        driver.write_tag(scratch_tag, 77, data_type="DINT")

    records = driver._policy.get_records()
    committed = [r for r in records if r.outcome == "committed"]
    assert len(committed) >= 1
    last = committed[-1]
    assert last.tag_name == scratch_tag
    assert last.intended_bytes == DINT.encode(77)
    assert last.when > 1_577_836_800.0  # wall-clock after 2020-01-01

    # Restore
    if original.error is None:
        with driver.armed():
            driver.write_tag(scratch_tag, original.value, data_type="DINT")
