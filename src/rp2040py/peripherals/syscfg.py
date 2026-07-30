from rp2040py.peripherals.peripheral import BasePeripheral

__all__ = ("RP2040SysCfg",)

PROC0_NMI_MASK = 0
PROC1_NMI_MASK = 4


class RP2040SysCfg(BasePeripheral):
    def read_uint32(self, offset: int) -> int:
        if offset == PROC0_NMI_MASK:
            return self.rp2040.core.interrupt_nmi_mask
        return super().read_uint32(offset)

    def write_uint32(self, offset: int, value: int) -> None:
        if offset == PROC0_NMI_MASK:
            self.rp2040.core.interrupt_nmi_mask = value
        else:
            super().write_uint32(offset, value)
