from typing import TYPE_CHECKING

from rp2040py.peripherals.peripheral import BasePeripheral

if TYPE_CHECKING:
    from rp2040py.rp2040 import RP2040

__all__ = ("RPReset",)

RESET = 0x0  # Reset control.
WDSEL = 0x4  # Watchdog select.
RESET_DONE = 0x8  # Reset Done

RESETS_BITS_MASK = 0x01FFFFFF

# Per-peripheral bit positions, from pico-sdk's own
# `src/rp2040/hardware_regs/include/hardware/regs/resets.h` (0027's 3g rule). Only the blocks this
# emulator models are named; `RP2040.reset()` maps each to the object it resets
# (docs/records/0089-one-reset-for-every-trigger.md, Phase 5).
RESET_USBCTRL = 0x01000000
RESET_UART1 = 0x00800000
RESET_UART0 = 0x00400000
RESET_TIMER = 0x00200000
RESET_SPI1 = 0x00020000
RESET_SPI0 = 0x00010000
RESET_RTC = 0x00008000
RESET_PWM = 0x00004000
RESET_PIO1 = 0x00000800
RESET_PIO0 = 0x00000400
RESET_PADS_QSPI = 0x00000200
RESET_PADS_BANK0 = 0x00000100
RESET_IO_QSPI = 0x00000040
RESET_IO_BANK0 = 0x00000020
RESET_I2C1 = 0x00000010
RESET_I2C0 = 0x00000008
RESET_DMA = 0x00000004
RESET_BUSCTRL = 0x00000002
RESET_ADC = 0x00000001


class RPReset(BasePeripheral):
    def __init__(self, rp2040: "RP2040", name: str):
        super().__init__(rp2040, name)
        self._reset = 0
        self._wdsel = 0
        self._reset_done = 0x1FFFFFF

    @property
    def wdsel(self) -> int:
        """Which peripheral blocks a *watchdog* reset resets directly. Usually 0 even on a real
        reboot - pico-sdk's `watchdog_reboot()` never writes it - because the peripherals get reset
        the other way round: `PSM.WDSEL`'s RESETS bit resets this whole controller, and its reset
        state holds every peripheral in reset. Both routes are honoured by `RP2040.reset()`."""
        return self._wdsel

    def read_uint32(self, offset: int) -> int:
        if offset == RESET:
            return self._reset
        if offset == WDSEL:
            return self._wdsel
        if offset == RESET_DONE:
            return self._reset_done
        return super().read_uint32(offset)

    def write_uint32(self, offset: int, value: int) -> None:
        if offset == RESET:
            self._reset = value & 0x1FFFFFF
        elif offset == WDSEL:
            self._wdsel = value & 0x1FFFFFF
        else:
            super().write_uint32(offset, value)
