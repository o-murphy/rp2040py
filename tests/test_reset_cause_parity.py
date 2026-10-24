"""The pure-Python chip and the native chip record the reset cause alike (docs/records/0096-cpp-mcu-core.md, step 1, F1): the same triggers, from a dirtied watchdog and a dirtied CHIP_RESET,
leave the same REASON, SCRATCH and CHIP_RESET - and the same cause the 0089 section 1.3 table lists.
"""

import pytest
from utils.chip_pair import Native, PurePython, make_chip

from rp2040py.peripherals._vreg_and_chip_reset import CHIP_RESET, HAD_POR, HAD_PSM_RESTART, HAD_RUN
from rp2040py.peripherals._watchdog import CTRL, FORCE, REASON, SCRATCH4, TRIGGER
from rp2040py.reset_cause import ResetCause

MAGIC = 0x6AB73121


def _after(chip_class, cause: ResetCause, start: int):
    chip = make_chip(chip_class)
    chip.watchdog.write_uint32(CTRL, TRIGGER)  # (the default trigger resets the chip, leaving REASON = FORCE)
    chip.watchdog.write_uint32(SCRATCH4, MAGIC)
    chip.vreg_and_chip_reset.record_reset_cause(start)
    chip.enter_reset(cause=cause)
    return (
        chip.watchdog.read_uint32(REASON),
        chip.watchdog.snapshot()["scratch_data"],
        chip.vreg_and_chip_reset.read_uint32(CHIP_RESET) & (HAD_POR | HAD_RUN | HAD_PSM_RESTART),
    )


@pytest.mark.parametrize("start", [HAD_POR, HAD_RUN, HAD_PSM_RESTART])
@pytest.mark.parametrize("cause", list(ResetCause))
def test_the_two_chips_leave_the_same_cause(cause, start):
    assert _after(Native, cause, start) == _after(PurePython, cause, start)


@pytest.mark.parametrize("chip_class", [Native, PurePython], ids=["native", "pure"])
def test_the_table_of_0089_section_1_3(chip_class):
    reason, scratch, chip_reset = _after(chip_class, ResetCause.RUN_PIN, HAD_POR)
    assert (reason, scratch, chip_reset) == (0, (0,) * 8, HAD_RUN)
    reason, scratch, chip_reset = _after(chip_class, ResetCause.POWER_ON, HAD_RUN)
    assert (reason, scratch, chip_reset) == (0, (0,) * 8, HAD_POR)
    reason, scratch, chip_reset = _after(chip_class, ResetCause.WATCHDOG, HAD_RUN)
    assert reason & FORCE and scratch[4] == MAGIC and chip_reset == HAD_RUN
