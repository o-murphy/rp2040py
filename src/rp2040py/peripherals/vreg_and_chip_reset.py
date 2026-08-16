"""VREG_AND_CHIP_RESET (`0x40064000`) - the on-chip voltage regulator plus the chip-level reset
status register.

Only exists because leaving this block unimplemented is actively harmful, not merely incomplete:
`BasePeripheral.read_uint32()` answers an unimplemented read with `0xFFFFFFFF`, and the bootrom
reads `CHIP_RESET` and tests `PSM_RESTART_FLAG` (bit 24) on its way through the reset path. All-ones
means that bit reads as permanently set, so the bootrom takes the "debugger issued a psm_restart"
branch, writes the bit to clear it (the write also goes nowhere), re-reads it as set, and loops
forever - which is exactly how CircuitPython 10.x hung on boot. See
docs/records/0050-vreg-chip-reset-bootrom-loop.md.

Nothing here models real regulator behavior (voltage selection has no meaning in an emulator); the
registers exist so their *values* are right.
"""

from rp2040py.peripherals.peripheral import BasePeripheral

__all__ = ("RPVREGAndChipReset",)

VREG = 0x00
BOD = 0x04
CHIP_RESET = 0x08

# Datasheet reset values (RP2040 §2.10.7). VREG: EN=1, VSEL=0b1011 (1.10V). BOD: EN=1, VSEL=0b1001.
_VREG_RESET = 0x000000B1
_BOD_RESET = 0x00000091

# CHIP_RESET's read-only "how did we get here" flags. HAD_POR is what a real power-on reset leaves
# set, and a fresh emulated chip is precisely that case.
HAD_POR = 1 << 8
HAD_RUN = 1 << 16
HAD_PSM_RESTART = 1 << 20
# The one writable bit: set by a debugger's psm_restart, cleared by writing 1 to it (which is what
# the bootrom does above).
PSM_RESTART_FLAG = 1 << 24


class RPVREGAndChipReset(BasePeripheral):
    def __init__(self, rp2040: "object", name: str) -> None:  # type: ignore[override]
        super().__init__(rp2040, name)  # type: ignore[arg-type]
        self.vreg = _VREG_RESET
        self.bod = _BOD_RESET
        self.chip_reset = HAD_POR

    def read_uint32(self, offset: int) -> int:
        if offset == VREG:
            return self.vreg
        if offset == BOD:
            return self.bod
        if offset == CHIP_RESET:
            return self.chip_reset
        return super().read_uint32(offset)

    def write_uint32(self, offset: int, value: int) -> None:
        if offset == VREG:
            self.vreg = value & 0xFFFFFFFF
            return
        if offset == BOD:
            self.bod = value & 0xFFFFFFFF
            return
        if offset == CHIP_RESET:
            # Write-1-to-clear, and only for PSM_RESTART_FLAG - the other three flags are read-only
            # status the bootrom is allowed to inspect but not to forge.
            if value & PSM_RESTART_FLAG:
                self.chip_reset &= ~PSM_RESTART_FLAG
            return
        super().write_uint32(offset, value)
