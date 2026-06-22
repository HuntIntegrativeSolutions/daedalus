"""daedalus — unified Allen-Bradley / EtherNet-IP library."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from typing import TYPE_CHECKING

from daedalus.drivers import AsyncLogixDriver, LogixDriver
from daedalus.runtime.write_policy import InMemorySink, WriteMode, WritePolicy, WriteRecord
from daedalus.tag import Tag, TagInfo

if TYPE_CHECKING:
    from daedalus.connection import AsyncLogixConnection, LogixConnection

try:
    __version__: str = version("daedalus")
except PackageNotFoundError:
    __version__ = "0.0.0"

__all__ = [
    "AsyncLogixConnection",
    "AsyncLogixDriver",
    "InMemorySink",
    "LogixConnection",
    "LogixDriver",
    "Tag",
    "TagInfo",
    "WriteMode",
    "WritePolicy",
    "WriteRecord",
    "__version__",
]


def __getattr__(name: str) -> object:
    if name in ("LogixConnection", "AsyncLogixConnection"):
        from daedalus import connection

        return getattr(connection, name)
    raise AttributeError(f"module 'daedalus' has no attribute {name!r}")
