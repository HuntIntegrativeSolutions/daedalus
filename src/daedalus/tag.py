"""Project-wide unified tag result type.

I/O-FORBIDDEN: this module must never import socket, ssl, asyncio, anyio,
selectors, socketserver, http, urllib, or requests.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from daedalus.cip.data_types import DATA_TYPES_BY_CODE

__all__ = ["Tag", "TagInfo"]

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
    udt_name: str | None = None  # resolved UDT name; set after template decode

    # ------------------------------------------------------------------
    # pycomm3 migration properties
    # ------------------------------------------------------------------

    @property
    def type(self) -> str | None:
        """CIP type name (e.g. ``"DINT"``); resolved UDT name for structs; ``None`` for unknown."""
        if self.type_code == _STRUCT_TYPE_CODE:
            return self.udt_name or "STRUCT"
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


# ---------------------------------------------------------------------------
# Tag-list result type
# ---------------------------------------------------------------------------


@dataclass
class TagInfo:
    """Result of a tag-list enumeration entry (Get Instance Attribute List).

    For atomic tags: ``data_type`` is the CIP type name (e.g. ``"DINT"``).
    For struct/UDT tags: ``data_type`` is ``None`` and ``template_instance_id``
    holds the template ID needed for Phase 2e member resolution.
    """

    tag_name: str
    instance_id: int
    is_struct: bool
    data_type: str | None
    template_instance_id: int | None
    dimensions: tuple[int, ...]
    scope: str
