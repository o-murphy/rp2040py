"""Unit tests for `Simulator._execute_batch()`'s per-batch step accounting (see docs/BACKLOG.md's
CDC performance investigation): a batch is supposed to bound each call to roughly a fixed amount
of *real* work before `execute()` yields back (`await asyncio.sleep(0)` between batches) - but a
WFI'd core jumping straight to the next clock alarm costs essentially nothing in real time no
matter how far away that alarm is, so weighting that jump by the simulated nanoseconds it covers
massively overcounts it against the budget. With a recurring short-period alarm (e.g. USB SOF,
every 1ms of sim time) that turned "device idle, waiting on an interrupt" into a real yield
roughly every 8ms of simulated time - thousands of avoidable yields, each exposed to real
scheduler jitter, for what should be a near-zero-cost wait.

Drives `_execute_batch()` directly rather than the `async def execute()` wrapper around it -
deterministic single-batch behavior without depending on asyncio scheduling order.
"""

from rp2040py.simulator import Simulator


def test_idle_core_advances_far_past_a_single_recurring_alarm_period_in_one_batch():
    """A WFI'd core with only a short recurring alarm (matching USB SOF's 1ms period) must not
    exhaust its step budget after just a handful of alarm firings - each idle jump should cost the
    same ~1 unit as a real instruction, not `nanos_jumped / cycle_nanos`."""
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

    simulator.stopped = False
    simulator._execute_batch()
    simulator.stop()

    # Before the fix, the idle branch added `period_nanos / cycle_nanos` (=125,000, for an 8ns
    # cycle at 125MHz) to the batch's 1,000,000-unit budget per firing, exhausting it after only
    # ~8 firings (~8ms of simulated time). The fix makes each firing cost ~1 unit, so a single
    # un-interrupted batch should cover far more firings than that.
    assert fire_count > 1000
    assert simulator.clock.nanos > 1000 * period_nanos
