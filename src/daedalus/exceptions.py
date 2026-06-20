"""Daedalus exception hierarchy."""

__all__ = [
    "BufferEmptyError",
    "CommError",
    "DaedalusError",
    "DataError",
    "RequestError",
    "ResponseError",
]


class DaedalusError(Exception):
    """Root exception for all daedalus errors."""


class CommError(DaedalusError):
    """Transport-level communication failure."""


class DataError(DaedalusError):
    """CIP data encoding or decoding failure."""


class BufferEmptyError(DataError):
    """Buffer exhausted during decode.

    Intentionally a DataError subclass so that the unbound-array termination
    mechanism works: Array._decode_all catches BufferEmptyError to detect end of
    stream, while every other decode wrapper re-raises it before converting to
    DataError.
    """


class ResponseError(DaedalusError):
    """CIP response indicated a service-level error."""


class RequestError(DaedalusError):
    """Request could not be built or dispatched."""
