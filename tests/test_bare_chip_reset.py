"""A bare `RP2040` - no `BaseDevice` anywhere - resets itself, both halves of it.

This is what docs/records/0057-run-pin-reset-hook.md's option B is for. The reset *sequence* used
to live in `BaseDevice`, so anything holding only the chip could reach `mcu.reset()` but never the
host side of the USB link: `rp2040py run` builds exactly that (a bare `RP2040` plus a `USBCDC`,
cli/__init__.py), and a guest calling `machine.reset()` there hit
`RPWatchdog._default_watchdog_trigger`'s "no reset handler provided" warning and span forever.
"""

from rp2040py.memory_map import FLASH_START_ADDRESS
from rp2040py.peripherals.psm import PSM_BITS_MASK, WDSEL, WDSEL_ROSC, WDSEL_XOSC
from rp2040py.peripherals.watchdog import CTRL, TRIGGER
from rp2040py.rp2040 import RP2040
from rp2040py.usb.cdc import USBCDC


def _enumerated(cdc: USBCDC) -> None:
    """The host-side state a completed enumeration leaves behind."""
    cdc._initialized = True
    cdc._descriptors_size = 9
    cdc._descriptors = [1, 2, 3]
    cdc._in_endpoint = 2
    cdc._out_endpoint = 3
    cdc.tx_fifo.push(0xFF)


def _assert_host_side_cleared(cdc: USBCDC) -> None:
    assert cdc._initialized is False
    assert cdc._descriptors_size is None
    assert cdc._descriptors == []
    assert cdc._in_endpoint == -1
    assert cdc._out_endpoint == -1
    assert cdc.tx_fifo.item_count == 0


def test_a_guest_watchdog_reset_resets_a_bare_chip():
    """`machine.reset()`'s TRIGGER write, with no device layer to catch it.

    WDSEL is set the way pico-sdk's own `_watchdog_enable()` sets it before rebooting - everything
    except the oscillators - because a watchdog reset only covers what the guest selected (0089's
    Phase 5), and asserting on blocks it did not select would be asserting the wrong thing."""
    mcu = RP2040()
    cdc = USBCDC(mcu.usb_ctrl)
    _enumerated(cdc)
    mcu.core.pc = 0x10001234
    mcu.pwm.write_uint32(0x00, 0x1F)
    mcu.psm.write_uint32(WDSEL, PSM_BITS_MASK & ~(WDSEL_ROSC | WDSEL_XOSC))

    mcu.watchdog.write_uint32(CTRL, TRIGGER)

    assert mcu.core.pc == FLASH_START_ADDRESS, "the chip must boot again"
    assert mcu.pwm.read_uint32(0x00) == 0, "its blocks must be back at reset values"
    _assert_host_side_cleared(cdc)


def test_the_run_pin_resets_a_bare_chip_over_both_edges():
    """A RESET button on a board file, with no device layer either. The press holds the chip in
    reset and the release boots it - the same two edges `set_run_pin()` has always had, now with
    a default that acts on them."""
    mcu = RP2040()
    cdc = USBCDC(mcu.usb_ctrl)
    _enumerated(cdc)
    mcu.core.pc = 0x10001234
    mcu.pwm.write_uint32(0x00, 0x1F)

    mcu.set_run_pin(low=True)
    assert mcu.run_pin_low is True
    assert mcu.pwm.read_uint32(0x00) == 0
    _assert_host_side_cleared(cdc)
    assert mcu.core.pc != FLASH_START_ADDRESS, "held in reset is not booted yet"

    mcu.set_run_pin(low=False)
    assert mcu.core.pc == FLASH_START_ADDRESS


def test_the_chip_notifies_whatever_is_on_its_usb_not_just_a_cdc():
    """`enter_reset()` fires `usb_ctrl.on_reset` for any consumer - the chip does not know what a
    CDC is, which is the point of routing this through a hook rather than a `cdc.reset()` call."""
    mcu = RP2040()
    fired = []
    mcu.usb_ctrl.on_reset = lambda: fired.append(mcu.core.pc)

    mcu.enter_reset()

    assert fired, "a chip reset must notify the USB consumer"
