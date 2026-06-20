"""Live-controller tag-list tests — env-gated, CI-skipped.

Run against a real Allen-Bradley Logix controller (or Logix Emulate) by setting:

    DAEDALUS_TEST_PLC=<ip>          # slot defaults to 0
    DAEDALUS_TEST_PLC=<ip>/<slot>   # explicit slot

These tests are read-only and never send any write or program-edit requests.
"""

from __future__ import annotations

import json
import os
import pathlib
from collections.abc import Callable

import pytest

from daedalus.drivers import LogixDriver
from daedalus.session import Session
from daedalus.transport import SyncTcpTransport

_PLC = os.environ.get("DAEDALUS_TEST_PLC")
_REPLAY_DIR = pathlib.Path(__file__).parent.parent / "fixtures" / "replay"
_REPLAY_FILE = _REPLAY_DIR / "tag_list_replay.json"

pytestmark = pytest.mark.skipif(not _PLC, reason="DAEDALUS_TEST_PLC not set")


def _parse_plc_addr(addr: str) -> tuple[str, int]:
    """Parse '<ip>[/<slot>]' → (ip, slot)."""
    if "/" in addr:
        ip, slot_s = addr.split("/", 1)
        return ip, int(slot_s)
    return addr, 0


def _make_send_recv(transport: SyncTcpTransport) -> Callable[[bytes], bytes]:
    def _inner(frame: bytes) -> bytes:
        transport.send_frame(frame)
        return transport.recv_frame()

    return _inner


def test_live_get_tag_list() -> None:
    """Connect to real controller; assert non-empty well-formed list; capture replay.

    Replay frames are saved to tests/fixtures/replay/tag_list_replay.json
    so the companion replay test can run in CI without hardware.
    """
    assert _PLC is not None
    ip, _slot = _parse_plc_addr(_PLC)

    session = Session()
    transport = SyncTcpTransport(ip, 44818)
    transport.connect()
    try:
        transport.send_frame(session.register_request())
        session.register_reply(transport.recv_frame())
        transport.send_frame(session.forward_open_request(large=False))
        session.forward_open_reply(transport.recv_frame())

        driver = LogixDriver(session, _make_send_recv(transport))
        tags = driver.get_tag_list()

        transport.send_frame(session.forward_close_request())
        session.forward_close_reply(transport.recv_frame())
        transport.send_frame(session.unregister_request())
    finally:
        transport.close()

    assert len(tags) > 0, "Expected at least one user tag"
    assert all(t.tag_name and (t.data_type is not None or t.is_struct) for t in tags), (
        "Every tag must have a name and either a data_type or be_struct=True"
    )

    # Save replay fixture for offline use
    _REPLAY_DIR.mkdir(parents=True, exist_ok=True)
    _REPLAY_FILE.write_text(
        json.dumps(
            [
                {
                    "tag_name": t.tag_name,
                    "instance_id": t.instance_id,
                    "is_struct": t.is_struct,
                    "data_type": t.data_type,
                    "template_instance_id": t.template_instance_id,
                    "dimensions": list(t.dimensions),
                    "scope": t.scope,
                }
                for t in tags
            ],
            indent=2,
        )
    )


def test_live_tag_set_parity_vs_pycomm3() -> None:
    """THE definitive tag-set parity gate: daedalus == pycomm3 on same controller.

    Connects both drivers to the same controller and diffs the tag name sets.
    Any divergence is a filter-logic bug — this catches mismatches (e.g. I/O
    tags missing) that offline payload-parse tests cannot detect.

    Requires pycomm3 to be installed (``pip install pycomm3``).
    """
    pycomm3 = pytest.importorskip("pycomm3")
    assert _PLC is not None
    ip, slot = _parse_plc_addr(_PLC)

    # daedalus result
    session = Session()
    transport = SyncTcpTransport(ip, 44818)
    transport.connect()
    try:
        transport.send_frame(session.register_request())
        session.register_reply(transport.recv_frame())
        transport.send_frame(session.forward_open_request(large=False))
        session.forward_open_reply(transport.recv_frame())

        driver = LogixDriver(session, _make_send_recv(transport))
        daedalus_tags = {t.tag_name for t in driver.get_tag_list()}

        transport.send_frame(session.forward_close_request())
        session.forward_close_reply(transport.recv_frame())
        transport.send_frame(session.unregister_request())
    finally:
        transport.close()

    # pycomm3 result (uses the program="*" sentinel to include all program scopes)
    with pycomm3.LogixDriver(ip, slot=slot) as plc:
        pycomm3_tags = {t["tag_name"] for t in plc.get_tag_list(program="*")}

    only_in_daedalus = daedalus_tags - pycomm3_tags
    only_in_pycomm3 = pycomm3_tags - daedalus_tags

    assert not only_in_daedalus and not only_in_pycomm3, (
        f"Tag-set divergence detected.\n"
        f"  Only in daedalus ({len(only_in_daedalus)}): "
        f"{sorted(only_in_daedalus)[:20]}\n"
        f"  Only in pycomm3  ({len(only_in_pycomm3)}): "
        f"{sorted(only_in_pycomm3)[:20]}"
    )
