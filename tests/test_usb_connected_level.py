"""`SIE_STATUS.CONNECTED` is a level, and write-1-to-clear only acknowledges the event.

pico-sdk's `rp2040_usb_device_enumeration_fix()` busy-waits on this bit
(`while (!(usb_hw->sie_status & USB_SIE_STATUS_CONNECTED_BITS));`) from inside a timer alarm, and
TinyUSB's ISR clears the connect status around the same moment. Modelling CONNECTED as a latch made
that a race the firmware lost on every second boot: the bit went away for good and the fix spun
forever with the emulated clock running - MicroPython 1.16/1.17 never came back from
`machine.reset()` because of it (docs/records/0089-one-reset-for-every-trigger.md's Phase 2).
"""

from rp2040py.peripherals.usb import (
    INTR_DEV_CONN_DIS,
    SIE_CONNECTED,
    SIE_STATUS,
    SOFTCON,
    TO_DIGITAL_PAD,
    USB_MUXING,
)
from rp2040py.rp2040 import RP2040

USB_BASE = 0x50110000


def _write(mcu: RP2040, offset: int, value: int) -> None:
    """Through the bus, not straight at the peripheral: write-1-to-clear reads
    `raw_write_value`, which only the memory path sets."""
    mcu.write_uint32(USB_BASE + offset, value)


def _usb(mcu: RP2040):
    # What the enumeration fix's "force LS J" step writes: the digital pad path, no PHY.
    _write(mcu, USB_MUXING, TO_DIGITAL_PAD | SOFTCON)
    return mcu.usb_ctrl


def test_connected_survives_write_one_to_clear():
    mcu = RP2040()
    usb = _usb(mcu)
    assert usb.read_uint32(SIE_STATUS) & SIE_CONNECTED

    # TinyUSB acknowledging the connect/disconnect interrupt.
    _write(mcu, SIE_STATUS, SIE_CONNECTED)

    # The host is still on the other end of the bus, so the *status* is still true...
    assert usb.read_uint32(SIE_STATUS) & SIE_CONNECTED
    # ...while the *event* it acknowledged is gone, so the interrupt does not re-raise.
    assert not usb._int_raw & INTR_DEV_CONN_DIS


def test_connecting_raises_the_event_once():
    mcu = RP2040()
    usb = _usb(mcu)
    assert usb._int_raw & INTR_DEV_CONN_DIS

    _write(mcu, SIE_STATUS, SIE_CONNECTED)
    assert not usb._int_raw & INTR_DEV_CONN_DIS

    # A second identical muxing write is not a new connection and must not re-raise it.
    _write(mcu, USB_MUXING, TO_DIGITAL_PAD | SOFTCON)
    assert not usb._int_raw & INTR_DEV_CONN_DIS
    assert usb.read_uint32(SIE_STATUS) & SIE_CONNECTED


def test_a_block_reset_disconnects():
    mcu = RP2040()
    usb = _usb(mcu)
    usb.reset()
    assert not usb.read_uint32(SIE_STATUS) & SIE_CONNECTED
    assert not usb._int_raw & INTR_DEV_CONN_DIS
