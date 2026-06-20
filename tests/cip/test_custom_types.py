"""Tests for daedalus.cip.custom_types."""

from daedalus.cip.custom_types import IPAddress, Revision


def test_ipaddress_encode_decode() -> None:
    ip = "192.168.1.100"
    encoded = IPAddress.encode(ip)
    assert len(encoded) == 4
    assert IPAddress.decode(encoded) == ip


def test_ipaddress_encode_produces_4_bytes() -> None:
    encoded = IPAddress.encode("10.0.0.1")
    assert len(encoded) == 4
    assert encoded == bytes([10, 0, 0, 1])


def test_revision_encode_decode() -> None:
    encoded = Revision.encode({"major": 1, "minor": 2})
    assert encoded == b"\x01\x02"
    decoded = Revision.decode(encoded)
    assert decoded == {"major": 1, "minor": 2}
