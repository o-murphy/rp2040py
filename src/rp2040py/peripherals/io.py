from typing import TYPE_CHECKING

from rp2040py.peripherals.peripheral import BasePeripheral

if TYPE_CHECKING:
    from rp2040py.gpio_pin import GPIOPin
    from rp2040py.rp2040 import RP2040

__all__ = ("RPIO",)

GPIO_CTRL_LAST = 0x0EC
INTR0 = 0xF0
PROC0_INTE0 = 0x100
PROC0_INTF0 = 0x110
PROC0_INTS0 = 0x120
PROC0_INTS3 = 0x12C


class RPIO(BasePeripheral):
    """The CTRL/STATUS/INTR register block shared by IO_BANK0 (`rp2040.gpio`, the default, 30
    general-purpose pins) and IO_QSPI (`rp2040.qspi`, 6 dedicated QSPI pins - same register
    layout, just a different, smaller pin list) - pass `pins` explicitly for the latter."""

    def __init__(self, rp2040: "RP2040", name: str, pins: "list[GPIOPin] | None" = None):
        super().__init__(rp2040, name)
        self.pins = pins if pins is not None else rp2040.gpio

    def get_pin_from_offset(self, offset: int):
        gpio_index = offset >> 3
        return self.pins[gpio_index], bool(offset & 0x4)

    def read_uint32(self, offset: int) -> int:
        if offset <= GPIO_CTRL_LAST:
            gpio, is_ctrl = self.get_pin_from_offset(offset)
            return gpio.ctrl if is_ctrl else gpio.status
        if INTR0 <= offset <= PROC0_INTS3:
            start_index = (offset & 0xF) * 2
            register = offset & ~0xF
            gpio = self.pins
            result = 0
            for index in range(7, -1, -1):
                pin = gpio[index + start_index] if index + start_index < len(gpio) else None
                if not pin:
                    continue
                result <<= 4
                if register == INTR0:
                    result |= pin.irq_status
                elif register == PROC0_INTE0:
                    result |= pin.irq_enable_mask
                elif register == PROC0_INTF0:
                    result |= pin.irq_force_mask
                elif register == PROC0_INTS0:
                    result |= (pin.irq_status & pin.irq_enable_mask) | pin.irq_force_mask
            return result
        return super().read_uint32(offset)

    def write_uint32(self, offset: int, value: int) -> None:
        if offset <= GPIO_CTRL_LAST:
            gpio, is_ctrl = self.get_pin_from_offset(offset)
            if is_ctrl:
                gpio.ctrl = value
                gpio.check_for_updates()
            return
        if INTR0 <= offset <= PROC0_INTS3:
            start_index = (offset & 0xF) * 2
            register = offset & ~0xF
            gpio = self.pins
            for index in range(8):
                pin = gpio[index + start_index] if index + start_index < len(gpio) else None
                if not pin:
                    continue
                pin_value = (value >> (index * 4)) & 0xF
                pin_raw_write_value = (self.raw_write_value >> (index * 4)) & 0xF
                if register == INTR0:
                    pin.update_irq_value(pin_raw_write_value)
                elif register == PROC0_INTE0 and pin.irq_enable_mask != pin_value:
                    pin.irq_enable_mask = pin_value
                    self.rp2040.update_io_interrupt()
                elif register == PROC0_INTF0 and pin.irq_force_mask != pin_value:
                    pin.irq_force_mask = pin_value
                    self.rp2040.update_io_interrupt()
            return

        super().write_uint32(offset, value)
