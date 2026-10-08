"""A lockstep differential oracle for the RTC (docs/records/0096-cpp-mcu-core.md, "The small blocks"): the same generated stimulus drives two chips and everything observable is compared after every step.

One chip's block is the *pure-Python* reference (``peripherals/_rtc.py``, built explicitly because the facade would hand out the native one), the other's is whatever the facade gives (the C++ block in
a native build). The operations are reads and writes of **every register offset through the bus and its XOR/SET/CLR aliases**, of offsets that are no register (so the warnings are compared), the
SDK's own sequences (``rtc_set_datetime()``, ``rtc_set_alarm()``, with times chosen next to every carry: a minute, an hour, a day, the end of a month, a leap day, a year, the 12-bit year and values the
datasheet calls illegal), **simulated time** (``clock.tick()`` - the second is an alarm that fires inside it), the clock tree (``CLK_RTC_CTRL``/``CLK_RTC_DIV``: the rate the block counts on, stopped
included), ``reset()``, and the **interrupt line** (a recording one, or one that raises: the contract of ``core_host.hpp``, a failing host call leaves exactly the state the reference's exception
would have left). After each step the registers (read through the bus except RTC_0, whose read has a side effect the stimulus makes on purpose), the block's attributes, ``raw_write_value``, the clock (whether
an alarm is scheduled and when) and the ordered, timestamped log of the warnings and of the interrupt line are compared; an exception on one side and not on the other is a difference like any other.

Logic mutants of the reference (one subclass each) prove that a change of *logic* is seen, not only a perturbation of the candidate's state.
"""

import random
from collections.abc import Callable
from typing import Any

from rp2040py.irq import IRQ
from rp2040py.peripherals import _rtc as R
from rp2040py.rp2040 import RP2040

ALIAS_BASES = (0x0000, 0x1000, 0x2000, 0x3000)  # normal, XOR, SET, CLR
BASE = 0x4005C000
REGISTERS = (
    R.CLKDIV_M1,
    R.SETUP_0,
    R.SETUP_1,
    R.CTRL,
    R.IRQ_SETUP_0,
    R.IRQ_SETUP_1,
    R.RTC_1,
    R.RTC_0,
    R.INTR,
    R.INTE,
    R.INTF,
    R.INTS,
)
READ_SAFE = tuple(offset for offset in REGISTERS if offset != R.RTC_0)  # a read of RTC_0 latches RTC_1
UNIMPLEMENTED = (0x30, 0x34, 0x40, 0xFFC)
CLK_RTC_CTRL, CLK_RTC_DIV = 0x40008000 + 0x6C, 0x40008000 + 0x70
ENABLE_BIT, KILL_BIT = 1 << 11, 1 << 10
CLOCK_CONFIGS = (  # (CLK_RTC_CTRL, CLK_RTC_DIV): stopped; XOSC/256 = 46875 Hz; ROSC/256; XOSC/1024; XOSC/65536 (INT 0); a killed generator; GPIN0 (not modelled)
    (0, 0x100),
    (ENABLE_BIT | (3 << 5), 256 << 8),
    (ENABLE_BIT | (3 << 5), 256 << 8),
    (ENABLE_BIT | (3 << 5), 256 << 8),
    (ENABLE_BIT | (2 << 5), 256 << 8),
    (ENABLE_BIT | (3 << 5), 1024 << 8),
    (ENABLE_BIT | (3 << 5), 0),
    (ENABLE_BIT | KILL_BIT | (3 << 5), 256 << 8),
    (ENABLE_BIT | (4 << 5), 256 << 8),
)
ATTRS = (
    "clkdiv_m1",
    "setup0",
    "setup1",
    "irq_setup0",
    "irq_setup1",
    "inte",
    "intf",
    "force_not_leap_year",
    "enable",
    "year",
    "month",
    "day",
    "dotw",
    "hour",
    "minute",
    "second",
    "latched_date",
    "ctrl",
    "running",
    "raw_interrupt",
    "match_ena",
    "date",
    "time",
)
TIMES = (
    1,
    500,
    20_000,
    100_000,
    400_000,
    2_000_000,
    8_000_000,
    25_000_000,
)  # ns; a second is 21 us .. 5 ms at the rates and dividers the stimulus uses

# (year, month, day, dotw, hour, minute, second): next to every carry, and illegal ones
DATES = (
    (2024, 2, 28, 3, 23, 59, 57),
    (2023, 2, 28, 2, 23, 59, 58),
    (2024, 2, 29, 4, 23, 59, 58),
    (2100, 2, 28, 0, 23, 59, 58),
    (2096, 2, 28, 3, 23, 59, 58),
    (4095, 12, 31, 6, 23, 59, 57),
    (2025, 12, 31, 3, 23, 58, 58),
    (2025, 4, 30, 3, 23, 59, 59),
    (2025, 1, 31, 5, 23, 59, 59),
    (2025, 6, 6, 5, 10, 20, 30),
    (2025, 6, 6, 6, 10, 59, 58),
    (2025, 6, 6, 6, 9, 59, 59),
    (0, 0, 0, 7, 31, 63, 63),
    (4095, 15, 31, 7, 31, 63, 62),
    (2025, 13, 32, 6, 24, 60, 60),
    (2025, 8, 30, 0, 23, 59, 59),
    (2025, 9, 30, 2, 23, 59, 59),
    (2025, 15, 30, 6, 23, 59, 59),
    (2025, 0, 30, 6, 23, 59, 59),
    (2025, 7, 31, 4, 23, 59, 59),
    (1, 1, 1, 0, 0, 0, 0),
)


class Boom(Exception):
    """What a failing interrupt line raises."""


class Rig:
    """One chip plus the log of every warning and every call of the interrupt line."""

    def __init__(self, kind: str, factory: "Callable[..., Any] | None" = None) -> None:
        self.chip = RP2040()
        self.log: list[tuple] = []
        self.raising = False
        if kind == "pure":
            self._replace_the_block(R.RP2040RTC)
        elif kind == "mutant":
            assert factory is not None
            self._replace_the_block(factory)
        self._tap()

    def _replace_the_block(self, factory: Callable[..., Any]) -> None:
        block = factory(self.chip, "RTC_BASE")
        self.chip.peripherals[BASE >> 12] = block
        self.chip.rtc = block  # the clock tree tells clk_rtc to `chip.rtc`, and RESETS resets it

    def _tap(self) -> None:
        log, chip = self.log, self.chip
        for method in ("warning", "error", "info", "debug"):
            setattr(
                chip.logger,
                method,
                lambda name, message, _m=method: log.append(
                    ("log", float(chip.clock.nanos), _m, str(name), str(message))
                ),
            )
        real_set_interrupt = chip.set_interrupt

        def set_interrupt(irq: int, value: bool) -> None:
            if irq == IRQ.RTC:
                log.append(("irq", float(chip.clock.nanos), bool(value), self.raising))
                if self.raising:
                    raise Boom("the interrupt line failed")
            real_set_interrupt(irq, value)

        chip.set_interrupt = set_interrupt

    @property
    def peripheral(self) -> Any:
        return self.chip.peripherals[BASE >> 12]

    def apply(self, op: tuple) -> Any:
        kind = op[0]
        chip = self.chip
        if kind == "write":
            chip.write_uint32(BASE + op[3] + op[1], op[2] & 0xFFFFFFFF)
        elif kind == "read":
            return int(chip.read_uint32(BASE + op[2] + op[1]))
        elif kind == "tick":
            chip.clock.tick(op[1])
        elif kind == "clock":
            chip.write_uint32(CLK_RTC_DIV, op[2])
            chip.write_uint32(CLK_RTC_CTRL, op[1])  # the clock tree tells the RTC (update_clocks)
        elif kind == "reset":
            self.peripheral.reset()
        elif kind == "raising":
            self.raising = op[1]
        else:
            raise ValueError(f"unknown op {op!r}")
        return None

    def snapshot(self) -> dict[str, Any]:
        peripheral, clock = self.peripheral, self.chip.clock
        return {
            "regs": tuple(int(self.chip.read_uint32(BASE + offset)) for offset in READ_SAFE),
            "attrs": tuple(getattr(peripheral, name) for name in ATTRS),
            "raw": int(peripheral.raw_write_value),
            "alarm": (bool(clock.has_scheduled_alarm), float(clock.nanos_to_next_alarm)),
            "time": float(clock.nanos),
        }


# --- logic mutants of the reference: what the oracle must see ----------------------------------------------------------------

MUTANTS = (
    # registers
    "clkdiv_mask_8",
    "setup0_unmasked",
    "setup1_drops_dotw",
    "irq_setup0_stores_match_active",
    "irq_setup1_mask_wrong",
    "inte_unmasked",
    "intf_unmasked",
    "ctrl_load_reads_back",
    "ctrl_active_ignores_clock",
    "ctrl_active_always",
    "force_not_stored",
    "match_active_missing",
    "match_active_always",
    "rtc1_live",
    "rtc0_no_latch",
    "rtc1_latch_is_time",
    "ints_ignores_inte",
    "ints_ignores_intf",
    "intr_includes_intf",
    "ro_write_warns",
    "unimplemented_read_zero",
    "date_layout_wrong",
    "time_layout_wrong",
    # counting
    "load_needs_enable",
    "load_keeps_phase",
    "load_ignored",
    "load_dotw_ignored",
    "second_ignores_clkdiv",
    "second_clkdiv_without_plus_one",
    "clkdiv_write_keeps_phase",
    "clk_rtc_change_keeps_phase",
    "running_ignores_clock",
    "running_ignores_enable",
    "restart_on_every_ctrl_write",
    "disable_does_not_stop",
    "sec_limit_60",
    "min_limit_60",
    "hour_limit_24",
    "dotw_never_increments",
    "dotw_wraps_at_7",
    "feb_always_28",
    "feb_always_29",
    "leap_ignores_force",
    "leap_gregorian",
    "leap_off_by_one_year",
    "thirty_days_wrong_month",
    "thirty_one_days_in_april",
    "illegal_month_has_30_days",
    "day_limit_off_by_one",
    "month_limit_13",
    "year_unmasked",
    "day_carry_skips_month",
    "carry_resets_second_only",
    # the alarm
    "match_ignores_year",
    "match_ignores_month",
    "match_ignores_day",
    "match_ignores_dotw",
    "match_ignores_hour",
    "match_ignores_minute",
    "match_ignores_second",
    "match_ignores_match_ena",
    "match_needs_a_field",
    "match_is_any_field",
    "year_enable_is_month_enable",
    "irq_not_updated_on_second",
    "irq_not_updated_on_setup",
    "irq_not_updated_on_inte",
    "irq_not_updated_on_intf",
    "irq_not_updated_on_load",
    "irq_ignores_inte",
    "irq_ignores_intf",
    "irq_only_when_changed",
    # reset
    "reset_keeps_clkdiv",
    "reset_keeps_setup",
    "reset_keeps_counter",
    "reset_keeps_latch",
    "reset_keeps_enable",
    "reset_keeps_force",
    "reset_keeps_irq_setup",
    "reset_keeps_inte",
    "reset_keeps_intf",
    "reset_keeps_running",
    "reset_leaves_irq_up",
)


def _advance(
    self: Any,
    *,
    sec_limit: int = 59,
    min_limit: int = 59,
    hour_limit: int = 23,
    dotw_mode: str = "wrap6",
    dim: "Callable[[int, bool], int] | None" = None,
    leap: "Callable[[int, bool], bool] | None" = None,
    month_limit: int = 12,
    year_mask: int = 0xFFF,
    skip_month_on_day_carry: bool = False,
    carry_clears_only_second: bool = False,
) -> None:
    """The reference's ``_advance_second`` with every constant a parameter: the defaults are the reference (a control that must agree), each mutant changes one."""
    dim = dim or R.days_in_month
    leap = leap or R.is_leap_year
    if self.second < sec_limit:
        self.second += 1
        return
    self.second = 0
    if carry_clears_only_second:
        return
    if self.minute < min_limit:
        self.minute += 1
        return
    self.minute = 0
    if self.hour < hour_limit:
        self.hour += 1
        return
    self.hour = 0
    if dotw_mode == "wrap6":
        self.dotw = 0 if self.dotw >= 6 else self.dotw + 1
    elif dotw_mode == "wrap7":
        self.dotw = 0 if self.dotw >= 7 else self.dotw + 1
    # "never": the day of the week is not touched
    if self.day < dim(self.month, leap(self.year, self.force_not_leap_year)):
        self.day += 1
        return
    self.day = 1
    if skip_month_on_day_carry:
        return
    if self.month < month_limit:
        self.month += 1
        return
    self.month = 1
    self.year = (self.year + 1) & year_mask


def mutant_rig(name: str) -> Rig:
    """A rig whose block is the pure-Python one with one piece of its logic changed, to prove the comparison sees a change of *logic*."""
    base = R.RP2040RTC

    class Mutant(base):  # type: ignore[valid-type, misc]
        def read_uint32(self, offset: int) -> int:
            if name == "unimplemented_read_zero" and offset not in REGISTERS:
                return 0
            latched = self.latched_date
            value = super().read_uint32(offset)
            if name == "rtc0_no_latch" and offset == R.RTC_0:
                self.latched_date = latched
            if name == "ctrl_load_reads_back" and offset == R.CTRL:
                return value | (R.CTRL_LOAD if self._loaded_flag else 0)
            if name == "match_active_missing" and offset == R.IRQ_SETUP_0:
                return value & ~R.IRQ_SETUP_0_MATCH_ACTIVE
            if name == "match_active_always" and offset == R.IRQ_SETUP_0:
                return value | R.IRQ_SETUP_0_MATCH_ACTIVE
            if name == "rtc1_live" and offset == R.RTC_1:
                return self.date
            if name == "rtc1_latch_is_time" and offset == R.RTC_0:
                self.latched_date = self.time
                return value
            if name == "ints_ignores_inte" and offset == R.INTS:
                return int(self.raw_interrupt) | self.intf
            if name == "ints_ignores_intf" and offset == R.INTS:
                return int(self.raw_interrupt) & self.inte
            if name == "intr_includes_intf" and offset == R.INTR:
                return int(self.raw_interrupt) | self.intf
            if name == "date_layout_wrong" and offset == R.RTC_1:
                return value ^ (value & 0xF00) >> 8  # month bits folded into the day field
            if name == "time_layout_wrong" and offset == R.RTC_0:
                return (value & ~0xFF000000) | ((self.dotw & 3) << 24)
            return value

        _loaded_flag = False

        @property  # type: ignore[misc]
        def ctrl(self) -> int:
            value = base.ctrl.fget(self)  # type: ignore[attr-defined]
            if name == "ctrl_active_ignores_clock" and self.enable:
                value |= R.CTRL_RTC_ACTIVE
            if name == "ctrl_active_always":
                value |= R.CTRL_RTC_ACTIVE
            return value

        @ctrl.setter
        def ctrl(self, value: int) -> None:
            base.ctrl.fset(self, value)  # type: ignore[attr-defined]

        @property  # type: ignore[misc]
        def running(self) -> bool:
            if name == "running_ignores_clock":
                return self.enable
            if name == "running_ignores_enable":
                return self._clk_rtc > 0
            return self.enable and self._clk_rtc > 0

        @property  # type: ignore[misc]
        def raw_interrupt(self) -> bool:
            if not self.match_ena and name != "match_ignores_match_ena":
                return False
            s0, s1 = self.irq_setup0, self.irq_setup1
            fields = 0
            matched = []
            checks = (
                ("year", s0 & R.IRQ_SETUP_0_YEAR_ENA, (s0 >> 12) & 0xFFF, self.year),
                ("month", s0 & R.IRQ_SETUP_0_MONTH_ENA, (s0 >> 8) & 0xF, self.month),
                ("day", s0 & R.IRQ_SETUP_0_DAY_ENA, s0 & 0x1F, self.day),
                ("dotw", s1 & R.IRQ_SETUP_1_DOTW_ENA, (s1 >> 24) & 0x7, self.dotw),
                ("hour", s1 & R.IRQ_SETUP_1_HOUR_ENA, (s1 >> 16) & 0x1F, self.hour),
                ("minute", s1 & R.IRQ_SETUP_1_MIN_ENA, (s1 >> 8) & 0x3F, self.minute),
                ("second", s1 & R.IRQ_SETUP_1_SEC_ENA, s1 & 0x3F, self.second),
            )
            for field, enabled, want, have in checks:
                if name == "year_enable_is_month_enable" and field == "year":
                    enabled = s0 & R.IRQ_SETUP_0_MONTH_ENA
                if name == f"match_ignores_{field}":
                    continue
                if enabled:
                    fields += 1
                    matched.append(want == have)
            if name == "match_needs_a_field" and fields == 0:
                return False
            if name == "match_is_any_field":
                return any(matched) if matched else True
            return all(matched)

        def _advance_second(self) -> None:
            kwargs: dict[str, Any] = {}
            if name == "sec_limit_60":
                kwargs["sec_limit"] = 60
            elif name == "min_limit_60":
                kwargs["min_limit"] = 60
            elif name == "hour_limit_24":
                kwargs["hour_limit"] = 24
            elif name == "dotw_never_increments":
                kwargs["dotw_mode"] = "never"
            elif name == "dotw_wraps_at_7":
                kwargs["dotw_mode"] = "wrap7"
            elif name == "feb_always_28":
                kwargs["dim"] = lambda month, leap: 28 if month == 2 else R.days_in_month(month, leap)
            elif name == "feb_always_29":
                kwargs["dim"] = lambda month, leap: 29 if month == 2 else R.days_in_month(month, leap)
            elif name == "leap_ignores_force":
                kwargs["leap"] = lambda year, force: year % 4 == 0
            elif name == "leap_gregorian":
                kwargs["leap"] = lambda year, force: (
                    year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) and not force
                )
            elif name == "leap_off_by_one_year":
                kwargs["leap"] = lambda year, force: (year + 1) % 4 == 0 and not force
            elif name == "thirty_days_wrong_month":
                kwargs["dim"] = lambda month, leap: 30 if month in (4, 6, 8, 9, 11) else R.days_in_month(month, leap)
            elif name == "thirty_one_days_in_april":
                kwargs["dim"] = lambda month, leap: 31 if month == 4 else R.days_in_month(month, leap)
            elif name == "illegal_month_has_30_days":
                kwargs["dim"] = lambda month, leap: 30 if month not in range(1, 13) else R.days_in_month(month, leap)
            elif name == "day_limit_off_by_one":
                kwargs["dim"] = lambda month, leap: R.days_in_month(month, leap) + 1
            elif name == "month_limit_13":
                kwargs["month_limit"] = 13
            elif name == "year_unmasked":
                kwargs["year_mask"] = 0xFFFF
            elif name == "day_carry_skips_month":
                kwargs["skip_month_on_day_carry"] = True
            elif name == "carry_resets_second_only":
                kwargs["carry_clears_only_second"] = True
            _advance(self, **kwargs)

        def _load(self) -> None:
            if name == "load_ignored":
                return
            super()._load()
            if name == "load_dotw_ignored":
                self.dotw = 0

        def second_nanos(self) -> float:
            if name == "second_ignores_clkdiv":
                return 1e9 / self._clk_rtc
            if name == "second_clkdiv_without_plus_one":
                return max(self.clkdiv_m1, 1) * 1e9 / self._clk_rtc
            return super().second_nanos()

        def clk_rtc_changed(self, clk_rtc: float) -> None:
            if name == "clk_rtc_change_keeps_phase":
                if clk_rtc == self._clk_rtc:
                    return
                self._clk_rtc = clk_rtc
                self._retime(False)
                return
            super().clk_rtc_changed(clk_rtc)

        def _retime(self, restart: bool) -> None:
            if name == "disable_does_not_stop" and not self.running and self._alarm_pending:
                return
            if name == "restart_on_every_ctrl_write" and restart is False and self.running:
                restart = True
            super()._retime(restart)

        def check_interrupts(self) -> None:
            raw = self.raw_interrupt
            intf, inte = self.intf, self.inte
            if name == "irq_ignores_inte":
                inte = 1
            if name == "irq_ignores_intf":
                intf = 0
            level = bool(((int(raw) | intf) & inte) & R.INT_RTC)
            if name == "irq_only_when_changed":
                if level == getattr(self, "_last_level", None):
                    return
                self._last_level = level
            self.rp2040.set_interrupt(R.IRQ.RTC, level)

        def _on_second(self) -> None:
            if name == "irq_not_updated_on_second":
                self._alarm_pending = False
                self._advance_second()
                self._retime(True)
                return
            super()._on_second()

        def reset(self) -> None:
            kept = {
                "reset_keeps_clkdiv": ("clkdiv_m1",),
                "reset_keeps_setup": ("setup0", "setup1"),
                "reset_keeps_counter": ("year", "month", "day", "dotw", "hour", "minute", "second"),
                "reset_keeps_latch": ("latched_date",),
                "reset_keeps_enable": ("enable",),
                "reset_keeps_force": ("force_not_leap_year",),
                "reset_keeps_irq_setup": ("irq_setup0", "irq_setup1"),
                "reset_keeps_inte": ("inte",),
                "reset_keeps_intf": ("intf",),
                "reset_keeps_running": ("enable",),
            }.get(name, ())
            saved = {attr: getattr(self, attr) for attr in kept}
            if name == "reset_leaves_irq_up":
                real, self.check_interrupts = self.check_interrupts, lambda: None  # type: ignore[method-assign]
                try:
                    super().reset()
                finally:
                    self.check_interrupts = real  # type: ignore[method-assign]
                return
            super().reset()
            for attr, value in saved.items():
                setattr(self, attr, value)
            if saved:
                self._retime(True)
                self.check_interrupts()

        def write_uint32(self, offset: int, value: int) -> None:
            value &= 0xFFFFFFFF
            if name == "clkdiv_mask_8" and offset == R.CLKDIV_M1:
                super().write_uint32(offset, value & 0xFF)
                return
            if name == "clkdiv_write_keeps_phase" and offset == R.CLKDIV_M1:
                self.clkdiv_m1 = value & R.CLKDIV_M1_MASK
                self._retime(False)
                return
            if name == "setup0_unmasked" and offset == R.SETUP_0:
                self.setup0 = value
                return
            if name == "setup1_drops_dotw" and offset == R.SETUP_1:
                super().write_uint32(offset, value & ~0x07000000)
                return
            if name == "irq_setup0_stores_match_active" and offset == R.IRQ_SETUP_0:
                super().write_uint32(offset, value)
                self.irq_setup0 |= value & R.IRQ_SETUP_0_MATCH_ACTIVE
                return
            if name == "irq_setup1_mask_wrong" and offset == R.IRQ_SETUP_1:
                super().write_uint32(offset, value & ~0x00001F00 | (value & 0x3F00))
                self.irq_setup1 &= ~0x80000000
                return
            if name == "inte_unmasked" and offset == R.INTE:
                self.inte = value
                self.check_interrupts()
                return
            if name == "intf_unmasked" and offset == R.INTF:
                self.intf = value
                self.check_interrupts()
                return
            if name == "force_not_stored" and offset == R.CTRL:
                super().write_uint32(offset, value & ~R.CTRL_FORCE_NOTLEAPYEAR)
                return
            if name == "ro_write_warns" and offset in (R.RTC_1, R.INTS):
                super(R.RP2040RTC, self).write_uint32(offset, value)
                return
            if name == "irq_not_updated_on_inte" and offset == R.INTE:
                self.inte = value & R.INT_RTC
                return
            if name == "irq_not_updated_on_intf" and offset == R.INTF:
                self.intf = value & R.INT_RTC
                return
            if name == "irq_not_updated_on_setup" and offset in (R.IRQ_SETUP_0, R.IRQ_SETUP_1):
                if offset == R.IRQ_SETUP_0:
                    self.irq_setup0 = value & R.IRQ_SETUP_0_MASK
                else:
                    self.irq_setup1 = value & R.IRQ_SETUP_1_MASK
                return
            if offset == R.CTRL and name in (
                "load_needs_enable",
                "load_keeps_phase",
                "irq_not_updated_on_load",
                "restart_on_every_ctrl_write",
            ):
                self.force_not_leap_year = bool(value & R.CTRL_FORCE_NOTLEAPYEAR)
                self.enable = bool(value & R.CTRL_RTC_ENABLE)
                load = bool(value & R.CTRL_LOAD)
                if name == "load_needs_enable":
                    load = load and self.enable
                if load:
                    self._load()
                self._retime(load and name != "load_keeps_phase")
                if load and name != "irq_not_updated_on_load":
                    self.check_interrupts()
                return
            if name == "ctrl_load_reads_back" and offset == R.CTRL:
                self._loaded_flag = bool(value & R.CTRL_LOAD)
            super().write_uint32(offset, value)

    return Rig("mutant", Mutant)


# --- the stimulus ----------------------------------------------------------------------------------------------------------


def _datetime_words(date: tuple) -> tuple[int, int]:
    year, month, day, dotw, hour, minute, second = date
    return (year << 12 | month << 8 | day) & 0xFFFFFFFF, (dotw << 24 | hour << 16 | minute << 8 | second) & 0xFFFFFFFF


def _random_date(r: random.Random) -> tuple:
    if r.random() < 0.8:
        return r.choice(DATES)
    return (
        r.choice((0, 1, 2024, 2025, 2100, 4095, r.randrange(4096))),
        r.randrange(0, 16),
        r.randrange(0, 32),
        r.randrange(0, 8),
        r.randrange(0, 32),
        r.randrange(0, 64),
        r.randrange(0, 64),
    )


def _alarm_words(r: random.Random) -> tuple[int, int]:
    """IRQ_SETUP_0 and IRQ_SETUP_1 as `rtc_set_alarm()` writes them: each field enabled or not (-1 in the SDK), the values near those the clock reaches."""

    def pick(options: "tuple[int, ...]", p: float = 0.4) -> "int | None":
        return r.choice(options) if r.random() < p else None

    year = pick((0, 1, 2024, 2025, 2026, 2100, 4095))
    month = pick((0, 1, 2, 4, 6, 12))
    day = pick((0, 1, 6, 28, 29, 30, 31))
    dotw = pick((0, 1, 3, 4, 5, 6))
    hour = pick((0, 1, 9, 10, 23))
    minute = pick((0, 1, 20, 21, 58, 59))
    second = pick((0, 1, 2, 3, 30, 57, 58, 59), 0.55)
    s0 = (
        ((year << 12 | 1 << 26) if year is not None else 0)
        | ((month << 8 | 1 << 25) if month is not None else 0)
        | ((day | 1 << 24) if day is not None else 0)
    )
    s1 = (
        ((dotw << 24 | 1 << 31) if dotw is not None else 0)
        | ((hour << 16 | 1 << 30) if hour is not None else 0)
        | ((minute << 8 | 1 << 29) if minute is not None else 0)
        | ((second | 1 << 28) if second is not None else 0)
    )
    return s0 & 0xFFFFFFFF, s1 & 0xFFFFFFFF


def _value(r: random.Random, offset: int) -> int:
    if offset == R.CLKDIV_M1:
        if r.random() < 0.7:
            return r.choice((0, 0, 1, 2, 3, 7, r.randrange(0, 40)))
        return r.choice((46874, 0xFFFF, 0x10000, 0xFFFFFFFF, r.getrandbits(32)))
    if offset == R.CTRL:
        return r.choice(
            (
                0,
                1,
                1,
                1,
                R.CTRL_LOAD,
                R.CTRL_LOAD | 1,
                R.CTRL_LOAD | 1,
                R.CTRL_FORCE_NOTLEAPYEAR,
                R.CTRL_FORCE_NOTLEAPYEAR | 1,
                0xFFFFFFFF,
                r.getrandbits(32),
                0x2,
            )
        )
    if offset in (R.SETUP_0, R.SETUP_1):
        words = _datetime_words(_random_date(r))
        return words[0 if offset == R.SETUP_0 else 1] | (r.getrandbits(32) if r.random() < 0.15 else 0)
    if offset in (R.IRQ_SETUP_0, R.IRQ_SETUP_1):
        words = _alarm_words(r)
        value = words[0 if offset == R.IRQ_SETUP_0 else 1]
        if offset == R.IRQ_SETUP_0 and r.random() < 0.7:
            value |= R.IRQ_SETUP_0_MATCH_ENA
        if r.random() < 0.1:
            value |= r.getrandbits(32)
        return value
    return r.choice((0, 1, 2, 0xFFFFFFFE, 0xFFFFFFFF, r.getrandbits(32)))


def generate(seed: int, steps: int) -> list[tuple]:
    r = random.Random(seed)
    ops: list[tuple] = [("clock", *CLOCK_CONFIGS[1])]  # the SDK's clocks_init(): clk_rtc = XOSC / 256
    ops.append(
        ("write", R.CLKDIV_M1, r.choice((0, 1, 2, 5)), 0)
    )  # rtc_init(): CLKDIV_M1 (a second is made short so that the run sees many)
    while len(ops) < steps:
        roll = r.random()
        alias = 0 if r.random() < 0.6 else r.choice(ALIAS_BASES[1:])
        if roll < 0.10:
            ops.append(("read", r.choice(REGISTERS), alias))
        elif roll < 0.13:
            ops.append(("read", r.choice(UNIMPLEMENTED), alias if r.random() < 0.5 else 0))
        elif roll < 0.33:
            offset = r.choice(REGISTERS)
            ops.append(("write", offset, _value(r, offset), alias))
        elif roll < 0.35:
            ops.append(("write", r.choice(UNIMPLEMENTED), r.getrandbits(32), alias if r.random() < 0.5 else 0))
        elif roll < 0.45:  # rtc_set_datetime()
            s0, s1 = _datetime_words(_random_date(r))
            ops.extend(
                [
                    ("write", R.CTRL, 0, 0),
                    ("write", R.SETUP_0, s0, 0),
                    ("write", R.SETUP_1, s1, 0),
                    ("write", R.CTRL, R.CTRL_LOAD, 0),
                    ("write", R.CTRL, R.CTRL_RTC_ENABLE | (R.CTRL_FORCE_NOTLEAPYEAR if r.random() < 0.15 else 0), 0),
                ]
            )
        elif roll < 0.55:  # rtc_set_alarm()
            s0, s1 = _alarm_words(r)
            ops.extend(
                [
                    ("write", R.IRQ_SETUP_0, 0, 0),
                    ("write", R.IRQ_SETUP_0, s0, 0),
                    ("write", R.IRQ_SETUP_1, s1, 0),
                    ("write", R.INTE, 1, 0),
                    ("write", R.IRQ_SETUP_0, s0 | R.IRQ_SETUP_0_MATCH_ENA, 0),
                ]
            )
            if r.random() < 0.5:  # the handler: rtc_disable_alarm(), and for a repeating alarm rtc_enable_alarm()
                ops.extend(
                    [
                        ("tick", r.choice(TIMES)),
                        ("write", R.IRQ_SETUP_0, s0, 0),
                        ("write", R.IRQ_SETUP_0, s0 | R.IRQ_SETUP_0_MATCH_ENA, 0),
                    ]
                )
        elif roll < 0.78:
            ops.append(("tick", r.choice(TIMES)))
        elif roll < 0.83:
            ops.append(("clock", *r.choice(CLOCK_CONFIGS)))
        elif roll < 0.88:
            ops.append(("reset",))
            if r.random() < 0.6:  # the firmware boots again: rtc_init()
                ops.extend([("clock", *CLOCK_CONFIGS[1]), ("write", R.CLKDIV_M1, r.choice((0, 1, 2, 5)), 0)])
        else:  # the interrupt line fails for a few operations
            ops.append(("raising", True))
            ops.append(
                ("tick", r.choice(TIMES)) if r.random() < 0.5 else ("write", R.IRQ_SETUP_0, _value(r, R.IRQ_SETUP_0), 0)
            )
            ops.append(("raising", False))
    return ops[:steps]


class Divergence:
    def __init__(self, step: int, op: tuple, what: str) -> None:
        self.step, self.op, self.what = step, op, what

    def __str__(self) -> str:
        return f"step {self.step} {self.op}: {self.what}"

    def __repr__(self) -> str:
        return f"Divergence({self})"


def _step(rig: Rig, op: tuple) -> tuple:
    """(result, exception name) of one operation: an exception on one side and not on the other is a difference like any other."""
    try:
        return rig.apply(op), None
    except Exception as error:  # noqa: BLE001 - what is compared is *that* it raised and what, not how it is handled
        return None, f"{type(error).__name__}: {error}"


def run_pair(
    ops: list[tuple],
    *,
    reference: "Callable[[], Rig] | None" = None,
    candidate: "Callable[[], Rig] | None" = None,
    perturb: "Callable[[Rig, int], None] | None" = None,
) -> "Divergence | None":
    """Runs `ops` on both rigs and returns the first difference, or None. `perturb(candidate, step)` lets a test damage the candidate to prove the oracle sees it."""
    a = reference() if reference else Rig("pure")
    b = candidate() if candidate else Rig("default")
    log_a = log_b = 0
    for step, op in enumerate(ops):
        result_a, error_a = _step(a, op)
        result_b, error_b = _step(b, op)
        if perturb is not None:
            perturb(b, step)
        if (result_a, error_a) != (result_b, error_b):
            return Divergence(step, op, f"result {result_a!r}/{error_a} vs {result_b!r}/{error_b}")
        new_a, new_b = a.log[log_a:], b.log[log_b:]
        log_a, log_b = len(a.log), len(b.log)
        if new_a != new_b:
            return Divergence(step, op, f"log: {new_a[:4]} vs {new_b[:4]}")
        snapshot_a, snapshot_b = a.snapshot(), b.snapshot()
        if snapshot_a != snapshot_b:
            return Divergence(step, op, f"{snapshot_a} vs {snapshot_b}")
    return None


def coverage(ops: list[tuple]) -> dict[str, int]:
    """What the run exercised, measured on the reference: the counts a test asserts so that a green run means something."""
    rig = Rig("pure")
    counts: dict[str, int] = {}

    def count(name: str, amount: int = 1) -> None:
        counts[name] = counts.get(name, 0) + amount

    for op in ops:
        before = len(rig.log)
        peripheral = rig.peripheral
        old = (peripheral.minute, peripheral.hour, peripheral.day, peripheral.month, peripheral.year, peripheral.dotw)
        if op[0] == "read":
            count("read.reg" if op[1] in REGISTERS else "read.unimplemented")
            if op[2]:
                count("read.alias")
        elif op[0] == "write":
            count("write.reg" if op[1] in REGISTERS else "write.unimplemented")
            if op[3]:
                count("write.alias")
        else:
            count(op[0])
        failed = _step(rig, op)[1] is not None
        if failed:
            count("exception")
        new = (peripheral.minute, peripheral.hour, peripheral.day, peripheral.month, peripheral.year, peripheral.dotw)
        if op[0] == "tick":
            for label, i in (("minute", 0), ("hour", 1), ("day", 2), ("month", 3), ("year", 4), ("dotw", 5)):
                if old[i] != new[i]:
                    count(f"carry.{label}")
            if old[3] == 2 and new[3] == 3 and old[2] >= 28:
                count("carry.february")
            if old[2] == 28 and new[2] == 29 and new[3] == 2:
                count("carry.leap_day")
        if peripheral.running:
            count("state.running")
        if peripheral.enable and not peripheral.running:
            count("state.enabled_without_clock")
        if peripheral.raw_interrupt:
            count("state.match")
        for entry in rig.log[before:]:
            if entry[0] == "irq":
                count("irq.high" if entry[2] else "irq.low")
                if entry[3]:
                    count("irq.raising")
            else:
                count("log.entries")
        if op[0] == "write" and op[1] == R.CTRL and op[2] & R.CTRL_LOAD:
            count("load")
    return counts
