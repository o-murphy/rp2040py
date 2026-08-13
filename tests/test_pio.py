"""Ported from rp2040js's peripherals/pio.spec.ts.

Note: rp2040py.utils.pio_assembler's pio_jmp/pio_mov take (address, cond, ...)/(dest, src, op, ...)
- the *cond*/*address* and *op*/*src* arguments are swapped relative to upstream's
  pioJMP(cond, address)/pioMOV(dest, op, src). Every call below accounts for that; see
  test_pio_assembler.py for the already-ported unit tests of the encoding itself.
"""

import asyncio

import pytest
from utils.cortex_test_driver import ICortexTestDriver
from utils.create_test_driver import create_test_driver

from rp2040py.simulator import Simulator
from rp2040py.utils.pio_assembler import (
    PIO_COND_ALWAYS,
    PIO_COND_NOTEMPTYOSR,
    PIO_COND_NOTX,
    PIO_COND_NOTY,
    PIO_COND_XDEC,
    PIO_COND_XNEY,
    PIO_COND_YDEC,
    PIO_DEST_EXEC,
    PIO_DEST_NULL,
    PIO_DEST_PC,
    PIO_DEST_PINS,
    PIO_DEST_X,
    PIO_DEST_Y,
    PIO_MOV_DEST_ISR,
    PIO_MOV_DEST_OSR,
    PIO_MOV_DEST_PC,
    PIO_MOV_DEST_X,
    PIO_MOV_DEST_Y,
    PIO_OP_BITREV,
    PIO_OP_INVERT,
    PIO_OP_NONE,
    PIO_SRC_NULL,
    PIO_SRC_STATUS,
    PIO_SRC_X,
    PIO_SRC_Y,
    PIO_WAIT_SRC_IRQ,
    pio_in,
    pio_irq,
    pio_jmp,
    pio_mov,
    pio_out,
    pio_pull,
    pio_push,
    pio_set,
    pio_wait,
)

CTRL = 0x50200000
FLEVEL = 0x5020000C
TXF0 = 0x50200010
RXF0 = 0x50200020
IRQ = 0x50200030
INSTR_MEM0 = 0x50200048
INSTR_MEM1 = 0x5020004C
INSTR_MEM2 = 0x50200050
INSTR_MEM3 = 0x50200054
SM0_SHIFTCTRL = 0x502000D0
SM0_EXECCTRL = 0x502000CC
SM0_ADDR = 0x502000D4
SM0_INSTR = 0x502000D8
SM0_PINCTRL = 0x502000DC
SM2_INSTR = 0x50200108
INTR = 0x50200128
IRQ0_INTE = 0x5020012C
FDEBUG = 0x50200008  # Debug register

NVIC_ISPR = 0xE000E200
NVIC_ICPR = 0xE000E280

# Interrupt flags
PIO_IRQ0 = 1 << 7
INTR_SM0_RXNEMPTY = 1 << 0
INTR_SM0_TXNFULL = 1 << 4

# SHIFTs for FLEVEL
TX0_SHIFT = 0
RX0_SHIFT = 4

# SM0_SHIFTCTRL bits:
FJOIN_RX = 1 << 30
IN_SHIFTDIR = 1 << 18
OUT_SHIFTDIR = 1 << 19
SHIFTCTRL_AUTOPULL = 1 << 17
SHIFTCTRL_AUTOPUSH = 1 << 16
SHIFTCTRL_PULL_THRESH_SHIFT = 25
SHIFTCTRL_PUSH_THRESH_SHIFT = 20

# EXECCTRL bits:
EXECCTRL_EXEC_STALLED = 1 << 31
EXECCTRL_STATUS_SEL = 1 << 4
EXECCTRL_WRAP_BOTTOM_SHIFT = 7
EXECCTRL_WRAP_TOP_SHIFT = 12
EXECCTRL_STATUS_N_SHIFT = 0

# FDEBUG bits
FDEBUG_TXSTALL = 1 << 24

DBG_PADOUT = 0x5020003C

SET_COUNT_SHIFT = 26
SET_COUNT_BASE = 5
OUT_COUNT_SHIFT = 20

VALID_PINS_MASK = 0x3FFFFFFF


class _LoopBoundDriver:
    """Wraps an ICortexTestDriver so every call runs inside `loop.run_until_complete()` on the
    test's own thread - RPPIO.write_uint32()'s CTRL branch (peripherals/pio.py) needs a running
    loop to schedule a long program's continuation onto (`asyncio.get_running_loop()`) - true
    automatically via Simulator's own permanent loop for every real caller, but a bare RP2040
    driven directly by these tests has no Simulator at all.

    Deliberately `run_until_complete()`, not a persistent background `run_forever()` thread: a
    background loop would let a long-running/never-self-stopping PIO continuation (e.g. a state
    machine parked on `wait irq`, which only ever needs to be re-checked when something explicit
    like an IRQ register write happens - see `RPPIO.irq_updated()`, called synchronously from
    `write_uint32()` itself) spin at full speed between test statements for no reason - measured
    multiple *seconds* of pure waste per such test. `run_until_complete()` gives the continuation
    task only as many turns as it takes for the awaited call to finish, then stops - no state is
    lost (each explicit write still synchronously re-checks whatever it needs to), it just doesn't
    keep grinding on its own between statements the way production's real Simulator loop
    legitimately does (there, unlike here, that's correct: real emulation should run as fast as
    possible)."""

    def __init__(self, driver: ICortexTestDriver, loop: asyncio.AbstractEventLoop) -> None:
        self._driver = driver
        self._loop = loop

    def __getattr__(self, name: str):
        attr = getattr(self._driver, name)
        if not callable(attr):
            return attr

        def _call(*args, **kwargs):
            async def _run():
                return attr(*args, **kwargs)

            return self._loop.run_until_complete(_run())

        return _call


@pytest.fixture
def cpu():
    driver = create_test_driver()
    loop = asyncio.new_event_loop()
    wrapped = _LoopBoundDriver(driver, loop)
    wrapped.init()
    yield wrapped
    wrapped.tear_down()
    loop.close()


def reset_state_machines(cpu) -> None:
    cpu.write_uint32(CTRL, 0xF0)
    cpu.write_uint32(SM0_INSTR, pio_jmp(0, PIO_COND_ALWAYS))  # Jump machine 0 to address 0
    # Clear FIFOs
    cpu.write_uint32(SM0_SHIFTCTRL, FJOIN_RX)
    # Values at reset
    cpu.write_uint32(SM0_SHIFTCTRL, IN_SHIFTDIR | OUT_SHIFTDIR)
    cpu.write_uint32(SM0_PINCTRL, 5 << SET_COUNT_SHIFT)


def test_set_pins_instruction(cpu):
    # SET PINS, 13
    # then check the debug register and verify that output from the pins matches the PINS value
    shift_amount = 0
    pins_qty = 5
    pins_value = 13
    reset_state_machines(cpu)
    cpu.write_uint32(SM0_PINCTRL, (pins_qty << SET_COUNT_SHIFT) | (shift_amount << SET_COUNT_BASE))
    cpu.write_uint32(SM0_INSTR, pio_set(PIO_DEST_PINS, pins_value))
    assert (cpu.read_uint32(DBG_PADOUT) & (((1 << pins_qty) - 1) << shift_amount)) == (pins_value << shift_amount)


def test_mov_pins_x_instruction(cpu):
    reset_state_machines(cpu)
    cpu.write_uint32(SM0_PINCTRL, 32 << OUT_COUNT_SHIFT)
    cpu.write_uint32(SM0_INSTR, pio_set(PIO_DEST_X, 8))
    cpu.write_uint32(SM0_INSTR, pio_mov(PIO_DEST_PINS, PIO_SRC_X, PIO_OP_NONE))
    assert cpu.read_uint32(DBG_PADOUT) == 8


def test_mov_pins_not_x_instruction(cpu):
    reset_state_machines(cpu)
    cpu.write_uint32(SM0_PINCTRL, 32 << OUT_COUNT_SHIFT)
    cpu.write_uint32(SM0_INSTR, pio_set(PIO_DEST_X, 29))
    cpu.write_uint32(SM0_INSTR, pio_mov(PIO_DEST_PINS, PIO_SRC_X, PIO_OP_INVERT))
    assert cpu.read_uint32(DBG_PADOUT) == (~29 & VALID_PINS_MASK)


def test_mov_pins_bitreverse_x_instruction(cpu):
    reset_state_machines(cpu)
    cpu.write_uint32(SM0_PINCTRL, 32 << OUT_COUNT_SHIFT)
    cpu.write_uint32(SM0_INSTR, pio_set(PIO_DEST_X, 0b11001))
    cpu.write_uint32(SM0_INSTR, pio_mov(PIO_DEST_PINS, PIO_SRC_X, PIO_OP_BITREV))
    assert cpu.read_uint32(DBG_PADOUT) == (0x98000000 & VALID_PINS_MASK)


def test_mov_y_x_instruction(cpu):
    reset_state_machines(cpu)
    cpu.write_uint32(SM0_PINCTRL, 32 << OUT_COUNT_SHIFT)
    cpu.write_uint32(SM0_INSTR, pio_set(PIO_DEST_X, 11))
    cpu.write_uint32(SM0_INSTR, pio_mov(PIO_MOV_DEST_Y, PIO_SRC_X, PIO_OP_NONE))
    cpu.write_uint32(SM0_INSTR, pio_mov(PIO_DEST_PINS, PIO_SRC_Y, PIO_OP_NONE))
    assert cpu.read_uint32(DBG_PADOUT) == 11


def test_mov_pc_y_instruction(cpu):
    reset_state_machines(cpu)
    assert cpu.read_uint32(SM0_ADDR) == 0
    cpu.write_uint32(SM0_INSTR, pio_set(PIO_DEST_Y, 23))
    cpu.write_uint32(SM0_INSTR, pio_mov(PIO_MOV_DEST_PC, PIO_SRC_Y, PIO_OP_NONE))
    assert cpu.read_uint32(SM0_ADDR) == 23


def test_mov_isr_status_instruction_when_status_sel_is_0_tx_fifo(cpu):
    reset_state_machines(cpu)
    cpu.write_uint32(SM0_EXECCTRL, 2 << EXECCTRL_STATUS_N_SHIFT)
    cpu.write_uint32(SM0_INSTR, pio_mov(PIO_MOV_DEST_ISR, PIO_SRC_STATUS, PIO_OP_NONE))
    cpu.write_uint32(SM0_INSTR, pio_push(False, False))
    assert cpu.read_uint32(RXF0) == 0xFFFFFFFF

    cpu.write_uint32(TXF0, 1)
    cpu.write_uint32(TXF0, 2)
    cpu.write_uint32(SM0_INSTR, pio_mov(PIO_MOV_DEST_ISR, PIO_SRC_STATUS, PIO_OP_NONE))
    cpu.write_uint32(SM0_INSTR, pio_push(False, False))
    assert cpu.read_uint32(RXF0) == 0


def test_mov_isr_status_instruction_when_status_sel_is_1_rx_fifo(cpu):
    reset_state_machines(cpu)
    cpu.write_uint32(SM0_EXECCTRL, (1 << EXECCTRL_STATUS_N_SHIFT) | EXECCTRL_STATUS_SEL)

    cpu.write_uint32(SM0_INSTR, pio_mov(PIO_MOV_DEST_ISR, PIO_SRC_STATUS, PIO_OP_NONE))
    cpu.write_uint32(SM0_INSTR, pio_push(False, False))
    cpu.write_uint32(SM0_INSTR, pio_mov(PIO_MOV_DEST_ISR, PIO_SRC_STATUS, PIO_OP_NONE))
    cpu.write_uint32(SM0_INSTR, pio_push(False, False))
    assert cpu.read_uint32(RXF0) == 0xFFFFFFFF
    assert cpu.read_uint32(RXF0) == 0


def test_jmp_always_instruction(cpu):
    reset_state_machines(cpu)
    cpu.write_uint32(SM0_INSTR, pio_jmp(10, PIO_COND_ALWAYS))
    assert cpu.read_uint32(SM0_ADDR) == 10


def test_jmp_not_x_instruction(cpu):
    reset_state_machines(cpu)
    assert cpu.read_uint32(SM0_ADDR) == 0
    cpu.write_uint32(SM0_INSTR, pio_set(PIO_DEST_X, 5))
    cpu.write_uint32(SM0_INSTR, pio_jmp(8, PIO_COND_NOTX))
    assert cpu.read_uint32(SM0_ADDR) == 0
    cpu.write_uint32(SM0_INSTR, pio_set(PIO_DEST_X, 0))
    cpu.write_uint32(SM0_INSTR, pio_jmp(8, PIO_COND_NOTX))
    assert cpu.read_uint32(SM0_ADDR) == 8


def test_jmp_x_dec_instruction(cpu):
    reset_state_machines(cpu)
    cpu.write_uint32(SM0_PINCTRL, 32 << OUT_COUNT_SHIFT)
    assert cpu.read_uint32(SM0_ADDR) == 0
    cpu.write_uint32(SM0_INSTR, pio_set(PIO_DEST_X, 5))
    cpu.write_uint32(SM0_INSTR, pio_jmp(12, PIO_COND_XDEC))
    assert cpu.read_uint32(SM0_ADDR) == 12
    # X should be 4:
    cpu.write_uint32(SM0_INSTR, pio_mov(PIO_DEST_PINS, PIO_SRC_X, PIO_OP_NONE))
    assert cpu.read_uint32(DBG_PADOUT) == 4
    # now set X to zero and ensure that we don't jump again
    cpu.write_uint32(SM0_INSTR, pio_set(PIO_DEST_X, 0))
    cpu.write_uint32(SM0_INSTR, pio_jmp(6, PIO_COND_XDEC))
    assert cpu.read_uint32(SM0_ADDR) == 12


def test_jmp_not_y_instruction(cpu):
    reset_state_machines(cpu)
    assert cpu.read_uint32(SM0_ADDR) == 0
    cpu.write_uint32(SM0_INSTR, pio_set(PIO_DEST_Y, 6))
    cpu.write_uint32(SM0_INSTR, pio_jmp(8, PIO_COND_NOTY))
    assert cpu.read_uint32(SM0_ADDR) == 0
    cpu.write_uint32(SM0_INSTR, pio_set(PIO_DEST_Y, 0))
    cpu.write_uint32(SM0_INSTR, pio_jmp(8, PIO_COND_NOTY))
    assert cpu.read_uint32(SM0_ADDR) == 8


def test_jmp_y_dec_instruction(cpu):
    reset_state_machines(cpu)
    cpu.write_uint32(SM0_PINCTRL, 32 << OUT_COUNT_SHIFT)
    assert cpu.read_uint32(SM0_ADDR) == 0
    cpu.write_uint32(SM0_INSTR, pio_set(PIO_DEST_Y, 15))
    cpu.write_uint32(SM0_INSTR, pio_jmp(12, PIO_COND_YDEC))
    assert cpu.read_uint32(SM0_ADDR) == 12
    # Y should be 14:
    cpu.write_uint32(SM0_INSTR, pio_mov(PIO_DEST_PINS, PIO_SRC_Y, PIO_OP_NONE))
    assert cpu.read_uint32(DBG_PADOUT) == 14
    # now set X to zero and ensure that we don't jump again
    cpu.write_uint32(SM0_INSTR, pio_set(PIO_DEST_Y, 0))
    cpu.write_uint32(SM0_INSTR, pio_jmp(6, PIO_COND_YDEC))
    assert cpu.read_uint32(SM0_ADDR) == 12


def test_jmp_x_not_equal_y_instruction(cpu):
    reset_state_machines(cpu)
    cpu.write_uint32(SM0_PINCTRL, 32 << OUT_COUNT_SHIFT)
    assert cpu.read_uint32(SM0_ADDR) == 0
    cpu.write_uint32(SM0_INSTR, pio_set(PIO_DEST_X, 23))
    cpu.write_uint32(SM0_INSTR, pio_set(PIO_DEST_Y, 23))
    cpu.write_uint32(SM0_INSTR, pio_jmp(26, PIO_COND_XNEY))
    assert cpu.read_uint32(SM0_ADDR) == 0
    # Set X to a value different from Y:
    cpu.write_uint32(SM0_INSTR, pio_set(PIO_DEST_X, 3))
    cpu.write_uint32(SM0_INSTR, pio_jmp(26, PIO_COND_XNEY))
    assert cpu.read_uint32(SM0_ADDR) == 26


def test_jmp_osre_instruction(cpu):
    reset_state_machines(cpu)
    cpu.write_uint32(SM0_PINCTRL, 32 << OUT_COUNT_SHIFT)
    assert cpu.read_uint32(SM0_ADDR) == 0
    # The following command fills the OSR (Output Shift Register)
    cpu.write_uint32(SM0_INSTR, pio_mov(PIO_MOV_DEST_OSR, PIO_SRC_NULL, PIO_OP_NONE))
    cpu.write_uint32(SM0_INSTR, pio_jmp(11, PIO_COND_NOTEMPTYOSR))
    assert cpu.read_uint32(SM0_ADDR) == 11
    # Now empty the OSR by shifting bits out of it, and observe that the JMP isn't taken
    cpu.write_uint32(SM0_INSTR, pio_out(PIO_DEST_NULL, 32))
    cpu.write_uint32(SM0_INSTR, pio_jmp(22, PIO_COND_NOTEMPTYOSR))
    assert cpu.read_uint32(SM0_ADDR) == 11


def test_program_with_pull_instruction(cpu):
    reset_state_machines(cpu)
    cpu.write_uint32(SM0_PINCTRL, 32 << OUT_COUNT_SHIFT)
    cpu.write_uint32(TXF0, 0x42F00D43)
    assert cpu.read_uint32(FLEVEL) == (1 << TX0_SHIFT)  # TX0 should have 1 item
    cpu.write_uint32(SM0_INSTR, pio_pull(False, False))
    assert cpu.read_uint32(FLEVEL) == (0 << TX0_SHIFT)  # TX0 should now have 0 items
    cpu.write_uint32(SM0_INSTR, pio_out(PIO_DEST_PINS, 32))
    assert cpu.read_uint32(DBG_PADOUT) == (0x42F00D43 & VALID_PINS_MASK)


def test_out_exec_instructions(cpu):
    reset_state_machines(cpu)
    cpu.write_uint32(SM0_PINCTRL, 32 << OUT_COUNT_SHIFT)
    cpu.write_uint32(TXF0, pio_jmp(16, PIO_COND_ALWAYS))
    cpu.write_uint32(SM0_INSTR, pio_pull(False, False))
    assert cpu.read_uint32(SM0_ADDR) == 0
    cpu.write_uint32(SM0_INSTR, pio_out(PIO_DEST_EXEC, 32))
    assert cpu.read_uint32(SM0_ADDR) == 16


def test_out_pc_instruction(cpu):
    reset_state_machines(cpu)
    cpu.write_uint32(SM0_PINCTRL, 32 << OUT_COUNT_SHIFT)
    cpu.write_uint32(TXF0, 29)
    cpu.write_uint32(SM0_INSTR, pio_pull(False, False))
    assert cpu.read_uint32(SM0_ADDR) == 0
    cpu.write_uint32(SM0_INSTR, pio_out(PIO_DEST_PC, 32))
    assert cpu.read_uint32(SM0_ADDR) == 29


def test_program_with_a_push_instruction(cpu):
    reset_state_machines(cpu)
    assert cpu.read_uint32(FLEVEL) == (0 << RX0_SHIFT)  # RX0 should have 0 items
    cpu.write_uint32(SM0_INSTR, pio_set(PIO_DEST_X, 9))
    cpu.write_uint32(SM0_INSTR, pio_in(PIO_SRC_X, 32))
    cpu.write_uint32(SM0_INSTR, pio_push(False, False))
    assert cpu.read_uint32(FLEVEL) == (1 << RX0_SHIFT)  # RX0 should now have 1 item
    cpu.write_uint32(SM0_INSTR, pio_push(False, False))
    assert cpu.read_uint32(FLEVEL) == (2 << RX0_SHIFT)  # RX0 should now have 2 item
    assert cpu.read_uint32(RXF0) == 9  # What we had in X
    assert cpu.read_uint32(FLEVEL) == (1 << RX0_SHIFT)  # RX0 should now have 1 item
    assert cpu.read_uint32(RXF0) == 0  # ISR should be zeroed after the first push
    assert cpu.read_uint32(FLEVEL) == (0 << RX0_SHIFT)  # RX0 should have 0 items


def test_program_with_an_irq_2_instruction(cpu):
    reset_state_machines(cpu)
    cpu.write_uint32(IRQ, 0xFF)
    cpu.write_uint32(SM0_INSTR, pio_irq(False, False, 2))
    assert cpu.read_uint32(IRQ) == (1 << 2)
    cpu.write_uint32(SM0_INSTR, pio_irq(True, False, 2))
    assert cpu.read_uint32(IRQ) == 0


def test_program_with_an_irq_3_rel_instruction(cpu):
    reset_state_machines(cpu)
    cpu.write_uint32(IRQ, 0xFF)
    cpu.write_uint32(SM2_INSTR, pio_irq(False, False, 0x13))
    assert cpu.read_uint32(IRQ) == (1 << 1)
    cpu.write_uint32(SM2_INSTR, pio_irq(True, False, 0x13))
    assert cpu.read_uint32(IRQ) == 0
    cpu.write_uint32(SM0_INSTR, pio_irq(False, False, 0x13))
    assert cpu.read_uint32(IRQ) == (1 << 3)


def test_program_with_a_wait_irq_7_instruction(cpu):
    reset_state_machines(cpu)
    cpu.write_uint32(IRQ, 0xFF)
    cpu.write_uint32(INSTR_MEM0, pio_mov(PIO_MOV_DEST_X, PIO_SRC_X, PIO_OP_NONE))
    cpu.write_uint32(INSTR_MEM1, pio_wait(True, PIO_WAIT_SRC_IRQ, 7))
    cpu.write_uint32(INSTR_MEM2, pio_jmp(2, PIO_COND_ALWAYS))
    cpu.write_uint32(CTRL, 1)  # Starts State Machine #0
    assert cpu.read_uint32(SM0_ADDR) == 1
    cpu.write_uint32(SM2_INSTR, pio_irq(False, False, 5))  # Set IRQ 5
    assert cpu.read_uint32(SM0_ADDR) == 1
    cpu.write_uint32(SM2_INSTR, pio_irq(False, False, 7))  # Set IRQ 7
    assert cpu.read_uint32(SM0_ADDR) == 2
    assert cpu.read_uint32(IRQ) == (1 << 5)  # Wait should have cleared IRQ 7


def test_program_with_a_wait_0_irq_7_instruction(cpu):
    reset_state_machines(cpu)
    cpu.write_uint32(IRQ, 0xFF)
    cpu.write_uint32(SM0_INSTR, pio_irq(False, False, 7))  # Set IRQ 7
    cpu.write_uint32(INSTR_MEM0, pio_mov(PIO_MOV_DEST_X, PIO_SRC_X, PIO_OP_NONE))
    cpu.write_uint32(INSTR_MEM1, pio_wait(False, PIO_WAIT_SRC_IRQ, 7))
    cpu.write_uint32(INSTR_MEM2, pio_jmp(2, PIO_COND_ALWAYS))
    cpu.write_uint32(CTRL, 1)  # Starts State Machine #0
    assert cpu.read_uint32(SM0_ADDR) == 1
    cpu.write_uint32(IRQ, 1 << 7)  # Clear IRQ 7
    assert cpu.read_uint32(SM0_ADDR) == 2


def test_updates_intr_after_executing_an_irq_2_instruction(cpu):
    reset_state_machines(cpu)
    cpu.write_uint32(IRQ, 0xFF)  # Clear all IRQs
    cpu.write_uint32(SM2_INSTR, pio_irq(False, False, 0x2))
    assert (cpu.read_uint32(INTR) & 0xF00) == (1 << 10)


def test_compares_x_to_0xffffffff_after_mov_x_not_null_instruction(cpu):
    reset_state_machines(cpu)
    cpu.write_uint32(TXF0, 0xFFFFFFFF)
    cpu.write_uint32(SM0_INSTR, pio_pull(False, False))
    cpu.write_uint32(SM0_INSTR, pio_mov(PIO_DEST_X, PIO_SRC_NULL, PIO_OP_INVERT))  # X <- ~0 = 0xffffffff
    cpu.write_uint32(SM0_INSTR, pio_out(PIO_DEST_Y, 32))  # Y <- 0xffffffff
    cpu.write_uint32(SM0_INSTR, pio_jmp(8, PIO_COND_ALWAYS))
    cpu.write_uint32(SM0_INSTR, pio_jmp(16, PIO_COND_XNEY))  # Shouldn't take the jump
    assert cpu.read_uint32(SM0_ADDR) == 8  # Assert that the 2nd jump wasn't taken


def test_wraps_the_program_when_it_gets_to_execctrl_wrap_top(cpu):
    reset_state_machines(cpu)
    cpu.write_uint32(SM0_EXECCTRL, (1 << EXECCTRL_WRAP_BOTTOM_SHIFT) | (2 << EXECCTRL_WRAP_TOP_SHIFT))

    # State machine Pseudo code:
    #   jmp .label2
    # .wrap_target
    # label1:
    #   jmp label1
    # label2:
    #   mov x, null
    # .wrap
    # label3:
    #   jmp label3

    cpu.write_uint32(INSTR_MEM0, pio_jmp(2, PIO_COND_ALWAYS))
    cpu.write_uint32(INSTR_MEM1, pio_jmp(1, PIO_COND_ALWAYS))  # infinite loop
    cpu.write_uint32(INSTR_MEM2, pio_mov(PIO_DEST_X, PIO_SRC_X, PIO_OP_NONE))
    cpu.write_uint32(INSTR_MEM3, pio_jmp(3, PIO_COND_ALWAYS))  # infinite loop

    cpu.write_uint32(CTRL, 1)  # Starts State Machine #0
    assert cpu.read_uint32(SM0_ADDR) == 1


def test_automatically_pulls_when_autopull_is_enabled(cpu):
    reset_state_machines(cpu)
    cpu.write_uint32(SM0_SHIFTCTRL, SHIFTCTRL_AUTOPULL | (4 << SHIFTCTRL_PULL_THRESH_SHIFT) | OUT_SHIFTDIR)
    cpu.write_uint32(TXF0, 0x5)
    cpu.write_uint32(TXF0, 0x6)
    cpu.write_uint32(TXF0, 0x7)

    cpu.write_uint32(SM0_INSTR, pio_out(PIO_DEST_X, 4))  # 5
    cpu.write_uint32(SM0_INSTR, pio_in(PIO_SRC_X, 4))
    cpu.write_uint32(SM0_INSTR, pio_out(PIO_DEST_X, 4))  # 6
    cpu.write_uint32(SM0_INSTR, pio_in(PIO_SRC_X, 4))
    cpu.write_uint32(SM0_INSTR, pio_out(PIO_DEST_X, 4))  # 7
    cpu.write_uint32(SM0_INSTR, pio_in(PIO_SRC_X, 4))
    cpu.write_uint32(SM0_INSTR, pio_push(False, False))

    assert cpu.read_uint32(RXF0) == 0x567


def test_does_not_autopull_in_the_middle_of_out_instruction(cpu):
    reset_state_machines(cpu)
    cpu.write_uint32(SM0_SHIFTCTRL, SHIFTCTRL_AUTOPULL | (4 << SHIFTCTRL_PULL_THRESH_SHIFT) | OUT_SHIFTDIR)
    cpu.write_uint32(TXF0, 0x25)
    cpu.write_uint32(TXF0, 0x36)

    cpu.write_uint32(SM0_INSTR, pio_out(PIO_DEST_X, 8))
    cpu.write_uint32(SM0_INSTR, pio_in(PIO_SRC_X, 8))
    cpu.write_uint32(SM0_INSTR, pio_push(False, False))

    assert cpu.read_uint32(RXF0) == 0x25


def test_stalls_until_the_tx_fifo_fills_when_executing_an_out_instruction_with_autopull(cpu):
    reset_state_machines(cpu)
    cpu.write_uint32(SM0_SHIFTCTRL, SHIFTCTRL_AUTOPULL | (4 << SHIFTCTRL_PULL_THRESH_SHIFT) | OUT_SHIFTDIR)

    cpu.write_uint32(SM0_INSTR, pio_out(PIO_DEST_X, 4))

    assert (cpu.read_uint32(SM0_EXECCTRL) & EXECCTRL_EXEC_STALLED) == EXECCTRL_EXEC_STALLED

    cpu.write_uint32(TXF0, 0x36)  # Unstalls the machine
    assert (cpu.read_uint32(SM0_EXECCTRL) & EXECCTRL_EXEC_STALLED) == 0


def test_automatically_pushes_when_autopush_is_enabled(cpu):
    reset_state_machines(cpu)
    cpu.write_uint32(SM0_SHIFTCTRL, SHIFTCTRL_AUTOPUSH | (8 << SHIFTCTRL_PUSH_THRESH_SHIFT) | OUT_SHIFTDIR)

    cpu.write_uint32(SM0_INSTR, pio_set(PIO_DEST_X, 0x13))
    cpu.write_uint32(SM0_INSTR, pio_in(PIO_SRC_X, 8))

    assert cpu.read_uint32(RXF0) == 0x13


def test_only_autopushes_at_the_end_of_the_in_instruction(cpu):
    reset_state_machines(cpu)
    cpu.write_uint32(SM0_SHIFTCTRL, SHIFTCTRL_AUTOPUSH | (8 << SHIFTCTRL_PUSH_THRESH_SHIFT) | OUT_SHIFTDIR)

    cpu.write_uint32(SM0_INSTR, pio_mov(PIO_DEST_X, PIO_SRC_NULL, PIO_OP_INVERT))
    cpu.write_uint32(SM0_INSTR, pio_in(PIO_SRC_X, 16))

    assert cpu.read_uint32(RXF0) == 0xFFFF


def test_stalls_until_the_rx_fifo_has_capacity_when_executing_an_in_instruction_with_autopush(cpu):
    reset_state_machines(cpu)
    cpu.write_uint32(SM0_SHIFTCTRL, SHIFTCTRL_AUTOPUSH | (8 << SHIFTCTRL_PUSH_THRESH_SHIFT) | OUT_SHIFTDIR)

    cpu.write_uint32(SM0_INSTR, pio_set(PIO_DEST_X, 15))
    cpu.write_uint32(SM0_INSTR, pio_in(PIO_SRC_X, 8))
    cpu.write_uint32(SM0_INSTR, pio_set(PIO_DEST_X, 16))
    cpu.write_uint32(SM0_INSTR, pio_in(PIO_SRC_X, 8))
    cpu.write_uint32(SM0_INSTR, pio_set(PIO_DEST_X, 17))
    cpu.write_uint32(SM0_INSTR, pio_in(PIO_SRC_X, 8))
    cpu.write_uint32(SM0_INSTR, pio_set(PIO_DEST_X, 18))
    cpu.write_uint32(SM0_INSTR, pio_in(PIO_SRC_X, 8))
    cpu.write_uint32(SM0_INSTR, pio_set(PIO_DEST_X, 19))
    cpu.write_uint32(SM0_INSTR, pio_in(PIO_SRC_X, 8))  # Should fill the RX FIFO and stall!

    assert (cpu.read_uint32(SM0_EXECCTRL) & EXECCTRL_EXEC_STALLED) == EXECCTRL_EXEC_STALLED

    assert cpu.read_uint16(RXF0) == 15  # Unstalls the machine
    assert (cpu.read_uint32(SM0_EXECCTRL) & EXECCTRL_EXEC_STALLED) == 0
    assert cpu.read_uint16(RXF0) == 16
    assert cpu.read_uint16(RXF0) == 17
    assert cpu.read_uint16(RXF0) == 18
    assert cpu.read_uint16(RXF0) == 19


def test_updates_txnfull_flag_in_intr_according_to_the_level_of_the_tx_fifo_issue_73(cpu):
    reset_state_machines(cpu)
    cpu.write_uint32(IRQ0_INTE, INTR_SM0_TXNFULL)
    assert (cpu.read_uint32(INTR) & INTR_SM0_TXNFULL) == INTR_SM0_TXNFULL
    assert (cpu.read_uint32(NVIC_ISPR) & PIO_IRQ0) == PIO_IRQ0
    cpu.write_uint32(TXF0, 1)
    cpu.write_uint32(TXF0, 2)
    cpu.write_uint32(TXF0, 3)
    cpu.write_uint32(NVIC_ICPR, PIO_IRQ0)
    assert (cpu.read_uint32(INTR) & INTR_SM0_TXNFULL) == INTR_SM0_TXNFULL
    cpu.write_uint32(TXF0, 3)
    cpu.write_uint32(NVIC_ICPR, PIO_IRQ0)

    # At this point, TX FIFO should be full and the flag/interrupt will be cleared
    assert (cpu.read_uint32(INTR) & INTR_SM0_TXNFULL) == 0
    assert (cpu.read_uint32(NVIC_ISPR) & PIO_IRQ0) == 0

    # Pull an item, so TX FIFO should be "not empty" again
    cpu.write_uint32(SM0_INSTR, pio_pull(False, False))
    assert (cpu.read_uint32(INTR) & INTR_SM0_TXNFULL) == INTR_SM0_TXNFULL
    assert (cpu.read_uint32(NVIC_ISPR) & PIO_IRQ0) == PIO_IRQ0


def test_sets_txstall_flag_in_fdebug_when_pulling_from_empty_tx_fifo_and_clears_it_only_once_unstalled(cpu):
    reset_state_machines(cpu)

    # Clear FDEBUG register
    cpu.write_uint32(FDEBUG, 0xFFFFFFFF)
    assert cpu.read_uint32(FDEBUG) == 0

    # Make sure TX FIFO is empty
    assert cpu.read_uint32(FLEVEL) == (0 << TX0_SHIFT)

    # Attempt to pull from an empty TX FIFO
    cpu.write_uint32(SM0_INSTR, pio_pull(False, False))

    # Check that the TXSTALL flag is set
    assert (cpu.read_uint32(FDEBUG) & (FDEBUG_TXSTALL << 0)) == (FDEBUG_TXSTALL << 0)

    # Try clearing the TXSTALL flag while TX FIFO is still empty
    cpu.write_uint32(FDEBUG, FDEBUG_TXSTALL << 0)

    # Verify the flag is NOT cleared because TX is still stalled
    assert (cpu.read_uint32(FDEBUG) & (FDEBUG_TXSTALL << 0)) == (FDEBUG_TXSTALL << 0)

    # Push something to TX FIFO to unstall it
    cpu.write_uint32(TXF0, 42)

    # Now try clearing the flag again
    cpu.write_uint32(FDEBUG, FDEBUG_TXSTALL << 0)

    # Now the flag should be cleared
    assert (cpu.read_uint32(FDEBUG) & (FDEBUG_TXSTALL << 0)) == 0


def test_updates_rxfnempty_flag_in_intr_according_to_the_level_of_the_rx_fifo_issue_73(cpu):
    reset_state_machines(cpu)
    cpu.write_uint32(IRQ0_INTE, INTR_SM0_RXNEMPTY)
    cpu.write_uint32(NVIC_ICPR, PIO_IRQ0)

    # RX FIFO starts empty
    assert (cpu.read_uint32(INTR) & INTR_SM0_RXNEMPTY) == 0
    assert (cpu.read_uint32(NVIC_ISPR) & PIO_IRQ0) == 0

    # Push an item so it's no longer empty...
    cpu.write_uint32(SM0_INSTR, pio_push(False, False))
    assert (cpu.read_uint32(INTR) & INTR_SM0_RXNEMPTY) == INTR_SM0_RXNEMPTY
    assert (cpu.read_uint32(NVIC_ISPR) & PIO_IRQ0) == PIO_IRQ0

    # Read the item and it should be empty again
    cpu.read_uint32(RXF0)
    cpu.write_uint32(NVIC_ICPR, PIO_IRQ0)
    assert (cpu.read_uint32(INTR) & INTR_SM0_RXNEMPTY) == 0
    assert (cpu.read_uint32(NVIC_ISPR) & PIO_IRQ0) == 0


def test_enabling_a_dma_fed_sm_does_not_run_steps_synchronously_when_a_simulator_owns_the_rp2040():
    """Regression test for docs/records/0043-pio-dma-first-batch-race.md - found investigating
    0027's v1.23.0-vs-v1.28.0 CYW43 WiFi regression. Real MicroPython v1.23.0's `cyw43-driver`
    (an older pinned pico-sdk) enables its gSPI PIO state machine, then immediately busy-polls
    `PIO.FDEBUG` directly for the TX-stall bit, with no other synchronization; v1.28.0's newer
    pico-sdk instead waits on the DMA channel's own completion status first, which happened to mask
    the same underlying race rather than avoid it - see this record for the full derivation.

    `RPPIO.write_uint32()`'s `CTRL` branch used to call `self._step_batch()` (up to 1000 PIO steps)
    synchronously, inline, inside the very same MMIO write that transitions a stopped PIO instance
    to running - with no `SimulationClock.tick()` calls interleaved during that burst at all
    (`tick()` only ever runs from `_execute_batch.py`/`native/_simulator.pyx`'s own outer
    per-CPU-instruction loop). A DMA-fed PIO TX FIFO (4 words deep) drains in ~8 `step()` calls
    here (a plain `pull`+`jmp` 2-instruction loop, no GPIO/pin config needed to reproduce this) -
    far short of the 1000-step ceiling - so that burst could genuinely outrun the DMA channel's own
    alarm-paced refill (`RPDMAChannel.schedule_transfer()`, `peripherals/dma.py`) and leave the
    state machine prematurely `FDEBUG_TXSTALL`'d with most of a larger transfer still undelivered,
    the instant a real `Simulator` owns this `RP2040` - exactly `cyw43_bus_pio_spi.c`'s own
    TX-only `cyw43_spi_transfer()` shape for CYW43's firmware/CLM download block writes.

    Uses a real `Simulator()`, not the no-owning-`Simulator` `cpu` fixture the rest of this file
    uses - the bug is specific to `rp2040.simulator is not None` (see `RPPIO.write_uint32()`'s own
    CTRL-branch comment for why `tests/test_pio.py`'s fixture never hit it: driving `RPPIO` with no
    outer per-instruction loop to defer to still needs the synchronous first batch)."""
    simulator = Simulator()
    rp2040 = simulator.rp2040

    word_count = 9  # more than the 4-word-deep TX FIFO, so one synchronous burst can't finish it.
    src_addr = 0x2001_0000
    for i in range(word_count):
        rp2040.write_uint32(src_addr + i * 4, 0xA000_0000 | i)

    # PIO0 SM0 program: PULL (block until TX FIFO has data) then loop back unconditionally -
    # consumes exactly one FIFO word per two step() calls.
    rp2040.write_uint32(INSTR_MEM0, pio_pull(False, False))
    rp2040.write_uint32(INSTR_MEM1, pio_jmp(0, PIO_COND_ALWAYS))

    # Historical note: RPDMA.reset() used to clear self.dreq unconditionally, discarding
    # SM0's construction-time DREQ_PIO0_TX0=True and starving this DMA channel until something
    # else nudged the peripheral's FIFO (fixed - see docs/tasks/main-spi-hang.md and
    # RPDMA.reset()'s own comment). This write predates that fix and is no longer required to
    # establish the initial DREQ level, but is kept anyway via the public TXF0 write path
    # (StateMachine.write_fifo() recomputes the DREQ level as a side effect) rather than a
    # native-Cython-inaccessible private method. The extra word just becomes SM0's own first
    # pull, ahead of the DMA-fed ones - harmless, this test only checks word *count*/completion,
    # not content.
    rp2040.write_uint32(TXF0, 0)

    # DMA channel 0: word_count 32-bit transfers from src_addr into PIO0 SM0's own TX FIFO
    # register (TXF0), paced by that SM's own TX dreq - the same shape
    # cyw43_bus_pio_spi.c's cyw43_spi_transfer() sets up for a TX-only gSPI transfer.
    dma_base = 0x5000_0000
    read_addr, write_addr, trans_count, ctrl_trig = (
        dma_base,
        dma_base + 0x4,
        dma_base + 0x8,
        dma_base + 0xC,
    )
    rp2040.write_uint32(read_addr, src_addr)
    rp2040.write_uint32(write_addr, TXF0)
    rp2040.write_uint32(trans_count, word_count)
    dreq_pio0_tx0 = 0
    en_bit, incr_read_bit, data_size_32 = 1, 1 << 4, 2 << 2
    rp2040.write_uint32(ctrl_trig, en_bit | incr_read_bit | data_size_32 | (dreq_pio0_tx0 << 15))

    # Let the DMA channel's own alarm chain fill the FIFO (up to 4 words) - a few idle
    # _execute_batch() passes reproduce the same clock.tick() cadence real CPU instructions would,
    # without needing an actual ARM program loaded.
    rp2040.core.waiting = True
    for _ in range(4):
        simulator.stopped = False
        simulator._execute_batch()
    simulator.stop()

    machine = rp2040.pio[0].machines[0]
    assert machine.cycles == 0  # SM0 not yet enabled - only DMA has run so far.

    # Enable SM0 - the exact MMIO write cyw43_bus_pio_spi.c's pio_sm_set_enabled(true) performs,
    # immediately followed (in real firmware) by a tight poll of PIO.FDEBUG.
    rp2040.write_uint32(CTRL, 1 | (1 << 4))  # SM0 enable (bit 0) + SM0 restart (bit 4)

    # The fix under test: this write must not have run any steps synchronously - only
    # _execute_batch()'s own per-CPU-instruction loop may step a Simulator-owned RPPIO, so
    # clock.tick() (which is what lets the DMA channel's pending alarm actually refill the FIFO)
    # always gets a turn between every single PIO step. Before the fix, _step_batch() ran inline
    # right here and both of these were already false.
    assert machine.cycles == 0
    assert not (rp2040.pio[0].fdebug & FDEBUG_TXSTALL)

    # Now genuinely drive it forward the way a real boot does (via _execute_batch()'s own outer
    # loop) - the whole transfer must complete correctly, not just the first FIFO's worth.
    dma_ctrl = 0
    busy_bit = 1 << 24
    for _ in range(20):
        simulator.stopped = False
        simulator._execute_batch()
        dma_ctrl = rp2040.read_uint32(ctrl_trig)
        if not (dma_ctrl & busy_bit):
            break
    simulator.stop()

    assert not (dma_ctrl & busy_bit)  # DMA channel finished, not stuck mid-transfer.
    assert rp2040.read_uint32(trans_count) == 0
    assert machine.cycles > 0  # SM0 genuinely ran (not just DMA looping without a consumer).
