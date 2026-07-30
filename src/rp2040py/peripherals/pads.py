from typing import TYPE_CHECKING, Literal

from rp2040py.peripherals.peripheral import BasePeripheral

if TYPE_CHECKING:
    from rp2040py.rp2040 import RP2040

__all__ = (
    "RPPADS",
    "IIOBank",
)

VOLTAGE_SELECT = 0
GPIO_FIRST = 0x4
GPIO_LAST = 0x78

QSPI_FIRST = 0x4
QSPI_LAST = 0x18

IIOBank = Literal["qspi", "bank0"]


class RPPADS(BasePeripheral):
    def __init__(self, rp2040: "RP2040", name: str, bank: IIOBank):
        super().__init__(rp2040, name)
        self.voltage_select = 0
        self.bank = bank
        self._first_pad_register = QSPI_FIRST if bank == "qspi" else GPIO_FIRST
        self._last_pad_register = QSPI_LAST if bank == "qspi" else GPIO_LAST

    def get_pin_from_offset(self, offset: int):
        gpio_index = (offset - self._first_pad_register) >> 2
        if self.bank == "qspi":
            return self.rp2040.qspi[gpio_index]
        return self.rp2040.gpio[gpio_index]

    def read_uint32(self, offset: int) -> int:
        if self._first_pad_register <= offset <= self._last_pad_register:
            gpio = self.get_pin_from_offset(offset)
            return gpio.pad_value
        if offset == VOLTAGE_SELECT:
            return self.voltage_select
        return super().read_uint32(offset)

    def write_uint32(self, offset: int, value: int) -> None:
        if self._first_pad_register <= offset <= self._last_pad_register:
            gpio = self.get_pin_from_offset(offset)
            old_input_enable = gpio.input_enable
            gpio.pad_value = value
            gpio.check_for_updates()
            if old_input_enable != gpio.input_enable:
                gpio.refresh_input()
            return
        if offset == VOLTAGE_SELECT:
            self.voltage_select = value & 1
        else:
            super().write_uint32(offset, value)
