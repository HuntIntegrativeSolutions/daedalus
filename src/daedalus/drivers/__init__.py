"""Layer 3 — Request building (drivers produce bytes + state; never move bytes).

I/O-FORBIDDEN: this package must never import socket, ssl, asyncio, anyio,
selectors, socketserver, http, urllib, or requests. Drivers hand bytes to L1 transports.
"""
