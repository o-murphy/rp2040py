from typing import TYPE_CHECKING, Literal

from rp2040py.peripherals.peripheral import BasePeripheral

if TYPE_CHECKING:
    from rp2040py.rp2040 import RP2040

__all__ = (
    "RPPADS",
    "IIOBank",
)

VOLTAGE_SELECT = 0
PAD_MASK = 0xFF
SWCLK = 0x7C  # PADS_BANK0 only: the debug port's two pads, no GPIOPin behind them (datasheet tables 342, 343)
SWD = 0x80
SWCLK_RESET = 0xDA  # OD 1, IE 1, DRIVE 1, PUE 1, PDE 0, SCHMITT 1
SWD_RESET = 0x5A  # as SWCLK with OD 0
GPIO_FIRST = 0x4
GPIO_LAST = 0x78

QSPI_FIRST = 0x4
QSPI_LAST = 0x18

IIOBank = Literal["qspi", "bank0"]

# PADS_QSPI lists its pads in a different order from IO_QSPI and `RP2040.qspi` (SCLK, SS, SD0..SD3): SCLK, SD0, SD1, SD2, SD3, SS (datasheet table 344).
_QSPI_PAD_TO_PIN = (0, 2, 3, 4, 5, 1)


class RPPADS(BasePeripheral):
    def __init__(self, rp2040: "RP2040", name: str, bank: IIOBank):
        super().__init__(rp2040, name)
        self.voltage_select = 0
        self.swclk = SWCLK_RESET
        self.swd = SWD_RESET
        self.bank = bank
        self._first_pad_register = QSPI_FIRST if bank == "qspi" else GPIO_FIRST
        self._last_pad_register = QSPI_LAST if bank == "qspi" else GPIO_LAST

    def reset(self) -> None:
        """`VOLTAGE_SELECT`, the only state this block owns itself - the per-pad registers live on
        the `GPIOPin`s and are reset by `GPIOPin.reset()` (0089 Phase 5). `bank` and the two
        register bounds are construction-time identity, not state."""
        self.voltage_select = 0
        self.swclk = SWCLK_RESET
        self.swd = SWD_RESET

    def get_pin_from_offset(self, offset: int):
        gpio_index = (offset - self._first_pad_register) >> 2
        if self.bank == "qspi":
            return self.rp2040.qspi[_QSPI_PAD_TO_PIN[gpio_index]]
        return self.rp2040.gpio[gpio_index]

    def snapshot(self) -> dict[str, int]:
        """The registers this block owns itself (the per-pad registers live on the pins) as plain values."""
        return {"voltage_select": self.voltage_select, "swclk": self.swclk, "swd": self.swd}

    def read_uint32(self, offset: int) -> int:
        if self._first_pad_register <= offset <= self._last_pad_register:
            gpio = self.get_pin_from_offset(offset)
            return gpio.pad_value
        if offset == VOLTAGE_SELECT:
            return self.voltage_select
        if self.bank == "bank0" and offset == SWCLK:
            return self.swclk
        if self.bank == "bank0" and offset == SWD:
            return self.swd
        return super().read_uint32(offset)

    def write_uint32(self, offset: int, value: int) -> None:
        if self._first_pad_register <= offset <= self._last_pad_register:
            gpio = self.get_pin_from_offset(offset)
            gpio.pad_value = value & PAD_MASK  # 31:8 are reserved
            gpio.check_for_updates()
            gpio.pad_changed()
            return
        if offset == VOLTAGE_SELECT:
            self.voltage_select = value & 1
        elif self.bank == "bank0" and offset == SWCLK:
            self.swclk = value & PAD_MASK
        elif self.bank == "bank0" and offset == SWD:
            self.swd = value & PAD_MASK
        else:
            super().write_uint32(offset, value)
