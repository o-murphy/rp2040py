"""The PIO's two shift registers are 32 bits wide: a left shift must drop what leaves the register.

The pure-Python ``StateMachine`` used to let ``OUT`` (left shift) and ``IN`` (left shift) grow ``output_shift_reg`` / ``input_shift_reg`` past 32 bits - a value no real
register holds and one the native state machine never produced (its fields are C ``unsigned int``). Found by the lockstep differential of
tests/utils/pio_diff.py (docs/records/0096-cpp-mcu-core.md, Phase 3), which compares the pure-Python PIO with the native one; the width is checked here directly,
on whichever implementation the facade gives and on the pure one.
"""

import pytest

from rp2040py.peripherals import _pio, _state_machine
from rp2040py.rp2040 import RP2040
from rp2040py.utils.pio_assembler import PIO_DEST_NULL, PIO_SRC_NULL, pio_in, pio_out

LEFT_SHIFT = 0  # SHIFTCTRL.IN_SHIFTDIR / OUT_SHIFTDIR = 0: shift left


def _machine(kind: str):
    chip = RP2040()
    if kind == "pure":
        original = _pio.StateMachine
        _pio.StateMachine = _state_machine.StateMachine  # the facade would hand the pure PIO the native class
        try:
            pio = _pio.RPPIO(chip, "PIO0", 7, 0)
        finally:
            _pio.StateMachine = original
        return pio.machines[0]
    return chip.pio[0].machines[0]


@pytest.mark.parametrize("kind", ["default", "pure"])
def test_out_shifting_left_keeps_the_output_shift_register_32_bits_wide(kind):
    machine = _machine(kind)
    machine.shift_ctrl = LEFT_SHIFT
    machine.output_shift_reg = 0xFFFFFFFF
    machine.execute_instruction(pio_out(PIO_DEST_NULL, 8))
    assert machine.output_shift_reg == 0xFFFFFF00


@pytest.mark.parametrize("kind", ["default", "pure"])
def test_in_shifting_left_keeps_the_input_shift_register_32_bits_wide(kind):
    machine = _machine(kind)
    machine.shift_ctrl = LEFT_SHIFT
    machine.input_shift_reg = 0xFFFFFFFF
    machine.execute_instruction(pio_in(PIO_SRC_NULL, 8))
    assert machine.input_shift_reg == 0xFFFFFF00
