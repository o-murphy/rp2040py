from __future__ import annotations

import math
from collections.abc import Callable
from enum import IntEnum

from rp2040py.clock.clock import IClock


class TimerMode(IntEnum):
    INCREMENT = 0
    DECREMENT = 1
    ZIGZAG = 2


def _js_round(value: float) -> int:
    """Mimics JS Math.round (rounds half towards +Infinity),
    unlike Python's round() which uses banker's rounding."""
    return math.floor(value + 0.5)


class Timer32:
    def __init__(self, clock: IClock, base_freq: int):
        self.clock = clock
        self._base_freq = base_freq

        self._base_value = 0
        self._base_nanos: float = 0
        self._top_value = 0xFFFFFFFF
        self._prescaler_value: float = 1
        self._timer_mode = TimerMode.INCREMENT
        self._enabled = True
        self.listeners: list[Callable[[], None]] = []

    def reset(self) -> None:
        self._base_nanos = self.clock.nanos
        self._base_value = 0
        self._updated()

    def set(self, value: int, zig_zag_down: bool = False) -> None:
        self._base_value = self._top_value * 2 - value if zig_zag_down else value
        self._base_nanos = self.clock.nanos
        self._updated()

    def advance(self, delta: int) -> None:
        """
        Advances the counter by the given amount. Note that this will
        decrease the counter if the timer is running in Decrement mode.

        :param delta: The value to add to the counter. Can be negative.
        """
        self._base_value += delta

    @property
    def raw_counter(self) -> int:
        base_freq = self._base_freq
        prescaler_value = self._prescaler_value
        base_nanos = self._base_nanos
        base_value = self._base_value
        enabled = self._enabled
        timer_mode = self._timer_mode

        if not base_freq or not prescaler_value or not enabled:
            return self._base_value

        zigzag = timer_mode == TimerMode.ZIGZAG
        ticks = ((self.clock.nanos - base_nanos) / 1e9) * (base_freq / prescaler_value)
        top_modulo = self._top_value * 2 if zigzag else self._top_value + 1
        delta = top_modulo - (ticks % top_modulo) if timer_mode == TimerMode.DECREMENT else ticks
        current_value = _js_round(base_value + delta)
        if self._top_value != 0xFFFFFFFF:
            current_value %= top_modulo
        return current_value

    @property
    def counter(self) -> int:
        current_value = self.raw_counter
        if self._timer_mode == TimerMode.ZIGZAG and current_value > self._top_value:
            current_value = self._top_value * 2 - current_value
        return current_value & 0xFFFFFFFF

    @property
    def top(self) -> int:
        return self._top_value

    @top.setter
    def top(self, value: int) -> None:
        counter = self.counter
        self._top_value = value
        self.set(counter if counter <= self._top_value else 0)

    @property
    def frequency(self) -> int:
        return self._base_freq

    @frequency.setter
    def frequency(self, value: int) -> None:
        self._base_value = self.counter
        self._base_nanos = self.clock.nanos
        self._base_freq = value
        self._updated()

    @property
    def prescaler(self) -> float:
        return self._prescaler_value

    @prescaler.setter
    def prescaler(self, value: float) -> None:
        self._base_value = self.counter
        self._base_nanos = self.clock.nanos
        self._enabled = self._prescaler_value != 0
        self._prescaler_value = value
        self._updated()

    def to_nanos(self, cycles: int) -> float:
        base_freq = self._base_freq
        prescaler_value = self._prescaler_value
        return (cycles * 1e9) / (base_freq / prescaler_value)

    @property
    def enable(self) -> bool:
        return self._enabled

    @enable.setter
    def enable(self, value: bool) -> None:
        if value != self._enabled:
            if value:
                self._base_nanos = self.clock.nanos
            else:
                self._base_value = self.counter
            self._enabled = value
            self._updated()

    @property
    def mode(self) -> TimerMode:
        return self._timer_mode

    @mode.setter
    def mode(self, value: TimerMode) -> None:
        if self._timer_mode != value:
            counter = self.counter
            self._timer_mode = value
            self.set(counter)

    def _updated(self) -> None:
        for listener in self.listeners:
            listener()


class Timer32PeriodicAlarm:
    def __init__(self, timer: Timer32, callback: Callable[[], None]):
        self.timer = timer
        self.callback = callback
        self._target_value = 0
        self._enabled = False

        self._clock_alarm = self.timer.clock.create_alarm(self._handle_alarm)
        timer.listeners.append(self._update)

    @property
    def enable(self) -> bool:
        return self._enabled

    @enable.setter
    def enable(self, value: bool) -> None:
        if value != self._enabled:
            self._enabled = value
            if value and self.timer.enable:
                self._schedule()
            else:
                self._cancel()

    @property
    def target(self) -> int:
        return self._target_value

    @target.setter
    def target(self, value: int) -> None:
        if value == self._target_value:
            return
        self._target_value = value
        if self._enabled and self.timer.enable:
            self._cancel()
            self._schedule()

    def _handle_alarm(self) -> None:
        self.callback()
        if self._enabled and self.timer.enable:
            self._schedule()

    def _update(self) -> None:
        self._cancel()
        if self._enabled and self.timer.enable:
            self._schedule()

    def _schedule(self) -> None:
        timer, target_value = self.timer, self._target_value
        top, mode, raw_counter = timer.top, timer.mode, timer.raw_counter

        cycle_delta = target_value - raw_counter
        if mode == TimerMode.ZIGZAG and cycle_delta < 0:
            if cycle_delta < -top:
                cycle_delta += 2 * top
            else:
                cycle_delta = top * 2 - target_value - raw_counter

        if top != 0xFFFFFFFF:
            if cycle_delta <= 0:
                cycle_delta += top + 1
            if target_value > top:
                # Skip alarm
                return

        if mode == TimerMode.DECREMENT:
            cycle_delta = top + 1 - cycle_delta

        cycles_to_alarm = cycle_delta & 0xFFFFFFFF
        if not cycles_to_alarm:
            # "Now" is not a future alarm. Rescheduling at a zero delta livelocks the clock: the
            # callback fires again at the same instant, forever, inside a single `tick()` - the
            # counter never advances, so simulated time stops for the whole chip.
            #
            # Reachable whenever the counter is *sitting on* the target as this reschedules, which
            # is exactly what `_handle_alarm()` does right after firing. The watchdog is the case
            # that found it (`target = 0`, DECREMENT, `top = 0xFFFFFFFF`): a timeout froze the
            # emulator instead of resetting the chip, and MicroPython 1.16/1.17 reach it from
            # `machine.reset()` itself, because pico-sdk 1.2.0's `_watchdog_enable()` reboots by
            # loading a 50 ms timeout rather than writing CTRL.TRIGGER (added in a later SDK).
            #
            # A counter that is already at the target reaches it again one full wrap later, which
            # is what the hardware does and what this schedules.
            cycles_to_alarm = top + 1
        nanos_to_alarm = timer.to_nanos(cycles_to_alarm)
        self._clock_alarm.schedule(nanos_to_alarm)

    def _cancel(self) -> None:
        self._clock_alarm.cancel()
