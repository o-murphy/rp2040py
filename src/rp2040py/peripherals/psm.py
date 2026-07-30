from typing import TYPE_CHECKING

from rp2040py.peripherals.peripheral import BasePeripheral

if TYPE_CHECKING:
    from rp2040py.rp2040 import RP2040

__all__ = ("RPPSM",)

FRCE_ON = 0x00
FRCE_OFF = 0x04
WDSEL = 0x08
DONE = 0x0C

PSM_BITS_MASK = 0x0001FFFF


class RPPSM(BasePeripheral):
    def __init__(self, rp2040: "RP2040", name: str):
        super().__init__(rp2040, name)
        self._frce_on = 0
        self._frce_off = 0
        self._wdsel = 0

    def read_uint32(self, offset: int) -> int:
        if offset == FRCE_ON:
            return self._frce_on
        if offset == FRCE_OFF:
            return self._frce_off
        if offset == WDSEL:
            return self._wdsel
        if offset == DONE:
            # Domains are ready unless forced off (FRCE_ON overrides FRCE_OFF)
            return (PSM_BITS_MASK & ~self._frce_off) | (self._frce_on & self._frce_off)
        return super().read_uint32(offset)

    def write_uint32(self, offset: int, value: int) -> None:
        if offset == FRCE_ON:
            self._frce_on = value & PSM_BITS_MASK
        elif offset == FRCE_OFF:
            self._frce_off = value & PSM_BITS_MASK
        elif offset == WDSEL:
            self._wdsel = value & PSM_BITS_MASK
        else:
            super().write_uint32(offset, value)
