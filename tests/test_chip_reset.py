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
from rp2040py.peripherals.psm import WDSEL_RESETS, WDSEL_SIO, WDSEL_XIP, WDSEL_XOSC
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


def test_the_remaining_register_blocks_reset_too(rp2040_factory):
    """RTC and BUSCTRL were the last two `RESETS` blocks inheriting the no-op default. SYSCFG/
    SYSINFO/TBMAN still do, and correctly - they hold no instance state at all."""
    rp2040 = rp2040_factory()
    rp2040.rtc.ctrl = 0xF
    rp2040.rtc.setup0 = 0x1234
    rp2040.busctrl.perf_ctr[1] = 99
    rp2040.busctrl.perf_sel[1] = 0

    rp2040.reset(preserve_flash=True)

    assert rp2040.rtc.ctrl == 0
    assert rp2040.rtc.setup0 == 0
    assert rp2040.busctrl.perf_ctr == [0, 0, 0, 0]
    assert rp2040.busctrl.perf_sel == [0x1F, 0x1F, 0x1F, 0x1F]


def test_xip_and_ssi_are_gated_on_the_psm_xip_domain_not_a_resets_bit(rp2040_factory):
    """There is no `RESETS_RESET_XIP` or `_SSI` - the XIP domain is a `PSM.WDSEL` bit, and both
    halves of it reset together."""
    rp2040 = rp2040_factory()
    rp2040.xip_ctrl.stream_addr = 0x1000
    rp2040.ssi._ssienr = 1
    rp2040.resets.write_uint32(0x04, 0x01FFFFFF)  # every RESETS bit, and it must not be enough

    rp2040.reset(preserve_flash=True, from_watchdog=True)
    assert rp2040.xip_ctrl.stream_addr == 0x1000
    assert rp2040.ssi._ssienr == 1

    rp2040.psm.write_uint32(0x08, WDSEL_XIP)
    rp2040.reset(preserve_flash=True, from_watchdog=True)
    assert rp2040.xip_ctrl.stream_addr == 0
    assert rp2040.ssi._ssienr == 0


def test_resetting_ssi_does_not_duplicate_its_chip_select_listener(rp2040_factory):
    """`RPSSI.__init__` registers a listener on QSPI_SS, and `_listeners` is a set of *bound
    methods* - a fresh one each time, so re-adding on reset would leave two firing per edge."""
    rp2040 = rp2040_factory()
    before = len(rp2040.qspi[1]._listeners)

    rp2040.reset(preserve_flash=True)

    assert len(rp2040.qspi[1]._listeners) == before


def test_resetting_ssi_re_syncs_chip_select_from_the_pin(rp2040_factory):
    """Hardcoding `_cs_asserted = False` here is the bug the constructor's own comment documents:
    an `always_output_enabled` pad with nothing driving it resolves LOW, i.e. already asserted, so
    the bootrom's first `flash_cs_force(low)` would fire no edge and its flash command would hang
    forever waiting for RX bytes that get silently dropped."""
    rp2040 = rp2040_factory()

    rp2040.reset(preserve_flash=True)

    from rp2040py.gpio_pin import GPIOPinState

    assert rp2040.ssi._cs_asserted == (rp2040.qspi[1].value == GPIOPinState.LOW)


def test_xosc_resets_on_a_power_on_but_not_on_a_watchdog_reboot(rp2040_factory):
    """The one place where honouring WDSEL is the *whole* behaviour rather than an optimisation.
    pico-sdk's `watchdog_reboot()` selects "everything apart from ROSC and XOSC" because the
    oscillators clock the reset logic and the booting CPU; a RUN-pin/power-on reset has no such
    exemption. Both halves are asserted here because getting only one right looks identical in
    every test that does not check the other."""
    rp2040 = rp2040_factory()

    # what firmware does: enable the crystal, which this model makes stable immediately
    rp2040.xosc.write_uint32(0x00, 0xFAB << 12)
    assert rp2040.xosc._enabled and rp2040.xosc._stable

    rp2040.psm.write_uint32(0x08, 0x1FFFC)  # watchdog_reboot()'s own value: XOSC/ROSC excluded
    rp2040.reset(preserve_flash=True, from_watchdog=True)
    assert rp2040.xosc._enabled, "a watchdog reboot must not stop the oscillator clocking it"

    rp2040.reset(preserve_flash=True)  # a RUN-pin/power-on reset selects everything
    assert not rp2040.xosc._enabled
    assert not rp2040.xosc._stable
    assert WDSEL_XOSC & 0x1FFFC == 0, "the bit watchdog_reboot() clears, spelled out"
