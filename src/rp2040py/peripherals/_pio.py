from typing import TYPE_CHECKING, Any

from rp2040py.peripherals._state_machine import StateMachine
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
    EXECCTRL_OUT_STICKY,
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
    WaitType,
)

if TYPE_CHECKING:
    from rp2040py.rp2040 import RP2040

__all__ = ("RPPIO",)

# What `snapshot()` reports (the native block reports the same names).
SNAPSHOT_BLOCK_FIELDS = (
    "irq", "fdebug", "tx_stall", "rx_stall", "input_sync_bypass", "pin_values", "pin_directions", "old_pin_values", "old_pin_directions",
    "cycle_fp", "next_due_fp", "resolve_due_fp", "backlog_drops", "stopped", "irq0_int_enable", "irq0_int_force", "irq1_int_enable", "irq1_int_force",
)  # fmt: skip
SNAPSHOT_MACHINE_FIELDS = (
    "x", "y", "pc", "input_shift_reg", "input_shift_count", "output_shift_reg", "output_shift_count", "cycles", "enabled", "waiting",
    "wait_type", "wait_index", "wait_polarity", "wait_delay", "next_due_fp", "due_rearmed", "exec_valid", "update_pc", "exec_opcode",
    "out_pin_values", "out_pin_direction", "div_fp", "exec_ctrl", "shift_ctrl", "pin_ctrl",
)  # fmt: skip

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

        # No lock and no task of its own: whoever advances the chip's clock steps this block (a Simulator's `_execute_batch()` once per CPU instruction /
        # idle-jump, or the caller of `advance()` / `step()` for a chip with no Simulator). CTRL only marks the block as running - docs/records/0037 and
        # 0043 (a competing asyncio task or an inline burst outruns a DMA-fed FIFO).

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
        # Pin-write arbitration (datasheet 3.5.6.1): `in_cycle` while `_run_due()` steps the machines (their writes are collected, then collated once); `resolve_due_fp` is the cycle at
        # which the image falls back to the held (`OUT_STICKY`) writes after a higher-numbered machine overwrote them for one cycle.
        self.in_cycle = False
        self.resolve_due_fp = NEVER_DUE

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
                # Nothing runs here: the block is stepped by whoever advances the clock (see __init__).
            if not should_run:
                self.stopped = True
                self.settle_held_writes()

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
            self.irq |= value & 0xFF  # IRQ_FORCE and IRQ have eight bits (3.7: 7:0, the rest reserved)
            self.irq_updated()

        elif offset in (
            FSTAT,
            FLEVEL,
            RXF0,
            RXF1,
            RXF2,
            RXF3,
            DBG_PADOUT,
            DBG_PADOE,
            DBG_CFGINFO,
            INTR,
            IRQ0_INTS,
            IRQ1_INTS,
        ):
            return  # read only (tables 371-387): a write is ignored, silently, as for the other read-only registers

        elif offset == IRQ0_INTE:
            self.irq0_int_enable = value & 0xFFF
            self._check_interrupts()
        elif offset == IRQ0_INTF:
            self.irq0_int_force = value & 0xFFF
            self._check_interrupts()
        elif offset == IRQ1_INTE:
            self.irq1_int_enable = value & 0xFFF
            self._check_interrupts()
        elif offset == IRQ1_INTF:
            self.irq1_int_force = value & 0xFFF
            self._check_interrupts()

        else:
            super().write_uint32(offset, value)

    @staticmethod
    def pin_mapping(value: int, first_pin: int, count: int) -> tuple[int, int]:
        """`(mask, data)` of a write of `count` bits at `first_pin`: "the least-significant bit of OUT data is mapped to PINCTRL_OUT_BASE, and this mapping continues for PINCTRL_OUT_COUNT
        bits, wrapping after GPIO31" (datasheet 3.5.6), so the data and the mask are rotated left by `first_pin` within 32 bits, not shifted."""
        value &= 0xFFFFFFFF
        mask = 0xFFFFFFFF if count > 31 else (1 << count) - 1
        if first_pin:
            value = ((value << first_pin) | (value >> (32 - first_pin))) & 0xFFFFFFFF
            mask = ((mask << first_pin) | (mask >> (32 - first_pin))) & 0xFFFFFFFF
        return mask, value

    def apply_pin_write(self, is_directions: bool, mask: int, data: int) -> None:
        """Applies a write to the pin levels or directions: the pins in `mask` take `data`, the others keep their value ("If no state machine asserts a write to a GPIO's level or
        direction, the value does not change")."""
        if is_directions:
            self.pin_directions = ((self.pin_directions & ~mask) | (data & mask)) & 0x3FFFFFFF
        else:
            self.pin_values = ((self.pin_values & ~mask) | (data & mask)) & 0x3FFFFFFF

    def pin_values_changed(self, value: int, first_pin: int, count: int) -> None:
        self.apply_pin_write(False, *self.pin_mapping(value, first_pin, count))

    def pin_directions_changed(self, value: int, first_pin: int, count: int) -> None:
        self.apply_pin_write(True, *self.pin_mapping(value, first_pin, count))

    def pin_routing(self) -> bool:
        """Do pin writes need the arbitration of `collate()`? Only while some machine has `OUT_STICKY` set or a write held - otherwise applying each write as it happens, in machine
        order, is the same thing (the highest-numbered machine's write is the last one applied)."""
        return any(
            machine.held_write is not None or machine.exec_ctrl & EXECCTRL_OUT_STICKY for machine in self.machines
        )

    def collate(self) -> None:
        """One system-clock cycle of pin writes (datasheet 3.5.6.1): "For each GPIO, PIO collates the writes from all four state machines, and applies the write from the highest-numbered
        state machine. This occurs separately for output levels and output directions." A machine's write of the cycle is what it wrote on that cycle, or - on the OUT/SET channel, with
        OUT_STICKY - the write it holds; a side-set write comes on top of its own OUT/SET in the overlap ("the side-set takes precedence")."""
        for machine in self.machines:
            write = machine.fresh_channel if machine.fresh_channel is not None else machine.held_write
            if write is not None:
                self.apply_pin_write(*write)
            if machine.fresh_side is not None:
                self.apply_pin_write(*machine.fresh_side)
            machine.fresh_channel = machine.fresh_side = None

    def held_image(self) -> tuple[int, int]:
        """The pin levels and directions after one more cycle in which no machine writes anything: every held write re-asserted, in machine order."""
        values, directions = self.pin_values, self.pin_directions
        for machine in self.machines:
            if machine.held_write is not None:
                is_directions, mask, data = machine.held_write
                if is_directions:
                    directions = ((directions & ~mask) | (data & mask)) & 0x3FFFFFFF
                else:
                    values = ((values & ~mask) | (data & mask)) & 0x3FFFFFFF
        return values, directions

    def settle_held_writes(self) -> None:
        """The block stops (no machine enabled) and nothing will step it to the next cycle: a held write that was overwritten for one cycle is back on the pins now, as it would be a
        cycle later (sticky writes are asserted whether or not their machine is enabled)."""
        if self.resolve_due_fp != NEVER_DUE:
            self.pin_values, self.pin_directions = self.held_image()
            self.resolve_due_fp = NEVER_DUE
            self.recompute_due()
            self.check_changed_pins()

    def schedule_resolve(self) -> None:
        """After a cycle's collation: if a held write was overwritten (by a higher-numbered machine's write, or by its own machine's side-set) the image falls back to the held
        writes one cycle later - unless something writes again."""
        if (self.pin_values, self.pin_directions) != self.held_image():
            self.resolve_due_fp = self.cycle_fp + (1 << 8)
        else:
            self.resolve_due_fp = NEVER_DUE

    def reset(self) -> None:
        """The whole block back to power-on (0089 Phase 5): instruction memory, the four state
        machines, the IRQ flags and the pacing state.

        Instruction memory is cleared deliberately - `PIO_INSTR_MEM` is RAM on real silicon and
        does not survive a reset, which is exactly why firmware re-uploads its programs on the way
        back up."""
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
        self.in_cycle = False
        self.resolve_due_fp = NEVER_DUE
        self.irq0_int_enable = 0
        self.irq0_int_force = 0
        self.irq1_int_enable = 0
        self.irq1_int_force = 0
        self._check_interrupts()

    def snapshot(self) -> dict[str, Any]:
        """Everything the block holds, as plain values in one call (the inspection port of docs/records/0096): the block's registers and pacing state, the instruction memory,
        and per machine its state, config words, held `OUT_STICKY` write and both FIFOs (oldest first)."""
        out: dict[str, Any] = {name: int(getattr(self, name)) for name in SNAPSHOT_BLOCK_FIELDS}
        out["instructions"] = tuple(int(word) for word in self.instructions)
        out["machines"] = tuple(
            {
                **{name: int(getattr(machine, name)) for name in SNAPSHOT_MACHINE_FIELDS},
                "held_write": tuple(int(v) for v in (machine.held_write or ())),
                "tx": tuple(int(v) for v in machine.tx_fifo.items),
                "rx": tuple(int(v) for v in machine.rx_fifo.items),
            }
            for machine in self.machines
        )
        return out

    def check_pin_waits(self, pin: int) -> None:
        """A pin's input level changed: wakes the enabled machines waiting on it with WAIT PIN."""
        for machine in self.machines:
            if machine.enabled and machine.waiting and machine.wait_type == WaitType.PIN and machine.wait_index == pin:
                machine.check_wait()

    def _check_interrupts(self) -> None:
        first_irq = self.first_irq
        self.rp2040.set_interrupt(first_irq, bool(self.irq0_int_status))
        self.rp2040.set_interrupt(first_irq + 1, bool(self.irq1_int_status))

    def irq_updated(self) -> None:
        for machine in self.machines:
            machine.check_wait()
        self._check_interrupts()

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
        self.next_due_fp = min(next_due_fp, self.resolve_due_fp)

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
        if self.resolve_due_fp <= cycle_fp:
            # A held write was overwritten for one cycle and that cycle is over: back to the held image, and the pins hear it before this cycle's writes
            self.pin_values, self.pin_directions = self.held_image()
            self.resolve_due_fp = NEVER_DUE
            self.check_changed_pins()
        routing = self.pin_routing()
        self.in_cycle = routing
        try:
            self._step_due_machines(cycle_fp)
        finally:
            self.in_cycle = False
        if routing or self.pin_routing():
            self.collate()
            self.schedule_resolve()
        self.recompute_due()
        self.check_changed_pins()

    def _step_due_machines(self, cycle_fp: int) -> None:
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

    def step(self) -> None:
        """One system clock. Kept as the entry point of a chip with no `Simulator` (and of tests
        driving `RPPIO` directly) - with the reset CLKDIV of 1 and no `[delay]`
        cycles this is exactly the "every machine executes one instruction" it has always been."""
        self.advance(1)

    def stop(self) -> None:
        for machine in self.machines:
            machine.enabled = False
        self.stopped = True
        # No task cancellation needed - run() notices self.stopped on its next iteration, same
        # "benign staleness, not corruption" tradeoff this always made (one extra scheduled batch
        # may still run).
