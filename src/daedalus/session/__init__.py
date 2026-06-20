"""Layer 2 — Sans-I/O session state machine.

I/O-FORBIDDEN: this package must never import socket, ssl, asyncio, anyio,
selectors, socketserver, http, urllib, or requests. Pure state transitions only.
"""

from daedalus.session._session import Session, SessionState

__all__ = ["Session", "SessionState"]
