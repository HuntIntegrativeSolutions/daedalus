"""Layer 1 — Transports: the ONLY place sockets live.

Supported transports: sync TCP, asyncio TCP, UDP Class 1, CSP (port 2222).
I/O is permitted here and only here. All other layers are I/O-FORBIDDEN.
"""

from daedalus.transport._async_tcp import AsyncTcpTransport
from daedalus.transport._tcp import SyncTcpTransport

__all__ = ["AsyncTcpTransport", "SyncTcpTransport"]
