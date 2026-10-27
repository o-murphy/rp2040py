"""Pure-Python reference implementation of Simulator._execute_batch()'s own per-instruction
dispatch/idle loop (simulator.py). Public callers never import this module directly - see
execute_batch.py, the facade that picks this or the native port in native/_simulator.pyx
(mirrors cortex_m0_core.py/_cortex_m0_core.py's own facade split exactly).

Split out of Simulator itself (rather than staying a plain method body) so the native port has a
single, unambiguous reference to be measured against and kept in sync with by hand - the same
reason peripherals/_state_machine.py exists alongside peripherals/state_machine.py.
"""

import math
import time
from typing import TYPE_CHECKING

from rp2040py.clock._simulation_clock import TICKS_PER_NANO, nanos_to_ticks

# Where LR points when core 1 starts (see `core/batch_dual.hpp`, `kCore1RomReturn`): an entry function that returns lands back in the boot ROM's `_wait_for_vector`.
CORE1_ROM_RETURN = 0x00003FFC

if TYPE_CHECKING:
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
# While a device waits on the real world (`Simulator.real_io_begin()`), an idle jump is capped at this much simulated time and ends the batch, so
# `Simulator.execute()` can sleep for it: simulated time may not outrun the wall clock while a real reply is on its way. Mirrored in native/_simulator.pyx.
_PACED_IDLE_NANOS = 1_000_000.0


def execute_batch(simulator: "Simulator", tick_batch: int) -> None:
    """One batch (see `_run_batch`), ended early by a change of `clk_sys`: the CPU's time per instruction is `1 / clk_sys` (docs/tasks/clocks-port.md, decision 2), the batch fixes it at its start,
    and a firmware that sets the clock tree mid-batch must see the new rate on the very next instruction - a clock listener raises the flag the loop checks, and the next batch starts at the new rate."""
    rp2040 = simulator.rp2040
    changed = [False]
    unsubscribe = rp2040.add_clock_listener(lambda _new, _old: changed.__setitem__(0, True))
    rp2040.batch_exit = (
        changed  # a launch or a hold of core 1 ends the batch too: the next one is run by the other loop
    )
    try:
        _run_batch(simulator, tick_batch, changed)
    finally:
        rp2040.batch_exit = None
        unsubscribe()


def _run_batch(simulator: "Simulator", tick_batch: int, changed: list[bool]) -> None:
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
    if rp2040.core1_running and not rp2040.run_pin_low:
        _run_batch_dual(
            simulator, changed
        )  # two cores (record 0104): the loop below is the single-core one and is not touched
        return
    real_io = getattr(
        simulator, "_real_io_flag", None
    )  # one byte, nonzero while a device waits on the real world (see _PACED_IDLE_NANOS)

    if rp2040.run_pin_low:
        # RUN held low: the chip is in reset and does nothing at all - no instruction, no PIO
        # step, and no simulated time (docs/records/0089-one-reset-for-every-trigger.md Phase 4,
        # which is 0057's option C). A third execution state, distinct from both of the ones
        # already here: `core.waiting` is a WFI'd *running* chip that still services alarms and is
        # cleared by an IRQ, and `simulator.stopped` ends the engine room entirely. Here the
        # emulator is still running and the chip is held, exactly as a finger on RESET holds it.
        #
        # Checked once per batch rather than per iteration because the level cannot change during
        # one: `ResetButton` hands press()/release() to this same engine-room loop via
        # schedule_threadsafe(), so a change can only land between batches. Returning immediately
        # would spin the loop - `Simulator.execute()` sleeps instead of yielding while this is set.
        return

    # Stepped once per loop iteration below (both the idle-jump and busy-instruction paths - real
    # PIO keeps running independent of CPU sleep state), the same "driven directly by this loop,
    # not a competing asyncio.Task" pattern clock.tick() itself already uses. Grabbed once here,
    # not re-read from rp2040.pio every iteration - a fixed 2-element list for the life of an
    # RP2040. See peripherals/pio.py's __init__/write_uint32() CTRL-branch comments and
    # docs/records/0037-pio-clock-coupled-stepping.md for why a separately-scheduled RPPIO.run()
    # task was a real bug here, not just a style choice.
    #
    # Advanced by the *cycles* each CPU instruction cost, not once per instruction: PIO runs off
    # the system clock, so a state machine executes when its own `SM_CLKDIV`-and-`[delay]` due
    # time arrives, which `RPPIO.advance()` owns end to end (docs/records/0063). The common case
    # there is a single integer compare - it subsumes, and is cheaper than, the
    # `_has_runnable_machine()` scan this replaced. That scan existed because a machine sitting in
    # `waiting=True` gains nothing from being re-`step()`'d every single instruction: every
    # StateMachine wait type (IRQ, PIN, RX_FIFO, TX_FIFO/OUT) already has its own targeted,
    # event-driven re-check elsewhere (RPPIO._irq_updated(), GPIOPin._apply_input_value(),
    # StateMachine.read_fifo()/write_fifo()), and those flip `waiting` back to False the instant
    # whatever it's blocked on actually changes. Found live, not theorized: profiling a real CYW43
    # boot after this file's own first version landed showed RPPIO.step()/_check_changed_pins() as
    # ~55% of profiled time in a 20s window (14.3M calls) - PIO1's SM0 sat permanently
    # `waiting=True` after a stalled gSPI transaction (see 0027's own write-up) yet `pio.stopped`
    # stays False (a real hardware SM doesn't need to be explicitly disabled just because it's
    # stalled). A stalled machine is now simply excluded from `RPPIO.next_due_fp` instead, and
    # `check_wait()` lowers it again the moment the stall ends.
    pios = rp2040.pio
    cycle_nanos = 1e9 / rp2040.clk_sys  # one system clock: 125 MHz until firmware configures the clock tree
    # One system clock in ticks of 1/16 ns: not a whole number in general (133 MHz is 120.30), so time advances in CYCLES with the fraction carried by the clock (docs/records/0100).
    ticks_per_cycle = cycle_nanos * TICKS_PER_NANO
    # The common case (125 MHz: 128 ticks) is a whole number of ticks per cycle: plain integer arithmetic, no remainder to carry.
    cycle_ticks = int(ticks_per_cycle) if ticks_per_cycle == int(ticks_per_cycle) else 0
    i: float = 0
    batch_start = time.monotonic()
    ticks_since_check = 0
    # RP2040PY_CLOCK_TICK_BATCH (default 1 - see _clock_batch_gate.py's own module docstring
    # for why this stays opt-in): >1 lets the busy branch below accumulate several
    # instructions' worth of simulated cycles into pending_cycles instead of calling the
    # real clock.tick() after every single one, flushing (the real tick(), firing any due
    # alarm exactly as it would have anyway) whenever tick_budget would go non-positive or
    # tick_batch instructions have accumulated - whichever comes first - so a scheduled alarm
    # never fires later than it does today, only the (typically far more common, per a real
    # measured boot - see the same module docstring) no-alarm-imminent case skips repeated
    # full tick() calls. tick_batch==1 (the default) takes the exact same one-call-per-
    # instruction path as always, just with the redundant branch below.
    pending_cycles = 0
    pending_count = 0
    idle_carry = 0.0  # idle time, in ticks, not yet handed to the PIO as whole cycles
    tick_budget = clock.ticks_to_next_alarm if clock.has_scheduled_alarm else None
    while i < 1000000 and not simulator.stopped and not changed[0]:
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
        paced = False
        if rp2040.core.waiting:
            if pending_cycles:
                # Flush whatever the busy branch below had accumulated first - the idle jump
                # right after must be relative to the real, ticked-forward clock, not stale by
                # however much was still pending.
                (
                    clock.tick_ticks(pending_cycles * cycle_ticks)
                    if cycle_ticks
                    else clock.advance_cycles(pending_cycles, ticks_per_cycle)
                )
                pending_cycles = 0
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
            idle_ticks = clock.ticks_to_next_alarm
            if real_io is not None and real_io[0]:
                idle_ticks = min(idle_ticks, nanos_to_ticks(_PACED_IDLE_NANOS))
                paced = True
            clock.tick_ticks(idle_ticks)
            # Real PIO keeps running while the CPU sleeps, so the jump has to be handed to it as
            # the system clocks it actually covers - not as the single step this used to give it
            # regardless of how far the jump went. `MAX_CATCHUP_STEPS` bounds what that can cost.
            # ...with a floor of one cycle: an idle jump with *no* alarm scheduled at all
            # advances simulated time by nothing at all (`nanos_to_next_alarm` is 0), and a PIO
            # frozen solid whenever the CPU parks in WFI with an empty alarm queue could never
            # make the progress that would let the CPU wake up again - which is exactly how a
            # DMA-fed state machine is driven with no ARM program loaded (tests/test_pio.py).
            # The fraction of a cycle a jump leaves is carried to the next jump, as the CPU's own time carries it (`advance_cycles`): flooring each jump on its own dropped it every time (record 0105, finding 2).
            idle_carry += idle_ticks
            cycles = int(idle_carry / ticks_per_cycle)
            idle_carry -= cycles * ticks_per_cycle
            cycles = cycles or 1
            if tick_batch > 1:
                tick_budget = clock.ticks_to_next_alarm if clock.has_scheduled_alarm else None
        else:
            cycles = rp2040.core.execute_instruction()
            if tick_batch <= 1:
                (
                    clock.defer_ticks(cycles * cycle_ticks)
                    if cycle_ticks
                    else clock.advance_cycles(cycles, ticks_per_cycle)
                )
            else:
                pending_cycles += cycles
                pending_count += 1
                if tick_budget is not None:
                    # rounded UP: an alarm is never flushed later than it is due, only (by under a tick) earlier
                    tick_budget -= math.ceil(cycles * ticks_per_cycle)
                if (tick_budget is not None and tick_budget <= 0) or pending_count >= tick_batch:
                    (
                        clock.tick_ticks(pending_cycles * cycle_ticks)
                        if cycle_ticks
                        else clock.advance_cycles(pending_cycles, ticks_per_cycle)
                    )
                    pending_cycles = 0
                    pending_count = 0
                    tick_budget = clock.ticks_to_next_alarm if clock.has_scheduled_alarm else None
        for pio in pios:
            if not pio.stopped:
                pio.advance(cycles)
        i += 1
        if paced:
            break  # one capped idle jump per batch: the caller sleeps for the simulated time it covered
    if pending_cycles:
        # End of batch (stopped, iteration ceiling, or the real-time yield budget above) with
        # something still un-flushed - must not leave simulated time silently behind by up to
        # a whole batch's worth of nanoseconds, e.g. for a caller reading clock.nanos right
        # after execute() yields.
        (
            clock.tick_ticks(pending_cycles * cycle_ticks)
            if cycle_ticks
            else clock.advance_cycles(pending_cycles, ticks_per_cycle)
        )


def _run_batch_dual(simulator: "Simulator", changed: list[bool]) -> None:
    """The batch loop for two cores on one clock: the Python reference of `core/batch_dual.hpp` (record 0104, D5 and the scheduling decision: F2 main, A strict), held to it by the lockstep oracle.

    Each core has its own local time. The cores run in quanta: from the clock's time to a horizon (`rp2040.quantum_cycles` cycles, cut at the next alarm), core 0 and then core 1 execute
    instructions until their local time reaches it, and the clock is ticked to the earlier of the two. Every access that leaves RAM and flash - the SIO, the PPB, every peripheral - is a
    SYNCHRONISATION POINT (`rp2040._sync`): before it happens the other core, if it is behind, executes up to this core's time (a nested catch-up), and the clock and the PIO are brought to
    it, so the access is ordered by time exactly as the exact scheduler (quantum 1) would order it. Alarms and the PIO advance by the clock's delta, the PIO by whole cycles of it."""
    rp2040, clock = simulator.rp2040, simulator.clock
    cores = rp2040.cores
    pios = rp2040.pio
    real_io = getattr(simulator, "_real_io_flag", None)
    ticks_per_cycle = 1e9 / rp2040.clk_sys * TICKS_PER_NANO
    cycle_ticks = int(ticks_per_cycle) if ticks_per_cycle == int(ticks_per_cycle) else 0
    quantum = max(1, rp2040.quantum_cycles) * (cycle_ticks if cycle_ticks else int(ticks_per_cycle))
    start = clock.ticks
    local = [start, start]  # each core's own time, in clock ticks
    wake_at = [
        start,
        start,
    ]  # the earliest time a core may run again: woken by the other (a FIFO word, a SEV) or by an alarm, it did not run before
    frac = [0.0, 0.0]  # the fraction of a tick a cycle that is not a whole number of ticks leaves, per core
    pio_carry = [0.0]  # clock ticks not yet handed to the PIO as whole cycles
    horizon = [0]
    current = [0]  # the core whose instruction is running
    iterations = 0

    def ticks_for(core: int, cycles: int) -> int:
        if cycle_ticks:
            return cycles * cycle_ticks
        frac[core] += cycles * ticks_per_cycle
        whole = math.floor(frac[core])
        frac[core] -= whole
        return whole

    def step_pio(ticks: int) -> None:
        if not pios:
            return
        pio_carry[0] += ticks
        cycles = int(pio_carry[0] / ticks_per_cycle)
        if cycles <= 0:
            return
        pio_carry[0] -= cycles * ticks_per_cycle
        for pio in pios:
            if not pio.stopped:
                pio.advance(cycles)

    def advance_to(t: int) -> None:
        dt = t - clock.ticks
        if dt > 0:
            clock.tick_ticks(dt)
            step_pio(dt)

    def note_wake() -> None:
        now = clock.ticks
        for i in range(2):
            wake_at[i] = max(wake_at[i], now)

    parked = [
        False
    ]  # core 1 returned from its entry function to the boot ROM (CORE1_ROM_RETURN): the batch ends and the chip puts it back to the ROM's wait

    def execute_one(core: int) -> None:
        nonlocal iterations
        if core == 1 and (cores[1].pc & ~1) == CORE1_ROM_RETURN:
            cores[1].waiting = True
            parked[0] = True
            return
        saved = current[0]
        current[0] = core
        rp2040.initiator = core
        try:
            cycles = cores[core].execute_instruction()
        finally:
            current[0] = saved
            rp2040.initiator = saved
        local[core] += ticks_for(core, cycles)
        iterations += 1

    def sync() -> None:
        me = current[0]
        other = 1 - me
        if not cores[other].waiting:
            local[other] = max(local[other], wake_at[other])
            while local[other] < local[me] and not cores[other].waiting:
                execute_one(other)
        advance_to(local[me])
        if clock.has_scheduled_alarm:
            horizon[0] = min(horizon[0], clock.ticks + clock.ticks_to_next_alarm)
        note_wake()
        # the access itself may wake the other core (a FIFO word, a SEV): it cannot have run before now
        wake_at[other] = max(wake_at[other], local[me])

    batch_start = time.monotonic()
    ticks_since_check = 0
    rp2040._sync = sync
    try:
        while iterations < 1000000 and not simulator.stopped and not changed[0] and not parked[0]:
            ticks_since_check += 1
            if ticks_since_check >= _TIME_CHECK_INTERVAL:
                ticks_since_check = 0
                if time.monotonic() - batch_start > _BATCH_YIELD_BUDGET_SECONDS:
                    break
            now = clock.ticks
            if (
                cores[0].waiting and cores[1].waiting
            ):  # both asleep: jump to the next alarm, as the single-core loop does (the PIO gets the cycles it covers, at least one)
                idle_ticks = clock.ticks_to_next_alarm if clock.has_scheduled_alarm else 0
                paced = False
                if real_io is not None and real_io[0]:
                    idle_ticks = min(idle_ticks, nanos_to_ticks(_PACED_IDLE_NANOS))
                    paced = True
                clock.tick_ticks(idle_ticks)
                step_pio(idle_ticks if idle_ticks > 0 else int(ticks_per_cycle))
                note_wake()
                iterations += 1
                if paced:
                    break
                continue
            # A quantum: to the horizon, cut at the next alarm, at least one cycle long.
            horizon[0] = now + quantum
            if clock.has_scheduled_alarm:
                horizon[0] = min(horizon[0], now + clock.ticks_to_next_alarm)
            horizon[0] = max(horizon[0], now + (cycle_ticks if cycle_ticks else 1))
            for core in range(2):
                if cores[core].waiting:
                    continue
                local[core] = max(local[core], now)
                local[core] = max(local[core], wake_at[core])
                while local[core] < horizon[0] and not cores[core].waiting and iterations < 1000000:
                    execute_one(core)
            # The clock goes to the earlier of the cores still running (a core that fell asleep is not waited for); if both did, to the later of the two sleeps.
            running = [local[core] for core in range(2) if not cores[core].waiting]
            advance_to(min(running) if running else max(local))
            note_wake()
    finally:
        rp2040._sync = None
        rp2040.initiator = 0
    if parked[0]:
        rp2040.hold_core1()  # (core 1 waits to be launched again, as after a reset)
