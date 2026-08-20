"""Double-buffered USB endpoint writes must reach the host in buffer order.

`RPUSBController.dpram_updated()` used to transfer buffer **1** before buffer 0 when the firmware
armed both at once, so a response too long for one 64-byte packet went out back-to-front. It stayed
invisible for as long as every emulated exchange fit in a single buffer; what found it was
MicroPython 1.19.1 answering a raw-REPL `print(marker)` with a 116-byte traceback, which arrived as
[tail, head] and split the response across the protocol's own \\x04 marker
(docs/records/0089-one-reset-for-every-trigger.md's Phase 2 live check, red on 1.19.1 only).
"""

from rp2040py.peripherals.usb import (
    EP1_IN_CONTROL,
    USB_BUF1_OFFSET,
    USB_BUF1_SHIFT,
    USB_BUF_CTRL_AVAILABLE,
    USB_BUF_CTRL_FULL,
    USB_CTRL_DOUBLE_BUF,
)
from rp2040py.rp2040 import RP2040

EP1_IN_BUFFER_CONTROL = 0x88
BUFFER_OFFSET = 0x180
ENDPOINT_ENABLE = 1 << 31


def _set_uint32(dpram, offset: int, value: int) -> None:
    dpram[offset : offset + 4] = value.to_bytes(4, "little")


def test_double_buffered_writes_arrive_in_buffer_order():
    mcu = RP2040()
    usb = mcu.usb_ctrl
    written: list[bytes] = []
    usb.on_endpoint_write = lambda endpoint, buffer: written.append(bytes(buffer))

    # Endpoint 1 IN: enabled, double-buffered, buffers at BUFFER_OFFSET (buffer 1 is 64 bytes on).
    _set_uint32(
        mcu.usb_dpram,
        EP1_IN_CONTROL,
        ENDPOINT_ENABLE | USB_CTRL_DOUBLE_BUF | BUFFER_OFFSET,
    )
    first, second = b"A" * 64, b"B" * 16
    mcu.usb_dpram[BUFFER_OFFSET : BUFFER_OFFSET + len(first)] = first
    mcu.usb_dpram[BUFFER_OFFSET + USB_BUF1_OFFSET : BUFFER_OFFSET + USB_BUF1_OFFSET + len(second)] = second

    # Arm both halves in one write, exactly as the firmware does.
    buffer_control = USB_BUF_CTRL_AVAILABLE | USB_BUF_CTRL_FULL | len(first)
    buffer_control |= (USB_BUF_CTRL_AVAILABLE | USB_BUF_CTRL_FULL | len(second)) << USB_BUF1_SHIFT
    _set_uint32(mcu.usb_dpram, EP1_IN_BUFFER_CONTROL, buffer_control)
    usb.dpram_updated(EP1_IN_BUFFER_CONTROL, buffer_control)

    mcu.clock.tick(1_000_000)  # let the write alarm run

    assert written == [first, second]
