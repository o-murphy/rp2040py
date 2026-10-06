"""PIO behaviours the datasheet pins down that the reference used to get wrong (docs/records/0098-datasheet-conformance-audit.md). Each test runs on the facade's state machine
and on the pure-Python one."""

import pytest

from rp2040py.peripherals import _pio, _state_machine
from rp2040py.rp2040 import RP2040
from rp2040py.utils.pio_assembler import PIO_DEST_NULL, pio_out

PUSH = 0x8000  # PUSH, block 0, iffull 0
PUSH_IFFULL = PUSH | (1 << 6)
PULL = 0x8000 | 0x80
PULL_IFEMPTY = PULL | (1 << 6)


def _machine(kind: str):
    chip = RP2040()
    if kind == "pure":
        original = _pio.StateMachine
        _pio.StateMachine = _state_machine.StateMachine
        try:
            pio = _pio.RPPIO(chip, "PIO0", 7, 0)
        finally:
            _pio.StateMachine = original
        return pio.machines[0]
    return chip.pio[0].machines[0]


KINDS = ["default", "pure"]


@pytest.mark.parametrize("kind", KINDS)
def test_push_iffull_tests_the_threshold_with_autopush_off(kind):
    machine = _machine(kind)
    machine.shift_ctrl = 8 << 20  # PUSH_THRESH 8, AUTOPUSH off
    machine.input_shift_count = 3
    machine.input_shift_reg = 0x55
    machine.execute_instruction(PUSH_IFFULL)
    assert machine.rx_fifo.item_count == 0 and machine.input_shift_reg == 0x55  # 3 < 8: "do nothing"
    machine.input_shift_count = 8
    machine.execute_instruction(PUSH_IFFULL)
    assert machine.rx_fifo.item_count == 1 and machine.input_shift_reg == 0


@pytest.mark.parametrize("kind", KINDS)
def test_pull_ifempty_tests_the_threshold_with_autopull_off(kind):
    machine = _machine(kind)
    machine.shift_ctrl = 16 << 25  # PULL_THRESH 16, AUTOPULL off
    machine.tx_fifo.push(0xAB)
    machine.output_shift_count = 8
    machine.execute_instruction(PULL_IFEMPTY)
    assert machine.tx_fifo.item_count == 1  # 8 < 16
    machine.output_shift_count = 16
    machine.execute_instruction(PULL_IFEMPTY)
    assert machine.tx_fifo.item_count == 0 and machine.output_shift_reg == 0xAB and machine.output_shift_count == 0


@pytest.mark.parametrize("kind", KINDS)
def test_pull_is_a_no_op_behind_an_autopull_while_the_osr_is_full(kind):
    machine = _machine(kind)
    machine.shift_ctrl = (1 << 17) | (8 << 25)  # AUTOPULL, PULL_THRESH 8
    machine.tx_fifo.push(0x11)
    machine.output_shift_count = 0
    machine.output_shift_reg = 0x77
    machine.execute_instruction(PULL)
    assert machine.tx_fifo.item_count == 1 and machine.output_shift_reg == 0x77  # "a no-op if the OSR is full"
    machine.output_shift_count = 3
    machine.execute_instruction(PULL)
    assert machine.tx_fifo.item_count == 0 and machine.output_shift_reg == 0x11


@pytest.mark.parametrize("kind", KINDS)
def test_out_32_empties_the_osr_and_a_reset_machine_starts_with_an_empty_osr(kind):
    machine = _machine(kind)
    assert machine.output_shift_count == 32  # "At reset ... OSR to 32 (nothing left to be shifted out)"
    machine.output_shift_reg = 0xDEADBEEF
    machine.output_shift_count = 0
    machine.execute_instruction(pio_out(PIO_DEST_NULL, 32))
    assert machine.output_shift_reg == 0 and machine.output_shift_count == 32


@pytest.mark.parametrize("kind", KINDS)
def test_execctrl_and_shiftctrl_reserved_bits_are_not_stored(kind):
    machine = _machine(kind)
    machine.write_uint32(0x04, 0xFFFFFFFF)  # SMx_EXECCTRL (offset from SM0_CLKDIV)
    machine.write_uint32(0x08, 0xFFFFFFFF)  # SMx_SHIFTCTRL
    assert machine.exec_ctrl == 0x7FFFFF9F
    assert machine.shift_ctrl == 0xFFFF0000
