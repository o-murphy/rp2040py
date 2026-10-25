"""The PIO's two shift registers are 32 bits wide: a left shift must drop what leaves the register.

The pure-Python ``StateMachine`` used to let ``OUT`` (left shift) and ``IN`` (left shift) grow ``output_shift_reg`` / ``input_shift_reg`` past 32 bits - a value no real
register holds and one the native state machine never produced (its fields are C ``unsigned int``). Found by the lockstep differential of
tests/utils/pio_diff.py (docs/records/0096-cpp-mcu-core.md, Phase 3), which compares the pure-Python PIO with the native one; the width is checked here directly,
on whichever implementation the facade gives and on the pure one.
"""

import pytest
from utils.pio_diff import Rig

from rp2040py.utils.pio_assembler import (
    PIO_DEST_NULL,
    PIO_MOV_DEST_ISR,
    PIO_OP_INVERT,
    PIO_SRC_NULL,
    pio_in,
    pio_mov,
    pio_out,
)

PIO0 = 0x50200000
TXF0, SM0_SHIFTCTRL, SM0_INSTR = 0x10, 0xD0, 0xD8
PULL = 0x8080
LEFT_SHIFT = 0  # SHIFTCTRL.IN_SHIFTDIR / OUT_SHIFTDIR = 0: shift left


@pytest.mark.parametrize("kind", ["default", "pure"])
def test_out_shifting_left_keeps_the_output_shift_register_32_bits_wide(kind):
    chip = Rig(kind).chip
    chip.write_uint32(PIO0 + SM0_SHIFTCTRL, LEFT_SHIFT)
    chip.write_uint32(PIO0 + TXF0, 0xFFFFFFFF)
    chip.write_uint32(PIO0 + SM0_INSTR, PULL)  # the OSR holds 0xFFFFFFFF
    chip.write_uint32(PIO0 + SM0_INSTR, pio_out(PIO_DEST_NULL, 8))
    assert chip.pio[0].snapshot()["machines"][0]["output_shift_reg"] == 0xFFFFFF00


@pytest.mark.parametrize("kind", ["default", "pure"])
def test_in_shifting_left_keeps_the_input_shift_register_32_bits_wide(kind):
    chip = Rig(kind).chip
    chip.write_uint32(PIO0 + SM0_SHIFTCTRL, LEFT_SHIFT)
    chip.write_uint32(
        PIO0 + SM0_INSTR, pio_mov(PIO_MOV_DEST_ISR, PIO_SRC_NULL, PIO_OP_INVERT)
    )  # the ISR holds 0xFFFFFFFF
    chip.write_uint32(PIO0 + SM0_INSTR, pio_in(PIO_SRC_NULL, 8))
    assert chip.pio[0].snapshot()["machines"][0]["input_shift_reg"] == 0xFFFFFF00
