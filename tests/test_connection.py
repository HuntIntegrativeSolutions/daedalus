"""Tests for the managed connection layer (LogixConnection / AsyncLogixConnection).

Covers: sync + async lifecycle, cleanup-on-exception, ForwardOpen large→standard
fallback, failed-connect cleanup, address parsing, and the lazy-export firewall.
"""

from __future__ import annotations

import subprocess
import sys
from typing import Any

import pytest

from daedalus.cip.data_types import DINT
from daedalus.connection import AsyncLogixConnection, LogixConnection, _parse_plc_addr
from daedalus.exceptions import CommError
from daedalus.session import SessionState

# ---------------------------------------------------------------------------
# Address parsing (no network)
# ---------------------------------------------------------------------------


def test_parse_plc_addr_no_slot() -> None:
    assert _parse_plc_addr("10.0.0.11") == ("10.0.0.11", 0)


def test_parse_plc_addr_with_slot() -> None:
    assert _parse_plc_addr("10.0.0.11/2") == ("10.0.0.11", 2)


def test_parse_plc_addr_slot_zero_explicit() -> None:
    assert _parse_plc_addr("10.0.0.11/0") == ("10.0.0.11", 0)


def test_logix_connection_host_kwarg_stores_correctly() -> None:
    conn = LogixConnection(host="10.0.0.11", slot=3)
    assert conn._host == "10.0.0.11"
    assert conn._slot == 3


def test_logix_connection_addr_no_slot() -> None:
    conn = LogixConnection("10.0.0.11")
    assert conn._host == "10.0.0.11"
    assert conn._slot == 0


def test_logix_connection_addr_with_slot() -> None:
    conn = LogixConnection("10.0.0.11/2")
    assert conn._host == "10.0.0.11"
    assert conn._slot == 2


def test_logix_connection_both_addr_and_host_raises() -> None:
    with pytest.raises(ValueError, match="addr OR host"):
        LogixConnection("10.0.0.11", host="10.0.0.11")


def test_logix_connection_neither_raises() -> None:
    with pytest.raises(ValueError, match="required"):
        LogixConnection()


# ---------------------------------------------------------------------------
# Sync lifecycle
# ---------------------------------------------------------------------------


def test_sync_lifecycle_happy_path(make_tag_server: Any) -> None:
    srv = make_tag_server({"MyDINT": (DINT.code, DINT.encode(42))})
    with LogixConnection(host=srv.host, port=srv.port) as plc:
        tag = plc.read_tag("MyDINT")
    assert tag.value == 42
    assert tag.error is None


def test_sync_cleanup_on_exception(make_tag_server: Any) -> None:
    srv = make_tag_server({"MyDINT": (DINT.code, DINT.encode(1))})
    conn = LogixConnection(host=srv.host, port=srv.port)
    with pytest.raises(RuntimeError, match="boom"), conn:
        raise RuntimeError("boom")
    assert conn._session is not None
    assert conn._session.state == SessionState.IDLE
    assert conn._transport is not None
    assert conn._transport._sock is None


def test_sync_forward_open_fallback(sim_server_rejecting_large: Any) -> None:
    srv = sim_server_rejecting_large
    with LogixConnection(host=srv.host, port=srv.port) as plc:
        assert plc._session is not None
        assert plc._session.connected


def test_sync_failed_connect_raises_comm_error() -> None:
    with pytest.raises(CommError), LogixConnection(host="127.0.0.1", port=1):
        pass  # pragma: no cover


# ---------------------------------------------------------------------------
# Async lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_async_lifecycle_happy_path(make_tag_server: Any) -> None:
    srv = make_tag_server({"AsyncDINT": (DINT.code, DINT.encode(99))})
    async with AsyncLogixConnection(host=srv.host, port=srv.port) as plc:
        tag = await plc.read_tag("AsyncDINT")
    assert tag.value == 99
    assert tag.error is None


@pytest.mark.anyio
async def test_async_cleanup_on_exception(make_tag_server: Any) -> None:
    srv = make_tag_server({"MyDINT": (DINT.code, DINT.encode(1))})
    conn = AsyncLogixConnection(host=srv.host, port=srv.port)
    with pytest.raises(RuntimeError, match="boom"):
        async with conn:
            raise RuntimeError("boom")
    assert conn._session is not None
    assert conn._session.state == SessionState.IDLE
    assert conn._transport is not None
    assert conn._transport._stream is None


@pytest.mark.anyio
async def test_async_forward_open_fallback(sim_server_rejecting_large: Any) -> None:
    srv = sim_server_rejecting_large
    async with AsyncLogixConnection(host=srv.host, port=srv.port) as plc:
        assert plc._session is not None
        assert plc._session.connected


# ---------------------------------------------------------------------------
# Lazy-export firewall — bare 'import daedalus' must not pull anyio
# ---------------------------------------------------------------------------


def test_daedalus_import_does_not_pull_anyio() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import daedalus; "
                "import sys; "
                "assert 'anyio' not in sys.modules, "
                "f'anyio pulled in by bare import daedalus: {list(sys.modules)}'"
            ),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        "Lazy-export regression — anyio pulled into bare 'import daedalus'.\n"
        f"stderr: {result.stderr}\n"
        f"stdout: {result.stdout}"
    )
