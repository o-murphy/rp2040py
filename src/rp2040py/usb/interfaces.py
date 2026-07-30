from dataclasses import dataclass
from enum import IntEnum

__all__ = (
    "DataDirection",
    "DescriptorType",
    "ISetupPacketParams",
    "SetupRecipient",
    "SetupRequest",
    "SetupType",
)


class DataDirection(IntEnum):
    HOST_TO_DEVICE = 0
    DEVICE_TO_HOST = 1


class SetupType(IntEnum):
    STANDARD = 0
    CLASS = 1
    VENDOR = 2
    RESERVED = 3


class SetupRecipient(IntEnum):
    DEVICE = 0
    INTERFACE = 1
    ENDPOINT = 2
    OTHER = 3


class SetupRequest(IntEnum):
    GET_STATUS = 0
    CLEAR_FEATURE = 1
    RESERVED1 = 2
    SET_FEATURE = 3
    RESERVED2 = 4
    SET_ADDRESS = 5
    GET_DESCRIPTOR = 6
    SET_DESCRIPTOR = 7
    GET_CONFIGURATION = 8
    SET_DEVICE_CONFIGURATION = 9
    GET_INTERFACE = 10
    SET_INTERFACE = 11
    SYNCH_FRAME = 12


class DescriptorType(IntEnum):
    DEVICE = 1
    CONFIGRATION = 2
    STRING = 3
    INTERFACE = 4
    ENDPOINT = 5


@dataclass
class ISetupPacketParams:
    data_direction: DataDirection
    type: SetupType
    recipient: SetupRecipient
    b_request: SetupRequest | int
    w_value: int  # 16 bits
    w_index: int  # 16 bits
    w_length: int  # 16 bits
