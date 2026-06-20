"""CIP Template Object (class 0x6C) models and pure codec helpers.

Covers the three-step UDT pipeline:
    1. parse_template_attr_reply  — decode GET_ATTRIBUTE_LIST response into TemplateAttributes
    2. parse_template_data        — parse raw template bytes (member-info + names blob)
                                    ONE level only; driver handles I/O recursion for nested types
    3. decode_struct              — offset-aware struct decode into {member: value} or str

The *resolved* tree (ResolvedMember / ResolvedTemplate) must be fully built by the
driver before decode_struct is called — no I/O happens at decode time.

I/O-FORBIDDEN: this module must never import socket, ssl, asyncio, anyio,
selectors, socketserver, http, urllib, or requests.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from io import BytesIO
from typing import Any

from daedalus.cip.data_types import (
    Array,
)
from daedalus.exceptions import DataError

from daedalus.cip.data_types import DataType

__all__ = [
    "TEMPLATE_MEMBER_INFO_LEN",
    "RawMember",
    "ResolvedMember",
    "ResolvedTemplate",
    "TemplateAttributes",
    "decode_struct",
    "parse_template_attr_reply",
    "parse_template_data",
]

# Each member-info entry in the template data is 8 bytes: UINT + UINT + UDINT.
TEMPLATE_MEMBER_INFO_LEN: int = 8


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class TemplateAttributes:
    """Parsed result of GET_ATTRIBUTE_LIST on a Template Object instance."""

    object_definition_size: int  # UDINT — template data size in 32-bit words
    structure_size: int  # UDINT — structure size in bytes
    member_count: int  # UINT
    structure_handle: int  # UINT — echoed back in struct write requests


@dataclass
class RawMember:
    """One unparsed member entry from the template member-info array (8 bytes)."""

    type_info: int  # UINT — bit number (BOOL), array length, or 0 for scalar
    typ: int  # UINT — full CIP type code (atomic) or packed struct reference
    offset: int  # UDINT — byte offset of this member within the struct


@dataclass
class ResolvedMember:
    """Fully resolved member of a UDT/struct template.

    Exactly one of ``atomic_type`` / ``nested_template`` is non-None.
    BOOL members always have ``atomic_type = BOOL``.
    """

    name: str
    offset: int  # byte offset within the parent struct
    is_private: bool  # excluded from decoded output
    is_bool: bool
    bit_number: int  # only meaningful when is_bool
    is_array: bool
    array_length: int  # only meaningful when is_array
    atomic_type: type[DataType[Any]] | None  # set for atomic/array/bool members
    nested_template: ResolvedTemplate | None  # set for nested struct members


@dataclass
class ResolvedTemplate:
    """Fully resolved UDT/struct template — safe to decode without I/O."""

    name: str
    structure_size: int
    structure_handle: int
    members: list[ResolvedMember] = field(default_factory=list)
    is_string: bool = False
    string_length: int | None = None  # DATA array length when is_string


# ---------------------------------------------------------------------------
# Parse helpers
# ---------------------------------------------------------------------------


def parse_template_attr_reply(payload: bytes) -> TemplateAttributes:
    """Parse the payload of a GET_ATTRIBUTE_LIST reply for template makeup attributes.

    Expected layout: UINT(count=4) then 4 attribute records in order: attrs 4, 5, 2, 1.
    Attrs 4 (object_definition_size) and 5 (structure_size) have UDINT values.
    Attrs 2 (member_count) and 1 (structure_handle) have UINT values.
    Matches the StructTemplateAttributes definition in pycomm3 custom_types.py.
    """
    if len(payload) < 2:
        raise DataError(f"Template attr reply too short: {len(payload)} bytes")
    stream = BytesIO(payload)
    count = struct.unpack_from("<H", payload, 0)[0]
    stream.read(2)  # consume count

    attrs: dict[int, int] = {}
    for _ in range(count):
        if stream.tell() + 4 > len(payload):
            raise DataError("Template attr reply: truncated attribute record")
        attr_num = struct.unpack("<H", stream.read(2))[0]
        _status = struct.unpack("<H", stream.read(2))[0]
        # attrs 4 and 5 are UDINT (4 bytes); attrs 2 and 1 are UINT (2 bytes)
        if attr_num in (4, 5):
            if stream.tell() + 4 > len(payload):
                raise DataError(f"Template attr reply: truncated value for attr {attr_num}")
            value = struct.unpack("<I", stream.read(4))[0]
        else:
            if stream.tell() + 2 > len(payload):
                raise DataError(f"Template attr reply: truncated value for attr {attr_num}")
            value = struct.unpack("<H", stream.read(2))[0]
        attrs[attr_num] = value

    for required in (4, 5, 2, 1):
        if required not in attrs:
            raise DataError(f"Template attr reply: missing attribute {required}")

    return TemplateAttributes(
        object_definition_size=attrs[4],
        structure_size=attrs[5],
        member_count=attrs[2],
        structure_handle=attrs[1],
    )


def parse_template_data(
    data: bytes,
    attrs: TemplateAttributes,
    instance_id: int,
) -> tuple[str, list[tuple[str, RawMember]]]:
    """Parse raw template bytes into (template_name, [(member_name, RawMember), ...]).

    Parses ONE level only — struct members are left as raw RawMember objects with
    typ & 0x0FFF as the nested instance_id.  The driver calls _get_template
    recursively to resolve them.

    Names blob format:
    - User-defined types: ``"TYPENAME;hex_suffix\0member1\0member2\0..."``
      (first name contains ";" — text before it is the template name)
    - Predefined types: first name IS the template name (no ";" separator);
      pop it from the member list (exact pycomm3 _parse_template_data order).
    """
    member_count = attrs.member_count
    info_len = member_count * TEMPLATE_MEMBER_INFO_LEN
    if len(data) < info_len:
        raise DataError(
            f"Template data too short: {len(data)} bytes, "
            f"need {info_len} for {member_count} members"
        )

    raw_members: list[RawMember] = []
    for i in range(member_count):
        base = i * TEMPLATE_MEMBER_INFO_LEN
        type_info, typ, offset = struct.unpack_from("<HHI", data, base)
        raw_members.append(RawMember(type_info=type_info, typ=typ, offset=offset))

    # Parse names blob (null-separated strings after the info array)
    names_blob = data[info_len:]
    all_names = [n.decode("ascii", errors="replace") for n in names_blob.split(b"\x00")]
    # Drop the trailing empty string from the final null terminator
    while all_names and all_names[-1] == "":
        all_names.pop()

    is_predefined = instance_id < 0x100 or instance_id > 0xEFF
    template_name: str | None = None
    member_names: list[str] = list(all_names)

    if member_names and ";" in member_names[0]:
        # User-defined type: first name is "TYPENAME;hex_suffix"
        template_name = member_names.pop(0).split(";", 1)[0]
    elif is_predefined and member_names:
        # Predefined type: first name IS the template name (pycomm3 exact behaviour)
        template_name = member_names.pop(0)
    # else: neither — template_name stays None (use fallback below)

    if template_name == "ASCIISTRING82":
        template_name = "STRING"

    final_name = template_name or "UNKNOWN"

    # Pad / truncate member_names to match member_count; fill gaps with __unknown{n}
    while len(member_names) < member_count:
        member_names.append(f"__unknown{len(member_names)}")
    member_names = member_names[:member_count]
    # Replace empty names
    for i, nm in enumerate(member_names):
        if not nm:
            member_names[i] = f"__unknown{i}"

    return final_name, list(zip(member_names, raw_members, strict=False))


# ---------------------------------------------------------------------------
# Offset-aware struct decoder
# ---------------------------------------------------------------------------


def decode_struct(member_data: bytes, template: ResolvedTemplate) -> dict[str, Any] | str:
    """Decode raw struct member bytes into a ``{member: value}`` dict (or str for strings).

    The *template* must be fully resolved (no I/O at decode time).
    Private members and BOOL-bit alias host members are excluded from the result.
    """
    if template.is_string:
        # String layout: LEN then DATA — use actual member offsets from resolved members
        non_private = [m for m in template.members if not m.is_private]
        len_m = next((m for m in non_private if m.name == "LEN"), None)
        data_m = next((m for m in non_private if m.name == "DATA"), None)
        if len_m is None or data_m is None or len_m.atomic_type is None:
            return ""
        try:
            length = len_m.atomic_type.decode(BytesIO(member_data[len_m.offset :]))
            chars = member_data[data_m.offset : data_m.offset + length]
            return chars.decode("utf-8", errors="replace")
        except Exception:
            return ""

    result: dict[str, Any] = {}
    raw = member_data
    for member in template.members:
        if member.is_private:
            continue
        try:
            if member.is_bool:
                byte_val = raw[member.offset] if member.offset < len(raw) else 0
                result[member.name] = bool(byte_val & (1 << member.bit_number))
            elif member.nested_template is not None:
                sub_size = member.nested_template.structure_size
                sub_data = raw[member.offset : member.offset + sub_size]
                result[member.name] = decode_struct(sub_data, member.nested_template)
            elif member.is_array and member.atomic_type is not None:
                arr_type = Array(member.array_length, member.atomic_type)
                result[member.name] = arr_type.decode(BytesIO(raw[member.offset :]))
            elif member.atomic_type is not None:
                result[member.name] = member.atomic_type.decode(BytesIO(raw[member.offset :]))
        except Exception as exc:
            result[member.name] = f"<decode error: {exc}>"

    return result
