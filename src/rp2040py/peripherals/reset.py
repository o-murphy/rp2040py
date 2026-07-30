from typing import TYPE_CHECKING

from rp2040py.peripherals.peripheral import BasePeripheral

if TYPE_CHECKING:
    from rp2040py.rp2040 import RP2040

__all__ = ("RPReset",)

RESET = 0x0  # Reset control.
WDSEL = 0x4  # Watchdog select.
RESET_DONE = 0x8  # Reset Done


class RPReset(BasePeripheral):
    def __init__(self, rp2040: "RP2040", name: str):
        super().__init__(rp2040, name)
        self._reset = 0
        self._wdsel = 0
        self._reset_done = 0x1FFFFFF

    def read_uint32(self, offset: int) -> int:
        if offset == RESET:
            return self._reset
        if offset == WDSEL:
            return self._wdsel
        if offset == RESET_DONE:
            return self._reset_done
        return super().read_uint32(offset)

    def write_uint32(self, offset: int, value: int) -> None:
        if offset == RESET:
            self._reset = value & 0x1FFFFFF
        elif offset == WDSEL:
            self._wdsel = value & 0x1FFFFFF
        else:
            super().write_uint32(offset, value)
