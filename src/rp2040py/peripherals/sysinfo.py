from rp2040py.peripherals.peripheral import BasePeripheral

__all__ = ("RP2040SysInfo",)

CHIP_ID = 0
PLATFORM = 0x4
GITREF_RP2040 = 0x40


class RP2040SysInfo(BasePeripheral):
    def read_uint32(self, offset: int) -> int:
        # All the values here were verified against the silicon
        if offset == CHIP_ID:
            return 0x10002927
        if offset == PLATFORM:
            return 0x00000002
        if offset == GITREF_RP2040:
            return 0xE0C912E8
        return super().read_uint32(offset)
