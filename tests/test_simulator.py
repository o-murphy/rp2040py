"""Unit tests for `Simulator.execute()`'s per-batch step accounting (see docs/BACKLOG.md's CDC
performance investigation): the loop is supposed to bound each call to roughly a fixed amount of
*real* work before yielding back via `threading.Timer` (so external `stop()` calls can get in) -
but a WFI'd core jumping straight to the next clock alarm costs essentially nothing in real time no
matter how far away that alarm is, so weighting that jump by the simulated nanoseconds it covers
massively overcounts it against the budget. With a recurring short-period alarm (e.g. USB SOF,
every 1ms of sim time) that turned "device idle, waiting on an interrupt" into a real OS-thread
handoff roughly every 8ms of simulated time - thousands of avoidable handoffs, each exposed to real
scheduler jitter, for what should be a near-zero-cost wait.
"""

import threading

import pytest

from rp2040py.simulator import Simulator


class _CountingTimer:
    """Stand-in for `threading.Timer` that only counts constructions instead of actually spawning
    a thread - a real reschedule would start a live, non-daemon thread that keeps calling
    `execute()` (which resets `self.stopped = False`) forever in the background, well past
    whichever test triggered it."""

    created = 0

    def __init__(self, *args, **kwargs):
        _CountingTimer.created += 1

    def start(self) -> None:
        pass

    def cancel(self) -> None:
        pass


@pytest.fixture
def no_op_timer(monkeypatch):
    _CountingTimer.created = 0
    monkeypatch.setattr(threading, "Timer", _CountingTimer)
    return _CountingTimer


def test_idle_core_advances_far_past_a_single_recurring_alarm_period_in_one_batch(no_op_timer):
    """A WFI'd core with only a short recurring alarm (matching USB SOF's 1ms period) must not
    exhaust its step budget after just a handful of alarm firings, and must not need a
    `threading.Timer`/OS-thread handoff to get past them - each idle jump should cost the same ~1
    unit as a real instruction, not `nanos_jumped / cycle_nanos`."""
    simulator = Simulator()
    rp2040 = simulator.rp2040
    rp2040.core.waiting = True

    period_nanos = 1_000_000  # 1ms, matching USBCTRL's SOF period
    fire_count = 0

    def _on_alarm() -> None:
        nonlocal fire_count
        fire_count += 1
        alarm.schedule(period_nanos)

    alarm = simulator.clock.create_alarm(_on_alarm)
    alarm.schedule(period_nanos)

    simulator.execute()
    simulator.stop()

    # Before the fix, the idle branch added `period_nanos / cycle_nanos` (=125,000, for an 8ns
    # cycle at 125MHz) to the loop's 1,000,000-unit budget per firing, exhausting a batch after
    # only ~8 firings (~8ms of simulated time) and forcing a handoff on every one of them. The fix
    # makes each firing cost ~1 unit, so a single un-interrupted batch should cover far more firings
    # than that, with only the one expected end-of-batch reschedule.
    assert fire_count > 1000
    assert simulator.clock.nanos > 1000 * period_nanos
    assert no_op_timer.created == 1
