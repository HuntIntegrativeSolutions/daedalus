"""Async Logix driver — drives the same sans-I/O generators via _run_async.

I/O-FORBIDDEN: this file is in drivers/ (L3) and is AST-scanned by the
sans-I/O firewall.  It may use ``async def``/``await`` (language syntax, not
a module import) but must import NO socket, asyncio, or anyio.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Generator, Sequence
from contextlib import AbstractContextManager
from typing import Any

from daedalus.cip.data_types import DataType
from daedalus.drivers._logix import (
    _T,
    _CipReply,
    _DriverCaches,
    _get_tag_list_gen,
    _read_tag_gen,
    _read_tags_gen,
    _run_async,
    _write_tag_gen,
    _write_tags_gen,
)
from daedalus.runtime.write_policy import WritePolicy
from daedalus.session import Session
from daedalus.tag import Tag, TagInfo


class AsyncLogixDriver:
    """Async twin of LogixDriver — same generators, awaitable send_recv.

    The driver never touches a socket.  Callers inject an async ``send_recv``
    callable that wires the L1 transport::

        async def _send_recv(frame: bytes) -> bytes:
            await transport.send_frame(frame)
            return await transport.recv_frame()

        driver = AsyncLogixDriver(session, _send_recv)
        tag = await driver.read_tag("Program:Main.Counter")

    Or use :class:`~daedalus.transport.AsyncTcpTransport`'s built-in
    ``send_recv`` method directly::

        async with AsyncTcpTransport(host, port) as t:
            driver = AsyncLogixDriver(session, t.send_recv)

    Args:
        session:   A Session in the CONNECTED state (Forward_Open completed).
        send_recv: Async callable that accepts a frame, sends it, awaits the
            reply, and returns the reply bytes.  Must be provided by the L1
            transport layer — AsyncLogixDriver never creates one itself.
        policy:    Write-safety policy.  Defaults to READ_ONLY if not provided.
    """

    def __init__(
        self,
        session: Session,
        send_recv: Callable[[bytes], Awaitable[bytes]],
        policy: WritePolicy | None = None,
    ) -> None:
        self._session = session
        self._send_recv = send_recv
        self._policy: WritePolicy = policy if policy is not None else WritePolicy()
        self._caches = _DriverCaches.empty()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _run(self, gen: Generator[bytes, _CipReply, _T]) -> _T:
        """Drive *gen* to completion over this driver's async transport."""
        return await _run_async(gen, self._session, self._send_recv)

    # ------------------------------------------------------------------
    # Write gating
    # ------------------------------------------------------------------

    def armed(self) -> AbstractContextManager[WritePolicy]:
        """Arm writes for the duration of a block; guarantee disarm on exit.

        Delegates to ``WritePolicy.armed()``; see that method for full docs.
        Stays SYNC — it only flips a mode flag, no I/O::

            with driver.armed():
                await driver.write_tag("ScratchDINT", 42)
            # policy is READ_ONLY again — even if write_tag raised
        """
        return self._policy.armed()

    # ------------------------------------------------------------------
    # Public API — async twins of LogixDriver methods
    # ------------------------------------------------------------------

    async def read_tag(self, tag_name: str, *, element_count: int = 1) -> Tag:
        """Read a tag over a Class 3 connected session.

        Args:
            tag_name:      Logix tag name (supports dotted paths and array indices).
            element_count: Number of elements to read (1 = scalar; >1 = array slice).

        Returns:
            A Tag with the decoded value.

        Raises:
            ResponseError: Device returned a CIP error status.
            DataError:     Reply too short or contains an unknown type code.
        """
        return await self._run(_read_tag_gen(self._caches, tag_name, element_count=element_count))

    async def read_tags(self, tag_names: Sequence[str]) -> list[Tag]:
        """Read multiple tags in one Multiple Service Packet (MSP, service 0x0A).

        Args:
            tag_names: Sequence of Logix tag names to read.

        Returns:
            List of Tags in the same order as *tag_names*.

        Raises:
            ResponseError: The MSP outer request itself failed.
            DataError:     The MSP reply is malformed.
        """
        return await self._run(_read_tags_gen(self._caches, tag_names))

    async def get_tag_list(self) -> list[TagInfo]:
        """Enumerate all user tags on the controller (controller + program scopes).

        Returns:
            Flat list of TagInfo entries ordered controller-scope first,
            then program-scope in discovery order.

        Raises:
            ResponseError: The device returned a CIP error status.
            DataError:     A reply payload is malformed or truncated.
        """
        return await self._run(_get_tag_list_gen(self._caches))

    async def write_tag(
        self,
        tag_name: str,
        value: Any,
        *,
        data_type: str | type[DataType[Any]] | None = None,
        element_count: int = 1,
    ) -> Tag:
        """Write one atomic scalar or array tag.

        Must be called inside ``with driver.armed():``.

        Returns:
            Tag with status=0 on success; Tag.error describes the failure on any
            error (policy denial, CIP error, or verify mismatch).
        """
        return await self._run(
            _write_tag_gen(
                self._caches,
                self._policy,
                tag_name,
                value,
                data_type=data_type,
                element_count=element_count,
            )
        )

    async def write_tags(
        self,
        tags: list[tuple[str, Any]],
        *,
        data_type: str | type[DataType[Any]] | None = None,
        element_count: int = 1,
    ) -> list[Tag]:
        """Write multiple atomic tags with batch critic approval.

        Arming/disarming is caller-managed (``with driver.armed():``).

        Args:
            tags:          List of ``(tag_name, value)`` pairs.
            data_type:     CIP type override applied to every tag in the batch.
            element_count: Element count applied to every tag.

        Returns:
            List of Tags in the same order as *tags*.
        """
        return await self._run(
            _write_tags_gen(
                self._caches,
                self._policy,
                tags,
                data_type=data_type,
                element_count=element_count,
            )
        )
