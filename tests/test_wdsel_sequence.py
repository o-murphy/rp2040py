"""A watchdog reset resets what PSM.WDSEL selects *and everything after it in the power-on sequence* (datasheet 2.13.4; docs/records/0096-cpp-mcu-core.md, gate G3).

The bits of PSM.WDSEL are in sequence order - ROSC 0, XOSC 1, CLOCKS 2, RESETS 3, BUSFABRIC 4, ROM 5, SRAM0-5 6..11, XIP 12, VREG_AND_CHIP_RESET 13, SIO 14, PROC0 15, PROC1 16 - so selecting one block
selects it and every block above it. The expectations below are written by hand from that order, not taken from either chip: a marker register of the blocks that show it (the oscillator's STARTUP, a
CLOCKS register, a peripheral behind the RESETS controller, the XIP's and SIO's registers) is made non-zero, the watchdog fires with a single bit selected, and the markers that read 0 afterwards are the
blocks that were reset. Both chips; they must agree with the table and, through tests/test_chip_diff.py, with each other.
"""

import pytest
from utils.chip_pair import Native, PurePython, make_chip

from rp2040py.reset_cause import ResetCause

PSM_WDSEL = 0x40010008
RESETS_WDSEL = 0x4000C004
MARKERS = {  # name: a register that is 0 at reset and plain read/write
    "xosc": 0x4002400C,  # XOSC.STARTUP is not 0 at reset (0xC4) - see below
    "clocks": 0x40008004,  # CLOCKS.CLK_GPOUT0_DIV
    "peripherals": 0x40034024,  # UART0.UARTIBRD (behind the RESETS controller)
    "xip": 0x14000000,  # XIP_CTRL.CTRL
    "sio": 0xD0000010,  # SIO.GPIO_OUT
}
# the block of each marker as the lowest PSM.WDSEL bit that resets it
FIRST_BIT = {"xosc": 1, "clocks": 2, "peripherals": 3, "xip": 12, "sio": 14}


@pytest.mark.parametrize("cls", [PurePython, Native], ids=["pure", "native"])
@pytest.mark.parametrize("bit", range(17))
def test_a_single_wdsel_bit_resets_its_block_and_everything_after_it(cls, bit):
    chip = make_chip(cls)
    before = {}
    for name, address in MARKERS.items():
        chip.write_uint32(address, 0x55 if name != "xosc" else 0x77)
        before[name] = chip.read_uint32(address)
    chip.write_uint32(PSM_WDSEL, 1 << bit)
    chip.write_uint32(RESETS_WDSEL, 0)
    chip.enter_reset(cause=ResetCause.WATCHDOG)
    for name, address in MARKERS.items():
        was_reset = chip.read_uint32(address) != before[name]
        assert was_reset == (bit <= FIRST_BIT[name]), (
            f"WDSEL bit {bit}: {name} {'was not' if not was_reset else 'was'} reset"
        )
