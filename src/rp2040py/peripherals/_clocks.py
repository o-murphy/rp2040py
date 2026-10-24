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
FC0_REF_KHZ = 0x80
FC0_MIN_KHZ = 0x84
FC0_MAX_KHZ = 0x88
FC0_DELAY = 0x8C
FC0_INTERVAL = 0x90
FC0_SRC = 0x94
FC0_STATUS = 0x98
FC0_RESULT = 0x9C
WAKE_EN0 = 0xA0
WAKE_EN1 = 0xA4
SLEEP_EN0 = 0xA8
SLEEP_EN1 = 0xAC
ENABLED0 = 0xB0
ENABLED1 = 0xB4
INTR = 0xB8
INTE = 0xBC
INTF = 0xC0
INTS = 0xC4

# The frequency counter (datasheet 2.15.4 and 2.15.6.2, tables 238-246): the widths and the reset values of its registers.
FC0_REF_KHZ_MASK = 0xFFFFF
FC0_MIN_KHZ_MASK = 0x1FFFFFF
FC0_MAX_KHZ_MASK = 0x1FFFFFF
FC0_MAX_KHZ_RESET = 0x1FFFFFF
FC0_DELAY_MASK = 0x7
FC0_DELAY_RESET = 0x1
FC0_INTERVAL_MASK = 0xF
FC0_INTERVAL_RESET = 0x8
FC0_SRC_MASK = 0xFF
FC0_STATUS_DIED = 1 << 28
FC0_STATUS_FAST = 1 << 24
FC0_STATUS_SLOW = 1 << 20
FC0_STATUS_FAIL = 1 << 16
FC0_STATUS_RUNNING = 1 << 8
FC0_STATUS_DONE = 1 << 4
FC0_STATUS_PASS = 1 << 0
FC0_RESULT_MASK = 0x3FFFFFFF  # KHZ 29:5 and FRAC 4:0: the frequency in kHz with five fractional bits
FC0_RESULT_FRAC_BITS = 5
FC0_INTERVAL_NANOS = 980.0  # "The test interval is 0.98us * 2**interval"
# FC0_SRC: the clock sent to the counter (table 243)
FC0_SRC_PLL_SYS, FC0_SRC_PLL_USB, FC0_SRC_ROSC, FC0_SRC_ROSC_PH, FC0_SRC_XOSC = 0x01, 0x02, 0x03, 0x04, 0x05
FC0_SRC_GPIN0, FC0_SRC_GPIN1, FC0_SRC_CLK_REF, FC0_SRC_CLK_SYS, FC0_SRC_CLK_PERI = 0x06, 0x07, 0x08, 0x09, 0x0A
FC0_SRC_CLK_USB, FC0_SRC_CLK_ADC, FC0_SRC_CLK_RTC = 0x0B, 0x0C, 0x0D

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

CLK_RTC_CTRL_ENABLE = 1 << 11
CLK_RTC_CTRL_KILL = 1 << 10
CLK_RTC_CTRL_AUXSRC_SHIFT = 5
CLK_RTC_CTRL_AUXSRC_MASK = 0x7
CLK_RTC_CTRL_AUXSRC_PLL_USB = 0x0  # datasheet table 234 (clk_peri's list starts with clk_sys; this one with PLL_USB)
CLK_RTC_CTRL_AUXSRC_PLL_SYS = 0x1
CLK_RTC_CTRL_AUXSRC_ROSC = 0x2
CLK_RTC_CTRL_AUXSRC_XOSC = 0x3

# CLK_REF_DIV, CLK_USB_DIV and CLK_ADC_DIV have no fractional part: INT is bits 9:8 and the rest is reserved
CLK_DIV_INT_ONLY_MASK = 0x300

# CLK_x_DIV: 24.8 fixed point (INT bits 31:8, FRAC bits 7:0)
CLK_DIV_INT_SHIFT = 8
CLK_DIV_FRAC_MASK = 0xFF

XOSC_STATUS = (
    0x04  # the XOSC block's STATUS register, read through the bus-side interface (the block is C++ in a native chip)
)
XOSC_STATUS_STABLE = 1 << 31

DEFAULT_CLK = 125e6  # what clk_sys and clk_peri are until firmware configures the tree


def xosc_hz(rp2040: "RP2040") -> float:
    """What the crystal oscillator delivers: its frequency while the XOSC block is stable (enabled and not dormant), nothing otherwise (datasheet 2.16: the oscillator only runs once enabled)."""
    return rp2040.xosc_freq if rp2040.xosc.read_uint32(XOSC_STATUS) & XOSC_STATUS_STABLE else 0.0


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
        self._fc_alarm = rp2040.clock.create_alarm(self._fc_done)
        self._fc_started_hz = 0.0
        self._fc_reset()

    def snapshot(self) -> dict[str, int]:
        """The ten clock generators' CTRL and DIV words (ref, peri, usb, sys, adc, rtc) as plain values; the frequencies derived from them are `ref_freq` ... `adc_freq`."""
        return {
            "ref_ctrl": self.ref_ctrl,
            "ref_div": self.ref_div,
            "peri_ctrl": self.peri_ctrl,
            "peri_div": self.peri_div,
            "usb_ctrl": self.usb_ctrl,
            "usb_div": self.usb_div,
            "sys_ctrl": self.sys_ctrl,
            "sys_div": self.sys_div,
            "adc_ctrl": self.adc_ctrl,
            "adc_div": self.adc_div,
            "rtc_ctrl": self.rtc_ctrl,
            "rtc_div": self.rtc_div,
        }

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
        self._fc_reset()

    def update(self) -> None:
        """Re-derives `clk_sys` / `clk_peri` and retunes what runs from them (the chip's `update_clocks()`); the native block has the same method."""
        update_clocks(self.rp2040)

    def reset_tree(self) -> None:
        """The CLOCKS domain was reset (after `reset()`): both PLLs to power-on, clk_sys / clk_peri back to 125 MHz; the native block has the same method."""
        reset_clock_tree(self.rp2040)

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
            return xosc_hz(rp2040)
        return 0

    def _aux_generator_freq(self, ctrl: int, div: int) -> float:
        """A clock generator driven from the aux mux that CLK_USB, CLK_ADC and CLK_RTC share (tables 230, 232, 234: AUXSRC 7:5 = PLL_USB, PLL_SYS, ROSC_PH, XOSC, GPIN0, GPIN1), over its divider. 0
        while the generator is stopped (ENABLE clear, which is its reset state, or KILL) and for the GPIN0/GPIN1 sources, which are not modelled."""
        rp2040 = self.rp2040
        if not (ctrl & CLK_RTC_CTRL_ENABLE) or ctrl & CLK_RTC_CTRL_KILL:
            return 0
        source = (ctrl >> CLK_RTC_CTRL_AUXSRC_SHIFT) & CLK_RTC_CTRL_AUXSRC_MASK
        if source == CLK_RTC_CTRL_AUXSRC_PLL_USB:
            frequency = rp2040.pll_usb.frequency
        elif source == CLK_RTC_CTRL_AUXSRC_PLL_SYS:
            frequency = rp2040.pll_sys.frequency
        elif source == CLK_RTC_CTRL_AUXSRC_ROSC:
            frequency = rp2040.rosc_freq
        elif source == CLK_RTC_CTRL_AUXSRC_XOSC:
            frequency = xosc_hz(rp2040)
        else:
            return 0
        return frequency / clock_divisor(div)

    @property
    def rtc_freq(self) -> float:
        """clk_rtc in Hz (datasheet 2.15, table 234/235): the RTC's reference, the aux source divided by CLK_RTC_DIV (24.8, INT = 0 divides by 2^16)."""
        return self._aux_generator_freq(self.rtc_ctrl, self.rtc_div)

    @property
    def usb_freq(self) -> float:
        """clk_usb in Hz (tables 230/231), by the same mux and enable as clk_rtc; the divider is INT only."""
        return self._aux_generator_freq(self.usb_ctrl, self.usb_div)

    @property
    def adc_freq(self) -> float:
        """clk_adc in Hz (tables 232/233), by the same mux and enable as clk_rtc; the divider is INT only."""
        return self._aux_generator_freq(self.adc_ctrl, self.adc_div)

    def frequency_of(self, source: int) -> float:
        """What the frequency counter measures for an FC0_SRC value, in Hz; 0 for NULL, a source that is stopped, GPIN0/GPIN1 (not modelled) and a value that selects nothing."""
        rp2040 = self.rp2040
        return {
            FC0_SRC_PLL_SYS: lambda: rp2040.pll_sys.frequency,
            FC0_SRC_PLL_USB: lambda: rp2040.pll_usb.frequency,
            FC0_SRC_ROSC: lambda: rp2040.rosc_freq,
            FC0_SRC_ROSC_PH: lambda: rp2040.rosc_freq,
            FC0_SRC_XOSC: lambda: xosc_hz(rp2040),
            FC0_SRC_CLK_REF: lambda: self.ref_freq,
            FC0_SRC_CLK_SYS: lambda: (
                rp2040.clk_sys
            ),  # the frequency the chip runs at: 125 MHz until firmware configures the tree
            FC0_SRC_CLK_PERI: lambda: rp2040.clk_peri,
            FC0_SRC_CLK_USB: lambda: self.usb_freq,
            FC0_SRC_CLK_ADC: lambda: self.adc_freq,
            FC0_SRC_CLK_RTC: lambda: self.rtc_freq,
        }.get(source, lambda: 0.0)()

    # ---- the frequency counter (2.15.4, 2.15.6.2) ----
    def _fc_reset(self) -> None:
        self._fc_ref_khz = 0
        self._fc_min_khz = 0
        self._fc_max_khz = FC0_MAX_KHZ_RESET
        self._fc_delay = FC0_DELAY_RESET
        self._fc_interval = FC0_INTERVAL_RESET
        self._fc_src = 0
        self._fc_status = 0
        self._fc_result = 0
        self._fc_alarm.cancel()

    def _fc_start(self, src: int) -> None:
        """Writing FC0_SRC "initiates the frequency count" (it runs even for NULL: pico-sdk's `frequency_count_khz()` says so). The test takes FC0_DELAY periods of clk_ref (from FC0_REF_KHZ) and then
        0.98 us * 2**FC0_INTERVAL; it is RUNNING until then and DONE after. The configuration is read at the start; a count already running is replaced."""
        self._fc_src = src
        self._fc_started_hz = self.frequency_of(src)
        self._fc_status = FC0_STATUS_RUNNING
        self._fc_result = 0
        delay = self._fc_delay * 1e6 / self._fc_ref_khz if self._fc_ref_khz else 0.0
        self._fc_alarm.schedule(delay + FC0_INTERVAL_NANOS * (1 << self._fc_interval))

    def _fc_done(self) -> None:
        """The test interval is over. The result is the clock's frequency in kHz with five fractional bits, exact (the real counter is only as accurate as table 206 says; not independently sourced).
        A clock that is stopped at the start or at the end of the test sets DIED (and, as the datasheet says, FAIL); a frequency above FC0_MAX_KHZ sets FAST and below FC0_MIN_KHZ SLOW; PASS is
        set when DONE is and none of them is. Whether DONE is set for a DIED test is not stated; here it is - pico-sdk would otherwise wait for it for ever."""
        end_hz = self.frequency_of(self._fc_src)
        died = self._fc_started_hz == 0 or end_hz == 0
        khz = 0.0 if died else end_hz / 1000.0
        status = FC0_STATUS_DONE
        if died:
            status |= FC0_STATUS_DIED
        else:
            if khz > self._fc_max_khz:
                status |= FC0_STATUS_FAST
            if khz < self._fc_min_khz:
                status |= FC0_STATUS_SLOW
        status |= FC0_STATUS_FAIL if status & (FC0_STATUS_DIED | FC0_STATUS_FAST | FC0_STATUS_SLOW) else FC0_STATUS_PASS
        self._fc_status = status
        self._fc_result = int(khz * (1 << FC0_RESULT_FRAC_BITS)) & FC0_RESULT_MASK

    @property
    def _ref_source_freq(self) -> float:
        rp2040 = self.rp2040
        src = self.ref_ctrl & CLK_REF_CTRL_SRC_MASK
        if src == CLK_REF_CTRL_SRC_ROSC:
            return rp2040.rosc_freq
        if src == CLK_REF_CTRL_SRC_XOSC:
            return xosc_hz(rp2040)
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
            return xosc_hz(rp2040)
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
        if offset == FC0_REF_KHZ:
            return self._fc_ref_khz
        if offset == FC0_MIN_KHZ:
            return self._fc_min_khz
        if offset == FC0_MAX_KHZ:
            return self._fc_max_khz
        if offset == FC0_DELAY:
            return self._fc_delay
        if offset == FC0_INTERVAL:
            return self._fc_interval
        if offset == FC0_SRC:
            return self._fc_src
        if offset == FC0_STATUS:
            return self._fc_status
        if offset == FC0_RESULT:
            return self._fc_result
        if offset in STORED_REGISTERS:
            return self._stored[offset]
        if offset in (ENABLED0, ENABLED1):
            return 0  # read only; the per-destination clock enables are not modelled (WAKE_EN / SLEEP_EN are stored), so they read their reset value
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
            self.rp2040.update_clocks()  # clk_rtc is the RTC's reference
        elif offset == CLK_RTC_DIV:
            self.rtc_div = value
            self.rp2040.update_clocks()
        elif offset == FC0_REF_KHZ:
            self._fc_ref_khz = value & FC0_REF_KHZ_MASK
        elif offset == FC0_MIN_KHZ:
            self._fc_min_khz = value & FC0_MIN_KHZ_MASK
        elif offset == FC0_MAX_KHZ:
            self._fc_max_khz = value & FC0_MAX_KHZ_MASK
        elif offset == FC0_DELAY:
            self._fc_delay = value & FC0_DELAY_MASK
        elif offset == FC0_INTERVAL:
            self._fc_interval = value & FC0_INTERVAL_MASK
        elif offset == FC0_SRC:
            self._fc_start(value & FC0_SRC_MASK)
        elif offset in (FC0_STATUS, FC0_RESULT):
            return  # read only
        elif offset in STORED_REGISTERS:
            self._stored[offset] = value & STORED_REGISTERS[offset][0]
        elif offset in (CLK_SYS_RESUS_STATUS, ENABLED0, ENABLED1, INTR, INTS):
            return  # read only
        else:
            super().write_uint32(offset, value)


def _set_clk_sys(rp2040: "RP2040", clk_sys: float) -> None:
    old_clk_sys = rp2040.clk_sys
    rp2040.clk_sys = clk_sys
    rp2040.ppb.clk_sys_changed(clk_sys)
    ppb1 = getattr(
        rp2040, "ppb1", None
    )  # core 1's own SysTick (the native chip's is fed by its C++ tree; a stand-in chip has none)
    if ppb1 is not None:
        ppb1.clk_sys_changed(clk_sys)
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
    rp2040.rtc.clk_rtc_changed(rp2040.clocks.rtc_freq)  # the RTC counts on clk_rtc / (CLKDIV_M1 + 1)


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
    rp2040.rtc.clk_rtc_changed(rp2040.clocks.rtc_freq)
