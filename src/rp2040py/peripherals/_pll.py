from typing import TYPE_CHECKING

from rp2040py.peripherals._clocks import xosc_hz
from rp2040py.peripherals.peripheral import BasePeripheral

if TYPE_CHECKING:
    from rp2040py.rp2040 import RP2040

__all__ = ("RPPLL",)

# PLL register offsets
PLL_CS = 0x00  # control and status
PLL_PWR = 0x04  # power control
PLL_FBDIV_INT = 0x08  # feedback divisor
PLL_PRIM = 0x0C  # primary post dividers

# PLL_CS bits
PLL_CS_LOCK = 1 << 31
PLL_CS_BYPASS = 1 << 8
PLL_CS_REFDIV_MASK = 0x3F

PLL_FBDIV_INT_MASK = 0xFFF

# Writable bits (datasheet 2.18.4: CS BYPASS and REFDIV, PWR VCOPD/POSTDIVPD/DSMPD/PD, FBDIV_INT 11:0, PRIM POSTDIV1 and POSTDIV2); the rest is reserved
PLL_CS_WRITE_MASK = PLL_CS_BYPASS | PLL_CS_REFDIV_MASK
PLL_PWR_WRITE_MASK = 0x2D
PLL_PWR_PD = 1 << 0
PLL_PWR_DSMPD = 1 << 2
PLL_PWR_POSTDIVPD = 1 << 3
PLL_PWR_VCOPD = 1 << 5
PLL_PRIM_WRITE_MASK = 0x77000

# PLL_PRIM bits
PLL_PRIM_POSTDIV1_SHIFT = 16
PLL_PRIM_POSTDIV2_SHIFT = 12
PLL_PRIM_POSTDIV_MASK = 0x7


class RPPLL(BasePeripheral):
    """PLL_SYS / PLL_USB (a port of rp2040js 1.4.0's `pll.ts`).

    Locks as soon as the VCO is powered and the crystal runs (there is no start-up delay to model) and derives its output frequency from the divider registers::

        f_out = (f_ref / REFDIV * FBDIV) / (POSTDIV1 * POSTDIV2)

    With BYPASS set the output is the reference divided by REFDIV alone. That frequency is what makes `RP2040.clk_sys` follow `set_sys_clock_khz()` and
    friends: every register write re-derives the chip's clocks (`rp2040.update_clocks()`).
    """

    def __init__(self, rp2040: "RP2040", name: str):
        super().__init__(rp2040, name)
        self.reset()

    def reset(self) -> None:
        self.cs = 0x1  # REFDIV = 1
        self.pwr = 0x2D  # VCO and post dividers powered down
        self.fbdiv_int = 0
        self.prim = 0x77000  # POSTDIV1 = 7, POSTDIV2 = 7

    @property
    def refdiv(self) -> int:
        return self.cs & PLL_CS_REFDIV_MASK

    @property
    def fbdiv(self) -> int:
        return self.fbdiv_int & PLL_FBDIV_INT_MASK

    @property
    def postdiv1(self) -> int:
        return (self.prim >> PLL_PRIM_POSTDIV1_SHIFT) & PLL_PRIM_POSTDIV_MASK

    @property
    def postdiv2(self) -> int:
        return (self.prim >> PLL_PRIM_POSTDIV2_SHIFT) & PLL_PRIM_POSTDIV_MASK

    @property
    def reference(self) -> float:
        """FREF / REFDIV in Hz: the crystal oscillator's output (0 until the XOSC is enabled) over the reference divider."""
        return xosc_hz(self.rp2040) / (self.refdiv or 1)

    @property
    def locked(self) -> bool:
        """CS.LOCK (table 275, "PLL is locked"): the VCO runs (PD and VCOPD clear) off a reference that is there. The model has no start-up delay (the datasheet gives none); constraints outside the
        design's range (reference 5-800 MHz, FBDIV 16-320, VCO 750-1600 MHz) have no stated behaviour and are not modelled."""
        return not self.pwr & (PLL_PWR_PD | PLL_PWR_VCOPD) and self.reference > 0

    @property
    def frequency(self) -> float:
        """The output frequency in Hz, derived from the current register values and 0 while the output is not driven: PD (table 276, "PLL powerdown") stops everything; BYPASS passes the reference
        (the VCO and post dividers may be powered down then: "To save power set high when PLL output not required or bypass=1"); otherwise the output needs the VCO and the post dividers powered
        (VCOPD and POSTDIVPD clear)."""
        if self.pwr & PLL_PWR_PD:
            return 0.0
        ref_freq = self.reference
        if self.cs & PLL_CS_BYPASS:
            return ref_freq
        if self.pwr & (PLL_PWR_VCOPD | PLL_PWR_POSTDIVPD):
            return 0.0
        postdiv = (self.postdiv1 or 1) * (self.postdiv2 or 1)
        return ref_freq * self.fbdiv / postdiv

    def snapshot(self) -> dict[str, int]:
        """The block's registers as plain values (the divisors, the reference and the lock are derived from them)."""
        return {"cs": self.cs, "pwr": self.pwr, "fbdiv_int": self.fbdiv_int, "prim": self.prim}

    def read_uint32(self, offset: int) -> int:
        if offset == PLL_CS:
            return self.cs | (PLL_CS_LOCK if self.locked else 0)
        if offset == PLL_PWR:
            return self.pwr
        if offset == PLL_FBDIV_INT:
            return self.fbdiv_int
        if offset == PLL_PRIM:
            return self.prim
        return super().read_uint32(offset)

    def write_uint32(self, offset: int, value: int) -> None:
        if offset == PLL_CS:
            self.cs = value & PLL_CS_WRITE_MASK
        elif offset == PLL_PWR:
            self.pwr = value & PLL_PWR_WRITE_MASK
        elif offset == PLL_FBDIV_INT:
            self.fbdiv_int = value & PLL_FBDIV_INT_MASK
        elif offset == PLL_PRIM:
            self.prim = value & PLL_PRIM_WRITE_MASK
        else:
            super().write_uint32(offset, value)
            return
        self.rp2040.update_clocks()
