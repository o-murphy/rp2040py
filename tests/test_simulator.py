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

import time

from rp2040py.simulator import Simulator


def test_idle_core_advances_far_past_a_single_recurring_alarm_period_in_one_batch(monkeypatch):
    """A WFI'd core with only a short recurring alarm (matching USB SOF's 1ms period) must not
    exhaust its step budget after just a handful of alarm firings - each idle jump should cost the
    same ~1 unit as a real instruction, not `nanos_jumped / cycle_nanos`."""
    # _execute_batch() bounds itself by real wall-clock time (_BATCH_YIELD_BUDGET_SECONDS,
    # checked via time.monotonic() every _TIME_CHECK_INTERVAL iterations) - exactly right for its
    # own job, but it means how many idle iterations fit in one batch depends on the host's CPU
    # speed/load, not on the thing this test actually checks (idle jump cost). Left as real
    # time.monotonic(), this was observed to intermittently fail on GitHub-hosted CI runners
    # (e.g. 767 firings on one run, below a hardcoded 1000 floor) despite the fix under test being
    # correct - a flaky threshold, not a real regression. Faking time.monotonic() to advance a
    # fixed amount per call removes the host-speed dependency entirely: every run sees the exact
    # same "elapsed" progression, so the number of firings before the budget trips is deterministic
    # regardless of how fast this machine happens to execute the loop body.
    fake_elapsed = iter(t * 0.0001 for t in range(1, 10_000))

    def _fake_monotonic() -> float:
        return next(fake_elapsed)

    monkeypatch.setattr(time, "monotonic", _fake_monotonic)

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
    # un-interrupted batch should cover far more firings than that - the fake clock above trips
    # the budget after ~50 checks (50 * 0.0001s > _BATCH_YIELD_BUDGET_SECONDS=0.005s), i.e. after
    # ~50 * _TIME_CHECK_INTERVAL(256) = 12,800 idle iterations, deterministically.
    min_expected_fires = 1000
    assert fire_count > min_expected_fires
    assert simulator.clock.nanos > min_expected_fires * period_nanos


def test_idle_core_yields_within_a_bounded_wall_clock_budget():
    """A purely-idle batch must return to execute()'s `await asyncio.sleep(0)` within roughly
    _IDLE_YIELD_BUDGET_SECONDS of real time, not whenever the 1,000,000-iteration ceiling happens
    to be reached - anything sharing this Simulator's engine-room loop (e.g.
    StdioInteractiveRepl's add_reader() callback) only gets a turn between batches, and CPython's
    per-iteration overhead makes 1,000,000 idle iterations take ~0.9-1.8s wall-clock (measured;
    upstream rp2040js hits the same 1,000,000 ceiling but V8 clears it in low milliseconds) -
    long enough that a keystroke typed during one batch would sit unread for the whole thing.
    See docs/BACKLOG.md's REPL keystroke-latency finding."""
    simulator = Simulator()
    rp2040 = simulator.rp2040
    rp2040.core.waiting = True

    period_nanos = 1_000_000  # 1ms, matching USBCTRL's SOF period

    def _on_alarm() -> None:
        alarm.schedule(period_nanos)

    alarm = simulator.clock.create_alarm(_on_alarm)
    alarm.schedule(period_nanos)

    simulator.stopped = False
    t0 = time.monotonic()
    simulator._execute_batch()
    elapsed = time.monotonic() - t0
    simulator.stop()

    # Generous upper bound (budget is 5ms) to absorb scheduler jitter on a loaded CI runner
    # without making this flaky - still an order of magnitude below the ~0.9-1.8s the unbounded
    # loop took.
    assert elapsed < 0.1


def test_batch_yields_within_budget_even_after_switching_from_idle_to_busy():
    """A real device idling at the REPL isn't purely WFI'd end to end - a periodic timer
    interrupt (SysTick, watchdog, ...) briefly flips core.waiting False before it eventually goes
    back to waiting. An earlier version of this bound tracked only an *uninterrupted* idle run's
    own elapsed time: the moment the core stopped waiting even once, that tracker reset to None
    and - since a real core rarely returns to waiting within the same batch once it starts
    executing real instructions - never got a chance to fire again for the rest of the batch,
    silently falling back to the full 1,000,000-iteration ceiling (confirmed: a batch that flips
    busy partway through still took ~0.95s wall time with that version). The budget must be
    tracked from the start of the whole batch instead, regardless of idle/busy transitions.

    core.pc is pointed at zeroed SRAM (matches test_instructions.py's own pattern) so the busy
    branch's real execute_instruction() call decodes a harmless all-zero opcode
    (`movs r0, r0`) instead of needing a fake."""
    simulator = Simulator()
    rp2040 = simulator.rp2040
    rp2040.core.pc = 0x20000000
    rp2040.core.waiting = True

    period_nanos = 1_000_000  # 1ms, matching USBCTRL's SOF period
    fire_count = 0

    def _on_alarm() -> None:
        nonlocal fire_count
        fire_count += 1
        if fire_count == 50:
            # From here on the core stays busy (harmless no-ops through zeroed SRAM) rather than
            # ever going back to waiting - the worst case for an idle-run-only budget, and exactly
            # what a real interrupt handler that doesn't re-arm WFI mid-batch looks like from this
            # loop's perspective.
            rp2040.core.waiting = False
        else:
            alarm.schedule(period_nanos)

    alarm = simulator.clock.create_alarm(_on_alarm)
    alarm.schedule(period_nanos)

    simulator.stopped = False
    t0 = time.monotonic()
    simulator._execute_batch()
    elapsed = time.monotonic() - t0
    simulator.stop()

    assert elapsed < 0.1
