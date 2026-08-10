from collections.abc import Callable, Sequence
from typing import NamedTuple

from rp2040py.peripherals.usb import RPUSBController
from rp2040py.usb.interfaces import DataDirection, DescriptorType, ISetupPacketParams, SetupRecipient, SetupType
from rp2040py.usb.setup import (
    create_setup_packet,
    get_descriptor_packet,
    set_device_address_packet,
    set_device_configuration_packet,
)
from rp2040py.utils.fifo import FIFO

__all__ = (
    "USBCDC",
    "extract_endpoint_numbers",
)

# CDC stuff
CDC_REQUEST_SET_CONTROL_LINE_STATE = 0x22

CDC_DTR = 1 << 0
CDC_RTS = 1 << 1

CDC_DATA_CLASS = 10
ENDPOINT_BULK = 2

TX_FIFO_SIZE = 512

ENDPOINT_ZERO = 0
CONFIGURATION_DESCRIPTOR_SIZE = 9


class EndpointNumbers(NamedTuple):
    in_endpoint: int
    out_endpoint: int


def extract_endpoint_numbers(descriptors: Sequence[int]) -> EndpointNumbers:
    index = 0
    found_interface = False
    in_endpoint = -1
    out_endpoint = -1
    while index < len(descriptors):
        length = descriptors[index]
        if length < 2 or len(descriptors) < index + length:
            break
        desc_type = descriptors[index + 1]
        if desc_type == DescriptorType.INTERFACE and length == 9:
            num_endpoints = descriptors[index + 4]
            interface_class = descriptors[index + 5]
            found_interface = num_endpoints == 2 and interface_class == CDC_DATA_CLASS
        if found_interface and desc_type == DescriptorType.ENDPOINT and length == 7:
            address = descriptors[index + 2]
            attributes = descriptors[index + 3]
            if (attributes & 0x3) == ENDPOINT_BULK:
                if address & 0x80:
                    in_endpoint = address & 0xF
                else:
                    out_endpoint = address & 0xF
        index += descriptors[index]
    return EndpointNumbers(in_endpoint=in_endpoint, out_endpoint=out_endpoint)


class USBCDC:
    def __init__(self, usb: RPUSBController):
        self.usb = usb
        self.tx_fifo = FIFO(TX_FIFO_SIZE)

        self.on_serial_data: Callable[[bytes | bytearray], None] | None = None
        self.on_device_connected: Callable[[], None] | None = None

        self._initialized = False
        self._descriptors_size: int | None = None
        self._descriptors: list[int] = []
        self._out_endpoint = -1
        self._in_endpoint = -1

        def _on_usb_enabled() -> None:
            self.usb.reset_device()

        def _on_reset_received() -> None:
            self.usb.send_setup_packet(set_device_address_packet(1))

        def _on_endpoint_write(endpoint: int, buffer: bytes | bytearray) -> None:
            if endpoint == ENDPOINT_ZERO and len(buffer) == 0:
                if self._descriptors_size is None:
                    self.usb.send_setup_packet(
                        get_descriptor_packet(DescriptorType.CONFIGRATION, CONFIGURATION_DESCRIPTOR_SIZE)
                    )
                # Acknowledgement
                elif not self._initialized:
                    self._cdc_set_control_line_state()
                    if self.on_device_connected:
                        self.on_device_connected()
            if endpoint == ENDPOINT_ZERO and len(buffer) > 1:
                if (
                    len(buffer) == CONFIGURATION_DESCRIPTOR_SIZE
                    and buffer[1] == DescriptorType.CONFIGRATION
                    and self._descriptors_size is None
                ):
                    self._descriptors_size = (buffer[3] << 8) | buffer[2]
                    self.usb.send_setup_packet(
                        get_descriptor_packet(DescriptorType.CONFIGRATION, self._descriptors_size)
                    )
                elif self._descriptors_size is not None and len(self._descriptors) < self._descriptors_size:
                    self._descriptors.extend(buffer)
                if self._descriptors_size == len(self._descriptors):
                    endpoints = extract_endpoint_numbers(self._descriptors)
                    self._in_endpoint = endpoints.in_endpoint
                    self._out_endpoint = endpoints.out_endpoint

                    # Now configure the device
                    self.usb.send_setup_packet(set_device_configuration_packet(1))
            if endpoint == self._in_endpoint and self.on_serial_data:
                self.on_serial_data(buffer)

        def _on_endpoint_read(endpoint: int, size: int) -> None:
            if endpoint == self._out_endpoint:
                buffer = bytearray(min(size, self.tx_fifo.item_count))
                for i in range(len(buffer)):
                    buffer[i] = self.tx_fifo.pull()
                self.usb.endpoint_read_done(self._out_endpoint, buffer)

        self.usb.on_usb_enabled = _on_usb_enabled
        self.usb.on_reset_received = _on_reset_received
        self.usb.on_endpoint_write = _on_endpoint_write
        self.usb.on_endpoint_read = _on_endpoint_read

    def _cdc_set_control_line_state(self, value: int | None = None, interface_number: int = 0) -> None:
        if value is None:
            value = CDC_DTR | CDC_RTS
        self.usb.send_setup_packet(
            create_setup_packet(
                ISetupPacketParams(
                    data_direction=DataDirection.HOST_TO_DEVICE,
                    type=SetupType.CLASS,
                    recipient=SetupRecipient.DEVICE,
                    b_request=CDC_REQUEST_SET_CONTROL_LINE_STATE,
                    w_value=value,
                    w_index=interface_number,
                    w_length=0,
                )
            )
        )
        self._initialized = True

    def send_serial_byte(self, data: int) -> None:
        self.tx_fifo.push(data)

    def reset(self) -> None:
        """For RPWatchdog.on_watchdog_trigger's live device reset (see base_device.py) - `self.usb`
        (the RP2040 object's own usb_ctrl) is reset in place rather than reconstructed, since this
        object holds a direct reference to it that must stay valid across the reset."""
        self.tx_fifo.reset()
        self._initialized = False
        self._descriptors_size = None
        self._descriptors = []
        self._out_endpoint = -1
        self._in_endpoint = -1
        self.usb.reset()
