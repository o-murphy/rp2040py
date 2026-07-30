from typing import TYPE_CHECKING

from rp2040py.peripherals.peripheral import BasePeripheral

if TYPE_CHECKING:
    from rp2040py.rp2040 import RP2040

__all__ = ("RPSSI",)

# See RP2040 datasheet sect 4.10.13
SSI_CTRLR0 = 0x00000000
SSI_CTRLR1 = 0x00000004
SSI_SSIENR = 0x00000008
SSI_MWCR = 0x0000000C
SSI_SER = 0x00000010
SSI_BAUDR = 0x00000014
SSI_TXFTLR = 0x00000018
SSI_RXFTLR = 0x0000001C
SSI_TXFLR = 0x00000020
SSI_RXFLR = 0x00000024
SSI_SR = 0x00000028
SSI_SR_TFNF_BITS = 0x00000002
SSI_SR_TFE_BITS = 0x00000004
SSI_SR_RFNE_BITS = 0x00000008
SSI_IMR = 0x0000002C
SSI_ISR = 0x00000030
SSI_RISR = 0x00000034
SSI_TXOICR = 0x00000038
SSI_RXOICR = 0x0000003C
SSI_RXUICR = 0x00000040
SSI_MSTICR = 0x00000044
SSI_ICR = 0x00000048
SSI_DMACR = 0x0000004C
SSI_DMATDLR = 0x00000050
SSI_DMARDLR = 0x00000054
# Identification register
SSI_IDR = 0x00000058
SSI_VERSION_ID = 0x0000005C
SSI_DR0 = 0x00000060
SSI_RX_SAMPLE_DLY = 0x000000F0
SSI_SPI_CTRL_R0 = 0x000000F4
SSI_TXD_DRIVE_EDGE = 0x000000F8

CMD_READ_STATUS = 0x05


class RPSSI(BasePeripheral):
    def __init__(self, rp2040: "RP2040", name: str):
        super().__init__(rp2040, name)
        self._dr0 = 0
        self._txflr = 0
        self._rxflr = 0
        self._baudr = 0
        self._crtlr0 = 0
        self._crtlr1 = 0
        self._ssienr = 0
        self._spictlr0 = 0
        self._rxsampldly = 0
        self._txddriveedge = 0

    def read_uint32(self, offset: int) -> int:
        if offset == SSI_TXFLR:
            return self._txflr
        if offset == SSI_RXFLR:
            return self._rxflr
        if offset == SSI_CTRLR0:
            return self._crtlr0  # & 0x017FFFFF = b23,b25..31 reserved
        if offset == SSI_CTRLR1:
            return self._crtlr1
        if offset == SSI_SSIENR:
            return self._ssienr
        if offset == SSI_BAUDR:
            return self._baudr
        if offset == SSI_SR:
            return SSI_SR_TFE_BITS | SSI_SR_RFNE_BITS | SSI_SR_TFNF_BITS
        if offset == SSI_IDR:
            return 0x51535049
        if offset == SSI_VERSION_ID:
            return 0x3430312A
        if offset == SSI_RX_SAMPLE_DLY:
            return self._rxsampldly
        if offset == SSI_TXD_DRIVE_EDGE:
            return self._txddriveedge
        if offset == SSI_SPI_CTRL_R0:
            return self._spictlr0  # b6,7,10,19..23 reserved
        if offset == SSI_DR0:
            return self._dr0
        return super().read_uint32(offset)

    def write_uint32(self, offset: int, value: int) -> None:
        if offset == SSI_TXFLR:
            self._txflr = value
        elif offset == SSI_RXFLR:
            self._rxflr = value
        elif offset == SSI_CTRLR0:
            self._crtlr0 = value  # & 0x017FFFFF = b23,b25..31 reserved
        elif offset == SSI_CTRLR1:
            self._crtlr1 = value
        elif offset == SSI_SSIENR:
            self._ssienr = value
        elif offset == SSI_BAUDR:
            self._baudr = value
        elif offset == SSI_RX_SAMPLE_DLY:
            self._rxsampldly = value & 0xFF
        elif offset == SSI_TXD_DRIVE_EDGE:
            self._txddriveedge = value & 0xFF
        elif offset == SSI_SPI_CTRL_R0:
            self._spictlr0 = value
        elif offset == SSI_DR0:
            if value == CMD_READ_STATUS:
                self._dr0 = 0  # tell stage2 that we completed a write
        else:
            super().write_uint32(offset, value)
