# Declaration file paired with _state_machine.pyx. See that file's module docstring for the
# overall port rationale (docs/... perf side quest, 2026-08-12).
#
# wait_type stores WaitType's (peripherals/pio_registers.py) own integer values directly, not the
# Python IntEnum itself - avoids boxing on every check_wait()/execute_instruction() comparison.
# Mirrored here as plain constants, matching that enum's values exactly (NONE=0, PIN=1,
# RX_FIFO=2, TX_FIFO=3, IRQ=4, OUT=5) - see _state_machine.pyx's own top-of-file comment for the
# single source of truth this must be kept in sync with.
cdef unsigned int WAIT_TYPE_NONE
cdef unsigned int WAIT_TYPE_PIN
cdef unsigned int WAIT_TYPE_RX_FIFO
cdef unsigned int WAIT_TYPE_TX_FIFO
cdef unsigned int WAIT_TYPE_IRQ
cdef unsigned int WAIT_TYPE_OUT


cdef class StateMachine:
    # rp2040/pio deliberately stay `object`, not typed as the concrete native RP2040/RPPIO:
    # RP2040.gpio/.dma (native/_rp2040.pxd) aren't in that class's typed .pxd surface (only the
    # bus read_uint32/write_uint32 family is), and RPPIO isn't natively typed at all (see the perf
    # side quest's own plan - only StateMachine is ported, RPPIO stays pure Python) - so nothing
    # would actually get faster by cimporting either, only added coupling risk. See
    # _state_machine.pyx's module docstring for the full reasoning.
    cdef public object rp2040
    cdef public object pio
    cdef public unsigned int index

    cdef public bint enabled

    cdef public unsigned int x
    cdef public unsigned int y
    cdef public unsigned int pc
    cdef public unsigned int input_shift_reg
    cdef public unsigned int input_shift_count
    cdef public unsigned int output_shift_reg
    cdef public unsigned int output_shift_count
    # signed, not unsigned: check_wait() can add a still-sentinel wait_delay (-1) to cycles if
    # called before execute_instruction() ever resolves it to a real delay (0-31) - matches the
    # pure-Python reference's own arbitrary-precision-int behavior (a transient decrement-by-one,
    # not a wraparound) exactly. cycles is write-only bookkeeping - nothing reads it anywhere in
    # this codebase today - so this only matters for exact behavioral fidelity with the reference
    # implementation, not for any currently-observable effect.
    cdef public long long cycles

    cdef public unsigned int exec_opcode
    cdef public bint exec_valid
    cdef public bint update_pc

    cdef public unsigned int clock_div_int
    cdef public unsigned int clock_div_frac
    # SM_CLKDIV as one 16.8 fixed-point divisor (1/256ths of a system clock), and the absolute
    # system-clock cycle - on that same scale - this machine may next execute at. Both are read as
    # direct C field reads from _pio.pyx's own advance()/recompute_due() hot path, which is why
    # they are declared here rather than left as Python attributes. docs/records/0063.
    cdef public long long div_fp
    cdef public long long next_due_fp
    cdef public bint due_rearmed
    cdef public unsigned int exec_ctrl
    cdef public unsigned int shift_ctrl
    cdef public unsigned int pin_ctrl
    cdef public object rx_fifo
    cdef public object tx_fifo

    cdef public unsigned int out_pin_values
    cdef public unsigned int out_pin_direction

    cdef public bint waiting
    cdef public unsigned int wait_type
    cdef public unsigned int wait_index
    cdef public bint wait_polarity
    cdef public int wait_delay

    cdef public object dreq_rx
    cdef public object dreq_tx

    cdef void _update_dma_tx(self)
    cdef void _update_dma_rx(self)
    cpdef write_fifo(self, unsigned int value)
    cpdef unsigned int read_fifo(self) except? 0xFFFFFFFF

    cdef bint jmp_condition(self, unsigned int condition) except -1
    cdef unsigned int in_source_value(self, unsigned int source) except? 0xFFFFFFFF
    cdef void write_out_value(self, unsigned int destination, unsigned int value, unsigned int bit_count)
    cdef void out_instruction(self, unsigned int arg)
    cpdef execute_instruction(self, unsigned int opcode)
    cdef void wait(self, unsigned int wait_type, bint polarity, unsigned int index)
    cdef void next_pc(self)
    cpdef step(self)
    cdef void set_set_pin_dirs(self, unsigned int value)
    cdef void set_set_pins(self, unsigned int value)
    cdef void set_sideset(self, unsigned int value, unsigned int count)
    cdef void set_out_pin_dirs(self, unsigned int value)
    cdef void set_out_pins(self, unsigned int value)
    cdef unsigned int transform_mov_value(self, unsigned int value, unsigned int op)
    cdef void set_mov_destination(self, unsigned int destination, unsigned int value)
    cpdef unsigned int read_uint32(self, unsigned int offset) except? 0xFFFFFFFF
    cpdef write_uint32(self, unsigned int offset, unsigned int value)
    cpdef restart(self)
    cpdef clk_div_restart(self)
    cpdef check_wait(self)
