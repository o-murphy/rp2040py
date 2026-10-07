"""SYSCFG (`0x40004000`, RP2040 datasheet 2.21): processor configuration registers.

Only PROC0_NMI_MASK has an effect here (the core's NMI mask). The rest are plain stored registers with
the datasheet's reserved bits and reset values - there is no second core, no debug port to attach and
no memory to power down, so they change nothing, but firmware (and a debugger) reads them back.
`PROC0_HALTED`/`PROC1_HALTED` (PROC_CONFIG bits 0 and 1, read-only) read 0 and the two SWDO bits of DBGFORCE
(read-only, reset "-") read 0: not independently sourced, no model behind them.
"""

from rp2040py.peripherals.peripheral import BasePeripheral

__all__ = ("RP2040SysCfg",)

PROC0_NMI_MASK = 0x00
PROC1_NMI_MASK = 0x04
PROC_CONFIG = 0x08
PROC_IN_SYNC_BYPASS = 0x0C
PROC_IN_SYNC_BYPASS_HI = 0x10
DBGFORCE = 0x14
MEMPOWERDOWN = 0x18

# (datasheet tables 354-360) writable bits and reset values
PROC_CONFIG_WRITABLE = 0xFF000000  # PROC1_DAP_INSTID 31:28, PROC0_DAP_INSTID 27:24; HALTED 1:0 are read-only
PROC_CONFIG_RESET = 0x10000000
PROC_IN_SYNC_BYPASS_MASK = 0x3FFFFFFF  # GPIO 0..29
PROC_IN_SYNC_BYPASS_HI_MASK = 0x0000003F  # GPIO 30..35, the QSPI pins
DBGFORCE_WRITABLE = 0x000000EE  # SWDO (bits 4 and 0) are read-only
DBGFORCE_RESET = 0x00000066  # both SWCLK and SWDI reset to 1
MEMPOWERDOWN_MASK = 0x000000FF


class RP2040SysCfg(BasePeripheral):
    def __init__(self, rp2040, name) -> None:  # type: ignore[no-untyped-def]
        super().__init__(rp2040, name)
        self.proc1_nmi_mask = 0
        self.proc_config = PROC_CONFIG_RESET
        self.proc_in_sync_bypass = 0
        self.proc_in_sync_bypass_hi = 0
        self.dbgforce = DBGFORCE_RESET
        self.mempowerdown = 0

    def reset(self) -> None:
        """`RESETS_RESET_SYSCFG`: every register back to its datasheet reset value (PROC0_NMI_MASK is the core's)."""
        self.rp2040.core.interrupt_nmi_mask = 0
        self.proc1_nmi_mask = 0
        self.proc_config = PROC_CONFIG_RESET
        self.proc_in_sync_bypass = 0
        self.proc_in_sync_bypass_hi = 0
        self.dbgforce = DBGFORCE_RESET
        self.mempowerdown = 0

    def read_uint32(self, offset: int) -> int:
        if offset == PROC0_NMI_MASK:
            return self.rp2040.core.interrupt_nmi_mask
        if offset == PROC1_NMI_MASK:
            return self.proc1_nmi_mask
        if offset == PROC_CONFIG:
            return self.proc_config
        if offset == PROC_IN_SYNC_BYPASS:
            return self.proc_in_sync_bypass
        if offset == PROC_IN_SYNC_BYPASS_HI:
            return self.proc_in_sync_bypass_hi
        if offset == DBGFORCE:
            return self.dbgforce
        if offset == MEMPOWERDOWN:
            return self.mempowerdown
        return super().read_uint32(offset)

    def write_uint32(self, offset: int, value: int) -> None:
        if offset == PROC0_NMI_MASK:
            self.rp2040.core.interrupt_nmi_mask = value
        elif offset == PROC1_NMI_MASK:
            self.proc1_nmi_mask = value & 0xFFFFFFFF
        elif offset == PROC_CONFIG:
            self.proc_config = (self.proc_config & ~PROC_CONFIG_WRITABLE) | (value & PROC_CONFIG_WRITABLE)
        elif offset == PROC_IN_SYNC_BYPASS:
            self.proc_in_sync_bypass = value & PROC_IN_SYNC_BYPASS_MASK
        elif offset == PROC_IN_SYNC_BYPASS_HI:
            self.proc_in_sync_bypass_hi = value & PROC_IN_SYNC_BYPASS_HI_MASK
        elif offset == DBGFORCE:
            self.dbgforce = (self.dbgforce & ~DBGFORCE_WRITABLE) | (value & DBGFORCE_WRITABLE)
        elif offset == MEMPOWERDOWN:
            self.mempowerdown = value & MEMPOWERDOWN_MASK
        else:
            super().write_uint32(offset, value)
