"""Two corners found by the pure-vs-native single-step differential (tests/test_cpu_parity.py, record 0096 Phase 2 step 4b).

Both used to differ between the cores; each is pinned on both so the next port cannot reintroduce it."""

import pytest
from utils.chip_pair import Native, PurePython, make_chip

from rp2040py.utils.assembler import opcode_ror

CODE = 0x20000100


@pytest.fixture(params=[PurePython, Native], ids=["pure", "native"])
def chip(request):
    return make_chip(request.param)


def test_a_stack_pointer_written_unaligned_into_the_inactive_bank_comes_back_aligned(chip):
    """`sp` always masks its low two bits; the bank that is switched in must too. The pure core did, the Cython one copied the
    banked word as it was."""
    core = chip.core
    core.write_special_register(9, 0x20001003)  # SYSM_PSP while running on MSP: lands in the banked word, unmasked
    core.write_special_register(20, 2)  # CONTROL.SPSEL = 1: switch to PSP

    assert core.sp == 0x20001000


def test_rotating_by_a_multiple_of_32_leaves_the_value_alone(chip):
    """ROR by 32 (register value 0x20, low byte 32 % 32 = 0) used to push `input << 32` through the native `u32` helper of a
    pure-Python core, which takes a C `long long`."""
    core = chip.core
    core.registers[0] = 0x20  # the shift amount
    core.registers[1] = 0x80000001
    chip.write_uint16(CODE, opcode_ror(1, 0))
    core.pc = CODE

    core.execute_instruction()

    assert core.registers[1] == 0x80000001
    assert core.n and not core.z and core.c
