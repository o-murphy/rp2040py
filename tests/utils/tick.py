"""What pico-sdk's ``clocks_init`` does to make the TIMER count: clk_ref from the crystal and the watchdog's tick generator at one tick per microsecond (docs/records/0098, the WATCHDOG
section; RP2040 datasheet 4.6.4: "The Watchdog tick must be running for the timer to start counting"). A bare chip does not count until this has run, as on silicon."""

from typing import Any

CLOCKS_BASE = 0x40008000
CLK_REF_CTRL = CLOCKS_BASE + 0x30
CLK_REF_SRC_XOSC = 0x2
WATCHDOG_TICK = 0x40058000 + 0x2C
TICK_ENABLE = 1 << 9
XOSC_MHZ = 12  # the chip's crystal (RP2040.xosc_freq): 12 cycles of it are one microsecond


def start_tick(chip: Any, cycles: int = XOSC_MHZ) -> None:
    """clk_ref = XOSC, then the tick generator at ``cycles`` of it per tick (12: 1 us)."""
    chip.write_uint32(CLK_REF_CTRL, CLK_REF_SRC_XOSC)
    chip.write_uint32(WATCHDOG_TICK, cycles | TICK_ENABLE)


CLK_RTC_CTRL = CLOCKS_BASE + 0x6C
CLK_RTC_DIV = CLOCKS_BASE + 0x70
CLK_RTC_ENABLE = 1 << 11
CLK_RTC_AUXSRC_XOSC = 3 << 5


def start_rtc_clock(chip: Any) -> None:
    """What pico-sdk's ``clocks_init`` does for clk_rtc: XOSC / 256 = 46875 Hz (``rtc_init()`` then divides it down to 1 Hz with CLKDIV_M1 = 46874). A bare chip's RTC does not count until
    this has run, as on silicon (the CLOCKS generator is stopped at reset)."""
    chip.write_uint32(CLK_RTC_DIV, 256 << 8)
    chip.write_uint32(CLK_RTC_CTRL, CLK_RTC_ENABLE | CLK_RTC_AUXSRC_XOSC)
