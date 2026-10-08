from typing import TYPE_CHECKING

from rp2040py.peripherals.peripheral import BasePeripheral

if TYPE_CHECKING:
    from rp2040py.rp2040 import RP2040

__all__ = ("RPClocks",)

CLK_GPOUT0_CTRL = 0x00
CLK_GPOUT0_DIV = 0x04
CLK_GPOUT0_SELECTED = 0x8
CLK_GPOUT1_CTRL = 0x0C
CLK_GPOUT1_DIV = 0x10
CLK_GPOUT1_SELECTED = 0x14
CLK_GPOUT2_CTRL = 0x18
CLK_GPOUT2_DIV = 0x01C
CLK_GPOUT2_SELECTED = 0x20
CLK_GPOUT3_CTRL = 0x24
CLK_GPOUT3_DIV = 0x28
CLK_GPOUT3_SELECTED = 0x2C
CLK_REF_CTRL = 0x30
CLK_REF_DIV = 0x34
CLK_REF_SELECTED = 0x38
CLK_SYS_CTRL = 0x3C
CLK_SYS_DIV = 0x40
CLK_SYS_SELECTED = 0x44
CLK_PERI_CTRL = 0x48
CLK_PERI_DIV = 0x4C
CLK_PERI_SELECTED = 0x50
CLK_USB_CTRL = 0x54
CLK_USB_DIV = 0x58
CLK_USB_SELECTED = 0x5C
CLK_ADC_CTRL = 0x60
CLK_ADC_DIV = 0x64
CLK_ADC_SELECTED = 0x68
CLK_RTC_CTRL = 0x6C
CLK_RTC_DIV = 0x70
CLK_RTC_SELECTED = 0x74
CLK_SYS_RESUS_CTRL = 0x78
CLK_SYS_RESUS_STATUS = 0x7C
WAKE_EN0 = 0xA0
WAKE_EN1 = 0xA4
SLEEP_EN0 = 0xA8
SLEEP_EN1 = 0xAC
INTR = 0xB8
INTE = 0xBC
INTF = 0xC0
INTS = 0xC4

# Registers that are stored and not acted on (clock gating in sleep, the resus and its interrupt are not modelled): offset -> (writable mask, reset value). WAKE_EN/SLEEP_EN
# reset to all ones (every clock runs in sleep); CLK_SYS_RESUS_CTRL is CLEAR 16, FRCE 12, ENABLE 8 and TIMEOUT 7:0 (reset 0xFF); INTE/INTF are the one bit of the resus interrupt.
STORED_REGISTERS = {
    CLK_SYS_RESUS_CTRL: (0x111FF, 0xFF),
    WAKE_EN0: (0xFFFFFFFF, 0xFFFFFFFF),
    WAKE_EN1: (0x7FFF, 0x7FFF),
    SLEEP_EN0: (0xFFFFFFFF, 0xFFFFFFFF),
    SLEEP_EN1: (0x7FFF, 0x7FFF),
    INTE: (0x1, 0),
    INTF: (0x1, 0),
}

# CLK_REF_CTRL
CLK_REF_CTRL_SRC_MASK = 0x3
CLK_REF_CTRL_SRC_ROSC = 0x0
CLK_REF_CTRL_SRC_AUX = 0x1
CLK_REF_CTRL_SRC_XOSC = 0x2
CLK_REF_CTRL_AUXSRC_SHIFT = 5
CLK_REF_CTRL_AUXSRC_MASK = 0x3
CLK_REF_CTRL_AUXSRC_PLL_USB = 0x0

# CLK_REF_DIV has no fractional part, only INT (bits 9:8)
CLK_REF_DIV_INT_BITS = 0x300

# CLK_SYS_CTRL
CLK_SYS_CTRL_SRC_MASK = 0x1
CLK_SYS_CTRL_SRC_REF = 0x0
CLK_SYS_CTRL_AUXSRC_SHIFT = 5
CLK_SYS_CTRL_AUXSRC_MASK = 0x7
CLK_SYS_CTRL_AUXSRC_PLL_SYS = 0x0
CLK_SYS_CTRL_AUXSRC_PLL_USB = 0x1
CLK_SYS_CTRL_AUXSRC_ROSC = 0x2
CLK_SYS_CTRL_AUXSRC_XOSC = 0x3

# CLK_PERI_CTRL: clk_peri has no SRC mux and no divider - it is driven straight from AUXSRC, which has its own encoding (not CLK_SYS's)
CLK_PERI_CTRL_ENABLE = 1 << 11
CLK_PERI_CTRL_KILL = 1 << 10
CLK_PERI_CTRL_AUXSRC_SHIFT = 5
CLK_PERI_CTRL_AUXSRC_MASK = 0x7
CLK_PERI_CTRL_AUXSRC_CLK_SYS = 0x0
CLK_PERI_CTRL_AUXSRC_PLL_SYS = 0x1
CLK_PERI_CTRL_AUXSRC_PLL_USB = 0x2
CLK_PERI_CTRL_AUXSRC_ROSC = 0x3
CLK_PERI_CTRL_AUXSRC_XOSC = 0x4

# CLK_REF_DIV, CLK_USB_DIV and CLK_ADC_DIV have no fractional part: INT is bits 9:8 and the rest is reserved
CLK_DIV_INT_ONLY_MASK = 0x300

# CLK_x_DIV: 24.8 fixed point (INT bits 31:8, FRAC bits 7:0)
CLK_DIV_INT_SHIFT = 8
CLK_DIV_FRAC_MASK = 0xFF

DEFAULT_CLK = 125e6  # what clk_sys and clk_peri are until firmware configures the tree


def clock_divisor(div: int) -> float:
    """Decodes a CLK_x_DIV register value. An INT of 0 means divide by 2**16."""
    integer = div >> CLK_DIV_INT_SHIFT
    return integer + (div & CLK_DIV_FRAC_MASK) / 256 if integer else 0x10000


class RPClocks(BasePeripheral):
    def __init__(self, rp2040: "RP2040", name: str):
        super().__init__(rp2040, name)
        self.gpout0_ctrl = 0
        self.gpout0_div = 0x100
        self.gpout1_ctrl = 0
        self.gpout1_div = 0x100
        self.gpout2_ctrl = 0
        self.gpout2_div = 0x100
        self.gpout3_ctrl = 0
        self.gpout3_div = 0x100
        self.ref_ctrl = 0
        self.ref_div = 0x100
        self.peri_ctrl = 0
        self.peri_div = 0x100
        self.usb_ctrl = 0
        self.usb_div = 0x100
        self.sys_ctrl = 0
        self.sys_div = 0x100
        self.adc_ctrl = 0
        self.adc_div = 0x100
        self.rtc_ctrl = 0
        self.rtc_div = 0x100
        self._stored = {offset: reset for offset, (_mask, reset) in STORED_REGISTERS.items()}

    def reset(self) -> None:
        """Every CLK_*_CTRL/DIV back to its power-on value (0089 Phase 5) - `0x100`, i.e. a
        divisor of 1.0 in the block's 8.8 fixed-point encoding, which is what construction uses."""
        self.gpout0_ctrl = 0
        self.gpout0_div = 0x100
        self.gpout1_ctrl = 0
        self.gpout1_div = 0x100
        self.gpout2_ctrl = 0
        self.gpout2_div = 0x100
        self.gpout3_ctrl = 0
        self.gpout3_div = 0x100
        self.ref_ctrl = 0
        self.ref_div = 0x100
        self.peri_ctrl = 0
        self.peri_div = 0x100
        self.usb_ctrl = 0
        self.usb_div = 0x100
        self.sys_ctrl = 0
        self.sys_div = 0x100
        self.adc_ctrl = 0
        self.adc_div = 0x100
        self.rtc_ctrl = 0
        self.rtc_div = 0x100
        self._stored = {offset: reset for offset, (_mask, reset) in STORED_REGISTERS.items()}

    @property
    def ref_freq(self) -> float:
        """clk_ref in Hz. GPIN0/GPIN1 are not modelled and give 0."""
        return self._ref_source_freq / clock_divisor(self.ref_div & CLK_REF_DIV_INT_BITS)

    @property
    def sys_freq(self) -> float:
        """clk_sys in Hz. GPIN0/GPIN1 are not modelled and give 0."""
        return self._sys_source_freq / clock_divisor(self.sys_div)

    @property
    def peri_freq(self) -> float:
        """clk_peri in Hz - what the UART and SPI baud rate generators run from. 0 while the clock generator is stopped, and for the GPIN0/GPIN1 sources
        that are not modelled."""
        rp2040 = self.rp2040
        ctrl = self.peri_ctrl
        if not (ctrl & CLK_PERI_CTRL_ENABLE) or ctrl & CLK_PERI_CTRL_KILL:
            return 0
        source = (ctrl >> CLK_PERI_CTRL_AUXSRC_SHIFT) & CLK_PERI_CTRL_AUXSRC_MASK
        if source == CLK_PERI_CTRL_AUXSRC_CLK_SYS:
            return self.sys_freq
        if source == CLK_PERI_CTRL_AUXSRC_PLL_SYS:
            return rp2040.pll_sys.frequency
        if source == CLK_PERI_CTRL_AUXSRC_PLL_USB:
            return rp2040.pll_usb.frequency
        if source == CLK_PERI_CTRL_AUXSRC_ROSC:
            return rp2040.rosc_freq
        if source == CLK_PERI_CTRL_AUXSRC_XOSC:
            return rp2040.xosc_freq
        return 0

    @property
    def _ref_source_freq(self) -> float:
        rp2040 = self.rp2040
        src = self.ref_ctrl & CLK_REF_CTRL_SRC_MASK
        if src == CLK_REF_CTRL_SRC_ROSC:
            return rp2040.rosc_freq
        if src == CLK_REF_CTRL_SRC_XOSC:
            return rp2040.xosc_freq
        if src == CLK_REF_CTRL_SRC_AUX:
            auxsrc = (self.ref_ctrl >> CLK_REF_CTRL_AUXSRC_SHIFT) & CLK_REF_CTRL_AUXSRC_MASK
            return rp2040.pll_usb.frequency if auxsrc == CLK_REF_CTRL_AUXSRC_PLL_USB else 0
        return 0

    @property
    def _sys_source_freq(self) -> float:
        rp2040 = self.rp2040
        if (self.sys_ctrl & CLK_SYS_CTRL_SRC_MASK) == CLK_SYS_CTRL_SRC_REF:
            return self.ref_freq
        auxsrc = (self.sys_ctrl >> CLK_SYS_CTRL_AUXSRC_SHIFT) & CLK_SYS_CTRL_AUXSRC_MASK
        if auxsrc == CLK_SYS_CTRL_AUXSRC_PLL_SYS:
            return rp2040.pll_sys.frequency
        if auxsrc == CLK_SYS_CTRL_AUXSRC_PLL_USB:
            return rp2040.pll_usb.frequency
        if auxsrc == CLK_SYS_CTRL_AUXSRC_ROSC:
            return rp2040.rosc_freq
        if auxsrc == CLK_SYS_CTRL_AUXSRC_XOSC:
            return rp2040.xosc_freq
        return 0

    def read_uint32(self, offset: int) -> int:
        if offset == CLK_GPOUT0_CTRL:
            return self.gpout0_ctrl & 0b100110001110111100000
        if offset == CLK_GPOUT0_DIV:
            return self.gpout0_div
        if offset == CLK_GPOUT0_SELECTED:
            return 1
        if offset == CLK_GPOUT1_CTRL:
            return self.gpout1_ctrl & 0b100110001110111100000
        if offset == CLK_GPOUT1_DIV:
            return self.gpout1_div
        if offset == CLK_GPOUT1_SELECTED:
            return 1
        if offset == CLK_GPOUT2_CTRL:
            return self.gpout2_ctrl & 0b100110001110111100000
        if offset == CLK_GPOUT2_DIV:
            return self.gpout2_div
        if offset == CLK_GPOUT2_SELECTED:
            return 1
        if offset == CLK_GPOUT3_CTRL:
            return self.gpout3_ctrl & 0b100110001110111100000
        if offset == CLK_GPOUT3_DIV:
            return self.gpout3_div
        if offset == CLK_GPOUT3_SELECTED:
            return 1
        if offset == CLK_REF_CTRL:
            return self.ref_ctrl & 0b000001100011
        if offset == CLK_REF_DIV:
            return self.ref_div & CLK_DIV_INT_ONLY_MASK  # b9:8 = int divisor. no frac divisor present
        if offset == CLK_REF_SELECTED:
            return 1 << (self.ref_ctrl & 0x03)
        if offset == CLK_SYS_CTRL:
            return self.sys_ctrl & 0b000011100001
        if offset == CLK_SYS_DIV:
            return self.sys_div
        if offset == CLK_SYS_SELECTED:
            return 1 << (self.sys_ctrl & 0x01)
        if offset == CLK_PERI_CTRL:
            return self.peri_ctrl & 0b110011100000
        if offset == CLK_PERI_DIV:
            return self.peri_div
        if offset == CLK_PERI_SELECTED:
            return 1
        if offset == CLK_USB_CTRL:
            return self.usb_ctrl & 0b100110000110011100000
        if offset == CLK_USB_DIV:
            return self.usb_div & CLK_DIV_INT_ONLY_MASK
        if offset == CLK_USB_SELECTED:
            return 1
        if offset == CLK_ADC_CTRL:
            return self.adc_ctrl & 0b100110000110011100000
        if offset == CLK_ADC_DIV:
            return self.adc_div & CLK_DIV_INT_ONLY_MASK
        if offset == CLK_ADC_SELECTED:
            return 1
        if offset == CLK_RTC_CTRL:
            return self.rtc_ctrl & 0b100110000110011100000
        if offset == CLK_RTC_DIV:
            return self.rtc_div
        if offset == CLK_RTC_SELECTED:
            return 1
        if offset in STORED_REGISTERS:
            return self._stored[offset]
        if offset == CLK_SYS_RESUS_STATUS:
            return 0  # clock resus not implemented
        if offset == INTR:
            return 0
        if offset == INTS:
            return self._stored[INTF]  # INTS = (INTR & INTE) | INTF, and nothing raises INTR
        return super().read_uint32(offset)

    def write_uint32(self, offset: int, value: int) -> None:
        if offset == CLK_GPOUT0_CTRL:
            self.gpout0_ctrl = value
        elif offset == CLK_GPOUT0_DIV:
            self.gpout0_div = value
        elif offset == CLK_GPOUT1_CTRL:
            self.gpout1_ctrl = value
        elif offset == CLK_GPOUT1_DIV:
            self.gpout1_div = value
        elif offset == CLK_GPOUT2_CTRL:
            self.gpout2_ctrl = value
        elif offset == CLK_GPOUT2_DIV:
            self.gpout2_div = value
        elif offset == CLK_GPOUT3_CTRL:
            self.gpout3_ctrl = value
        elif offset == CLK_GPOUT3_DIV:
            self.gpout3_div = value
        elif offset == CLK_REF_CTRL:
            self.ref_ctrl = value
            self.rp2040.update_clocks()
        elif offset == CLK_REF_DIV:
            self.ref_div = value & CLK_DIV_INT_ONLY_MASK
            self.rp2040.update_clocks()
        elif offset == CLK_SYS_CTRL:
            self.sys_ctrl = value
            self.rp2040.update_clocks()
        elif offset == CLK_SYS_DIV:
            self.sys_div = value
            self.rp2040.update_clocks()
        elif offset == CLK_PERI_CTRL:
            self.peri_ctrl = value
            self.rp2040.update_clocks()
        elif offset == CLK_PERI_DIV:
            self.peri_div = value
        elif offset == CLK_USB_CTRL:
            self.usb_ctrl = value
        elif offset == CLK_USB_DIV:
            self.usb_div = value & CLK_DIV_INT_ONLY_MASK
        elif offset == CLK_ADC_CTRL:
            self.adc_ctrl = value
        elif offset == CLK_ADC_DIV:
            self.adc_div = value & CLK_DIV_INT_ONLY_MASK
        elif offset == CLK_RTC_CTRL:
            self.rtc_ctrl = value
        elif offset == CLK_RTC_DIV:
            self.rtc_div = value
        elif offset in STORED_REGISTERS:
            self._stored[offset] = value & STORED_REGISTERS[offset][0]
        elif offset in (CLK_SYS_RESUS_STATUS, INTR, INTS):
            return  # read only
        else:
            super().write_uint32(offset, value)


def _set_clk_sys(rp2040: "RP2040", clk_sys: float) -> None:
    old_clk_sys = rp2040.clk_sys
    rp2040.clk_sys = clk_sys
    rp2040.ppb.clk_sys_changed(clk_sys)
    pwm = rp2040.pwm
    for channel in pwm.channels:
        channel.timer.frequency = pwm.clock_freq
    for listener in list(rp2040._clock_listeners):
        listener(clk_sys, old_clk_sys)


def _set_clk_peri(rp2040: "RP2040", clk_peri: float) -> None:
    rp2040.clk_peri = clk_peri
    for uart in rp2040.uart:
        uart.clk_peri_changed()


def update_clocks(rp2040: "RP2040") -> None:
    """Re-derives `clk_sys` and `clk_peri` from the PLL and CLOCKS registers and retunes what runs from them (a port of rp2040js 1.4.0's `updateClocks()`).

    A frequency of 0 - a clock source that is not modelled, or a stopped generator - and an unchanged one both keep the last value and notify nobody, so a
    chip whose firmware never configures the clock tree stays at its 125 MHz default. On a real change of clk_sys the SysTick counter and every PWM counter
    continue at the new rate and the clock listeners get `(new, old)`; on one of clk_peri every UART re-announces its baud rate.

    Shared by both chip implementations (`_rp2040.py`, `native/_rp2040.pyx`): the registers it reads and the blocks it retunes are the same Python objects on
    each.
    """
    clk_sys = rp2040.clocks.sys_freq
    if clk_sys and clk_sys != rp2040.clk_sys:
        _set_clk_sys(rp2040, clk_sys)
    clk_peri = rp2040.clocks.peri_freq
    if clk_peri and clk_peri != rp2040.clk_peri:
        _set_clk_peri(rp2040, clk_peri)
    rp2040.watchdog.clk_ref_changed(
        rp2040.clocks.ref_freq
    )  # the watchdog's tick (the TIMER's and SysTick's reference) is derived from clk_ref


def reset_clock_tree(rp2040: "RP2040") -> None:
    """The CLOCKS domain was reset: both PLLs back to power-on, and clk_sys/clk_peri back to the 125 MHz default the chip starts with. Not in upstream (which
    never resets): without it the registers would read as reset while a SysTick, PWM or UART still ran at whatever the firmware had configured, and firmware
    that then leaves the tree at its defaults would never get the default rate back."""
    rp2040.pll_sys.reset()
    rp2040.pll_usb.reset()
    if rp2040.clk_sys != DEFAULT_CLK:
        _set_clk_sys(rp2040, DEFAULT_CLK)
    if rp2040.clk_peri != DEFAULT_CLK:
        _set_clk_peri(rp2040, DEFAULT_CLK)
    rp2040.watchdog.clk_ref_changed(rp2040.clocks.ref_freq)
