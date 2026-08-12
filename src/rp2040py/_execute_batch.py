"""Pure-Python reference implementation of Simulator._execute_batch()'s own per-instruction
dispatch/idle loop (simulator.py). Public callers never import this module directly - see
execute_batch.py, the facade that picks this or the native port in native/_simulator.pyx
(mirrors cortex_m0_core.py/_cortex_m0_core.py's own facade split exactly).

Split out of Simulator itself (rather than staying a plain method body) so the native port has a
single, unambiguous reference to be measured against and kept in sync with by hand - the same
reason peripherals/_state_machine.py exists alongside peripherals/state_machine.py.
"""

import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from rp2040py.peripherals.pio import RPPIO
    from rp2040py.simulator import Simulator

# A purely-idle (WFI'd) batch can legitimately jump thousands of simulated-time units ahead per
# iteration for ~0 simulated-time cost, so the 1,000,000-iteration ceiling below stops bounding how
# much *real* wall-clock time one batch takes once nothing is stopping it from running all
# 1,000,000 idle iterations - upstream rp2040js hits the same ceiling per batch (see simulator.ts)
# but V8 clears 1,000,000 loop iterations in low milliseconds; CPython's per-iteration attribute-
# access/function-call overhead was measured (see docs/BACKLOG.md) taking ~0.9-1.8s for the same
# ceiling - long enough to stall anything sharing this Simulator's engine-room loop (e.g.
# StdioInteractiveRepl's add_reader() callback - a keystroke could sit unread for the entire
# batch). Bounding this isn't free either (a real WFI'd device should still advance as far as it
# can per batch for throughput), so this is checked only every _TIME_CHECK_INTERVAL iterations,
# not every one - time.monotonic() itself must not become the hot-path cost this is trying to
# avoid. Tracked from the start of the whole batch, unconditionally - not just during an
# uninterrupted idle run - since a real device idling at the REPL isn't purely WFI'd end to end
# (a periodic timer interrupt briefly flips core.waiting False for a handful of instructions
# before going back to waiting); an idle-run-only version of this check kept getting reset by
# those interludes and never fired in practice, see git history for the confirmed repro.
_BATCH_YIELD_BUDGET_SECONDS = 0.005
_TIME_CHECK_INTERVAL = 256


def execute_batch(simulator: "Simulator", tick_batch: int) -> None:
    """Runs up to 1,000,000 instructions/idle-jumps, or until `simulator.stopped` is set, or until
    the batch itself has eaten its own real-time budget (_BATCH_YIELD_BUDGET_SECONDS) - whichever
    comes first. Synchronous and self-contained (no `await` anywhere in here) so it can be driven
    directly, e.g. from a test that wants deterministic single-batch behavior without depending on
    asyncio scheduling order - see tests/test_simulator.py.

    `Simulator` (simulator.py) is imported only under TYPE_CHECKING to avoid a real import cycle
    (simulator.py imports this module's facade, execute_batch.py) - this function only ever
    touches `.rp2040`, `.clock`, and `.stopped`, structurally, the same three attributes
    native/_simulator.pyx's own execute_batch() reads."""
    rp2040, clock = simulator.rp2040, simulator.clock

    # Stepped once per loop iteration below (both the idle-jump and busy-instruction paths - real
    # PIO keeps running independent of CPU sleep state), the same "driven directly by this loop,
    # not a competing asyncio.Task" pattern clock.tick() itself already uses. Grabbed once here,
    # not re-read from rp2040.pio every iteration - a fixed 2-element list for the life of an
    # RP2040. See peripherals/pio.py's __init__/write_uint32() CTRL-branch comments and
    # docs/records/0037-pio-clock-coupled-stepping.md for why a separately-scheduled RPPIO.run()
    # task was a real bug here, not just a style choice.
    #
    # Only actually stepped below when at least one machine is enabled and not currently blocked
    # in a wait (`_has_runnable_machine()`) - a machine sitting in `waiting=True` gains nothing
    # from being re-`step()`'d every single instruction: every StateMachine wait type (IRQ, PIN,
    # RX_FIFO, TX_FIFO/OUT) already has its own targeted, event-driven re-check elsewhere
    # (RPPIO.irq_updated(), GPIOPin._apply_input_value(), StateMachine.read_fifo()/write_fifo()) -
    # those already flip `waiting` back to False the instant whatever it's blocked on actually
    # changes, and this loop picks the machine back up on its very next iteration once that
    # happens. Found live, not theorized: profiling a real CYW43 boot after this file's own first
    # version landed showed RPPIO.step()/check_changed_pins() as ~55% of profiled time in a 20s
    # window (14.3M calls) - PIO1's SM0 sat permanently `waiting=True` after a stalled gSPI
    # transaction (see 0027's own write-up) yet `pio.stopped` stays False (a real hardware SM
    # doesn't need to be explicitly disabled just because it's stalled), so every single
    # subsequent CPU instruction was paying full step() cost for a machine that could not
    # possibly make progress until an external event it wasn't currently waiting to be told
    # about anyway.
    def _has_runnable_machine(pio: "RPPIO") -> bool:
        for machine in pio.machines:
            if machine.enabled and not machine.waiting:
                return True
        return False

    pios = rp2040.pio
    cycle_nanos = 1e9 / 125_000_000  # 125 MHz
    i: float = 0
    batch_start = time.monotonic()
    ticks_since_check = 0
    # RP2040PY_CLOCK_TICK_BATCH (default 1 - see _clock_batch_gate.py's own module docstring
    # for why this stays opt-in): >1 lets the busy branch below accumulate several
    # instructions' worth of simulated nanoseconds into pending_nanos instead of calling the
    # real clock.tick() after every single one, flushing (the real tick(), firing any due
    # alarm exactly as it would have anyway) whenever nanos_budget would go non-positive or
    # tick_batch instructions have accumulated - whichever comes first - so a scheduled alarm
    # never fires later than it does today, only the (typically far more common, per a real
    # measured boot - see the same module docstring) no-alarm-imminent case skips repeated
    # full tick() calls. tick_batch==1 (the default) takes the exact same one-call-per-
    # instruction path as always, just with the redundant branch below.
    pending_nanos = 0.0
    pending_count = 0
    nanos_budget = clock.nanos_to_next_alarm if clock.has_scheduled_alarm else float("inf")
    while i < 1000000 and not simulator.stopped:
        # Checked every _TIME_CHECK_INTERVAL iterations regardless of idle/busy - not just
        # during an uninterrupted idle run. A real device idling at the REPL isn't purely
        # WFI'd end to end: a periodic timer interrupt (SysTick, watchdog, ...) briefly flips
        # core.waiting False for a handful of instructions before it goes back to waiting -
        # an earlier version of this check reset its clock whenever that happened, so it
        # never accumulated enough uninterrupted idle time to fire and the batch fell back to
        # running the full 1,000,000-iteration ceiling anyway (confirmed: a batch with a
        # busy interlude every ~50 idle ticks still took ~0.95s wall time with that version).
        # Tracking one clock from the start of the whole batch, unconditionally, doesn't have
        # that gap.
        ticks_since_check += 1
        if ticks_since_check >= _TIME_CHECK_INTERVAL:
            ticks_since_check = 0
            if time.monotonic() - batch_start > _BATCH_YIELD_BUDGET_SECONDS:
                # Free to keep going indefinitely in simulated time (see below) but not in
                # real time - stdio_repl.py's add_reader() callback shares this same
                # engine-room loop and only gets a turn between batches, so a batch that never
                # ends is indistinguishable from a hung keyboard. Breaking here just ends this
                # batch early; execute()'s own loop immediately starts the next one after
                # yielding, so nothing about idle state or instruction execution is lost.
                break
        if rp2040.core.waiting:
            if pending_nanos:
                # Flush whatever the busy branch below had accumulated first - the idle jump
                # right after must be relative to the real, ticked-forward clock, not stale by
                # however much was still pending.
                clock.tick(pending_nanos)
                pending_nanos = 0.0
                pending_count = 0
            # Jumping straight to the next alarm costs ~nothing in real time no matter how far
            # away it is (no instructions execute), so it must not be weighted by the simulated
            # nanoseconds it covers - that previously counted a single 1ms USB SOF-driven idle
            # tick as ~125,000 "instructions" against the budget above, exhausting a whole batch
            # in ~8 idle ticks and forcing a yield (see execute()'s `await asyncio.sleep(0)`)
            # roughly every 8ms of simulated idle time. A real WFI'd device sits idle almost all
            # the time once booted, so that turned "wait for USB-CDC output" into thousands of
            # avoidable yields - each one exposed to real scheduler jitter - which is what
            # actually produced the wildly variable wall-clock times noted in
            # docs/BACKLOG.md's CDC investigation, not anything USB-specific.
            clock.tick(clock.nanos_to_next_alarm)
            if tick_batch > 1:
                nanos_budget = clock.nanos_to_next_alarm if clock.has_scheduled_alarm else float("inf")
        else:
            cycles = rp2040.core.execute_instruction()
            delta_nanos = cycles * cycle_nanos
            if tick_batch <= 1:
                clock.tick(delta_nanos)
            else:
                pending_nanos += delta_nanos
                pending_count += 1
                nanos_budget -= delta_nanos
                if nanos_budget <= 0 or pending_count >= tick_batch:
                    clock.tick(pending_nanos)
                    pending_nanos = 0.0
                    pending_count = 0
                    nanos_budget = clock.nanos_to_next_alarm if clock.has_scheduled_alarm else float("inf")
        for pio in pios:
            if not pio.stopped and _has_runnable_machine(pio):
                pio.step()
        i += 1
    if pending_nanos:
        # End of batch (stopped, iteration ceiling, or the real-time yield budget above) with
        # something still un-flushed - must not leave simulated time silently behind by up to
        # a whole batch's worth of nanoseconds, e.g. for a caller reading clock.nanos right
        # after execute() yields.
        clock.tick(pending_nanos)
