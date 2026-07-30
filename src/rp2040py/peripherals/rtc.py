from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from rp2040py.peripherals.peripheral import BasePeripheral

if TYPE_CHECKING:
    from rp2040py.rp2040 import RP2040

__all__ = ("RP2040RTC",)

RTC_SETUP0 = 0x04
RTC_SETUP1 = 0x08
RTC_CTRL = 0x0C
IRQ_SETUP_0 = 0x10
RTC_RTC1 = 0x18
RTC_RTC0 = 0x1C

RTC_ENABLE_BITS = 0x01
RTC_ACTIVE_BITS = 0x2
RTC_LOAD_BITS = 0x10

SETUP_0_YEAR_SHIFT = 12
SETUP_0_YEAR_MASK = 0xFFF
SETUP_0_MONTH_SHIFT = 8
SETUP_0_MONTH_MASK = 0xF
SETUP_0_DAY_SHIFT = 0
SETUP_0_DAY_MASK = 0x1F

SETUP_1_DOTW_SHIFT = 24
SETUP_1_DOTW_MASK = 0x7
SETUP_1_HOUR_SHIFT = 16
SETUP_1_HOUR_MASK = 0x1F
SETUP_1_MIN_SHIFT = 8
SETUP_1_MIN_MASK = 0x3F
SETUP_1_SEC_SHIFT = 0
SETUP_1_SEC_MASK = 0x3F

RTC_0_YEAR_SHIFT = 12
RTC_0_YEAR_MASK = 0xFFF
RTC_0_MONTH_SHIFT = 8
RTC_0_MONTH_MASK = 0xF
RTC_0_DAY_SHIFT = 0
RTC_0_DAY_MASK = 0x1F

RTC_1_DOTW_SHIFT = 24
RTC_1_DOTW_MASK = 0x7
RTC_1_HOUR_SHIFT = 16
RTC_1_HOUR_MASK = 0x1F
RTC_1_MIN_SHIFT = 8
RTC_1_MIN_MASK = 0x3F
RTC_1_SEC_SHIFT = 0
RTC_1_SEC_MASK = 0x3F


def _js_day_of_week(date: datetime) -> int:
    # Python's weekday() is Monday=0..Sunday=6; JS getDay() is Sunday=0..Saturday=6
    return (date.weekday() + 1) % 7


class RP2040RTC(BasePeripheral):
    def __init__(self, rp2040: "RP2040", name: str):
        super().__init__(rp2040, name)
        self.setup0 = 0
        self.setup1 = 0
        self.ctrl = 0
        self.baseline = datetime(2021, 1, 1, tzinfo=timezone.utc)
        self.baseline_nanos: float = 0

    def read_uint32(self, offset: int) -> int:
        date = self.baseline + timedelta(milliseconds=(self.rp2040.clock.nanos - self.baseline_nanos) / 1_000_000)
        if offset == RTC_SETUP0:
            return self.setup0
        if offset == RTC_SETUP1:
            return self.setup1
        if offset == RTC_CTRL:
            return self.ctrl
        if offset == IRQ_SETUP_0:
            return 0
        if offset == RTC_RTC1:
            return (
                ((date.year & RTC_0_YEAR_MASK) << RTC_0_YEAR_SHIFT)
                | ((date.month & RTC_0_MONTH_MASK) << RTC_0_MONTH_SHIFT)
                | ((date.day & RTC_0_DAY_MASK) << RTC_0_DAY_SHIFT)
            )
        if offset == RTC_RTC0:
            return (
                (_js_day_of_week(date) & RTC_1_DOTW_MASK) << RTC_1_DOTW_SHIFT
                | ((date.hour & RTC_1_HOUR_MASK) << RTC_1_HOUR_SHIFT)
                | ((date.minute & RTC_1_MIN_MASK) << RTC_1_MIN_SHIFT)
                | ((date.second & RTC_1_SEC_MASK) << RTC_1_SEC_SHIFT)
            )
        return super().read_uint32(offset)

    def write_uint32(self, offset: int, value: int) -> None:
        if offset == RTC_SETUP0:
            self.setup0 = value
        elif offset == RTC_SETUP1:
            self.setup1 = value
        elif offset == RTC_CTRL:
            # Though RTC_LOAD_BITS is type SC and should be cleared on next cycle, pico-sdk write
            # RTC_LOAD_BITS & RTC_ENABLE_BITS seperatly.
            # https://github.com/raspberrypi/pico-sdk/blob/master/src/rp2_common/hardware_rtc/rtc.c#L76-L80
            if value & RTC_LOAD_BITS:
                self.ctrl |= RTC_LOAD_BITS
            if value & RTC_ENABLE_BITS:
                self.ctrl |= RTC_ENABLE_BITS
                self.ctrl |= RTC_ACTIVE_BITS
                if self.ctrl & RTC_LOAD_BITS:
                    year = (self.setup0 >> SETUP_0_YEAR_SHIFT) & SETUP_0_YEAR_MASK
                    month = (self.setup0 >> SETUP_0_MONTH_SHIFT) & SETUP_0_MONTH_MASK
                    day = (self.setup0 >> SETUP_0_DAY_SHIFT) & SETUP_0_DAY_MASK
                    hour = (self.setup1 >> SETUP_1_HOUR_SHIFT) & SETUP_1_HOUR_MASK
                    minute = (self.setup1 >> SETUP_1_MIN_SHIFT) & SETUP_1_MIN_MASK
                    sec = (self.setup1 >> SETUP_1_SEC_SHIFT) & SETUP_1_SEC_MASK
                    self.baseline = datetime(year, month, day, hour, minute, sec, tzinfo=timezone.utc)
                    self.baseline_nanos = self.rp2040.clock.nanos
                    self.ctrl &= ~RTC_LOAD_BITS
            else:
                self.ctrl &= ~RTC_ENABLE_BITS
                self.ctrl &= ~RTC_ACTIVE_BITS
        else:
            super().write_uint32(offset, value)
