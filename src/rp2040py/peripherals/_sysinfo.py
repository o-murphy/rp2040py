"""SYSINFO: the chip's identity registers (RP2040 datasheet 2.20), the pure-Python reference that the native block (`native/core/sysinfo.hpp`) is held to by `tests/test_ident_diff.py`.

All three registers are read-only. A write to one is ignored, as on the chip (the reference used to log it as an "unimplemented peripheral write", which it is not); a write to an offset that is
no register still logs that.

What is sourced and what is not:

- `CHIP_ID` is `REVISION[31:28] | PART[27:12] | MANUFACTURER[11:0]`. pico-sdk's `platform.c` (`rp2040_chip_version()`) asserts `MANUFACTURER_RPI == 0x927` and `PART_RP2 == 0x2`, and `platform.h` documents
  the revision as "1 for B0/B1, 2 for B2" - the register's reset value in `hardware/regs/sysinfo.h` (revision 2, manufacturer 0x926) disagrees with both and is not what the code expects. The revision
  follows the bootrom that is loaded: the version byte at ROM address 0x13 is "1 for RP2040-B0, 2 for B1, 3 for B2" (`rp2040_rom_version()`), so B2's ROM reports chip revision 2 and the others 1. A chip
  with no bootrom loaded reads revision 1.
- `PLATFORM` reads 0x2 (ASIC). The datasheet's table gives both fields reset 0x0 and its overview says the register "will always read as 1"; neither matches the value this model has always returned (it
  came from rp2040js with the comment "verified against the silicon"), which is kept. **Not independently sourced.**
- `GITREF_RP2040` reads 0xE0C912E8, likewise from rp2040js. **Not independently sourced.**
"""

from rp2040py.peripherals.peripheral import BasePeripheral

__all__ = ("RP2040SysInfo", "chip_revision", "rom_version")

CHIP_ID = 0
PLATFORM = 0x4
GITREF_RP2040 = 0x40

MANUFACTURER = 0x927
PART = 0x0002
# The bootrom's version byte: "rp2040_rom_version() ... *(uint8_t*)0x13", little-endian, so bits 31:24 of the word at 0x10
ROM_VERSION_WORD = 0x13 // 4
ROM_VERSION_SHIFT = 8 * (0x13 % 4)
ROM_VERSION_B2 = 3


def rom_version(rp2040) -> int:
    """The version byte of the bootrom loaded into `rp2040` (0 when none is)."""
    return (rp2040.bootrom[ROM_VERSION_WORD] >> ROM_VERSION_SHIFT) & 0xFF


def chip_revision(version: int) -> int:
    """CHIP_ID.REVISION for a bootrom version byte: 2 for B2's ROM (3), else 1 (B0 and B1 share revision 1)."""
    return 2 if version == ROM_VERSION_B2 else 1


class RP2040SysInfo(BasePeripheral):
    def read_uint32(self, offset: int) -> int:
        if offset == CHIP_ID:
            return (chip_revision(rom_version(self.rp2040)) << 28) | (PART << 12) | MANUFACTURER
        if offset == PLATFORM:
            return 0x00000002
        if offset == GITREF_RP2040:
            return 0xE0C912E8
        return super().read_uint32(offset)

    def write_uint32(self, offset: int, value: int) -> None:
        if offset in (CHIP_ID, PLATFORM, GITREF_RP2040):
            return  # read-only registers: the write has no effect
        super().write_uint32(offset, value)
