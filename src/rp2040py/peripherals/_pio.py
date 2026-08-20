import asyncio
from typing import TYPE_CHECKING

from rp2040py.peripherals.peripheral import BasePeripheral
from rp2040py.peripherals.pio_registers import (
    CTRL,
    DBG_CFGINFO,
    DBG_PADOE,
    DBG_PADOUT,
    DREQ_RX0,
    DREQ_RX1,
    DREQ_TX0,
    DREQ_TX1,
    FDEBUG,
    FLEVEL,
    FSTAT,
    INPUT_SYNC_BYPASS,
    INSTR_MEM0,
    INSTR_MEM31,
    INTR,
    IRQ,
    IRQ0_INTE,
    IRQ0_INTF,
    IRQ0_INTS,
    IRQ1_INTE,
    IRQ1_INTF,
    IRQ1_INTS,
    IRQ_FORCE,
    RXF0,
    RXF1,
    RXF2,
    RXF3,
    SM0_CLKDIV,
    SM0_PINCTRL,
    SM1_CLKDIV,
    SM1_PINCTRL,
    SM2_CLKDIV,
    SM2_PINCTRL,
    SM3_CLKDIV,
    SM3_PINCTRL,
    TXF0,
    TXF1,
    TXF2,
    TXF3,
)
from rp2040py.peripherals.state_machine import StateMachine

if TYPE_CHECKING:
    from rp2040py.rp2040 import RP2040

__all__ = ("RPPIO",)

# "No enabled, unstalled machine has anything to do" - larger than any cycle count a simulated
# RP2040 can reach (2**54 system clocks at 125 MHz is ~4.5 years of simulated time, and this is
# the 1/256ths of one), small enough to leave headroom under a C `long long` in the native twin.
NEVER_DUE = 1 << 62

# How far behind its own due time a state machine may fall before the backlog is written off and
# it is re-armed from the current cycle, in 1/256ths of a system clock. Comfortably more than the
# handful of cycles any one CPU instruction costs (so ordinary running keeps an exact schedule and
# never drifts), and far less than an idle jump, which goes straight to the next scheduled alarm -
# possibly a millisecond, i.e. 125,000 cycles, away. See advance() for why that backlog can never
# be walked through anyway. docs/records/0063.
MAX_ARREARS_FP = 8 << 8


class RPPIO(BasePeripheral):
    def __init__(self, rp2040: "RP2040", name: str, first_irq: int, index: int):
        super().__init__(rp2040, name)
        self.first_irq = first_irq
        self.index = index

        self.instructions = [0] * 32
        self.dreq_rx = DREQ_RX1 if self.index else DREQ_RX0
        self.dreq_tx = DREQ_TX1 if self.index else DREQ_TX0
        self.machines = [StateMachine(self.rp2040, self, i) for i in range(4)]

        # No lock needed here (there used to be one - see git history / CHANGELOG for the real,
        # reproduced race a background threading.Timer caused): run()'s continuation is now an
        # asyncio coroutine scheduled on Simulator's own engine-room event loop
        # (rp2040py.simulator.Simulator._ensure_loop()), the same loop write_uint32() itself always
        # runs on (it's only ever called from execute_instruction()'s bus dispatch, which only ever
        # runs inside Simulator.execute() on that loop). Exactly one thread can reach this state
        # now, by construction - not "races are unlikely," structurally impossible.
        #
        # Only used when this RP2040 has no owning Simulator at all (rp2040.simulator is None -
        # e.g. tests/test_pio.py's fixture, driving RPPIO directly with its own bounded event
        # loop). When a real Simulator does own this RP2040, its own _execute_batch()
        # (_execute_batch.py/native/_simulator.pyx) steps every non-stopped RPPIO once per CPU
        # instruction/idle-jump directly - see write_uint32()'s CTRL branch below for why a
        # separately-scheduled task there was a real bug, not just a style choice: a competing
        # asyncio.Task only gets a turn once per up-to-1,000,000-instruction CPU batch, so real
        # firmware bit-banging a slow peripheral (CYW43's gSPI over PIO+DMA) could never make
        # enough real progress before its own timeout-and-retry logic gave up and restarted it -
        # a genuine livelock in native (fast CPU dispatch) mode, not just slow. Full derivation in
        # docs/records/0037-pio-clock-coupled-stepping.md.
        self._run_task: asyncio.Task[None] | None = None

        self.stopped = True
        # System-clock cycles elapsed, in 1/256ths so a fractional CLKDIV needs no float anywhere
        # (docs/records/0063). advance() moves it; the machines' own next_due_fp is on the same
        # scale, and next_due_fp here is the earliest of theirs - the single integer compare that
        # decides whether any state machine has to be touched at all this cycle.
        self.cycle_fp = 0
        self.next_due_fp = NEVER_DUE
        # Counts machine-steps that arrived so late their backlog was written off (see
        # _run_due()) - a diagnostic for "how much of this waveform did a sleeping CPU eat".
        self.backlog_drops = 0
        self.fdebug = 0
        self.tx_stall = 0
        self.rx_stall = 0
        self.input_sync_bypass = 0
        self.irq = 0
        self.pin_values = 0
        self.pin_directions = 0
        self.old_pin_values = 0
        self.old_pin_directions = 0

        self.irq0_int_enable = 0
        self.irq0_int_force = 0
        self.irq1_int_enable = 0
        self.irq1_int_force = 0

    @property
    def int_raw(self) -> int:
        return (
            ((self.irq & 0xF) << 8)
            | (0x80 if not self.machines[3].tx_fifo.full else 0)
            | (0x40 if not self.machines[2].tx_fifo.full else 0)
            | (0x20 if not self.machines[1].tx_fifo.full else 0)
            | (0x10 if not self.machines[0].tx_fifo.full else 0)
            | (0x08 if not self.machines[3].rx_fifo.empty else 0)
            | (0x04 if not self.machines[2].rx_fifo.empty else 0)
            | (0x02 if not self.machines[1].rx_fifo.empty else 0)
            | (0x01 if not self.machines[0].rx_fifo.empty else 0)
        )

    @property
    def irq0_int_status(self) -> int:
        return (self.int_raw & self.irq0_int_enable) | self.irq0_int_force

    @property
    def irq1_int_status(self) -> int:
        return (self.int_raw & self.irq1_int_enable) | self.irq1_int_force

    def read_uint32(self, offset: int) -> int:
        if SM0_CLKDIV <= offset <= SM0_PINCTRL:
            return self.machines[0].read_uint32(offset - SM0_CLKDIV)
        if SM1_CLKDIV <= offset <= SM1_PINCTRL:
            return self.machines[1].read_uint32(offset - SM1_CLKDIV)
        if SM2_CLKDIV <= offset <= SM2_PINCTRL:
            return self.machines[2].read_uint32(offset - SM2_CLKDIV)
        if SM3_CLKDIV <= offset <= SM3_PINCTRL:
            return self.machines[3].read_uint32(offset - SM3_CLKDIV)

        if offset == CTRL:
            return (
                (1 << 0 if self.machines[0].enabled else 0)
                | (1 << 1 if self.machines[1].enabled else 0)
                | (1 << 2 if self.machines[2].enabled else 0)
                | (1 << 3 if self.machines[3].enabled else 0)
            )
        if offset == FSTAT:
            return (
                self.machines[0].fifo_stat
                | self.machines[1].fifo_stat
                | self.machines[2].fifo_stat
                | self.machines[3].fifo_stat
            )
        if offset == FDEBUG:
            return self.fdebug
        if offset == FLEVEL:
            return (
                (self.machines[0].tx_fifo.item_count & 0xF)
                | ((self.machines[0].rx_fifo.item_count & 0xF) << 4)
                | ((self.machines[1].tx_fifo.item_count & 0xF) << 8)
                | ((self.machines[1].rx_fifo.item_count & 0xF) << 12)
                | ((self.machines[2].tx_fifo.item_count & 0xF) << 16)
                | ((self.machines[2].rx_fifo.item_count & 0xF) << 20)
                | ((self.machines[3].tx_fifo.item_count & 0xF) << 24)
                | ((self.machines[3].rx_fifo.item_count & 0xF) << 28)
            )

        if offset == RXF0:
            return self.machines[0].read_fifo()
        if offset == RXF1:
            return self.machines[1].read_fifo()
        if offset == RXF2:
            return self.machines[2].read_fifo()
        if offset == RXF3:
            return self.machines[3].read_fifo()
        if offset == IRQ:
            return self.irq
        if offset == IRQ_FORCE:
            return 0
        if offset == INPUT_SYNC_BYPASS:
            return self.input_sync_bypass
        if offset == DBG_PADOUT:
            return self.pin_values
        if offset == DBG_PADOE:
            return self.pin_directions
        if offset == DBG_CFGINFO:
            return 0x200404
        if offset == INTR:
            return self.int_raw
        if offset == IRQ0_INTE:
            return self.irq0_int_enable
        if offset == IRQ0_INTF:
            return self.irq0_int_force
        if offset == IRQ0_INTS:
            return self.irq0_int_status
        if offset == IRQ1_INTE:
            return self.irq1_int_enable
        if offset == IRQ1_INTF:
            return self.irq1_int_force
        if offset == IRQ1_INTS:
            return self.irq1_int_status
        return super().read_uint32(offset)

    def write_uint32(self, offset: int, value: int) -> None:
        if INSTR_MEM0 <= offset <= INSTR_MEM31:
            index = (offset - INSTR_MEM0) >> 2
            self.instructions[index] = value & 0xFFFF
            return
        if SM0_CLKDIV <= offset <= SM0_PINCTRL:
            self.machines[0].write_uint32(offset - SM0_CLKDIV, value)
            return
        if SM1_CLKDIV <= offset <= SM1_PINCTRL:
            self.machines[1].write_uint32(offset - SM1_CLKDIV, value)
            return
        if SM2_CLKDIV <= offset <= SM2_PINCTRL:
            self.machines[2].write_uint32(offset - SM2_CLKDIV, value)
            return
        if SM3_CLKDIV <= offset <= SM3_PINCTRL:
            self.machines[3].write_uint32(offset - SM3_CLKDIV, value)
            return

        if offset == CTRL:
            for index in range(4):
                machine = self.machines[index]
                enabled = bool(value & (1 << index))
                if enabled and not machine.enabled:
                    # Starts running now, not at whatever due time it left behind last time it was
                    # disabled (which may be arbitrarily far in the past - docs/records/0063).
                    machine.next_due_fp = self.cycle_fp
                machine.enabled = enabled
                if value & (1 << (4 + index)):
                    machine.restart()
                if value & (1 << (8 + index)):
                    machine.clk_div_restart()
            self.recompute_due()
            should_run = value & 0xF
            if self.stopped and should_run:
                self.stopped = False
                # **Only run the first batch synchronously inline when no Simulator owns this
                # RP2040** (docs/records/0043-pio-dma-first-batch-race.md - found investigating
                # 0027's v1.23.0-vs-v1.28.0 CYW43 regression, same "shared simulator
                # infrastructure, not CYW43-specific" theme as 0037, which this directly follows
                # up). The comment this replaces justified running `_step_batch()` (up to 1000 PIO
                # steps) synchronously, right here inside the MMIO write itself, as a convenience -
                # "most PIO programs are short enough that a caller reading FIFO/register state
                # right after this write already sees the result, with no yield in between." That's
                # true for a program with no other peripheral dependency, but a DMA-fed transfer
                # (exactly `cyw43_bus_pio_spi.c`'s own gSPI TX, this project's own paced-by-DREQ
                # `RPDMAChannel`/`peripherals/dma.py`) needs `SimulationClock` alarms to fire
                # between FIFO drains to keep refilling it - and those alarms only fire from
                # `clock.tick()`, called once per CPU instruction by
                # `_execute_batch.py`/`native/_simulator.pyx`'s own outer loop, *never* from inside
                # this write handler's own call stack. A up-to-1000-step synchronous burst here can
                # drain a DMA-fed PIO TX FIFO (4 words deep, ~64 steps per 32-bit word for
                # `cyw43_bus_pio_spi.pio`'s bit-at-a-time program - so a couple hundred steps, well
                # under 1000) faster than any DMA alarm gets a chance to run, since none of those
                # alarms are serviced during this burst - producing a real (if premature and
                # transient) `FDEBUG_TXSTALL` the moment the FIFO runs dry mid-transfer, which a
                # driver polling `PIO.FDEBUG` directly (as `cyw43_spi_transfer()`'s TX-only branch
                # in the pico-sdk pinned by MicroPython v1.23.0's `cyw43-driver` does) reads as "the
                # whole transfer is done" after only the first few words - not what MicroPython
                # v1.28.0's newer pico-sdk observes, since it inserts
                # `dma_channel_wait_for_finish_blocking()` (polling the DMA channel's own transfer
                # count, not FDEBUG, via ordinary CPU instructions that a real Simulator's own
                # `_execute_batch()` loop already correctly interleaves with `clock.tick()`) before
                # ever reaching the same FDEBUG poll, by which point the real transfer has already
                # finished - masking the identical race rather than avoiding it. Skipping the
                # synchronous burst here and letting `_execute_batch()`'s own "step every
                # non-stopped RPPIO once per CPU instruction" loop (see __init__'s `_run_task`
                # comment) pick this SM up on its very next iteration - exactly how the >1000-step
                # "continuation" already had to work - guarantees `clock.tick()` runs between every
                # single PIO step whenever a real Simulator owns this RP2040, so a DMA-fed FIFO can
                # never be observably outrun. Only `tests/test_pio.py`'s no-owning-`Simulator`
                # fixture (which drives `RPPIO` directly, with no per-instruction outer loop to fall
                # back on) still needs the synchronous burst - the same `rp2040.simulator is None`
                # distinction the task-scheduling branch immediately below this already draws, now
                # extended to gate the first batch too, not just the continuation.
                if self.rp2040.simulator is None:
                    self._step_batch()
                if not self.stopped and self.rp2040.simulator is None:
                    self._run_task = asyncio.get_running_loop().create_task(self.run())
            if not should_run:
                self.stopped = True

        elif offset == FDEBUG:
            self.fdebug &= ~self.raw_write_value
            self.fdebug |= self.tx_stall | self.rx_stall

        elif offset == TXF0:
            self.machines[0].write_fifo(value)
        elif offset == TXF1:
            self.machines[1].write_fifo(value)
        elif offset == TXF2:
            self.machines[2].write_fifo(value)
        elif offset == TXF3:
            self.machines[3].write_fifo(value)

        elif offset == IRQ:
            self.irq &= ~self.raw_write_value
            self.irq_updated()

        elif offset == INPUT_SYNC_BYPASS:
            self.input_sync_bypass = value

        elif offset == IRQ_FORCE:
            self.irq |= value
            self.irq_updated()

        elif offset == IRQ0_INTE:
            self.irq0_int_enable = value & 0xFFF
            self.check_interrupts()
        elif offset == IRQ0_INTF:
            self.irq0_int_force = value & 0xFFF
            self.check_interrupts()
        elif offset == IRQ1_INTE:
            self.irq1_int_enable = value & 0xFFF
            self.check_interrupts()
        elif offset == IRQ1_INTF:
            self.irq1_int_force = value & 0xFFF
            self.check_interrupts()

        else:
            super().write_uint32(offset, value)

    def pin_values_changed(self, value: int, first_pin: int, count: int) -> None:
        # TODO: wrapping after pin 31
        mask = 0xFFFFFFFF if count > 31 else ((1 << count) - 1) << first_pin
        new_value = ((self.pin_values & ~mask) | ((value << first_pin) & mask)) & 0x3FFFFFFF
        self.pin_values = new_value

    def pin_directions_changed(self, value: int, first_pin: int, count: int) -> None:
        # TODO: wrapping after pin 31
        mask = 0xFFFFFFFF if count > 31 else ((1 << count) - 1) << first_pin
        new_value = ((self.pin_directions & ~mask) | ((value << first_pin) & mask)) & 0x3FFFFFFF
        self.pin_directions = new_value

    def reset(self) -> None:
        """The whole block back to power-on (0089 Phase 5): instruction memory, the four state
        machines, the IRQ flags and the pacing state.

        Instruction memory is cleared deliberately - `PIO_INSTR_MEM` is RAM on real silicon and
        does not survive a reset, which is exactly why firmware re-uploads its programs on the way
        back up. `_run_task` is left alone: it only exists on the no-owning-`Simulator` fallback
        path (see `__init__`), where it is the caller's loop, not chip state."""
        for machine in self.machines:
            machine.reset()
        self.instructions = [0] * 32
        self.stopped = True
        self.cycle_fp = 0
        self.next_due_fp = NEVER_DUE
        self.backlog_drops = 0
        self.fdebug = 0
        self.tx_stall = 0
        self.rx_stall = 0
        self.input_sync_bypass = 0
        self.irq = 0
        self.pin_values = 0
        self.pin_directions = 0
        self.old_pin_values = 0
        self.old_pin_directions = 0
        self.irq0_int_enable = 0
        self.irq0_int_force = 0
        self.irq1_int_enable = 0
        self.irq1_int_force = 0
        self.check_interrupts()

    def check_interrupts(self) -> None:
        first_irq = self.first_irq
        self.rp2040.set_interrupt(first_irq, bool(self.irq0_int_status))
        self.rp2040.set_interrupt(first_irq + 1, bool(self.irq1_int_status))

    def irq_updated(self) -> None:
        for machine in self.machines:
            machine.check_wait()
        self.check_interrupts()

    def check_changed_pins(self) -> None:
        changed_pins = (self.old_pin_directions ^ self.pin_directions) | (self.old_pin_values ^ self.pin_values)
        if changed_pins:
            self.old_pin_directions = self.pin_directions
            self.old_pin_values = self.pin_values

            # Notify GPIO about the changed pins. Iterate only the set bits of `changed_pins`
            # (typically ~1) rather than scanning all 30 GPIO every step: during a PIO-driven
            # transfer (e.g. CYW43's bit-banged SPI) this runs once per PIO clock edge, and the
            # old full 30-slot scan spent ~97% of its iterations bit-testing unchanged pins.
            # Set-bit iteration preserves the original low-to-high visitation order.
            gpio = self.rp2040.gpio
            n = len(gpio)
            remaining = changed_pins
            while remaining:
                gpio_index = (remaining & -remaining).bit_length() - 1
                if gpio_index < n:
                    gpio[gpio_index].check_for_updates()
                remaining &= remaining - 1

    def recompute_due(self) -> None:
        """Recomputes `next_due_fp` - the earliest cycle any state machine has something to do -
        from scratch. Called after anything that can change a machine's schedule other than the
        machine executing an instruction: enable/restart, a CLKDIV write, a stall beginning."""
        next_due_fp = NEVER_DUE
        for machine in self.machines:
            if machine.enabled and not machine.waiting and machine.next_due_fp < next_due_fp:
                next_due_fp = machine.next_due_fp
        self.next_due_fp = next_due_fp

    def notify_due(self, due_fp: int) -> None:
        """A machine just re-armed itself out of band - i.e. `check_wait()` unstalled it from an
        MMIO write, a GPIO edge or an IRQ, not from inside `advance()`. Only ever lowers
        `next_due_fp`, so the machine gets picked up on the very next `advance()` rather than
        having to wait for whatever the previous earliest due time was."""
        self.next_due_fp = min(self.next_due_fp, due_fp)

    def advance(self, cycles: int) -> None:
        """Advances this PIO block by `cycles` system clocks - the paced entry point
        `_execute_batch()` calls once per CPU instruction (or idle jump), with that instruction's
        own cycle count.

        This is where `SM_CLKDIV` and `[delay]` finally get honoured (docs/records/0063). Nothing
        decouples from the CPU's instruction loop - 0037 coupled them deliberately, and this stays
        inside that coupling - but a state machine no longer executes one instruction per CPU
        instruction *regardless* of what it asked for: it executes when its own due time arrives,
        which for anything with a divider or a delay is less often than that. The common case is a
        single integer compare against the earliest due time of any machine, so a block whose
        machines are all disabled, stalled, or simply not due yet costs less than the
        `_has_runnable_machine()` scan this replaced.

        **At most one instruction per machine per call, and that ceiling is load-bearing**, not a
        performance shortcut. docs/records/0043 turns on `clock.tick()` running between every
        single PIO step: a DMA-fed TX FIFO is refilled by `SimulationClock` alarms that only fire
        from the CPU loop, so a state machine allowed to take two steps between two ticks can
        drain a FIFO the DMA channel has had no chance to refill, raise a premature
        `FDEBUG_TXSTALL`, and be read by `cyw43_spi_transfer()`'s TX-only branch as "transfer
        complete" after the first few words. Measured, not theorized: letting `advance()` run the
        machine as many times as its due time said it was owed - which for CYW43's CLKDIV=2 is
        occasionally twice - turns MicroPython v1.23.0's `nic.scan()` into `OSError: EPERM`
        instantly, every time. So this only ever *slows* a machine down relative to the CPU: a
        divider below the average cycles-per-instruction still runs "as fast as the CPU dispatches
        instructions", exactly as everything did before this change."""
        self.cycle_fp += cycles << 8
        if self.cycle_fp < self.next_due_fp:
            return
        self._run_due()

    def _run_due(self) -> None:
        cycle_fp = self.cycle_fp
        for machine in self.machines:
            if machine.enabled and not machine.waiting and machine.next_due_fp <= cycle_fp:
                if cycle_fp - machine.next_due_fp > MAX_ARREARS_FP:
                    # Owed more instructions than one-per-CPU-instruction can ever deliver - an
                    # idle jump straight to the next alarm, essentially. Write the backlog off and
                    # re-arm from now, so the machine keeps running at the *rate* it asked for
                    # instead of flat out until it has paid off a debt it accrued while the CPU
                    # was asleep. This is the same ceiling PIO always had here (an idle jump was
                    # worth exactly one PIO step before this change too), now explicit.
                    self.backlog_drops += 1
                    machine.next_due_fp = cycle_fp
                machine.step()
        self.recompute_due()
        self.check_changed_pins()

    def step(self) -> None:
        """One system clock. Kept as the no-owning-`Simulator` entry point (`_step_batch()`/
        `run()`, and tests driving `RPPIO` directly) - with the reset CLKDIV of 1 and no `[delay]`
        cycles this is exactly the "every machine executes one instruction" it has always been."""
        self.advance(1)

    def _step_batch(self) -> None:
        i = 0
        while i < 1000 and not self.stopped:
            self.advance(1)
            i += 1

    async def run(self) -> None:
        # Continuation of the first batch write_uint32()'s CTRL branch already ran synchronously -
        # only reached at all when this RP2040 has no owning Simulator (see __init__'s _run_task
        # comment/write_uint32()'s CTRL branch) - a real Simulator's own _execute_batch() drives
        # the continuation directly instead, once per CPU instruction/idle-jump, never scheduling
        # this task. Upstream rp2040js uses `setTimeout(() => this.run(), 0)` for the analogous
        # no-owning-loop-driver case, to yield back to the single-threaded JS event loop every 1000
        # steps so external stop() calls get a chance to run - `await asyncio.sleep(0)` is the
        # direct analogue here.
        while not self.stopped:
            await asyncio.sleep(0)
            self._step_batch()

    def stop(self) -> None:
        for machine in self.machines:
            machine.enabled = False
        self.stopped = True
        # No task cancellation needed - run() notices self.stopped on its next iteration, same
        # "benign staleness, not corruption" tradeoff this always made (one extra scheduled batch
        # may still run).
