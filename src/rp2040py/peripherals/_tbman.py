"""TBMAN: the testbench manager (RP2040 datasheet 2.22). On the real chip it "has no effect other than providing a single PLATFORM register", which duplicates SYSINFO's. The pure-Python reference that
the native block (`native/core/tbman.hpp`) is held to by `tests/test_ident_diff.py`.

`PLATFORM` (offset 0) is read-only: bit 0 ASIC (reset 1), bit 1 FPGA (reset 0), the rest reserved - it reads 1 here. A write to it is ignored, as on the chip; a write to an offset that is no register still
logs an "unimplemented peripheral write". (pico-sdk's `tbman.h` gives the register's reset as 0x5, which is not what the field table says and not what firmware relies on: bits 31:2 are reserved.)
"""

from rp2040py.peripherals.peripheral import BasePeripheral

__all__ = ("RPTBMAN",)

PLATFORM = 0
ASIC = 1


class RPTBMAN(BasePeripheral):
    def read_uint32(self, offset: int) -> int:
        if offset == PLATFORM:
            return ASIC
        return super().read_uint32(offset)

    def write_uint32(self, offset: int, value: int) -> None:
        if offset == PLATFORM:
            return  # read-only: the write has no effect
        super().write_uint32(offset, value)
