"""CIP Object Library: class codes, instance types, and common attributes.

I/O-FORBIDDEN: this module must never import socket, ssl, asyncio, anyio,
selectors, socketserver, http, urllib, or requests.
"""

from enum import IntEnum
from typing import Any, NamedTuple

from daedalus.cip.data_types import (
    BYTE,
    INT,
    SHORT_STRING,
    STRINGI,
    UDINT,
    UINT,
    USINT,
    WORD,
    Array,
    DataType,
    Struct,
)

__all__ = [
    "Attribute",
    "ClassCode",
    "CommonClassAttributes",
    "ConnectionManagerInstance",
    "FileObjectClassAttributes",
    "FileObjectInstance",
    "FileObjectInstanceAttributes",
    "IdentityObjectInstanceAttributes",
]


class Attribute(NamedTuple):
    attr_id: int
    data_type: DataType[Any] | type[DataType[Any]]


class ClassCode(IntEnum):
    """CIP Object Class codes."""

    IDENTITY_OBJECT = 0x01
    MESSAGE_ROUTER = 0x02
    DEVICE_NET = 0x03
    ASSEMBLY = 0x04
    CONNECTION = 0x05
    CONNECTION_MANAGER = 0x06
    REGISTER = 0x07
    DISCRETE_INPUT = 0x08
    DISCRETE_OUTPUT = 0x09
    ANALOG_INPUT = 0x0A
    ANALOG_OUTPUT = 0x0B
    PRESENCE_SENSING = 0x0E
    PARAMETER = 0x0F
    PARAMETER_GROUP = 0x10
    GROUP = 0x12
    DISCRETE_INPUT_GROUP = 0x1D
    DISCRETE_OUTPUT_GROUP = 0x1E
    DISCRETE_GROUP = 0x1F
    ANALOG_INPUT_GROUP = 0x20
    ANALOG_OUTPUT_GROUP = 0x21
    ANALOG_GROUP = 0x22
    POSITION_SENSOR = 0x23
    POSITION_CONTROLLER_SUPERVISOR = 0x24
    POSITION_CONTROLLER = 0x25
    BLOCK_SEQUENCER = 0x26
    COMMAND_BLOCK = 0x27
    MOTOR_DATA = 0x28
    CONTROL_SUPERVISOR = 0x29
    AC_DC_DRIVE = 0x2A
    ACKNOWLEDGE_HANDLER = 0x2B
    OVERLOAD = 0x2C
    SOFTSTART = 0x2D
    SELECTION = 0x2E
    S_DEVICE_SUPERVISOR = 0x30
    S_ANALOG_SENSOR = 0x31
    S_ANALOG_ACTUATOR = 0x32
    S_SINGLE_STAGE_CONTROLLER = 0x33
    S_GAS_CALIBRATION = 0x34
    TRIP_POINT = 0x35
    FILE_OBJECT = 0x37
    S_PARTIAL_PRESSURE = 0x38
    SAFETY_SUPERVISOR = 0x39
    SAFETY_VALIDATOR = 0x3A
    SAFETY_DISCRETE_OUTPUT_POINT = 0x3B
    SAFETY_DISCRETE_OUTPUT_GROUP = 0x3C
    SAFETY_DISCRETE_INPUT_POINT = 0x3D
    SAFETY_DISCRETE_INPUT_GROUP = 0x3E
    SAFETY_DUAL_CHANNEL_OUTPUT = 0x3F
    S_SENSOR_CALIBRATION = 0x40
    EVENT_LOG = 0x41
    MOTION_AXIS = 0x42
    TIME_SYNC = 0x43
    MODBUS = 0x44
    MODBUS_SERIAL_LINK = 0x46
    PROGRAM_NAME = 0x64
    SYMBOL_OBJECT = 0x6B
    TEMPLATE_OBJECT = 0x6C
    WALL_CLOCK_TIME = 0x8B
    CONTROLNET = 0xF0
    CONTROLNET_KEEPER = 0xF1
    CONTROLNET_SCHEDULING = 0xF2
    CONNECTION_CONFIGURATION = 0xF3
    PORT = 0xF4
    TCP_IP_INTERFACE = 0xF5
    ETHERNET_LINK = 0xF6
    COMPONET_LINK = 0xF7
    COMPONET_REPEATER = 0xF8


class ConnectionManagerInstance(IntEnum):
    """Connection Manager Object instance numbers."""

    OPEN_REQUEST = 0x01
    OPEN_FORMAT_REJECTED = 0x02
    OPEN_RESOURCE_REJECTED = 0x03
    OPEN_OTHER_REJECTED = 0x04
    CLOSE_REQUEST = 0x05
    CLOSE_FORMAT_REQUEST = 0x06
    CLOSE_OTHER_REQUEST = 0x07
    CONNECTION_TIMEOUT = 0x08


CommonClassAttributes: dict[str, Attribute] = {
    "revision": Attribute(1, UINT("revision")),
    "max_instance": Attribute(2, UINT("max_instance")),
    "number_of_instances": Attribute(3, UINT("number_of_instances")),
    "optional_attribute_list": Attribute(4, Array(UINT, UINT)),
    "optional_service_list": Attribute(5, Array(UINT, UINT)),
    "max_id_number_class_attributes": Attribute(6, UINT("max_id_class_attrs")),
    "max_id_number_instance_attributes": Attribute(7, UINT("max_id_instance_attrs")),
}

IdentityObjectInstanceAttributes: dict[str, Attribute] = {
    "vendor_id": Attribute(1, UINT("vendor_id")),
    "device_type": Attribute(2, UINT("device_type")),
    "product_code": Attribute(3, UINT("product_code")),
    "revision": Attribute(4, Struct(USINT("major"), USINT("minor"))),
    "status": Attribute(5, WORD("status")),
    "serial_number": Attribute(6, UDINT("serial_number")),
    "product_name": Attribute(7, SHORT_STRING("product_name")),
}

FileObjectClassAttributes: dict[str, Attribute] = {
    "directory": Attribute(
        32,
        Struct(UINT("instance_number"), STRINGI("instance_name"), STRINGI("file_name")),
    ),
}

FileObjectInstanceAttributes: dict[str, Attribute] = {
    "state": Attribute(1, USINT("state")),
    "instance_name": Attribute(2, STRINGI("instance_name")),
    "instance_format_version": Attribute(3, UINT("instance_format_version")),
    "file_name": Attribute(4, STRINGI("file_name")),
    "file_revision": Attribute(5, Struct(USINT("major"), USINT("minor"))),
    "file_size": Attribute(6, UDINT("file_size")),
    "file_checksum": Attribute(7, INT("file_checksum")),
    "invocation_method": Attribute(8, USINT("invocation_method")),
    "file_save_params": Attribute(9, BYTE("file_save_params")),
    "file_type": Attribute(10, USINT("file_type")),
    "file_encoding_format": Attribute(11, USINT("file_encoding_format")),
}


class FileObjectInstance(IntEnum):
    """Well-known File Object instance numbers."""

    EDS_FILE_AND_ICON = 0xC8
    RELATED_EDS_FILES_AND_ICONS = 0xC9
