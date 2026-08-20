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
    "LineCoding",
    "extract_control_interface_number",
    "extract_endpoint_numbers",
)

# CDC stuff
CDC_REQUEST_SET_LINE_CODING = 0x20
CDC_REQUEST_SET_CONTROL_LINE_STATE = 0x22

CDC_DTR = 1 << 0
CDC_RTS = 1 << 1

CDC_COMM_CLASS = 2
CDC_DATA_CLASS = 10
ENDPOINT_BULK = 2

LINE_CODING_SIZE = 7

TX_FIFO_SIZE = 512

ENDPOINT_ZERO = 0
CONFIGURATION_DESCRIPTOR_SIZE = 9


class EndpointNumbers(NamedTuple):
    in_endpoint: int
    out_endpoint: int


class LineCoding(NamedTuple):
    """The 7-byte payload of CDC PSTN's `SET_LINE_CODING` (0x20), in the order the spec lays it
    out (USB CDC PSTN 1.2, table 17). The defaults are what both firmware families here come up
    with anyway - 115200 8N1 - so `set_line_coding(1200)` changes exactly one field.

    `stop_bits`/`parity` keep the spec's own encodings rather than friendlier ones: this is a wire
    format, and a `1` here means 1.5 stop bits, not one."""

    baud_rate: int = 115200
    stop_bits: int = 0  # 0 = 1, 1 = 1.5, 2 = 2
    parity: int = 0  # 0 = none, 1 = odd, 2 = even, 3 = mark, 4 = space
    data_bits: int = 8  # 5, 6, 7, 8 or 16

    def to_bytes(self) -> bytes:
        return bytes(
            (
                self.baud_rate & 0xFF,
                (self.baud_rate >> 8) & 0xFF,
                (self.baud_rate >> 16) & 0xFF,
                (self.baud_rate >> 24) & 0xFF,
                self.stop_bits & 0xFF,
                self.parity & 0xFF,
                self.data_bits & 0xFF,
            )
        )


def extract_control_interface_number(descriptors: Sequence[int]) -> int:
    """`bInterfaceNumber` of the CDC *communications* (control) interface, or -1 if there is none.

    Which interface a CDC class request names is not decoration: PSTN specifies an **interface**
    recipient with `wIndex` naming this interface, and a stack is free to reject anything else.
    TinyUSB happens to accept the device-recipient form this project used to send (0088 section 1
    measured it reaching the firmware), but that is leniency rather than correctness, so the
    requests are now addressed the way a real host addresses them.

    The control interface is the one carrying class 0x02 - separate from the data interface
    (class 0x0A) `extract_endpoint_numbers()` above looks for, and always the lower of the two in
    every descriptor set here (0 on MicroPython, CircuitPython and the pico-sdk alike)."""
    index = 0
    while index < len(descriptors):
        length = descriptors[index]
        if length < 2 or len(descriptors) < index + length:
            break
        if (
            descriptors[index + 1] == DescriptorType.INTERFACE
            and length == 9
            and descriptors[index + 5] == CDC_COMM_CLASS
        ):
            return descriptors[index + 2]
        index += length
    return -1


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
        self._control_interface = -1
        self._control_line_state = 0
        self._line_coding = LineCoding()
        self._pending_line_coding: LineCoding | None = None

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
                    self._control_interface = extract_control_interface_number(self._descriptors)

                    # Now configure the device
                    self.usb.send_setup_packet(set_device_configuration_packet(1))
            if endpoint == self._in_endpoint and self.on_serial_data:
                self.on_serial_data(buffer)

        def _on_endpoint_read(endpoint: int, size: int) -> None:
            # The data stage of the one control request here that has one: the firmware arms EP0
            # OUT for `wLength` bytes after accepting SET_LINE_CODING's setup packet, and this is
            # the host handing them over. Cleared as it is served, so an unrelated EP0 OUT
            # transfer never gets a stale line coding fed to it.
            if endpoint == ENDPOINT_ZERO and self._pending_line_coding is not None:
                coding = self._pending_line_coding
                self._pending_line_coding = None
                self._line_coding = coding
                self.usb.endpoint_read_done(ENDPOINT_ZERO, coding.to_bytes()[:size])
                return
            if endpoint == self._out_endpoint:
                buffer = bytearray(min(size, self.tx_fifo.item_count))
                for i in range(len(buffer)):
                    buffer[i] = self.tx_fifo.pull()
                self.usb.endpoint_read_done(self._out_endpoint, buffer)

        self.usb.on_usb_enabled = _on_usb_enabled
        self.usb.on_reset_received = _on_reset_received
        self.usb.on_endpoint_write = _on_endpoint_write
        self.usb.on_endpoint_read = _on_endpoint_read

    def _cdc_set_control_line_state(self, value: int | None = None, interface_number: int | None = None) -> None:
        if value is None:
            value = CDC_DTR | CDC_RTS
        self._send_class_request(CDC_REQUEST_SET_CONTROL_LINE_STATE, value, interface_number, w_length=0)
        self._control_line_state = value
        self._initialized = True

    def _send_class_request(self, b_request: int, w_value: int, interface_number: int | None, *, w_length: int) -> None:
        """One shape for every CDC class request this host sends.

        `recipient=INTERFACE` with `wIndex` naming the CDC control interface, per PSTN - see
        `extract_control_interface_number()` for why it is not the `recipient=DEVICE` this used to
        send. Falls back to interface 0 before enumeration has read the descriptors, which is the
        control interface on every descriptor set here anyway."""
        if interface_number is None:
            interface_number = max(self._control_interface, 0)
        self.usb.send_setup_packet(
            create_setup_packet(
                ISetupPacketParams(
                    data_direction=DataDirection.HOST_TO_DEVICE,
                    type=SetupType.CLASS,
                    recipient=SetupRecipient.INTERFACE,
                    b_request=b_request,
                    w_value=w_value,
                    w_index=interface_number,
                    w_length=w_length,
                )
            )
        )

    @property
    def control_line_state(self) -> int:
        """The DTR/RTS bits as last sent (`CDC_DTR | CDC_RTS`), not as the firmware reports them -
        this host has no way to read them back."""
        return self._control_line_state

    @property
    def dtr(self) -> bool:
        return bool(self._control_line_state & CDC_DTR)

    @property
    def rts(self) -> bool:
        return bool(self._control_line_state & CDC_RTS)

    @property
    def line_coding(self) -> LineCoding:
        """The line coding this host last successfully sent. `LineCoding()`'s defaults until
        `set_line_coding()` is called - nothing is read back from the firmware."""
        return self._line_coding

    def set_control_lines(self, *, dtr: bool, rts: bool) -> None:
        """Assert or drop DTR/RTS after enumeration - what a terminal opening or closing the port
        does to a real board (0088 section 1).

        Both firmware families here watch DTR: TinyUSB's `tud_cdc_n_connected()` *is* the DTR bit,
        so CircuitPython's `supervisor.runtime.serial_connected`/`usb_cdc.console.connected`
        follow it, and MicroPython's `tud_cdc_line_state_cb()` uses it to arm its TX flush delay.
        Dropping DTR therefore stops the guest seeing a console attached - which is the point.

        Engine-room state: call this from the simulator's own loop (`schedule_threadsafe()` from
        any other thread, per 0030), not inline from a host thread."""
        self._cdc_set_control_line_state((CDC_DTR if dtr else 0) | (CDC_RTS if rts else 0))

    def set_line_coding(self, baud_rate: int, *, stop_bits: int = 0, parity: int = 0, data_bits: int = 8) -> None:
        """Send CDC PSTN's `SET_LINE_CODING` (0x20) - baud rate, stop bits, parity, data bits.

        Emulated USB, so the baud rate is not a wire speed and changes nothing about how fast bytes
        move; it is guest-visible state (CircuitPython's `usb_cdc.console.baudrate`, TinyUSB's
        `tud_cdc_n_get_line_coding()`). The one gesture that would give it a *behaviour* - the
        1200-bps touch - is deliberately not built: no firmware this project runs honours it, and
        its destination is the bootrom's USB mass-storage mode, which is out of scope (0088's
        2026-08-20 update, and 0089 section 5).

        Same threading rule as `set_control_lines()`."""
        coding = LineCoding(baud_rate=baud_rate, stop_bits=stop_bits, parity=parity, data_bits=data_bits)
        # Armed *before* the setup packet: the firmware may well arm EP0 OUT synchronously inside
        # the register write that this send_setup_packet() triggers.
        self._pending_line_coding = coding
        self._send_class_request(CDC_REQUEST_SET_LINE_CODING, 0, None, w_length=LINE_CODING_SIZE)

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
        self._control_interface = -1
        # A chip that dropped off the bus is asserting nothing, and the firmware coming back up
        # starts from its own defaults - so both are back to what a fresh USBCDC has, and the
        # re-enumeration asserts DTR/RTS again through the same path a first boot uses.
        self._control_line_state = 0
        self._line_coding = LineCoding()
        self._pending_line_coding = None
        self.usb.reset()
