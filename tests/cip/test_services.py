"""Tests for daedalus.cip.services."""

from daedalus.cip.services import (
    MULTI_PACKET_SERVICES,
    CIPService,
    ConnectionManagerService,
    EncapsulationCommand,
)


def test_encapsulation_command_values() -> None:
    assert int(EncapsulationCommand.REGISTER_SESSION) == 0x65
    assert int(EncapsulationCommand.UNREGISTER_SESSION) == 0x66
    assert int(EncapsulationCommand.SEND_RR_DATA) == 0x6F
    assert int(EncapsulationCommand.SEND_UNIT_DATA) == 0x70
    assert int(EncapsulationCommand.LIST_IDENTITY) == 0x63


def test_cip_service_values() -> None:
    assert int(CIPService.READ_TAG) == 0x4C
    assert int(CIPService.WRITE_TAG) == 0x4D
    assert int(CIPService.READ_TAG_FRAGMENTED) == 0x52
    assert int(CIPService.WRITE_TAG_FRAGMENTED) == 0x53
    assert int(CIPService.GET_INSTANCE_ATTRIBUTE_LIST) == 0x55
    assert int(CIPService.MULTIPLE_SERVICE_REQUEST) == 0x0A
    assert int(CIPService.GET_ATTRIBUTE_SINGLE) == 0x0E


def test_connection_manager_service_values() -> None:
    assert int(ConnectionManagerService.FORWARD_OPEN) == 0x54
    assert int(ConnectionManagerService.LARGE_FORWARD_OPEN) == 0x5B
    assert int(ConnectionManagerService.FORWARD_CLOSE) == 0x4E
    assert int(ConnectionManagerService.UNCONNECTED_SEND) == 0x52


def test_cip_service_from_reply_strips_reply_bit() -> None:
    reply_byte = int(CIPService.READ_TAG) | 0x80
    assert CIPService.from_reply(reply_byte) == CIPService.READ_TAG


def test_multi_packet_services_contents() -> None:
    assert CIPService.READ_TAG_FRAGMENTED in MULTI_PACKET_SERVICES
    assert CIPService.WRITE_TAG_FRAGMENTED in MULTI_PACKET_SERVICES
    assert CIPService.GET_INSTANCE_ATTRIBUTE_LIST in MULTI_PACKET_SERVICES
    assert CIPService.MULTIPLE_SERVICE_REQUEST in MULTI_PACKET_SERVICES
    assert CIPService.GET_ATTRIBUTE_LIST in MULTI_PACKET_SERVICES


def test_cip_service_is_int() -> None:
    assert isinstance(int(CIPService.READ_TAG), int)
    assert int(CIPService.READ_TAG) == 0x4C


def test_encapsulation_command_is_int() -> None:
    assert int(EncapsulationCommand.REGISTER_SESSION) == 0x65
