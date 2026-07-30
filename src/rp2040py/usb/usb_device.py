"""Interface for simulated USB devices that can be connected to the RP2040 USB host controller."""

from dataclasses import dataclass
from enum import IntEnum
from typing import Literal, Protocol

__all__ = (
    "USB_DIR_IN",
    "USB_DIR_OUT",
    "USB_RECIP_DEVICE",
    "USB_RECIP_ENDPOINT",
    "USB_RECIP_INTERFACE",
    "USB_RECIP_OTHER",
    "USB_TYPE_CLASS",
    "USB_TYPE_STANDARD",
    "USB_TYPE_VENDOR",
    "DescriptorType",
    "StandardRequest",
    "USBDevice",
    "USBEndpointTransfer",
    "USBTransferResult",
    "USBTransferStatus",
    "parse_setup_packet",
)

USBTransferStatus = Literal["ack", "nak", "stall"]


@dataclass
class USBTransferResult:
    status: USBTransferStatus
    data: bytes | bytearray | None = None


@dataclass
class USBEndpointTransfer:
    endpoint_address: int
    data: bytes


class USBDevice(Protocol):
    """Abstract interface for a USB device that can be connected to the RP2040 USB host."""

    # Current device address (0 = default, 1-127 = assigned)
    address: int

    def handle_setup_packet(self, setup: bytes) -> USBTransferResult:
        """Handle a SETUP packet from the host.

        :param setup: 8-byte setup packet
        :return: Response data for control IN transfers, or status for control OUT
        """
        ...

    def handle_data_out(self, endpoint_address: int, data: bytes) -> USBTransferResult:
        """Handle data OUT from host to device (non-control endpoint).

        :param endpoint_address: Endpoint address (0x01-0x0F for OUT endpoints)
        :param data: Data received from host
        """
        ...

    def handle_data_in(self, endpoint_address: int) -> USBTransferResult:
        """Handle data IN request from host (non-control endpoint).

        :param endpoint_address: Endpoint address (0x81-0x8F for IN endpoints)
        :return: Data to send to host, or NAK if no data available
        """
        ...

    def on_reset(self) -> None:
        """Called when the host resets the USB bus."""
        ...

    # The two methods below are optional in the upstream TS interface - implementers may not
    # define them. Call sites use getattr(device, "...", None) to mirror JS optional chaining.

    def on_address_assigned(self, address: int) -> None:
        """Called when the host assigns a new address to the device."""
        ...

    def on_configured(self, configuration_value: int) -> None:
        """Called when the host sets the device configuration."""
        ...


# USB Setup packet fields - bmRequestType bit masks
# Direction (bit 7)
USB_DIR_OUT = 0x00
USB_DIR_IN = 0x80
# Type (bits 6:5)
USB_TYPE_STANDARD = 0x00
USB_TYPE_CLASS = 0x20
USB_TYPE_VENDOR = 0x40
# Recipient (bits 4:0)
USB_RECIP_DEVICE = 0x00
USB_RECIP_INTERFACE = 0x01
USB_RECIP_ENDPOINT = 0x02
USB_RECIP_OTHER = 0x03


class StandardRequest(IntEnum):
    GET_STATUS = 0
    CLEAR_FEATURE = 1
    SET_FEATURE = 3
    SET_ADDRESS = 5
    GET_DESCRIPTOR = 6
    SET_DESCRIPTOR = 7
    GET_CONFIGURATION = 8
    SET_CONFIGURATION = 9
    GET_INTERFACE = 10
    SET_INTERFACE = 11
    SYNCH_FRAME = 12


class DescriptorType(IntEnum):
    DEVICE = 1
    CONFIGURATION = 2
    STRING = 3
    INTERFACE = 4
    ENDPOINT = 5
    DEVICE_QUALIFIER = 6
    OTHER_SPEED_CONFIGURATION = 7
    INTERFACE_POWER = 8
    HID = 0x21
    HID_REPORT = 0x22
    HID_PHYSICAL = 0x23


@dataclass
class SetupPacket:
    bm_request_type: int
    b_request: int
    w_value: int
    w_index: int
    w_length: int
    # Helpers
    direction: Literal["in", "out"]
    type: int  # 0=Standard, 1=Class, 2=Vendor
    recipient: int  # 0=Device, 1=Interface, 2=Endpoint, 3=Other


def parse_setup_packet(setup: bytes) -> SetupPacket:
    return SetupPacket(
        bm_request_type=setup[0],
        b_request=setup[1],
        w_value=setup[2] | (setup[3] << 8),
        w_index=setup[4] | (setup[5] << 8),
        w_length=setup[6] | (setup[7] << 8),
        direction="in" if setup[0] & 0x80 else "out",
        type=(setup[0] >> 5) & 0x03,
        recipient=setup[0] & 0x1F,
    )
