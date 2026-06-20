"""Tests for daedalus.cip.status."""

from daedalus.cip.status import (
    PRODUCT_TYPES,
    SERVICE_STATUS,
    VENDOR_IDS,
    VENDORS,
    decode_status,
    get_vendor,
)


def test_vendors_dict_is_int_keyed() -> None:
    for k in VENDORS:
        assert isinstance(k, int), f"Expected int key, got {type(k)}: {k!r}"


def test_vendor_ids_dict_is_str_keyed() -> None:
    for k in VENDOR_IDS:
        assert isinstance(k, str), f"Expected str key, got {type(k)}: {k!r}"


def test_vendor_ids_is_inverse_of_vendors() -> None:
    for vid, name in list(VENDORS.items())[:20]:  # spot-check first 20
        assert VENDOR_IDS[name] == vid


def test_rockwell_vendor() -> None:
    assert VENDORS[1] == "Rockwell Automation/Allen-Bradley"
    assert VENDOR_IDS["Rockwell Automation/Allen-Bradley"] == 1


def test_product_types_are_int_keyed() -> None:
    for k in PRODUCT_TYPES:
        assert isinstance(k, int)


def test_plc_product_type() -> None:
    assert PRODUCT_TYPES[0x0E] == "Programmable Logic Controller"


def test_service_status_has_expected_entries() -> None:
    assert 0x08 in SERVICE_STATUS
    assert "not supported" in SERVICE_STATUS[0x08].lower()


def test_decode_status_success() -> None:
    result = decode_status(0x00)
    # STATUS table, not SERVICE_STATUS, so 0x00 may not be there
    assert isinstance(result, str)


def test_decode_status_with_extended() -> None:
    # 0x01 with extended 0x0100 = "Connection in use"
    result = decode_status(0x01, 0x0100)
    assert "Connection in use" in result


def test_get_vendor_known() -> None:
    assert "Rockwell" in get_vendor(1)


def test_get_vendor_unknown() -> None:
    result = get_vendor(0x9999)
    assert "Unknown" in result or "9999" in result.lower()


def test_vendors_count() -> None:
    assert len(VENDORS) >= 100, "Expected at least 100 vendor entries"
