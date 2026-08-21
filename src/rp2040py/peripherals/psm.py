from typing import TYPE_CHECKING

from rp2040py.peripherals.peripheral import BasePeripheral

if TYPE_CHECKING:
    from rp2040py.rp2040 import RP2040

__all__ = ("RPPSM",)

FRCE_ON = 0x00
FRCE_OFF = 0x04
WDSEL = 0x08
DONE = 0x0C

PSM_BITS_MASK = 0x0001FFFF

# PSM's per-domain bit positions, from pico-sdk's own
# `src/rp2040/hardware_regs/include/hardware/regs/psm.h` (0027's 3g rule - these are not guessed
# and not read off a datasheet table by eye). Only the domains this emulator actually models
# something for are named here; the rest are covered by PSM_BITS_MASK above.
#
# WDSEL is what makes them matter: a *watchdog* reset only resets the domains the guest selected
# here, where a RUN-pin/power-on reset resets everything regardless
# (docs/records/0089-one-reset-for-every-trigger.md, D5). pico-sdk's `watchdog_reboot()` sets
# "everything apart from ROSC and XOSC" before triggering, which is why a real `machine.reset()`
# resets far more than the bare TRIGGER bit alone would suggest.
WDSEL_PROC1 = 0x00010000
WDSEL_PROC0 = 0x00008000
WDSEL_SIO = 0x00004000
WDSEL_VREG_AND_CHIP_RESET = 0x00002000
WDSEL_XIP = 0x00001000
WDSEL_ROM = 0x00000020
WDSEL_BUSFABRIC = 0x00000010
# The one that does the most work: it resets the RESETS *controller*, whose own reset state holds
# every peripheral in reset - which is how a real watchdog reboot resets the peripheral blocks
# even though `RESETS.WDSEL` itself is left at 0 by pico-sdk.
WDSEL_RESETS = 0x00000008
WDSEL_CLOCKS = 0x00000004
WDSEL_XOSC = 0x00000002
WDSEL_ROSC = 0x00000001


class RPPSM(BasePeripheral):
    def __init__(self, rp2040: "RP2040", name: str):
        super().__init__(rp2040, name)
        self._frce_on = 0
        self._frce_off = 0
        self._wdsel = 0

    @property
    def wdsel(self) -> int:
        """Which power-manager domains a *watchdog* reset resets - read by `RP2040.reset()` on the
        watchdog path (0089's D5). A real `@property` rather than a bare `_wdsel` read from the
        outside, per CLAUDE.md's rule on cross-class private access."""
        return self._wdsel

    def read_uint32(self, offset: int) -> int:
        if offset == FRCE_ON:
            return self._frce_on
        if offset == FRCE_OFF:
            return self._frce_off
        if offset == WDSEL:
            return self._wdsel
        if offset == DONE:
            # Domains are ready unless forced off (FRCE_ON overrides FRCE_OFF)
            return (PSM_BITS_MASK & ~self._frce_off) | (self._frce_on & self._frce_off)
        return super().read_uint32(offset)

    def write_uint32(self, offset: int, value: int) -> None:
        if offset == FRCE_ON:
            self._frce_on = value & PSM_BITS_MASK
        elif offset == FRCE_OFF:
            self._frce_off = value & PSM_BITS_MASK
        elif offset == WDSEL:
            self._wdsel = value & PSM_BITS_MASK
        else:
            super().write_uint32(offset, value)
