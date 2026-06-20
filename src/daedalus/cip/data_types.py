"""CIP data-type codec (L0 — pure, no I/O).

Every public type exposes:
    encode(value) -> bytes
    decode(buffer: bytes | BytesIO) -> <Python value>

I/O-FORBIDDEN: this module must never import socket, ssl, asyncio, anyio,
selectors, socketserver, http, urllib, or requests.
"""

from __future__ import annotations

import reprlib
from collections.abc import Sequence
from dataclasses import dataclass
from io import BytesIO
from itertools import chain
from struct import pack, unpack
from typing import TYPE_CHECKING, Any, ClassVar, Final, Generic, TypeVar

from daedalus.exceptions import BufferEmptyError, DataError

if TYPE_CHECKING:
    pass

__all__ = [
    # Boolean
    "BOOL",
    # Bit arrays
    "BYTE",
    # Registries
    "DATA_TYPES_BY_CODE",
    "DATA_TYPES_BY_NAME",
    "DATE",
    "DATE_AND_TIME",
    "DINT",
    "DT",
    "DWORD",
    "ENGUNIT",
    # EPATH placeholder (type code only; full EPATH codec is in segments.py)
    "EPATH_TYPE",
    "FTIME",
    "INT",
    "ITIME",
    "LDT",
    "LINT",
    "LOGIX_STRING",
    "LREAL",
    "LTIME",
    "LWORD",
    # Floats
    "REAL",
    "SHORT_STRING",
    # Elementary signed integers
    "SINT",
    "STIME",
    "STRING",
    "STRING2",
    "STRINGI",
    "STRINGN",
    # Time/date
    "TIME",
    "TIME32",
    "TIME_OF_DAY",
    "UDINT",
    "UINT",
    "ULINT",
    # Elementary unsigned integers
    "USINT",
    "WORD",
    "Array",
    "ArrayType",
    # Base
    "DataType",
    "DateAndTimeValue",
    # Strings
    "StringDataType",
    "StringIEntry",
    "Struct",
    "StructType",
]

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

T = TypeVar("T")
_BufferType = bytes | BytesIO


def _as_stream(buffer: _BufferType) -> BytesIO:
    if isinstance(buffer, BytesIO):
        return buffer
    return BytesIO(buffer)


def _stream_read(stream: BytesIO, size: int) -> bytes:
    """Read exactly *size* bytes; raise BufferEmptyError on exhaustion."""
    if size == 0:
        return b""
    data = stream.read(size)
    if not data:
        raise BufferEmptyError()
    return data


def _repr(buffer: _BufferType) -> str:
    if isinstance(buffer, BytesIO):
        return reprlib.repr(buffer.getvalue())
    return reprlib.repr(buffer)


# ---------------------------------------------------------------------------
# Metaclass
# ---------------------------------------------------------------------------


class _DataTypeMeta(type):
    def __repr__(cls) -> str:
        return cls.__name__


class _ArrayReprMeta(_DataTypeMeta):
    def __repr__(cls) -> str:
        try:
            return f"{cls.element_type!r}[{cls.length!r}]"  # type: ignore[attr-defined]
        except AttributeError:
            return cls.__name__


class _StructReprMeta(_DataTypeMeta):
    def __repr__(cls) -> str:
        try:
            return f"Struct({', '.join(repr(m) for m in cls.members)})"  # type: ignore[attr-defined]
        except AttributeError:
            return cls.__name__


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------


class DataType(Generic[T], metaclass=_DataTypeMeta):
    """Abstract base for all CIP data types.

    Subclasses override *_encode* / *_decode* (private, no error handling
    needed) or the public *encode* / *decode* (must handle exceptions).
    """

    name: str | None = None
    code: ClassVar[int] = 0x00
    size: ClassVar[int] = 0

    def __init__(self, name: str | None = None) -> None:
        self.name = name

    @classmethod
    def encode(cls, value: Any) -> bytes:
        try:
            return cls._encode(value)
        except Exception as exc:
            raise DataError(f"Error packing {value!r} as {cls.__name__}") from exc

    @classmethod
    def _encode(cls, value: Any) -> bytes:
        raise NotImplementedError

    @classmethod
    def decode(cls, buffer: _BufferType) -> Any:
        try:
            stream = _as_stream(buffer)
            return cls._decode(stream)
        except Exception as exc:
            if isinstance(exc, BufferEmptyError):
                raise
            raise DataError(f"Error unpacking {_repr(buffer)} as {cls.__name__}") from exc

    @classmethod
    def _decode(cls, stream: BytesIO) -> Any:
        raise NotImplementedError

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name={self.name!r})"

    __str__ = __repr__


# ---------------------------------------------------------------------------
# Intermediate base types
# ---------------------------------------------------------------------------


class ElementaryDataType(DataType[T]):
    """Single primitive value with a fixed struct format."""

    _format: ClassVar[str] = ""

    @classmethod
    def _encode(cls, value: Any) -> bytes:
        return pack(cls._format, value)

    @classmethod
    def _decode(cls, stream: BytesIO) -> Any:
        data = _stream_read(stream, cls.size)
        return unpack(cls._format, data)[0]


class DerivedDataType(DataType[T]):
    """Base for composed (Array/Struct) types."""


class BitArrayType(ElementaryDataType[list[bool]]):
    """Array of booleans packed into an integer."""

    host_type: ClassVar[type[ElementaryDataType[int]]]

    @classmethod
    def _decode(cls, stream: BytesIO) -> list[bool]:
        val: int = cls.host_type.decode(stream)
        bits = [c == "1" for c in bin(val)[2:]]
        bools = [False] * (cls.size * 8 - len(bits)) + bits
        bools.reverse()
        return bools

    @classmethod
    def _encode(cls, value: list[bool]) -> bytes:
        if len(value) != cls.size * 8:
            raise DataError(
                f"boolean array must have exactly {cls.size * 8} elements, got {len(value)}"
            )
        int_val = 0
        for i, bit in enumerate(value):
            if bit:
                int_val |= 1 << i
        return cls.host_type.encode(int_val)


class BytesDataType(ElementaryDataType[bytes]):
    """Raw bytes placeholder."""

    @classmethod
    def _encode(cls, value: bytes) -> bytes:
        return value[: cls.size] if cls.size != -1 else value[:]

    @classmethod
    def _decode(cls, stream: BytesIO) -> bytes:
        return _stream_read(stream, cls.size)


class StringDataType(ElementaryDataType[str]):
    """Variable-length CIP string (length-prefix + data bytes)."""

    len_type: ClassVar[type[ElementaryDataType[int]]]
    encoding: ClassVar[str] = "iso-8859-1"

    @classmethod
    def _encode(cls, value: str) -> bytes:
        encoded = value.encode(cls.encoding)
        return cls.len_type.encode(len(encoded)) + encoded

    @classmethod
    def _decode(cls, stream: BytesIO) -> str:
        str_len: int = cls.len_type.decode(stream)
        if str_len == 0:
            return ""
        data = _stream_read(stream, str_len)
        return data.decode(cls.encoding)


# ---------------------------------------------------------------------------
# ArrayType and StructType stubs (concrete classes returned by factories)
# ---------------------------------------------------------------------------


class ArrayType(DerivedDataType[list[Any]], metaclass=_ArrayReprMeta):
    """Base for Array(...) generated classes."""

    length: ClassVar[int | type[DataType[Any]] | None]
    element_type: ClassVar[type[DataType[Any]]]

    @classmethod
    def _decode_all(cls, stream: BytesIO) -> list[Any]:
        result: list[Any] = []
        while True:
            try:
                result.append(cls.element_type.decode(stream))
            except BufferEmptyError:
                break
        return result


class StructType(DerivedDataType[dict[str, Any]], metaclass=_StructReprMeta):
    """Base for Struct(...) generated classes."""

    members: ClassVar[tuple[DataType[Any], ...]]


# ---------------------------------------------------------------------------
# Elementary signed integers
# ---------------------------------------------------------------------------


class SINT(ElementaryDataType[int]):
    """Signed 8-bit integer."""

    code: ClassVar[int] = 0xC2
    size: ClassVar[int] = 1
    _format: ClassVar[str] = "<b"


class INT(ElementaryDataType[int]):
    """Signed 16-bit integer."""

    code: ClassVar[int] = 0xC3
    size: ClassVar[int] = 2
    _format: ClassVar[str] = "<h"


class DINT(ElementaryDataType[int]):
    """Signed 32-bit integer."""

    code: ClassVar[int] = 0xC4
    size: ClassVar[int] = 4
    _format: ClassVar[str] = "<i"


class LINT(ElementaryDataType[int]):
    """Signed 64-bit integer."""

    code: ClassVar[int] = 0xC5
    size: ClassVar[int] = 8
    _format: ClassVar[str] = "<q"


# ---------------------------------------------------------------------------
# Elementary unsigned integers
# ---------------------------------------------------------------------------


class USINT(ElementaryDataType[int]):
    """Unsigned 8-bit integer."""

    code: ClassVar[int] = 0xC6
    size: ClassVar[int] = 1
    _format: ClassVar[str] = "<B"


class UINT(ElementaryDataType[int]):
    """Unsigned 16-bit integer."""

    code: ClassVar[int] = 0xC7
    size: ClassVar[int] = 2
    _format: ClassVar[str] = "<H"


class UDINT(ElementaryDataType[int]):
    """Unsigned 32-bit integer."""

    code: ClassVar[int] = 0xC8
    size: ClassVar[int] = 4
    _format: ClassVar[str] = "<I"


class ULINT(ElementaryDataType[int]):
    """Unsigned 64-bit integer."""

    code: ClassVar[int] = 0xC9
    size: ClassVar[int] = 8
    _format: ClassVar[str] = "<Q"


# ---------------------------------------------------------------------------
# Floats
# ---------------------------------------------------------------------------


class REAL(ElementaryDataType[float]):
    """32-bit IEEE 754 float."""

    code: ClassVar[int] = 0xCA
    size: ClassVar[int] = 4
    _format: ClassVar[str] = "<f"


class LREAL(ElementaryDataType[float]):
    """64-bit IEEE 754 double."""

    code: ClassVar[int] = 0xCB
    size: ClassVar[int] = 8
    _format: ClassVar[str] = "<d"


# ---------------------------------------------------------------------------
# Boolean
# ---------------------------------------------------------------------------


class BOOL(DataType[bool]):
    """Boolean.

    Encodes True as 0xFF per CIP spec (not 0x01).
    Decodes any non-zero byte as True.
    """

    code: ClassVar[int] = 0xC1
    size: ClassVar[int] = 1

    @classmethod
    def encode(cls, value: Any) -> bytes:
        try:
            return b"\xff" if value else b"\x00"
        except Exception as exc:
            raise DataError(f"Error packing {value!r} as BOOL") from exc

    @classmethod
    def decode(cls, buffer: _BufferType) -> bool:
        try:
            stream = _as_stream(buffer)
            return _stream_read(stream, 1)[0] != 0
        except Exception as exc:
            if isinstance(exc, BufferEmptyError):
                raise
            raise DataError(f"Error unpacking {_repr(buffer)} as BOOL") from exc


# ---------------------------------------------------------------------------
# Bit arrays
# ---------------------------------------------------------------------------


class BYTE(BitArrayType):
    """Bit string — 8 bits."""

    code: ClassVar[int] = 0xD1
    size: ClassVar[int] = 1
    host_type = USINT


class WORD(BitArrayType):
    """Bit string — 16 bits."""

    code: ClassVar[int] = 0xD2
    size: ClassVar[int] = 2
    host_type = UINT


class DWORD(BitArrayType):
    """Bit string — 32 bits."""

    code: ClassVar[int] = 0xD3
    size: ClassVar[int] = 4
    host_type = UDINT


class LWORD(BitArrayType):
    """Bit string — 64 bits."""

    code: ClassVar[int] = 0xD4
    size: ClassVar[int] = 8
    host_type = ULINT


# ---------------------------------------------------------------------------
# Time / date types (all backed by integer primitives)
# ---------------------------------------------------------------------------


class TIME(DINT):
    """Duration in milliseconds."""

    code: ClassVar[int] = 0xDB


class LTIME(LINT):
    """Duration — long (nanoseconds)."""

    code: ClassVar[int] = 0xD7


class ITIME(INT):
    """Duration — short (milliseconds, 16-bit)."""

    code: ClassVar[int] = 0xD8


class LDT(ULINT):
    """Long date and time in nanoseconds.

    Canonical owner of type code 0xCC.
    """

    code: ClassVar[int] = 0xCC


class STIME(DINT):
    """Synchronous time information.

    Alias sharing type code 0xCC with LDT; LDT is the canonical registry entry.
    """

    code: ClassVar[int] = 0xCC


class DT(ULINT):
    """Date and time in microseconds."""

    code: ClassVar[int] = 0xC0


class DATE(UINT):
    """Date information."""

    code: ClassVar[int] = 0xCD


class TIME_OF_DAY(UDINT):
    """Time of day."""

    code: ClassVar[int] = 0xCE


class FTIME(DINT):
    """Duration — high resolution (microseconds).

    Canonical owner of type code 0xD6.
    """

    code: ClassVar[int] = 0xD6


class TIME32(UDINT):
    """Duration in microseconds (unsigned 32-bit).

    Alias sharing type code 0xD6 with FTIME; FTIME is the canonical registry entry.
    """

    code: ClassVar[int] = 0xD6


@dataclass(frozen=True)
class DateAndTimeValue:
    """Decoded value for DATE_AND_TIME."""

    time_of_day: int  # UDINT — milliseconds since midnight
    date: int  # UINT — days since 1/1/1972


class DATE_AND_TIME(DataType[DateAndTimeValue]):
    """Date and time of day.

    pycomm3 declares size=8 but the wire encoding is UDINT(4)+UINT(2)=6 bytes.
    """

    code: ClassVar[int] = 0xCF
    size: ClassVar[int] = 6

    @classmethod
    def encode(cls, value: DateAndTimeValue) -> bytes:
        try:
            return UDINT.encode(value.time_of_day) + UINT.encode(value.date)
        except Exception as exc:
            raise DataError(f"Error packing {value!r} as DATE_AND_TIME") from exc

    @classmethod
    def decode(cls, buffer: _BufferType) -> DateAndTimeValue:
        try:
            stream = _as_stream(buffer)
            tod = UDINT.decode(stream)
            date = UINT.decode(stream)
            return DateAndTimeValue(time_of_day=tod, date=date)
        except Exception as exc:
            if isinstance(exc, BufferEmptyError):
                raise
            raise DataError(f"Error unpacking {_repr(buffer)} as DATE_AND_TIME") from exc


# ---------------------------------------------------------------------------
# Engineering units
# ---------------------------------------------------------------------------


class ENGUNIT(WORD):
    """Engineering units."""

    code: ClassVar[int] = 0xDD


# ---------------------------------------------------------------------------
# EPATH type-code placeholder
# ---------------------------------------------------------------------------


class EPATH_TYPE(DataType[bytes]):
    """Placeholder type representing the EPATH data-type code 0xDC.

    Full EPATH encode/decode is in daedalus.cip.segments.
    """

    code: ClassVar[int] = 0xDC
    size: ClassVar[int] = 0


# ---------------------------------------------------------------------------
# String types
# ---------------------------------------------------------------------------


class STRING(StringDataType):
    """Character string: 1 byte/char, 2-byte length prefix."""

    code: ClassVar[int] = 0xD0
    len_type = UINT


class STRING2(StringDataType):
    """Character string: 2 bytes/char (UTF-16-LE), 2-byte length prefix."""

    code: ClassVar[int] = 0xD5
    len_type = UINT
    encoding: ClassVar[str] = "utf-16-le"


class STRINGN(DataType[str]):
    """Character string with variable character width."""

    code: ClassVar[int] = 0xD9

    _ENCODINGS: ClassVar[dict[int, str]] = {
        1: "utf-8",
        2: "utf-16-le",
        4: "utf-32-le",
    }

    @classmethod
    def encode(cls, value: str, char_size: int = 1) -> bytes:
        try:
            enc = cls._ENCODINGS[char_size]
            encoded = value.encode(enc)
            return UINT.encode(char_size) + UINT.encode(len(value)) + encoded
        except Exception as exc:
            raise DataError(f"Error packing {value!r} as STRINGN") from exc

    @classmethod
    def decode(cls, buffer: _BufferType) -> str:
        try:
            stream = _as_stream(buffer)
            char_size = UINT.decode(stream)
            char_count = UINT.decode(stream)
            byte_count = char_size * char_count
            data = _stream_read(stream, byte_count)
            enc = cls._ENCODINGS.get(char_size, "utf-8")
            return data.decode(enc)
        except Exception as exc:
            if isinstance(exc, BufferEmptyError):
                raise
            raise DataError(f"Error unpacking {_repr(buffer)} as STRINGN") from exc


class SHORT_STRING(StringDataType):
    """Character string: 1 byte/char, 1-byte length prefix."""

    code: ClassVar[int] = 0xDA
    len_type = USINT


class LOGIX_STRING(StringDataType):
    """Allen-Bradley extension: 1 byte/char, 4-byte length prefix.

    Not a standard CIP type; code=0x00 (no assigned CIP code).
    """

    code: ClassVar[int] = 0x00
    len_type = UDINT


# ---------------------------------------------------------------------------
# STRINGI
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StringIEntry:
    """One entry in a STRINGI international string."""

    text: str
    language: str  # exactly 3 ASCII chars, e.g. "eng"
    char_set: int  # e.g. 4 = iso-8859-1, 1000 = utf-16-le
    string_type: type[DataType[str]]


class STRINGI(DataType[list[StringIEntry]]):
    """International character string (STRINGI, type 0xDE).

    pycomm3 has asymmetric encode(*args) / decode() → 3-tuple interface.
    Daedalus uses a symmetric list[StringIEntry] value for both.
    """

    code: ClassVar[int] = 0xDE

    _STRING_TYPES: ClassVar[dict[int, type[DataType[str]]]] = {
        STRING.code: STRING,
        STRING2.code: STRING2,
        STRINGN.code: STRINGN,
        SHORT_STRING.code: SHORT_STRING,
    }

    LANGUAGE_CODES: ClassVar[dict[str, str]] = {
        "english": "eng",
        "french": "fra",
        "spanish": "spa",
        "italian": "ita",
        "german": "deu",
        "japanese": "jpn",
        "portuguese": "por",
        "chinese": "zho",
        "russian": "rus",
    }

    CHARACTER_SETS: ClassVar[dict[str, int]] = {
        "iso-8859-1": 4,
        "iso-8859-2": 5,
        "iso-8859-3": 6,
        "iso-8859-4": 7,
        "iso-8859-5": 8,
        "iso-8859-6": 9,
        "iso-8859-7": 10,
        "iso-8859-8": 11,
        "iso-8859-9": 12,
        "utf-16-le": 1000,
        "utf-32-le": 1001,
    }

    @classmethod
    def encode(cls, value: list[StringIEntry]) -> bytes:
        try:
            data = USINT.encode(len(value))
            for entry in value:
                lang = entry.language.encode("ascii")  # 3 raw ASCII bytes
                str_type_code = bytes([entry.string_type.code])
                char_set = UINT.encode(entry.char_set)
                string = entry.string_type.encode(entry.text)
                data += lang + str_type_code + char_set + string
            return data
        except Exception as exc:
            raise DataError("Error packing STRINGI entries") from exc

    @classmethod
    def decode(cls, buffer: _BufferType) -> list[StringIEntry]:
        stream = _as_stream(buffer)
        try:
            count = USINT.decode(stream)
            entries: list[StringIEntry] = []
            for _ in range(count):
                # 3 raw ASCII bytes (not a SHORT_STRING decode — that's a pycomm3 hack)
                lang_raw = _stream_read(stream, 3)
                language = lang_raw.decode("ascii")
                str_type_code = _stream_read(stream, 1)[0]
                str_type = cls._STRING_TYPES[str_type_code]
                char_set = UINT.decode(stream)
                text = str_type.decode(stream)
                entries.append(
                    StringIEntry(
                        text=text, language=language, char_set=char_set, string_type=str_type
                    )
                )
            return entries
        except Exception as exc:
            if isinstance(exc, BufferEmptyError):
                raise
            raise DataError(f"Error unpacking {_repr(buffer)} as STRINGI") from exc


# ---------------------------------------------------------------------------
# Array factory
# ---------------------------------------------------------------------------


def Array(
    length_: int | type[DataType[Any]] | None,
    element_type_: type[DataType[Any]],
) -> type[ArrayType]:
    """Create an array type of *element_type_* with given *length_*.

    *length_* can be:
    - ``int`` — fixed length
    - ``DataType`` subclass — length read from buffer as that type
    - ``None`` — unbound; consumes entire buffer on decode
    """

    class _Array(ArrayType):
        length: ClassVar[int | type[DataType[Any]] | None] = length_
        element_type: ClassVar[type[DataType[Any]]] = element_type_

        @classmethod
        def encode(cls, values: list[Any], length: int | None = None) -> bytes:
            _length = length if length is not None else cls.length

            if isinstance(_length, int):
                if len(values) < _length:
                    raise DataError(
                        f"Not enough values for {cls.element_type!r}[{_length}]: got {len(values)}"
                    )
                _len = _length
            elif _length is None:
                _len = len(values)
            else:
                # _length is a DataType subclass — prefix encoded count
                _len = len(values)
                try:
                    prefix = _length.encode(_len)
                except Exception as exc:
                    raise DataError("Error encoding array length prefix") from exc

                try:
                    if issubclass(cls.element_type, BitArrayType):
                        chunk = cls.element_type.size * 8
                        chunks = [values[i : i + chunk] for i in range(0, len(values), chunk)]
                        return prefix + b"".join(cls.element_type.encode(c) for c in chunks)
                    return prefix + b"".join(
                        cls.element_type.encode(values[i]) for i in range(_len)
                    )
                except DataError:
                    raise
                except Exception as exc:
                    raise DataError("Error encoding array elements") from exc

            try:
                if issubclass(cls.element_type, BitArrayType):
                    chunk = cls.element_type.size * 8
                    real_len = len(values) // chunk
                    chunks = [values[i : i + chunk] for i in range(0, len(values), chunk)]
                    return b"".join(cls.element_type.encode(chunks[i]) for i in range(real_len))
                return b"".join(cls.element_type.encode(values[i]) for i in range(_len))
            except DataError:
                raise
            except Exception as exc:
                raise DataError(
                    f"Error encoding {reprlib.repr(values)} as {cls.element_type!r}[{_length!r}]"
                ) from exc

        @classmethod
        def decode(cls, buffer: _BufferType, length: int | None = None) -> list[Any]:
            _length: int | type[DataType[Any]] | None = length if length is not None else cls.length
            try:
                stream = _as_stream(buffer)
                if _length is None:
                    result = cls._decode_all(stream)
                elif isinstance(_length, type) and issubclass(_length, DataType):
                    # Bug fix: read count from buffer, not iterate over the DataType class
                    _len: int = _length.decode(stream)
                    result = [cls.element_type.decode(stream) for _ in range(_len)]
                else:
                    _len = _length
                    result = [cls.element_type.decode(stream) for _ in range(_len)]

                if issubclass(cls.element_type, BitArrayType):
                    return list(chain.from_iterable(result))
                return result
            except Exception as exc:
                if isinstance(exc, BufferEmptyError):
                    raise
                raise DataError(
                    f"Error unpacking {_repr(buffer)} as {cls.element_type!r}[{_length!r}]"
                ) from exc

        def __repr__(self) -> str:
            return f"{self.__class__.__name__}(name={self.name!r})"

    _Array.__name__ = f"Array({element_type_!r}, {length_!r})"
    _Array.__qualname__ = _Array.__name__
    return _Array


# ---------------------------------------------------------------------------
# Struct factory
# ---------------------------------------------------------------------------


def Struct(*members_: DataType[Any]) -> type[StructType]:
    """Create a struct type from *members_*.

    Members with a ``name`` attribute produce ``{name: value}`` in decoded dicts.
    Unnamed members are consumed but excluded from the result.
    """

    class _Struct(StructType):
        members: ClassVar[tuple[DataType[Any], ...]] = members_

        @classmethod
        def _encode(cls, values: dict[str, Any] | Sequence[Any]) -> bytes:
            if isinstance(values, dict):
                return b"".join(typ.encode(values[typ.name]) for typ in cls.members if typ.name)
            return b"".join(typ.encode(val) for typ, val in zip(cls.members, values, strict=False))

        @classmethod
        def _decode(cls, stream: BytesIO) -> dict[str, Any]:
            values: dict[str, Any] = {}
            for typ in cls.members:
                val = typ.decode(stream)
                if typ.name:
                    values[typ.name] = val
            return values

    _Struct.__name__ = f"Struct({', '.join(repr(m) for m in members_)})"
    _Struct.__qualname__ = _Struct.__name__
    return _Struct


# ---------------------------------------------------------------------------
# Type registries
# ---------------------------------------------------------------------------

_ALL_CONCRETE: Final[list[type[DataType[Any]]]] = [
    BOOL,
    SINT,
    INT,
    DINT,
    LINT,
    USINT,
    UINT,
    UDINT,
    ULINT,
    REAL,
    LREAL,
    DT,  # 0xC0 — must be before DT alias
    LDT,  # 0xCC canonical
    STIME,  # 0xCC alias — will be overridden
    DATE,
    TIME_OF_DAY,
    DATE_AND_TIME,
    FTIME,  # 0xD6 canonical
    TIME32,  # 0xD6 alias — will be overridden
    BYTE,
    WORD,
    DWORD,
    LWORD,
    STRING,
    STRING2,
    STRINGN,
    SHORT_STRING,
    LOGIX_STRING,
    STRINGI,
    ENGUNIT,
    EPATH_TYPE,
    TIME,
    LTIME,
    ITIME,
]

# Build registry from each type's .code; then pin canonical owners of collisions
_code_map: dict[int, type[DataType[Any]]] = {cls.code: cls for cls in _ALL_CONCRETE}
_code_map[0xCC] = LDT  # pin canonical over STIME
_code_map[0xD6] = FTIME  # pin canonical over TIME32

DATA_TYPES_BY_CODE: Final[dict[int, type[DataType[Any]]]] = _code_map

_name_map: dict[str, type[DataType[Any]]] = {cls.__name__.lower(): cls for cls in _ALL_CONCRETE}
# Manual aliases for non-canonical names
_name_map["stime"] = STIME
_name_map["time32"] = TIME32

DATA_TYPES_BY_NAME: Final[dict[str, type[DataType[Any]]]] = _name_map
