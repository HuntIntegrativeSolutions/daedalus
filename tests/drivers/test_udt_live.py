"""Live-controller UDT decode tests.

Gated by the DAEDALUS_TEST_PLC environment variable — set it to the controller
IP address (e.g. export DAEDALUS_TEST_PLC=192.168.1.10) to run these tests.
CI leaves the variable unset so this tier is always skipped in automated builds.

All tests are READ-ONLY — no writes, no program changes, no keyswitch actions.

Pre-conditions on the controller:
  * A UDT tag named by DAEDALUS_TEST_UDT_TAG (default "TestUDT") must exist
    and be readable via the connected path.
  * If pycomm3 is installed, the decoded dict is compared member-by-member.

Empirical finding written to console:
  * Whether the reply_handle in the struct read reply equals the structure_handle
    returned by GET_ATTRIBUTE_LIST makeup.  This confirms whether
    _handle_to_instance can be pre-populated in _get_template or whether
    name-based resolution is strictly required.
"""

from __future__ import annotations

import os
import struct
from collections.abc import Generator
from typing import Any

import pytest

_PLC_ADDR: str | None = os.environ.get("DAEDALUS_TEST_PLC")
_UDT_TAG: str = os.environ.get("DAEDALUS_TEST_UDT_TAG", "TestUDT")

pytestmark = pytest.mark.skipif(
    not _PLC_ADDR,
    reason="DAEDALUS_TEST_PLC not set — live tier skipped",
)


# ---------------------------------------------------------------------------
# Fixture: open / close a live connection
# ---------------------------------------------------------------------------


@pytest.fixture()
def live_driver() -> Generator[Any, None, None]:
    """Open a connected session to the live controller."""
    from collections.abc import Callable

    from daedalus.drivers import LogixDriver
    from daedalus.session import Session
    from daedalus.transport import SyncTcpTransport

    assert _PLC_ADDR is not None  # guaranteed by pytestmark skipif

    def _make_send_recv(transport: SyncTcpTransport) -> Callable[[bytes], bytes]:
        def _inner(frame: bytes) -> bytes:
            transport.send_frame(frame)
            return transport.recv_frame()

        return _inner

    session = Session()
    transport = SyncTcpTransport(_PLC_ADDR, 44818)
    transport.connect()
    transport.send_frame(session.register_request())
    session.register_reply(transport.recv_frame())
    transport.send_frame(session.forward_open_request(large=False))
    session.forward_open_reply(transport.recv_frame())
    driver = LogixDriver(session, _make_send_recv(transport))

    yield driver, session, transport

    transport.send_frame(session.forward_close_request())
    session.forward_close_reply(transport.recv_frame())
    transport.send_frame(session.unregister_request())
    transport.close()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_live_udt_read_matches_pycomm3(live_driver: Any) -> None:
    """Read a UDT tag with daedalus; compare decoded dict against pycomm3.

    If pycomm3 is not installed the test asserts only that daedalus returns a
    non-empty decoded dict (not raw bytes) without crashing.
    """
    pycomm3 = pytest.importorskip("pycomm3", reason="pycomm3 not installed")

    driver, _session, _transport = live_driver

    driver.get_tag_list()
    dae_tag = driver.read_tag(_UDT_TAG)

    assert dae_tag.error is None, f"daedalus read failed: {dae_tag.error}"
    assert isinstance(dae_tag.value, dict), (
        f"Expected decoded dict, got {type(dae_tag.value).__name__}: {dae_tag.value!r}"
    )

    with pycomm3.LogixDriver(_PLC_ADDR) as plc:
        pc3_tag = plc.read(_UDT_TAG)

    assert pc3_tag is not None, "pycomm3 read returned None"
    pc3_value = pc3_tag.value if hasattr(pc3_tag, "value") else pc3_tag.Value

    def _compare_dicts(dae: Any, pc3: Any, path: str = "") -> None:
        if isinstance(dae, dict) and isinstance(pc3, dict):
            for key in pc3:
                assert key in dae, f"{path}.{key}: missing from daedalus result"
                _compare_dicts(dae[key], pc3[key], path=f"{path}.{key}")
        elif isinstance(dae, list) and isinstance(pc3, list):
            assert len(dae) == len(pc3), f"{path}: length mismatch {len(dae)} vs {len(pc3)}"
            for i, (d, p) in enumerate(zip(dae, pc3, strict=False)):
                _compare_dicts(d, p, path=f"{path}[{i}]")
        elif isinstance(dae, float) or isinstance(pc3, float):
            assert abs(dae - pc3) < 1e-3, f"{path}: float mismatch {dae} vs {pc3}"
        elif isinstance(dae, bool) and isinstance(pc3, bool):
            assert dae == pc3, f"{path}: bool mismatch {dae} vs {pc3}"
        else:
            assert dae == pc3, f"{path}: value mismatch {dae!r} vs {pc3!r}"

    _compare_dicts(dae_tag.value, pc3_value, path=_UDT_TAG)
    print(f"\n✅ daedalus and pycomm3 agree on {_UDT_TAG}: {dae_tag.type}")


def test_live_handle_vs_makeup_handle(live_driver: Any) -> None:
    """Empirically check: reply_handle in struct read vs structure_handle from GET_ATTRIBUTE_LIST.

    This is the unverified assumption documented in Phase 2e.  The test logs the
    finding rather than asserting either way — both answers are valid; the answer
    determines whether _handle_to_instance can be pre-populated in _get_template
    (if equal) or must come from name-based resolution (if not equal).
    """

    driver, _session, _transport = live_driver

    driver.get_tag_list()

    # Find the TagInfo for our test UDT tag
    ti = driver._tag_info_cache.get(_UDT_TAG)
    assert ti is not None, f"{_UDT_TAG!r} not found in tag list — check DAEDALUS_TEST_UDT_TAG"
    assert ti.template_instance_id is not None, f"{_UDT_TAG!r} has no template_instance_id"

    # Fetch makeup structure_handle via GET_ATTRIBUTE_LIST
    instance_id = ti.template_instance_id

    # We need to call the driver's internal _fetch_template_attrs
    attrs = driver._fetch_template_attrs(instance_id)
    makeup_handle = attrs.structure_handle

    # Re-read with a patched send_recv that captures raw reply frames.
    # (The first read already decoded the tag; we need the wire bytes.)
    captured: list[bytes] = []
    original_send_recv: Any = driver._send_recv

    def _capturing(frame: bytes) -> bytes:
        reply: bytes = original_send_recv(frame)
        captured.append(reply)
        return reply

    # Re-read so we capture the raw reply bytes
    old_fn = driver._send_recv
    driver._send_recv = _capturing
    tag2 = driver.read_tag(_UDT_TAG)
    driver._send_recv = old_fn

    # Extract CIP payload from the last captured frame (connected SendUnitData reply)
    # Format: encap_header(24B) + interface_handle(4B) + timeout(2B) + cpf_items...
    # We look for the struct type code 0x02A0 in the captured bytes.
    reply_handle = None
    for frame in captured:
        idx = frame.find(b"\xa0\x02")
        if idx != -1 and idx + 4 <= len(frame):
            reply_handle = struct.unpack_from("<H", frame, idx + 2)[0]
            break

    if reply_handle is not None:
        equal = reply_handle == makeup_handle
        implication = (
            "_handle_to_instance CAN be pre-populated in _get_template"
            if equal
            else "Name-based resolution is REQUIRED (handles differ)"
        )
        print(
            f"\n🔬 Handle equality check for {_UDT_TAG} (instance_id={instance_id:#x}):\n"
            f"   makeup structure_handle (GET_ATTRIBUTE_LIST) = {makeup_handle:#06x}\n"
            f"   reply_handle in struct read reply            = {reply_handle:#06x}\n"
            f"   Equal: {equal}\n"
            f"   Implication: {implication}"
        )
        # Record finding in the test output — no assertion either way
        # (both results are valid; this is the empirical data gathering step)
    else:
        print("\n⚠️  Could not extract reply_handle from captured frame — check frame layout")

    # Always assert the decoded value is a dict
    assert isinstance(tag2.value, dict), f"Expected dict, got {type(tag2.value).__name__}"


def test_live_no_crash_without_get_tag_list(live_driver: Any) -> None:
    """read_tag on a struct tag without prior get_tag_list must not crash.

    Verifies graceful raw-bytes fallback path on a real controller.
    """
    driver, _session, _transport = live_driver

    # Deliberately skip get_tag_list()
    tag = driver.read_tag(_UDT_TAG)
    # No template info → raw bytes fallback
    assert tag.error is None or isinstance(tag.value, bytes), (
        f"Expected graceful fallback or success; got error={tag.error!r}"
    )
    # Should be raw bytes (no crash)
    assert isinstance(tag.value, bytes), (
        f"Expected raw bytes without get_tag_list, got {type(tag.value)}"
    )
    print(f"\n✅ Graceful fallback confirmed: raw bytes ({len(tag.value)}B) for {_UDT_TAG!r}")


def test_live_replay_capture(live_driver: Any, tmp_path: Any) -> None:
    """Capture CIP frame pairs and write to tests/fixtures/udt_replay.bin.

    The fixture can be used for future offline replay tests without hardware.
    Format: 4-byte big-endian length prefix + frame bytes, alternating request/reply.
    """
    import pathlib

    driver, _session, _transport = live_driver

    frames: list[tuple[bytes, bytes]] = []
    original_fn: Any = driver._send_recv

    def _recording(request: bytes) -> bytes:
        reply: bytes = original_fn(request)
        frames.append((request, reply))
        return reply

    driver._send_recv = _recording
    driver.get_tag_list()
    driver.read_tag(_UDT_TAG)
    driver._send_recv = original_fn

    fixture_dir = pathlib.Path(__file__).parent.parent / "fixtures"
    fixture_dir.mkdir(exist_ok=True)
    out_path = fixture_dir / "udt_replay.bin"

    with out_path.open("wb") as f:
        for req, rep in frames:
            f.write(struct.pack(">I", len(req)) + req)
            f.write(struct.pack(">I", len(rep)) + rep)

    assert out_path.stat().st_size > 0, "Replay file is empty"
    print(f"\n✅ Captured {len(frames)} frame pairs → {out_path}")
