"""Project-wide unified tag result type.

I/O-FORBIDDEN: this module must never import socket, ssl, asyncio, anyio,
selectors, socketserver, http, urllib, or requests.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from daedalus.cip.data_types import DATA_TYPES_BY_CODE

__all__ = ["Tag"]

# CIP type code returned for all UDT / struct reads before template is fetched.
_STRUCT_TYPE_CODE: int = 0x02A0


@dataclass
class Tag:
    """Result of a tag read (or write) operation.

    Merges the pycomm3 and pylogix attribute conventions so existing scripts
    can migrate with an import-only change.

    pycomm3 attributes: ``.value``, ``.error``, ``.type``
    pylogix attributes: ``.TagName``, ``.Value``, ``.Status``
    """

    tag_name: str
    value: Any
    type_code: int
    status: int = 0
    error: str | None = None

    # ------------------------------------------------------------------
    # pycomm3 migration properties
    # ------------------------------------------------------------------

    @property
    def type(self) -> str | None:
        """CIP type name (e.g. ``"DINT"``); ``"STRUCT"`` for UDTs; ``None`` for unknown."""
        if self.type_code == _STRUCT_TYPE_CODE:
            return "STRUCT"
        dt = DATA_TYPES_BY_CODE.get(self.type_code)
        return dt.__name__ if dt is not None else None

    # ------------------------------------------------------------------
    # pylogix migration properties
    # ------------------------------------------------------------------

    @property
    def TagName(self) -> str:
        """Alias for ``tag_name`` (pylogix migration)."""
        return self.tag_name

    @property
    def Value(self) -> Any:
        """Alias for ``value`` (pylogix migration)."""
        return self.value

    @property
    def Status(self) -> int:
        """Alias for ``status`` (pylogix migration)."""
        return self.status
