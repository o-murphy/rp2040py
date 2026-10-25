# cython: language_level=3
"""The native RP2040 PIO: a Python-facing shell over the C++ `PioBlock` of `core/pio.hpp` (docs/records/0096-cpp-mcu-core.md, Phase 3).
`peripherals/_pio.py` + `_state_machine.py` are the pure-Python reference and the oracle (judged against this by tests/utils/pio_diff.py);
The two chips import their PIO blocks directly (no facade: nothing outside them needs the name).

Everything a firmware does to a PIO - the registers, the FIFOs the DMA feeds, the instruction semantics, the pacing of record 0063 - is the C++ block.
When the chip adopts this object (`RP2040.peripherals[0x50200] = pio`) it registers the block's own C++ read/write functions in the bus's window table
(see `_native_window`), so a register access never touches Python, and the batch loop steps the block through `advance()` as one C++ call.

The chip's C++ `Mcu` builds the block (record 0096, step 3a): its lines, its DREQs, its window and its pins are wired there, so this object is only the Python view of it. What the
`Mcu` hands to Python is the logger (`_warn_kind`) and the "no `Simulator` owns the chip" fallback of CTRL (`_world_started`, the asyncio task of `run()`).

The machines, the FIFOs and the instruction memory are not exposed object by object: everything a firmware does goes through the bus (`SMx_INSTR`, `INSTR_MEM`, the FIFO registers),
and everything a test or a tool wants to *see* comes out of `snapshot()` in one call. What stays on the object is the plumbing the rest of the chip needs (the pin images, `advance()`, `stop()`).
"""
import asyncio

from libc.stdint cimport int64_t, uint32_t

from rp2040py.native._native_block cimport NativeBlock, register_ops
from rp2040py.native._pending cimport raise_if_pending
from rp2040py.native._window_map cimport WindowHandler

from rp2040py.peripherals.pio_registers import DREQ_RX0, DREQ_RX1, DREQ_TX0, DREQ_TX1
from rp2040py.native._register_warning import register_warning

__all__ = ("RPPIO",)


cdef tuple _fifo_items(PioFifo* fifo):
    return tuple([fifo.buffer[(fifo.start + i) % fifo.size] for i in range(fifo.used)])


cdef class RPPIO(NativeBlock):
    def __cinit__(self, rp2040, str name, unsigned int first_irq, unsigned int index):
        block_address = getattr(rp2040, "_native_pio_address", None)
        if block_address is None:
            raise TypeError(
                "the native PIO is a view of a block of the native chip's C++ core; "
                f"{type(rp2040).__name__} is not that (the pure-Python chip has the pure-Python peripherals/_pio.RPPIO)"
            )
        self._from_mcu = True
        address = block_address(index)  # (the chip's `Mcu` builds the block: its lines, requests, pins and window are wired inside)
        if address is None:
            raise ValueError(f"the chip has no PIO block {index}")
        self._block = <PioBlock*> <size_t> address
        self._ops = register_ops(self._block)
        self.rp2040 = rp2040
        self.name = name
        self.first_irq = first_irq
        self.index = index
        self.dreq_rx = DREQ_RX1 if index else DREQ_RX0
        self.dreq_tx = DREQ_TX1 if index else DREQ_TX0
        self._run_task = None

    cdef _warn(self, msg):
        self.rp2040.logger.warning(self.name, msg)

    def _warn_kind(self, kind, a, b):
        """A register access the block does not implement (or an invalid state machine register), as the logger's message (the chip's host calls this for the block it built)."""
        if kind == kPioErrorSmRead:
            self.error(f"Read from invalid state machine register: {a}")
        elif kind == kPioErrorSmWrite:
            self.error(f"Write to invalid state machine register: {a}")
        else:
            self._warn(register_warning(kind, a, b))

    def _world_started(self):
        """A CTRL write started a stopped block. Only run the first batch synchronously inline when no Simulator owns this RP2040
        (docs/records/0043-pio-dma-first-batch-race.md / 0037) - a real Simulator's own _execute_batch() steps every non-stopped RPPIO once per CPU
        instruction instead, so clock.tick() runs between every PIO step and a DMA-fed FIFO can't be outrun."""
        if self.rp2040.simulator is None:
            self._step_batch()
        if not self._block.stopped and self.rp2040.simulator is None:
            self._run_task = asyncio.get_running_loop().create_task(self.run())

    def write_uint32_atomic(self, offset, value, atomic_type):
        if not self._block.write_atomic(<uint32_t> offset, <int64_t> value, <uint32_t> atomic_type):
            raise_if_pending()

    # --- state: what the rest of the chip reads (everything else is in `snapshot()`) ----------------------------------------

    @property
    def stopped(self):
        return bool(self._block.stopped)

    @property
    def pin_values(self):
        return self._block.pin_values

    @property
    def pin_directions(self):
        return self._block.pin_directions

    # --- register dispatch ------------------------------------------------------------------------------------------------

    def write_uint32(self, offset, value):
        # A direct write keeps whatever raw_write_value the last atomic write left, as the pure-Python block does.
        if not self._block.write32(<uint32_t> offset, <uint32_t> value):
            raise_if_pending()

    # --- the pins, the interrupts, the pacing -----------------------------------------------------------------------------

    def pin_values_changed(self, unsigned int value, unsigned int first_pin, unsigned int count):
        self._block.pin_values_changed(value, first_pin, count)

    def pin_directions_changed(self, unsigned int value, unsigned int first_pin, unsigned int count):
        self._block.pin_directions_changed(value, first_pin, count)

    def reset(self):
        """The whole block back to power-on (0089 Phase 5): instruction memory, the four state machines, the IRQ flags and the pacing state.

        Instruction memory is cleared deliberately - `PIO_INSTR_MEM` is RAM on real silicon and does not survive a reset, which is exactly why
        firmware re-uploads its programs on the way back up. `_run_task` is left alone: it only exists on the no-owning-`Simulator` fallback
        path, where it is the caller's loop, not chip state."""
        if not self._block.reset():
            raise_if_pending()

    def snapshot(self):
        """Everything the block holds, as plain values in one call (the same dict as `peripherals/_pio.RPPIO.snapshot()`)."""
        cdef PioBlock* b = self._block
        cdef PioMachine* m
        cdef PioPinWrite* held
        cdef int i
        cdef list machines = []
        for i in range(4):
            m = &b.machines[i]
            held = &m.held_write
            machines.append({
                "x": m.x, "y": m.y, "pc": m.pc,
                "input_shift_reg": m.input_shift_reg, "input_shift_count": m.input_shift_count,
                "output_shift_reg": m.output_shift_reg, "output_shift_count": m.output_shift_count,
                "cycles": m.cycles, "enabled": int(m.enabled), "waiting": int(m.waiting),
                "wait_type": m.wait_type, "wait_index": m.wait_index, "wait_polarity": int(m.wait_polarity), "wait_delay": m.wait_delay,
                "next_due_fp": m.next_due_fp, "due_rearmed": int(m.due_rearmed), "exec_valid": int(m.exec_valid),
                "update_pc": int(m.update_pc), "exec_opcode": m.exec_opcode,
                "out_pin_values": m.out_pin_values, "out_pin_direction": m.out_pin_direction, "div_fp": m.div_fp,
                "exec_ctrl": m.exec_ctrl, "shift_ctrl": m.shift_ctrl, "pin_ctrl": m.pin_ctrl,
                "held_write": (int(held.directions), held.mask, held.data) if held.valid else (),
                "tx": _fifo_items(&m.tx),
                "rx": _fifo_items(&m.rx),
            })
        return {
            "irq": b.irq, "fdebug": b.fdebug, "tx_stall": b.tx_stall, "rx_stall": b.rx_stall,
            "input_sync_bypass": b.input_sync_bypass, "pin_values": b.pin_values, "pin_directions": b.pin_directions,
            "old_pin_values": b.old_pin_values, "old_pin_directions": b.old_pin_directions,
            "cycle_fp": b.cycle_fp, "next_due_fp": b.next_due_fp, "resolve_due_fp": b.resolve_due_fp,
            "backlog_drops": b.backlog_drops, "stopped": int(b.stopped),
            "irq0_int_enable": b.irq0_int_enable, "irq0_int_force": b.irq0_int_force,
            "irq1_int_enable": b.irq1_int_enable, "irq1_int_force": b.irq1_int_force,
            "instructions": tuple([b.instructions[i] for i in range(32)]),
            "machines": tuple(machines),
        }

    def check_pin_waits(self, unsigned int pin):
        """A pin's input level changed: wakes the enabled machines waiting on it with WAIT PIN."""
        cdef int i
        cdef PioMachine* m
        for i in range(4):
            m = &self._block.machines[i]
            if m.enabled and m.waiting and m.wait_type == 1 and m.wait_index == pin:
                if not m.check_wait():
                    raise_if_pending()

    def check_interrupts(self):
        if not self._block.check_interrupts():
            raise_if_pending()

    def irq_updated(self):
        if not self._block.irq_updated():
            raise_if_pending()

    cpdef check_changed_pins(self):
        if not self._block.check_changed_pins():
            raise_if_pending()

    cpdef recompute_due(self):
        """Earliest cycle any machine has something to do, from scratch."""
        self._block.recompute_due()

    cpdef notify_due(self, long long due_fp):
        """A machine re-armed itself out of band - only ever lowers next_due_fp."""
        self._block.notify_due(due_fp)

    cpdef advance(self, long long cycles):
        """Advance this PIO block by `cycles` system clocks - the paced entry point `_execute_batch()` calls per CPU instruction. At most one
        instruction per machine per call, which docs/records/0043 depends on (see docs/records/0063 for the pacing)."""
        if not self._block.advance(cycles):
            raise_if_pending()

    cpdef step(self):
        """One system clock - the no-owning-Simulator entry point."""
        self.advance(1)

    def _step_batch(self):
        cdef int i = 0
        while i < 1000 and not self._block.stopped:
            self.advance(1)
            i += 1

    async def run(self):
        while not self._block.stopped:
            await asyncio.sleep(0)
            self._step_batch()

    def stop(self):
        self._block.stop()
