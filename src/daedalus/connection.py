"""Managed connection layer — composes L1 transport + L2 session + L3 driver.

Intentionally separate from drivers/ (L3) to preserve the sans-I/O firewall:
the L3 drivers may never import transport (socket/anyio). This module sits at
the composition boundary and is explicitly allowed to import both.
"""

from __future__ import annotations

import contextlib
from collections.abc import Sequence
from contextlib import AbstractContextManager
from types import TracebackType
from typing import Any

from daedalus.cip.data_types import DataType
from daedalus.drivers._async_logix import AsyncLogixDriver
from daedalus.drivers._logix import LogixDriver
from daedalus.exceptions import LargeForwardOpenRejected
from daedalus.packets.cip import MSG_ROUTER_PATH, backplane_path
from daedalus.runtime.write_policy import WritePolicy
from daedalus.session import Session
from daedalus.tag import Tag, TagInfo
from daedalus.transport._async_tcp import AsyncTcpTransport
from daedalus.transport._tcp import SyncTcpTransport

__all__ = ["AsyncLogixConnection", "LogixConnection"]


def _parse_plc_addr(addr: str) -> tuple[str, int]:
    """Parse '<ip>[/<slot>]' → (ip, slot)."""
    if "/" in addr:
        ip, slot_s = addr.split("/", 1)
        return ip, int(slot_s)
    return addr, 0


def _make_send_recv(transport: SyncTcpTransport) -> Any:
    def _inner(frame: bytes) -> bytes:
        transport.send_frame(frame)
        return transport.recv_frame()

    return _inner


class LogixConnection:
    """Managed sync Logix connection: one-call connect + guaranteed teardown.

    Composes a SyncTcpTransport (L1), Session (L2), and LogixDriver (L3) into
    a single context manager.  RegisterSession + Forward_Open are sequenced on
    enter; ForwardClose + UnregisterSession + socket close are guaranteed in a
    ``finally`` on exit, even if the body raises.

    Usage::

        with LogixConnection("10.0.0.11/0") as plc:
            tag = plc.read_tag("MyDINT")

    Args:
        addr:    ``"<ip>"`` or ``"<ip>/<slot>"`` positional address string.
        host:    Explicit IP/hostname (keyword-only alternative to ``addr``).
        slot:    Backplane slot; ``0`` (default) routes via MSG_ROUTER_PATH.
        policy:  Write-safety policy (defaults to READ_ONLY).
        port:    TCP port (default 44818).
        timeout: Socket timeout in seconds (default 5.0).

    Raises:
        ValueError:       Neither ``addr`` nor ``host`` provided, or both.
        CommError:        Transport-level failure.
        ForwardOpenError: PLC rejected Forward_Open after the large→standard fallback.
    """

    def __init__(
        self,
        addr: str | None = None,
        *,
        host: str | None = None,
        slot: int = 0,
        policy: WritePolicy | None = None,
        port: int = 44818,
        timeout: float = 5.0,
    ) -> None:
        if addr is not None and host is not None:
            raise ValueError("Provide addr OR host, not both")
        if addr is None and host is None:
            raise ValueError("Either addr or host is required")
        if addr is not None:
            host, slot = _parse_plc_addr(addr)
        assert host is not None
        self._host = host
        self._slot = slot
        self._port = port
        self._timeout = timeout
        self._policy = policy
        self._transport: SyncTcpTransport | None = None
        self._session: Session | None = None
        self._driver: LogixDriver | None = None

    def __enter__(self) -> LogixConnection:
        conn_path = backplane_path(self._slot) if self._slot else MSG_ROUTER_PATH
        transport = SyncTcpTransport(self._host, self._port, self._timeout)
        session = Session()
        self._transport = transport
        self._session = session
        try:
            transport.connect()
            transport.send_frame(session.register_request())
            session.register_reply(transport.recv_frame())
            try:
                transport.send_frame(
                    session.forward_open_request(large=True, connection_path=conn_path)
                )
                session.forward_open_reply(transport.recv_frame())
            except LargeForwardOpenRejected:
                # Session reset to REGISTERED by forward_open_reply; retry standard.
                transport.send_frame(
                    session.forward_open_request(large=False, connection_path=conn_path)
                )
                session.forward_open_reply(transport.recv_frame())
            self._driver = LogixDriver(session, _make_send_recv(transport), self._policy)
        except Exception:
            self._cleanup()
            raise
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self._cleanup()

    def _cleanup(self) -> None:
        transport = self._transport
        session = self._session
        try:
            if session is not None and transport is not None:
                if session.connected:
                    try:
                        transport.send_frame(session.forward_close_request())
                        session.forward_close_reply(transport.recv_frame())
                    except Exception:
                        pass
                if session.registered:
                    with contextlib.suppress(Exception):
                        transport.send_frame(session.unregister_request())
        finally:
            if transport is not None:
                transport.close()

    # ------------------------------------------------------------------
    # Public API — delegates to inner LogixDriver
    # ------------------------------------------------------------------

    def read_tag(self, tag_name: str, *, element_count: int = 1) -> Tag:
        assert self._driver is not None, "Not connected — use inside 'with' block"
        return self._driver.read_tag(tag_name, element_count=element_count)

    def read_tags(self, tag_names: Sequence[str]) -> list[Tag]:
        assert self._driver is not None, "Not connected — use inside 'with' block"
        return self._driver.read_tags(tag_names)

    def get_tag_list(self) -> list[TagInfo]:
        assert self._driver is not None, "Not connected — use inside 'with' block"
        return self._driver.get_tag_list()

    def write_tag(
        self,
        tag_name: str,
        value: Any,
        *,
        data_type: str | type[DataType[Any]] | None = None,
        element_count: int = 1,
    ) -> Tag:
        assert self._driver is not None, "Not connected — use inside 'with' block"
        return self._driver.write_tag(
            tag_name, value, data_type=data_type, element_count=element_count
        )

    def write_tags(
        self,
        tags: list[tuple[str, Any]],
        *,
        data_type: str | type[DataType[Any]] | None = None,
        element_count: int = 1,
    ) -> list[Tag]:
        assert self._driver is not None, "Not connected — use inside 'with' block"
        return self._driver.write_tags(tags, data_type=data_type, element_count=element_count)

    def armed(self) -> AbstractContextManager[WritePolicy]:
        assert self._driver is not None, "Not connected — use inside 'with' block"
        return self._driver.armed()


class AsyncLogixConnection:
    """Managed async Logix connection: one-call connect + guaranteed teardown.

    Async twin of :class:`LogixConnection`.  Uses anyio via
    :class:`~daedalus.transport.AsyncTcpTransport`.

    Usage::

        async with AsyncLogixConnection("10.0.0.11/0") as plc:
            tag = await plc.read_tag("MyDINT")

    Args:
        addr:    ``"<ip>"`` or ``"<ip>/<slot>"`` positional address string.
        host:    Explicit IP/hostname (keyword-only alternative to ``addr``).
        slot:    Backplane slot; ``0`` (default) routes via MSG_ROUTER_PATH.
        policy:  Write-safety policy (defaults to READ_ONLY).
        port:    TCP port (default 44818).
        timeout: Socket timeout in seconds (default 5.0).
    """

    def __init__(
        self,
        addr: str | None = None,
        *,
        host: str | None = None,
        slot: int = 0,
        policy: WritePolicy | None = None,
        port: int = 44818,
        timeout: float = 5.0,
    ) -> None:
        if addr is not None and host is not None:
            raise ValueError("Provide addr OR host, not both")
        if addr is None and host is None:
            raise ValueError("Either addr or host is required")
        if addr is not None:
            host, slot = _parse_plc_addr(addr)
        assert host is not None
        self._host = host
        self._slot = slot
        self._port = port
        self._timeout = timeout
        self._policy = policy
        self._transport: AsyncTcpTransport | None = None
        self._session: Session | None = None
        self._driver: AsyncLogixDriver | None = None

    async def __aenter__(self) -> AsyncLogixConnection:
        conn_path = backplane_path(self._slot) if self._slot else MSG_ROUTER_PATH
        transport = AsyncTcpTransport(self._host, self._port, self._timeout)
        session = Session()
        self._transport = transport
        self._session = session
        try:
            await transport.connect()
            await transport.send_frame(session.register_request())
            session.register_reply(await transport.recv_frame())
            try:
                await transport.send_frame(
                    session.forward_open_request(large=True, connection_path=conn_path)
                )
                session.forward_open_reply(await transport.recv_frame())
            except LargeForwardOpenRejected:
                await transport.send_frame(
                    session.forward_open_request(large=False, connection_path=conn_path)
                )
                session.forward_open_reply(await transport.recv_frame())
            self._driver = AsyncLogixDriver(session, transport.send_recv, self._policy)
        except Exception:
            await self._cleanup()
            raise
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        await self._cleanup()

    async def _cleanup(self) -> None:
        transport = self._transport
        session = self._session
        try:
            if session is not None and transport is not None:
                if session.connected:
                    try:
                        await transport.send_frame(session.forward_close_request())
                        session.forward_close_reply(await transport.recv_frame())
                    except Exception:
                        pass
                if session.registered:
                    with contextlib.suppress(Exception):
                        await transport.send_frame(session.unregister_request())
        finally:
            if transport is not None:
                await transport.close()

    # ------------------------------------------------------------------
    # Public API — delegates to inner AsyncLogixDriver
    # ------------------------------------------------------------------

    async def read_tag(self, tag_name: str, *, element_count: int = 1) -> Tag:
        assert self._driver is not None, "Not connected — use inside 'async with' block"
        return await self._driver.read_tag(tag_name, element_count=element_count)

    async def read_tags(self, tag_names: Sequence[str]) -> list[Tag]:
        assert self._driver is not None, "Not connected — use inside 'async with' block"
        return await self._driver.read_tags(tag_names)

    async def get_tag_list(self) -> list[TagInfo]:
        assert self._driver is not None, "Not connected — use inside 'async with' block"
        return await self._driver.get_tag_list()

    async def write_tag(
        self,
        tag_name: str,
        value: Any,
        *,
        data_type: str | type[DataType[Any]] | None = None,
        element_count: int = 1,
    ) -> Tag:
        assert self._driver is not None, "Not connected — use inside 'async with' block"
        return await self._driver.write_tag(
            tag_name, value, data_type=data_type, element_count=element_count
        )

    async def write_tags(
        self,
        tags: list[tuple[str, Any]],
        *,
        data_type: str | type[DataType[Any]] | None = None,
        element_count: int = 1,
    ) -> list[Tag]:
        assert self._driver is not None, "Not connected — use inside 'async with' block"
        return await self._driver.write_tags(tags, data_type=data_type, element_count=element_count)

    def armed(self) -> AbstractContextManager[WritePolicy]:
        assert self._driver is not None, "Not connected — use inside 'async with' block"
        return self._driver.armed()
