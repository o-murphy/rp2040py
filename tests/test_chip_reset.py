"""The fuller chip reset - docs/records/0089-one-reset-for-every-trigger.md's Phase 5.

Two things are under test, and they fail in different ways:

1. **What a reset covers.** A RUN-pin/power-on reset covers everything; a *watchdog* reset covers
   only the domains the guest selected in `PSM.WDSEL`/`RESETS.WDSEL` (0089's D5). Getting this
   wrong is silent - the firmware simply reports the wrong world on the way back up.
2. **What a reset must not touch.** Registers are state; callbacks, GPIO listeners, DREQ identity
   and analog inputs are *wiring*. A reset that clears wiring would detach every
   `ExternalDevice` on the board while every test that only checks registers still passed.

Both `RP2040` twins are covered by the suite running under `RP2040PY_SKIP_CYTHON=1` and `=0`.
"""

from rp2040py.gpio_pin import FUNCTION_SIO
from rp2040py.peripherals.psm import WDSEL_RESETS, WDSEL_SIO
from rp2040py.peripherals.reset import RESET_UART0
from rp2040py.qspi_pads import QSPI_PAD_RESET_VALUES


def _drive_gpio_high(rp2040, gpio: int) -> None:
    """What `machine.Pin(gpio, Pin.OUT).on()` reaches - see tests/test_led_mock.py's twin."""
    pin = rp2040.gpio[gpio]
    pin.ctrl = (pin.ctrl & ~0x1F) | FUNCTION_SIO
    rp2040.sio.gpio_output_enable |= 1 << gpio
    rp2040.sio.gpio_value |= 1 << gpio
    pin.check_for_updates()


def _select_everything(rp2040) -> None:
    """What pico-sdk's `watchdog_reboot()` writes before triggering: every PSM domain except ROSC
    and XOSC. `PSM.WDSEL`'s RESETS bit is the one that reaches the peripherals."""
    rp2040.psm.write_uint32(0x08, 0x1FFFC)


# -- what a full (RUN-pin/power-on) reset covers -------------------------------------------------


def test_a_driven_pad_stops_driving(rp2040_factory):
    """0089's Appendix, point 5 - the observable this phase existed for. An LED left on by
    firmware goes dark, because both halves of the pin (IO_BANK0's ctrl and SIO's output enable)
    are reset."""
    rp2040 = rp2040_factory()
    _drive_gpio_high(rp2040, 15)
    assert rp2040.gpio[15].ctrl & 0x1F == FUNCTION_SIO

    rp2040.reset(preserve_flash=True)

    assert rp2040.gpio[15].ctrl & 0x1F == 0x1F
    assert rp2040.sio.gpio_output_enable == 0
    assert rp2040.gpio[15].pad_value == 0b0110110


def test_qspi_pads_go_back_to_their_own_reset_values_not_bank0s(rp2040_factory):
    """PADS_QSPI does not share BANK0's reset value: GPIO_QSPI_SS's pull-up is what holds the flash
    deselected and what makes BOOTSEL readable (record 0050). A reset that applied the generic
    default would boot a board that cannot see its own flash."""
    rp2040 = rp2040_factory()
    for pin in rp2040.qspi:
        pin.pad_value = 0

    rp2040.reset(preserve_flash=True)

    assert [pin.pad_value for pin in rp2040.qspi] == list(QSPI_PAD_RESET_VALUES)


def test_peripheral_registers_go_back_to_power_on(rp2040_factory):
    rp2040 = rp2040_factory()
    rp2040.uart[0]._interrupt_mask = 0xFF
    rp2040.spi[0]._control0 = 0x1234
    rp2040.i2c[1].target_address = 0x42
    rp2040.adc.cs = 0xFF
    rp2040.clocks.sys_div = 0x999
    rp2040.pio[0].instructions[0] = 0xDEAD
    rp2040.pio[0].machines[0].enabled = True

    rp2040.reset(preserve_flash=True)

    assert rp2040.uart[0]._interrupt_mask == 0
    assert rp2040.spi[0]._control0 == 0
    assert rp2040.i2c[1].target_address == 0x55
    assert rp2040.adc.cs == 0
    assert rp2040.clocks.sys_div == 0x100
    assert rp2040.pio[0].instructions[0] == 0
    assert rp2040.pio[0].machines[0].enabled is False


def test_sram_and_flash_survive(rp2040_factory):
    """0089's D6: a PSM reset resets the SRAM *controllers*, not the array - and `preserve_flash`
    is what keeps the running firmware in place across a live reset."""
    rp2040 = rp2040_factory()
    rp2040.sram[0x100] = 0x42
    rp2040.flash[0x200] = 0x43

    rp2040.reset(preserve_flash=True)

    assert rp2040.sram[0x100] == 0x42
    assert rp2040.flash[0x200] == 0x43


# -- what a reset must not touch: wiring ---------------------------------------------------------


def test_gpio_listeners_survive(rp2040_factory):
    """The failure this guards is invisible to every register assertion above: a reset that reset
    the listener set would silently detach every `ExternalDevice` on the board."""
    rp2040 = rp2040_factory()
    seen = []
    rp2040.gpio[15].add_listener(lambda new, old: seen.append(new))

    rp2040.reset(preserve_flash=True)
    _drive_gpio_high(rp2040, 15)

    assert seen, "the pin lost its listeners across the reset"


def test_externally_driven_input_survives(rp2040_factory):
    """A button held down is still held after a chip reset - the chip's own side is what resets,
    not what the outside world is doing to the pin."""
    rp2040 = rp2040_factory()
    rp2040.gpio[16].set_input_value(True)

    rp2040.reset(preserve_flash=True)

    assert rp2040.gpio[16]._driven is True
    assert rp2040.gpio[16]._raw_input_value is True


def test_peripheral_callbacks_and_analog_inputs_survive(rp2040_factory):
    rp2040 = rp2040_factory()
    marker = object()
    rp2040.uart[0].on_byte = marker
    rp2040.spi[0].on_transmit = marker
    rp2040.adc.channel_values[2] = 0x123
    usb_ctrl_before = rp2040.usb_ctrl

    rp2040.reset(preserve_flash=True)

    assert rp2040.uart[0].on_byte is marker
    assert rp2040.spi[0].on_transmit is marker
    assert rp2040.adc.channel_values[2] == 0x123, "an analog input is wiring, not chip state"
    assert rp2040.usb_ctrl is usb_ctrl_before


# -- WDSEL: what a *watchdog* reset covers -------------------------------------------------------


def test_a_watchdog_reset_that_selected_nothing_leaves_peripherals_alone(rp2040_factory):
    """`PSM.WDSEL`/`RESETS.WDSEL` both default to 0, and a watchdog reset then resets only the
    processor. Firmware that wants more says so - which pico-sdk's `watchdog_reboot()` does."""
    rp2040 = rp2040_factory()
    _drive_gpio_high(rp2040, 15)
    rp2040.uart[0]._interrupt_mask = 0xFF

    rp2040.reset(preserve_flash=True, from_watchdog=True)

    assert rp2040.gpio[15].ctrl & 0x1F == FUNCTION_SIO
    assert rp2040.uart[0]._interrupt_mask == 0xFF


def test_the_core_is_reset_even_when_nothing_was_selected(rp2040_factory):
    """Deliberately broader than the hardware, and documented as such on `RP2040.reset()`: with
    PROC0 unselected the emulated CPU would otherwise run on into the reset it just asked for,
    which is the hang `BaseDevice._on_watchdog_trigger()` exists to prevent."""
    rp2040 = rp2040_factory()
    rp2040.core.pc = 0x2000_1234

    rp2040.reset(preserve_flash=True, from_watchdog=True)

    assert rp2040.core.pc != 0x2000_1234


def test_psm_wdsel_resets_bit_reaches_every_peripheral(rp2040_factory):
    """The indirect route, and the one a real `machine.reset()` takes: `PSM.WDSEL`'s RESETS bit
    resets the RESETS *controller*, whose own reset state holds every peripheral in reset - so
    selecting it selects them all, even though `RESETS.WDSEL` itself stays 0."""
    rp2040 = rp2040_factory()
    _drive_gpio_high(rp2040, 15)
    rp2040.uart[0]._interrupt_mask = 0xFF
    _select_everything(rp2040)
    assert rp2040.resets.wdsel == 0, "pico-sdk never writes RESETS.WDSEL - the PSM bit is the route"

    rp2040.reset(preserve_flash=True, from_watchdog=True)

    assert rp2040.gpio[15].ctrl & 0x1F == 0x1F
    assert rp2040.uart[0]._interrupt_mask == 0


def test_resets_wdsel_selects_individual_blocks(rp2040_factory):
    """The direct route: a guest that selects only UART0 gets only UART0 reset."""
    rp2040 = rp2040_factory()
    rp2040.uart[0]._interrupt_mask = 0xFF
    rp2040.uart[1]._interrupt_mask = 0xFF
    rp2040.resets.write_uint32(0x04, RESET_UART0)

    rp2040.reset(preserve_flash=True, from_watchdog=True)

    assert rp2040.uart[0]._interrupt_mask == 0
    assert rp2040.uart[1]._interrupt_mask == 0xFF


def test_psm_wdsel_sio_bit_gates_sio(rp2040_factory):
    """SIO is a PSM domain, not a RESETS one - the two registers are read separately."""
    rp2040 = rp2040_factory()
    rp2040.sio.gpio_output_enable = 0xFF
    rp2040.psm.write_uint32(0x08, WDSEL_SIO)

    rp2040.reset(preserve_flash=True, from_watchdog=True)

    assert rp2040.sio.gpio_output_enable == 0
    assert WDSEL_RESETS & rp2040.psm.wdsel == 0, "only SIO was selected"
