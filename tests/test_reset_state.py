"""A chip reset puts every register of the blocks it resets back where a fresh chip has it (docs/records/0096-cpp-mcu-core.md, gate G3).

The registers are the ones of `tests/data/rp2040_reset_values.json` (Raspberry Pi's RP2040.svd, flattened by scripts/audit/svd_reset_values.py): every register that can be read without a side
effect is read on a fresh chip, a random word is written to every register of the blocks the reset covers, the chip is reset, and the registers are read again. A register that still holds
the word - a channel address the DMA reset forgot, an interrupt mask the PWM reset forgot - is a block whose reset is not a reset. The same runs on both chips, so the native one is held to it too.
"""

import json
import random
from pathlib import Path

import pytest
from utils.chip_pair import Native, PurePython, make_chip

REGISTERS = json.loads((Path(__file__).parent / "data" / "rp2040_reset_values.json").read_text())["registers"]

# What a chip reset does not bring back to a register's reset value, or what must not be written blindly:
#   the blocks the reset is itself configured through (RESETS, PSM) and the ones that keep their state on purpose (WATCHDOG scratch, VREG_AND_CHIP_RESET's cause), the clock tree and the oscillators
#   (their state is the boot ROM's, see feature-coverage), TBMAN, the core's PPB and SIO (own tests), the USB DPRAM (RAM), and the SVD's VIRTUAL placeholder.
SKIPPED_BLOCKS = (
    "RESETS.", "PSM.", "WATCHDOG.", "VREG_AND_CHIP_RESET.", "CLOCKS.", "PLL_", "XOSC.", "ROSC.", "TBMAN.", "PPB.", "SIO.", "USB_DEVICE_DPRAM.", "VIRTUAL.",
    "PIO0.CTRL", "PIO1.CTRL",  # a write starts state machines on the event loop
)  # fmt: skip


def _selected(registers):
    return [r for r in registers if not r["name"].startswith(SKIPPED_BLOCKS)]


READABLE = [r for r in _selected(REGISTERS) if not r["read_modifies"] and r["mask"]]
WRITABLE = _selected(REGISTERS)


def _snapshot(chip):
    return {r["name"]: chip.read_uint32(r["address"]) for r in READABLE}


@pytest.mark.parametrize("cls", [PurePython, Native], ids=["pure", "native"])
@pytest.mark.parametrize("seed", range(3))
def test_a_reset_brings_every_register_back_to_where_a_fresh_chip_has_it(cls, seed):
    chip = make_chip(cls)
    fresh = _snapshot(chip)
    rng = random.Random(seed)
    for register in WRITABLE:
        chip.write_uint32(register["address"], rng.getrandbits(32) | 1)
    chip.reset(preserve_flash=False, from_watchdog=False)
    after = _snapshot(chip)
    left = {name: (hex(fresh[name]), hex(after[name])) for name in fresh if fresh[name] != after[name]}
    assert not left, (
        f"{len(left)} registers differ from a fresh chip after the reset (fresh, after): {dict(list(left.items())[:12])}"
    )
