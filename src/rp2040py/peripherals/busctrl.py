from typing import TYPE_CHECKING

from rp2040py.peripherals.peripheral import BasePeripheral

if TYPE_CHECKING:
    from rp2040py.rp2040 import RP2040

__all__ = ("RPBUSCTRL",)

# Bus priority acknowledge
BUS_PRIORITY_ACK = 0x004

# Bus fabric performance counter 0
PERFCTR0 = 0x008
# Bus fabric performance event select for PERFCTR0
PERFSEL0 = 0x00C

# Bus fabric performance counter 1
PERFCTR1 = 0x010
# Bus fabric performance event select for PERFCTR1
PERFSEL1 = 0x014

# Bus fabric performance counter 2
PERFCTR2 = 0x018
# Bus fabric performance event select for PERFCTR2
PERFSEL2 = 0x01C

# Bus fabric performance counter 3
PERFCTR3 = 0x020
# Bus fabric performance event select for PERFCTR3
PERFSEL3 = 0x024


class RPBUSCTRL(BasePeripheral):
    def __init__(self, rp2040: "RP2040", name: str):
        super().__init__(rp2040, name)
        self.voltage_select = 0
        self.perf_ctr = [0, 0, 0, 0]
        self.perf_sel = [0x1F, 0x1F, 0x1F, 0x1F]

    def reset(self) -> None:
        """`RESETS_RESET_BUSCTRL` (0089 Phase 5). The four performance counters and their selectors
        are the block's whole state; `0x1F` is each selector's own reset value, not a placeholder."""
        self.voltage_select = 0
        self.perf_ctr = [0, 0, 0, 0]
        self.perf_sel = [0x1F, 0x1F, 0x1F, 0x1F]

    def read_uint32(self, offset: int) -> int:
        if offset == BUS_PRIORITY_ACK:
            return 1
        if offset == PERFCTR0:
            return self.perf_ctr[0]
        if offset == PERFSEL0:
            return self.perf_sel[0]
        if offset == PERFCTR1:
            return self.perf_ctr[1]
        if offset == PERFSEL1:
            return self.perf_sel[1]
        if offset == PERFCTR2:
            return self.perf_ctr[2]
        if offset == PERFSEL2:
            return self.perf_sel[2]
        if offset == PERFCTR3:
            return self.perf_ctr[3]
        if offset == PERFSEL3:
            return self.perf_sel[3]
        return super().read_uint32(offset)

    def write_uint32(self, offset: int, value: int) -> None:
        if offset == PERFCTR0:
            self.perf_ctr[0] = 0
        elif offset == PERFSEL0:
            self.perf_sel[0] = value & 0x1F
        elif offset == PERFCTR1:
            self.perf_ctr[1] = 0
        elif offset == PERFSEL1:
            self.perf_sel[1] = value & 0x1F
        elif offset == PERFCTR2:
            self.perf_ctr[2] = 0
        elif offset == PERFSEL2:
            self.perf_sel[2] = value & 0x1F
        elif offset == PERFCTR3:
            self.perf_ctr[3] = 0
        elif offset == PERFSEL3:
            self.perf_sel[3] = value & 0x1F
        else:
            super().write_uint32(offset, value)
