from rp2040py.peripherals.peripheral import BasePeripheral

__all__ = ("RPTBMAN",)

PLATFORM = 0
ASIC = 1


class RPTBMAN(BasePeripheral):
    def read_uint32(self, offset: int) -> int:
        if offset == PLATFORM:
            return ASIC
        return super().read_uint32(offset)
