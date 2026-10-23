# cython: language_level=3
"""The native RP2040 PIO: a Python-facing shell over the C++ `PioBlock` of `core/pio.hpp` (docs/records/0096-cpp-mcu-core.md, Phase 3).
`peripherals/_pio.py` + `_state_machine.py` are the pure-Python reference and the oracle (judged against this by tests/utils/pio_diff.py);
`peripherals/pio.py` and `peripherals/state_machine.py` are the facades that pick between them, as for the other native ports.

Everything a firmware does to a PIO - the registers, the FIFOs the DMA feeds, the instruction semantics, the pacing of record 0063 - is the C++ block.
When the chip adopts this object (`RP2040.peripherals[0x50200] = pio`) it registers the block's own C++ read/write functions in the bus's window table
(see `_native_window`), so a register access never touches Python, and the batch loop steps the block through `advance()` as one C++ call.

The chip's C++ `Mcu` builds the block (record 0096, step 3a): its lines, its DREQs, its window and its pins are wired there, so this object is only the Python view of it. What the
`Mcu` hands to Python is the logger (`_warn_kind`) and the "no `Simulator` owns the chip" fallback of CTRL (`_world_started`, the asyncio task of `run()`).

`StateMachine`, the FIFOs and the instruction memory are *views* of the C++ state with the attribute names the Python classes always had (`machines[i].x`,
`tx_fifo.push()`, `instructions[i]`, ...), so tests, devices and the pin layer see no difference. A `StateMachine` only exists as a part of an `RPPIO`.
"""
import asyncio

from libc.stdint cimport int64_t, uint32_t

from rp2040py.native._native_block cimport NativeBlock, register_ops
from rp2040py.native._pending cimport raise_if_pending
from rp2040py.native._window_map cimport WindowHandler

from rp2040py.peripherals.pio_registers import DREQ_RX0, DREQ_RX1, DREQ_TX0, DREQ_TX1
from rp2040py.native._register_warning import register_warning

__all__ = ("RPPIO", "StateMachine")


# --- views of the C++ state -------------------------------------------------------------------------------------------------

cdef class _FifoView:
    """`utils/fifo.FIFO`'s interface over one C++ FIFO (4 entries, or 8 / 0 with SHIFTCTRL.FJOIN)."""
    cdef PioFifo* _fifo

    def push(self, value):
        self._fifo.push(<uint32_t> (value & 0xFFFFFFFF))

    def pull(self):
        return self._fifo.pull()

    def peek(self):
        return self._fifo.peek()

    def reset(self):
        self._fifo.reset()

    @property
    def size(self):
        return self._fifo.size

    @property
    def item_count(self):
        return self._fifo.used

    @property
    def empty(self):
        return bool(self._fifo.empty())

    @property
    def full(self):
        return bool(self._fifo.full())

    @property
    def buffer(self):
        return [self._fifo.buffer[i] for i in range(self._fifo.size)]

    @property
    def items(self):
        return [self._fifo.buffer[(self._fifo.start + i) % self._fifo.size] for i in range(self._fifo.used)]


cdef _FifoView _fifo_view(PioFifo* fifo):
    cdef _FifoView view = _FifoView.__new__(_FifoView)
    view._fifo = fifo
    return view


cdef class _InstructionsView:
    """The 32 words of instruction memory as the Python `list` they used to be (index, assign, iterate, len)."""
    cdef RPPIO _owner

    def __len__(self):
        return 32

    def __getitem__(self, index):
        if isinstance(index, slice):
            return [self._owner._block.instructions[i] for i in range(*index.indices(32))]
        if index < -32 or index >= 32:
            raise IndexError("list index out of range")
        return self._owner._block.instructions[index % 32]

    def __setitem__(self, index, value):
        if index < -32 or index >= 32:
            raise IndexError("list assignment index out of range")
        self._owner._block.instructions[index % 32] = value

    def __iter__(self):
        return iter([self._owner._block.instructions[i] for i in range(32)])

    def __eq__(self, other):
        return list(self) == list(other)

    def __repr__(self):
        return repr(list(self))


cdef class StateMachine:
    """One of the four C++ state machines of an `RPPIO`, with the attribute surface of the pure-Python `StateMachine`."""
    cdef RPPIO _owner
    cdef PioMachine* _m
    cdef public object rp2040
    cdef public object pio
    cdef public object rx_fifo
    cdef public object tx_fifo
    cdef public object dreq_rx
    cdef public object dreq_tx

    def __init__(self, rp2040, pio, unsigned int index):
        if not isinstance(pio, RPPIO):
            raise TypeError("the native StateMachine is a view of a machine of the native RPPIO; "
                            f"{type(pio).__name__} is not one (use peripherals/_state_machine.StateMachine with it)")
        self._owner = <RPPIO> pio
        self._m = &self._owner._block.machines[index]
        self.rp2040 = rp2040
        self.pio = pio
        self.rx_fifo = _fifo_view(&self._m.rx)
        self.tx_fifo = _fifo_view(&self._m.tx)
        self.dreq_rx = pio.dreq_rx[index]
        self.dreq_tx = pio.dreq_tx[index]

    # --- the machine's own fields -----------------------------------------------------------------------------------------

    @property
    def index(self):
        return self._m.index

    @property
    def enabled(self):
        return bool(self._m.enabled)

    @enabled.setter
    def enabled(self, bint value):
        self._m.enabled = value

    @property
    def x(self):
        return self._m.x

    @x.setter
    def x(self, unsigned int value):
        self._m.x = value

    @property
    def y(self):
        return self._m.y

    @y.setter
    def y(self, unsigned int value):
        self._m.y = value

    @property
    def pc(self):
        return self._m.pc

    @pc.setter
    def pc(self, unsigned int value):
        self._m.pc = value

    @property
    def input_shift_reg(self):
        return self._m.input_shift_reg

    @input_shift_reg.setter
    def input_shift_reg(self, unsigned int value):
        self._m.input_shift_reg = value

    @property
    def input_shift_count(self):
        return self._m.input_shift_count

    @input_shift_count.setter
    def input_shift_count(self, unsigned int value):
        self._m.input_shift_count = value

    @property
    def output_shift_reg(self):
        return self._m.output_shift_reg

    @output_shift_reg.setter
    def output_shift_reg(self, unsigned int value):
        self._m.output_shift_reg = value

    @property
    def output_shift_count(self):
        return self._m.output_shift_count

    @output_shift_count.setter
    def output_shift_count(self, unsigned int value):
        self._m.output_shift_count = value

    @property
    def cycles(self):
        return self._m.cycles

    @cycles.setter
    def cycles(self, long long value):
        self._m.cycles = value

    @property
    def exec_opcode(self):
        return self._m.exec_opcode

    @exec_opcode.setter
    def exec_opcode(self, unsigned int value):
        self._m.exec_opcode = value

    @property
    def exec_valid(self):
        return bool(self._m.exec_valid)

    @exec_valid.setter
    def exec_valid(self, bint value):
        self._m.exec_valid = value

    @property
    def update_pc(self):
        return bool(self._m.update_pc)

    @update_pc.setter
    def update_pc(self, bint value):
        self._m.update_pc = value

    @property
    def clock_div_int(self):
        return self._m.clock_div_int

    @clock_div_int.setter
    def clock_div_int(self, unsigned int value):
        self._m.clock_div_int = value

    @property
    def clock_div_frac(self):
        return self._m.clock_div_frac

    @clock_div_frac.setter
    def clock_div_frac(self, unsigned int value):
        self._m.clock_div_frac = value

    @property
    def div_fp(self):
        return self._m.div_fp

    @div_fp.setter
    def div_fp(self, long long value):
        self._m.div_fp = value

    @property
    def next_due_fp(self):
        return self._m.next_due_fp

    @next_due_fp.setter
    def next_due_fp(self, long long value):
        self._m.next_due_fp = value

    @property
    def due_rearmed(self):
        return bool(self._m.due_rearmed)

    @due_rearmed.setter
    def due_rearmed(self, bint value):
        self._m.due_rearmed = value

    @property
    def exec_ctrl(self):
        return self._m.exec_ctrl

    @exec_ctrl.setter
    def exec_ctrl(self, unsigned int value):
        self._m.exec_ctrl = value

    @property
    def shift_ctrl(self):
        return self._m.shift_ctrl

    @shift_ctrl.setter
    def shift_ctrl(self, unsigned int value):
        self._m.shift_ctrl = value

    @property
    def pin_ctrl(self):
        return self._m.pin_ctrl

    @pin_ctrl.setter
    def pin_ctrl(self, unsigned int value):
        self._m.pin_ctrl = value

    @property
    def out_pin_values(self):
        return self._m.out_pin_values

    @out_pin_values.setter
    def out_pin_values(self, unsigned int value):
        self._m.out_pin_values = value

    @property
    def out_pin_direction(self):
        return self._m.out_pin_direction

    @out_pin_direction.setter
    def out_pin_direction(self, unsigned int value):
        self._m.out_pin_direction = value

    @property
    def held_write(self):
        """The write `OUT_STICKY` keeps re-asserting, `(is_directions, mask, data)`, or None (the reference's attribute)."""
        if not self._m.held_write.valid:
            return None
        return (bool(self._m.held_write.directions), self._m.held_write.mask, self._m.held_write.data)

    @property
    def waiting(self):
        return bool(self._m.waiting)

    @waiting.setter
    def waiting(self, bint value):
        self._m.waiting = value

    @property
    def wait_type(self):
        return self._m.wait_type

    @wait_type.setter
    def wait_type(self, unsigned int value):
        self._m.wait_type = value

    @property
    def wait_index(self):
        return self._m.wait_index

    @wait_index.setter
    def wait_index(self, unsigned int value):
        self._m.wait_index = value

    @property
    def wait_polarity(self):
        return bool(self._m.wait_polarity)

    @wait_polarity.setter
    def wait_polarity(self, bint value):
        self._m.wait_polarity = value

    @property
    def wait_delay(self):
        return self._m.wait_delay

    @wait_delay.setter
    def wait_delay(self, int value):
        self._m.wait_delay = value

    # --- what the config words say ----------------------------------------------------------------------------------------

    @property
    def push_threshold(self):
        return self._m.push_threshold()

    @property
    def pull_threshold(self):
        return self._m.pull_threshold()

    @property
    def sideset_count(self):
        return self._m.sideset_count()

    @property
    def set_count(self):
        return self._m.set_count()

    @property
    def out_count(self):
        return self._m.out_count()

    @property
    def in_base(self):
        return self._m.in_base()

    @property
    def sideset_base(self):
        return self._m.sideset_base()

    @property
    def set_base(self):
        return self._m.set_base()

    @property
    def out_base(self):
        return self._m.out_base()

    @property
    def jmp_pin(self):
        return self._m.jmp_pin()

    @property
    def wrap_top(self):
        return self._m.wrap_top()

    @property
    def wrap_bottom(self):
        return self._m.wrap_bottom()

    @property
    def status(self):
        return self._m.status()

    @property
    def fifo_stat(self):
        return self._m.fifo_stat()

    @property
    def in_pins(self):
        cdef uint32_t values = 0
        cdef uint32_t base = self._m.in_base()
        if not self._owner._block.gpio_values(&values):
            raise_if_pending()
        if base:
            return ((values << (32 - base)) | (values >> base)) & 0xFFFFFFFF
        return values

    # --- behaviour --------------------------------------------------------------------------------------------------------

    def write_fifo(self, unsigned int value):
        if not self._m.write_fifo(value):
            raise_if_pending()

    def read_fifo(self):
        cdef uint32_t result = 0
        if not self._m.read_fifo(&result):
            raise_if_pending()
        return result

    def execute_instruction(self, unsigned int opcode):
        if not self._m.execute_instruction(opcode):
            raise_if_pending()

    def step(self):
        if not self._m.step():
            raise_if_pending()

    def check_wait(self):
        if not self._m.check_wait():
            raise_if_pending()

    def restart(self):
        if not self._m.restart():
            raise_if_pending()

    def clk_div_restart(self):
        """`CTRL.CLKDIV_RESTART`: restarts the clock divider from phase 0 (docs/records/0063)."""
        self._m.clk_div_restart()

    def reset(self):
        """Every state-machine register back to power-on (0089 Phase 5); distinct from `restart()`, which is `CTRL.SM_RESTART`."""
        if not self._m.reset():
            raise_if_pending()

    def read_uint32(self, unsigned int offset):
        cdef uint32_t value = 0
        if not self._m.read32(offset, &value):
            raise_if_pending()
        return value

    def write_uint32(self, unsigned int offset, unsigned int value):
        if not self._m.write32(offset, value):
            raise_if_pending()


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
        self.machines = [StateMachine(rp2040, self, i) for i in range(4)]
        cdef _InstructionsView view = _InstructionsView.__new__(_InstructionsView)
        view._owner = self
        self._instructions = view

    # --- BasePeripheral surface (Peripheral Protocol) ---------------------------------------------------------------------

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

    # --- the native bus protocol ------------------------------------------------------------------------------------------

    # --- state ------------------------------------------------------------------------------------------------------------

    @property
    def instructions(self):
        return self._instructions

    @instructions.setter
    def instructions(self, value):
        for i in range(32):
            self._block.instructions[i] = value[i]

    @property
    def raw_write_value(self):
        return self._block.raw_write_value()

    @raw_write_value.setter
    def raw_write_value(self, value):
        self._block.set_raw_write_value(value)

    @property
    def stopped(self):
        return bool(self._block.stopped)

    @stopped.setter
    def stopped(self, bint value):
        self._block.stopped = value

    @property
    def cycle_fp(self):
        return self._block.cycle_fp

    @cycle_fp.setter
    def cycle_fp(self, long long value):
        self._block.cycle_fp = value

    @property
    def next_due_fp(self):
        return self._block.next_due_fp

    @next_due_fp.setter
    def next_due_fp(self, long long value):
        self._block.next_due_fp = value

    @property
    def backlog_drops(self):
        return self._block.backlog_drops

    @backlog_drops.setter
    def backlog_drops(self, long long value):
        self._block.backlog_drops = value

    @property
    def fdebug(self):
        return self._block.fdebug

    @fdebug.setter
    def fdebug(self, unsigned int value):
        self._block.fdebug = value

    @property
    def tx_stall(self):
        return self._block.tx_stall

    @tx_stall.setter
    def tx_stall(self, unsigned int value):
        self._block.tx_stall = value

    @property
    def rx_stall(self):
        return self._block.rx_stall

    @rx_stall.setter
    def rx_stall(self, unsigned int value):
        self._block.rx_stall = value

    @property
    def input_sync_bypass(self):
        return self._block.input_sync_bypass

    @input_sync_bypass.setter
    def input_sync_bypass(self, unsigned int value):
        self._block.input_sync_bypass = value

    @property
    def irq(self):
        return self._block.irq

    @irq.setter
    def irq(self, unsigned int value):
        self._block.irq = value

    @property
    def pin_values(self):
        return self._block.pin_values

    @pin_values.setter
    def pin_values(self, unsigned int value):
        self._block.pin_values = value

    @property
    def pin_directions(self):
        return self._block.pin_directions

    @pin_directions.setter
    def pin_directions(self, unsigned int value):
        self._block.pin_directions = value

    @property
    def resolve_due_fp(self):
        return self._block.resolve_due_fp

    @property
    def in_cycle(self):
        return bool(self._block.in_cycle)

    @property
    def old_pin_values(self):
        return self._block.old_pin_values

    @old_pin_values.setter
    def old_pin_values(self, unsigned int value):
        self._block.old_pin_values = value

    @property
    def old_pin_directions(self):
        return self._block.old_pin_directions

    @old_pin_directions.setter
    def old_pin_directions(self, unsigned int value):
        self._block.old_pin_directions = value

    @property
    def irq0_int_enable(self):
        return self._block.irq0_int_enable

    @irq0_int_enable.setter
    def irq0_int_enable(self, unsigned int value):
        self._block.irq0_int_enable = value

    @property
    def irq0_int_force(self):
        return self._block.irq0_int_force

    @irq0_int_force.setter
    def irq0_int_force(self, unsigned int value):
        self._block.irq0_int_force = value

    @property
    def irq1_int_enable(self):
        return self._block.irq1_int_enable

    @irq1_int_enable.setter
    def irq1_int_enable(self, unsigned int value):
        self._block.irq1_int_enable = value

    @property
    def irq1_int_force(self):
        return self._block.irq1_int_force

    @irq1_int_force.setter
    def irq1_int_force(self, unsigned int value):
        self._block.irq1_int_force = value

    @property
    def int_raw(self):
        return self._block.int_raw()

    @property
    def irq0_int_status(self):
        return self._block.irq0_int_status()

    @property
    def irq1_int_status(self):
        return self._block.irq1_int_status()

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
