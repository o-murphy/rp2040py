"""The RP2040 RTC (datasheet 4.8) - the pure-Python reference of record 0096/0098.

What the block does, with its sources (RP2040 datasheet RP-008371-DS-1, section 4.8, and pico-sdk `hardware_rtc/rtc.c`):

- **Time** is seven fields held as the datasheet's table 551 gives them (year 12 bits, month 4, day 5, day of week 3, hour 5, minute 6, second 6). It advances once per *second*, where a second is
  `(CLKDIV_M1 + 1)` periods of `clk_rtc` (4.8.4); no `clk_rtc` (the CLOCKS generator is stopped, which is its reset state) means no count. The block does not check the values it is given ("Illegal
  values may cause unexpected behaviour", 4.8.1) and this model never raises on them: a field that is past its limit wraps at the next carry (second/minute >= 59, hour >= 23, month >= 12, day >= the
  month's length - the length of a month outside 1..12 is 31, of a day of week >= 6 wraps to 0). *Not independently sourced.*
- **Day of week** is only incremented (4.8.1.1: "it will only increment the existing value"), at each day change, Sun 0 .. Sat 6.
- **Leap year** (4.8.2): a year divisible by 4 has a Feb 29th unless `CTRL.FORCE_NOTLEAPYEAR` is set. The datasheet words it as "the current value of YEAR in SETUP_0"; the NOTE under it (set the bit "after
  2096 Mar 1 ... before 2100 Feb 28") only makes sense for the running year, which is what this model tests.
- **SETUP_0/SETUP_1** are the values `CTRL.LOAD` copies into the counter ("Load setup values into rtc clock domain", rtc.c); LOAD (type SC) reads back 0. A load works whether or not the RTC is enabled (the
  SDK loads first and enables after) and restarts the second divider. Real hardware takes two `clk_rtc` periods for it to arrive (4.8.4 NOTE); this model is instant. *Not modelled.*
- **CTRL.RTC_ACTIVE** (RO) is the synchronised ENABLE: it reads 1 while the RTC is enabled *and* `clk_rtc` runs (the SDK's `rtc_set_datetime()` polls it, so firmware that never configured `clk_rtc` waits, as
  on silicon). Instant here for the same reason.
- **RTC_0 / RTC_1** are read-only; "Read this before RTC 1": reading RTC_0 latches the date half (RTC_1), which is what a read of RTC_1 returns (4.8.5.3). Nothing is latched before the first RTC_0 read (RTC_1
  reads 0). *Not independently sourced.*
- **Alarm** (4.8.3, 4.8.5.4): `IRQ_SETUP_0/1` hold a time and one enable bit per field; with `MATCH_ENA` set, the *raw* interrupt (`INTR.RTC`) is high while every enabled field equals the counter - a level, which
  stays up for the whole second the match lasts (the SDK handler's comment: "If it matches on a second it can keep firing for that second"). `MATCH_ACTIVE` (RO) is the synchronised MATCH_ENA, instant here. With
  no field enabled and `MATCH_ENA` set, every second matches. `INTE`/`INTF`/`INTS` are the usual masking and forcing; the line is NVIC interrupt 25. The wake of ROSC/XOSC from dormant mode (4.8.3) is not modelled.
- **Reset** (`RESETS.RESET_RTC`; the block is "reset" by pico-sdk's `rtc_init()` before use): every register 0, the counter 0, the latch 0, the divider stopped. Not independently sourced: the counter's reset value
  ("-" in table 559/560).
"""

from typing import TYPE_CHECKING

from rp2040py.irq import IRQ
from rp2040py.peripherals.peripheral import BasePeripheral

if TYPE_CHECKING:
    from rp2040py.rp2040 import RP2040

__all__ = ("RP2040RTC", "days_in_month", "is_leap_year")

CLKDIV_M1 = 0x00
SETUP_0 = 0x04
SETUP_1 = 0x08
CTRL = 0x0C
IRQ_SETUP_0 = 0x10
IRQ_SETUP_1 = 0x14
RTC_1 = 0x18
RTC_0 = 0x1C
INTR = 0x20
INTE = 0x24
INTF = 0x28
INTS = 0x2C

CLKDIV_M1_MASK = 0xFFFF

SETUP_0_MASK = 0x00FFFF1F  # YEAR 23:12, MONTH 11:8, DAY 4:0
SETUP_1_MASK = 0x071F3F3F  # DOTW 26:24, HOUR 20:16, MIN 13:8, SEC 5:0

CTRL_FORCE_NOTLEAPYEAR = 1 << 8
CTRL_LOAD = 1 << 4
CTRL_RTC_ACTIVE = 1 << 1
CTRL_RTC_ENABLE = 1 << 0

IRQ_SETUP_0_MATCH_ACTIVE = 1 << 29
IRQ_SETUP_0_MATCH_ENA = 1 << 28
IRQ_SETUP_0_YEAR_ENA = 1 << 26
IRQ_SETUP_0_MONTH_ENA = 1 << 25
IRQ_SETUP_0_DAY_ENA = 1 << 24
IRQ_SETUP_0_MASK = 0x17FFFF1F  # MATCH_ENA, the three enables, YEAR, MONTH, DAY (MATCH_ACTIVE is read-only)

IRQ_SETUP_1_DOTW_ENA = 1 << 31
IRQ_SETUP_1_HOUR_ENA = 1 << 30
IRQ_SETUP_1_MIN_ENA = 1 << 29
IRQ_SETUP_1_SEC_ENA = 1 << 28
IRQ_SETUP_1_MASK = 0xF71F3F3F

INT_RTC = 1  # INTR/INTE/INTF/INTS: the one bit


def is_leap_year(year: int, force_not_leap_year: bool) -> bool:
    """Feb 28th is followed by Feb 29th in a year divisible by 4 unless `CTRL.FORCE_NOTLEAPYEAR` forces it off (4.8.2)."""
    return year % 4 == 0 and not force_not_leap_year


def days_in_month(month: int, leap: bool) -> int:
    """The day the month ends on. A month outside 1..12 (the block does not check) has 31 days; not independently sourced."""
    if month in (4, 6, 9, 11):
        return 30
    if month == 2:
        return 29 if leap else 28
    return 31


class RP2040RTC(BasePeripheral):
    def __init__(self, rp2040: "RP2040", name: str):
        super().__init__(rp2040, name)
        self.clkdiv_m1 = 0
        self.setup0 = 0
        self.setup1 = 0
        self.irq_setup0 = 0
        self.irq_setup1 = 0
        self.inte = 0
        self.intf = 0
        self.force_not_leap_year = False
        self.enable = False
        self.year = self.month = self.day = self.dotw = self.hour = self.minute = self.second = 0
        self.latched_date = 0  # what RTC_1 reads: the date half as of the last read of RTC_0
        self._clk_rtc = 0.0  # clk_rtc in Hz, pushed by the chip (`clk_rtc_changed`); 0 = stopped
        self._alarm_pending = False  # a second is under way
        self._alarm = rp2040.clock.create_alarm(self._on_second)

    # ---- the registers the tests and the chip reach as attributes ----
    @property
    def ctrl(self) -> int:
        return (
            (CTRL_FORCE_NOTLEAPYEAR if self.force_not_leap_year else 0)
            | (CTRL_RTC_ACTIVE if self.running else 0)
            | (CTRL_RTC_ENABLE if self.enable else 0)
        )

    @ctrl.setter
    def ctrl(self, value: int) -> None:
        self.force_not_leap_year = bool(value & CTRL_FORCE_NOTLEAPYEAR)
        self.enable = bool(value & CTRL_RTC_ENABLE)
        self._retime(False)

    @property
    def running(self) -> bool:
        """The counter is counting: enabled, and clk_rtc runs."""
        return self.enable and self._clk_rtc > 0

    @property
    def match_ena(self) -> bool:
        return bool(self.irq_setup0 & IRQ_SETUP_0_MATCH_ENA)

    @property
    def raw_interrupt(self) -> bool:
        """INTR.RTC: MATCH_ENA is set and every enabled field equals the counter."""
        if not self.match_ena:
            return False
        s0, s1 = self.irq_setup0, self.irq_setup1
        if s0 & IRQ_SETUP_0_YEAR_ENA and (s0 >> 12) & 0xFFF != self.year:
            return False
        if s0 & IRQ_SETUP_0_MONTH_ENA and (s0 >> 8) & 0xF != self.month:
            return False
        if s0 & IRQ_SETUP_0_DAY_ENA and s0 & 0x1F != self.day:
            return False
        if s1 & IRQ_SETUP_1_DOTW_ENA and (s1 >> 24) & 0x7 != self.dotw:
            return False
        if s1 & IRQ_SETUP_1_HOUR_ENA and (s1 >> 16) & 0x1F != self.hour:
            return False
        if s1 & IRQ_SETUP_1_MIN_ENA and (s1 >> 8) & 0x3F != self.minute:
            return False
        return not (s1 & IRQ_SETUP_1_SEC_ENA and s1 & 0x3F != self.second)

    @property
    def date(self) -> int:
        """The RTC_1 word of the counter now."""
        return (self.year << 12) | (self.month << 8) | self.day

    @property
    def time(self) -> int:
        """The RTC_0 word of the counter now."""
        return (self.dotw << 24) | (self.hour << 16) | (self.minute << 8) | self.second

    # ---- clocking ----
    def clk_rtc_changed(self, clk_rtc: float) -> None:
        """clk_rtc is now `clk_rtc` Hz (called by `update_clocks`; 0: the generator is stopped): the second follows it."""
        if clk_rtc == self._clk_rtc:
            return
        self._clk_rtc = clk_rtc
        self._retime(True)

    def second_nanos(self) -> float:
        """One second of the counter in simulated nanoseconds: (CLKDIV_M1 + 1) periods of clk_rtc."""
        return (self.clkdiv_m1 + 1) * 1e9 / self._clk_rtc

    def _retime(self, restart: bool) -> None:
        """The divider runs while `running`; `restart` begins a new second (a load, a change of rate or of divider), otherwise a second already under way is left alone."""
        if not self.running:
            self._alarm.cancel()
            self._alarm_pending = False
        elif restart or not self._alarm_pending:
            self._alarm.schedule(self.second_nanos())
            self._alarm_pending = True

    def _on_second(self) -> None:
        self._alarm_pending = False
        self._advance_second()
        self._retime(True)
        self.check_interrupts()

    def _advance_second(self) -> None:
        if self.second < 59:
            self.second += 1
            return
        self.second = 0
        if self.minute < 59:
            self.minute += 1
            return
        self.minute = 0
        if self.hour < 23:
            self.hour += 1
            return
        self.hour = 0
        self.dotw = 0 if self.dotw >= 6 else self.dotw + 1
        if self.day < days_in_month(self.month, is_leap_year(self.year, self.force_not_leap_year)):
            self.day += 1
            return
        self.day = 1
        if self.month < 12:
            self.month += 1
            return
        self.month = 1
        self.year = (self.year + 1) & 0xFFF

    def _load(self) -> None:
        self.year = (self.setup0 >> 12) & 0xFFF
        self.month = (self.setup0 >> 8) & 0xF
        self.day = self.setup0 & 0x1F
        self.dotw = (self.setup1 >> 24) & 0x7
        self.hour = (self.setup1 >> 16) & 0x1F
        self.minute = (self.setup1 >> 8) & 0x3F
        self.second = self.setup1 & 0x3F

    def check_interrupts(self) -> None:
        raw = self.raw_interrupt
        self.rp2040.set_interrupt(IRQ.RTC, bool(((int(raw) | self.intf) & self.inte) & INT_RTC))

    def reset(self) -> None:
        """`RESETS_RESET_RTC` (0089 Phase 5). pico-sdk's own `rtc_init()` starts with `reset_block(RESETS_RESET_RTC_BITS)`, so firmware re-initialises this block after a reset rather than
        expecting it to keep time. `clk_rtc` is not the block's: it is kept."""
        self.clkdiv_m1 = 0
        self.setup0 = self.setup1 = self.irq_setup0 = self.irq_setup1 = 0
        self.inte = self.intf = 0
        self.force_not_leap_year = False
        self.enable = False
        self.year = self.month = self.day = self.dotw = self.hour = self.minute = self.second = 0
        self.latched_date = 0
        self._retime(True)
        self.check_interrupts()

    def read_uint32(self, offset: int) -> int:
        if offset == CLKDIV_M1:
            return self.clkdiv_m1
        if offset == SETUP_0:
            return self.setup0
        if offset == SETUP_1:
            return self.setup1
        if offset == CTRL:
            return self.ctrl
        if offset == IRQ_SETUP_0:
            return self.irq_setup0 | (IRQ_SETUP_0_MATCH_ACTIVE if self.match_ena else 0)
        if offset == IRQ_SETUP_1:
            return self.irq_setup1
        if offset == RTC_1:
            return self.latched_date
        if offset == RTC_0:
            self.latched_date = self.date
            return self.time
        raw = int(self.raw_interrupt)
        if offset == INTR:
            return raw
        if offset == INTE:
            return self.inte
        if offset == INTF:
            return self.intf
        if offset == INTS:
            return (raw | self.intf) & self.inte
        return super().read_uint32(offset)

    def write_uint32(self, offset: int, value: int) -> None:
        if offset == CLKDIV_M1:
            self.clkdiv_m1 = value & CLKDIV_M1_MASK
            self._retime(True)
        elif offset == SETUP_0:
            self.setup0 = value & SETUP_0_MASK
        elif offset == SETUP_1:
            self.setup1 = value & SETUP_1_MASK
        elif offset == CTRL:
            self.force_not_leap_year = bool(value & CTRL_FORCE_NOTLEAPYEAR)
            self.enable = bool(value & CTRL_RTC_ENABLE)
            load = bool(value & CTRL_LOAD)
            if load:
                self._load()
            self._retime(load)
            if load:
                self.check_interrupts()
        elif offset == IRQ_SETUP_0:
            self.irq_setup0 = value & IRQ_SETUP_0_MASK
            self.check_interrupts()
        elif offset == IRQ_SETUP_1:
            self.irq_setup1 = value & IRQ_SETUP_1_MASK
            self.check_interrupts()
        elif offset == INTE:
            self.inte = value & INT_RTC
            self.check_interrupts()
        elif offset == INTF:
            self.intf = value & INT_RTC
            self.check_interrupts()
        elif offset in (RTC_1, RTC_0, INTR, INTS):
            return  # read only
        else:
            super().write_uint32(offset, value)
