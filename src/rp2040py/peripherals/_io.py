from typing import TYPE_CHECKING

from rp2040py.peripherals.peripheral import BasePeripheral

if TYPE_CHECKING:
    from rp2040py._gpio_pin import GPIOPin
    from rp2040py.rp2040 import RP2040

__all__ = ("RPIO",)

CTRL_MASK = 0x3003331F  # IRQOVER, INOVER, OEOVER, OUTOVER, FUNCSEL (datasheet table 285); the rest is reserved

# Each interrupt group is (kind, destination, offset): the kinds are INTR (raw), INTE (enable), INTF (force) and INTS (status after masking and forcing); the destinations proc 0, proc 1
# and dormant wake. IO_BANK0's groups are four words of eight pins each (datasheet table 283); IO_QSPI's six pins fit one word each, and the groups sit right after the pins (table 326).
_BANK0_GROUPS = (
    ("intr", None, 0x0F0),
    ("inte", 0, 0x100),
    ("intf", 0, 0x110),
    ("ints", 0, 0x120),
    ("inte", 1, 0x130),
    ("intf", 1, 0x140),
    ("ints", 1, 0x150),
    ("inte", 2, 0x160),
    ("intf", 2, 0x170),
    ("ints", 2, 0x180),
)
_QSPI_GROUPS = (
    ("intr", None, 0x30),
    ("inte", 0, 0x34),
    ("intf", 0, 0x38),
    ("ints", 0, 0x3C),
    ("inte", 1, 0x40),
    ("intf", 1, 0x44),
    ("ints", 1, 0x48),
    ("inte", 2, 0x4C),
    ("intf", 2, 0x50),
    ("ints", 2, 0x54),
)
PINS_PER_WORD = 8


class RPIO(BasePeripheral):
    """The CTRL/STATUS/INTR register block of IO_BANK0 (`rp2040.gpio`, the default, 30 general-purpose pins) and of IO_QSPI (`rp2040.qspi`, 6 dedicated QSPI pins): the same per-pin
    registers, but the interrupt groups sit at different offsets (IO_QSPI's are one word each, right after the six pins) - pass `pins` for the latter and the layout follows.

    Proc 0's enable and force masks live on the pins (`GPIOPin.irq_enable_mask`, `irq_force_mask`: they raise the IO interrupt); proc 1's and dormant wake's are stored here and drive
    no interrupt line: there is no second core and no dormant mode to wake (docs/records/0098)."""

    def __init__(self, rp2040: "RP2040", name: str, pins: "list[GPIOPin] | None" = None):
        super().__init__(rp2040, name)
        self.pins = pins if pins is not None else rp2040.gpio
        self._groups = _BANK0_GROUPS if pins is None else _QSPI_GROUPS
        self._words = (len(self.pins) + PINS_PER_WORD - 1) // PINS_PER_WORD
        self._extra = {
            dest: ([0] * len(self.pins), [0] * len(self.pins)) for dest in (1, 2)
        }  # (enable, force) per pin, for proc 1 and dormant wake
        self._pin_registers_end = len(self.pins) * 8

    def snapshot(self) -> dict[str, tuple]:
        """The proc 1 / dormant wake masks this block stores itself (proc 0's are on the pins): `extra` is, for each of the two destinations, the enable and the force mask of every pin."""
        return {"extra": tuple(tuple(tuple(masks) for masks in self._extra[dest]) for dest in (1, 2))}

    def reset(self) -> None:
        """The stored proc 1 / dormant wake masks back to zero (proc 0's are on the pins, reset with them)."""
        for enable, force in self._extra.values():
            for index in range(len(self.pins)):
                enable[index] = force[index] = 0

    def get_pin_from_offset(self, offset: int):
        gpio_index = offset >> 3
        return self.pins[gpio_index], bool(offset & 0x4)

    def _locate(self, offset: int):
        """(kind, destination, first pin) of an interrupt register, or None."""
        for kind, dest, base in self._groups:
            if base <= offset < base + 4 * self._words:
                return kind, dest, ((offset - base) >> 2) * PINS_PER_WORD
        return None

    def _masks(self, dest: int, index: int) -> "tuple[int, int]":
        pin = self.pins[index]
        if dest == 0:
            return pin.irq_enable_mask, pin.irq_force_mask
        enable, force = self._extra[dest]
        return enable[index], force[index]

    def read_uint32(self, offset: int) -> int:
        if offset < self._pin_registers_end:
            gpio, is_ctrl = self.get_pin_from_offset(offset)
            return gpio.ctrl if is_ctrl else gpio.status
        located = self._locate(offset) if not offset & 3 else None
        if located is None:
            return super().read_uint32(offset)
        kind, dest, first = located
        result = 0
        for slot in range(PINS_PER_WORD):
            index = first + slot
            if index >= len(self.pins):
                break
            pin = self.pins[index]
            if kind == "intr":
                nibble = pin.irq_status
            else:
                enable, force = self._masks(dest, index)
                nibble = enable if kind == "inte" else force if kind == "intf" else (pin.irq_status & enable) | force
            result |= nibble << (slot * 4)
        return result

    def write_uint32(self, offset: int, value: int) -> None:
        if offset < self._pin_registers_end:
            gpio, is_ctrl = self.get_pin_from_offset(offset)
            if is_ctrl:
                gpio.ctrl = value & CTRL_MASK
                gpio.check_for_updates()
            return
        located = self._locate(offset) if not offset & 3 else None
        if located is None:
            super().write_uint32(offset, value)
            return
        kind, dest, first = located
        for slot in range(PINS_PER_WORD):
            index = first + slot
            if index >= len(self.pins):
                break
            pin = self.pins[index]
            nibble = (value >> (slot * 4)) & 0xF
            if kind == "intr":
                pin.update_irq_value(
                    (self.raw_write_value >> (slot * 4)) & 0xF
                )  # by the bits the bus passed, whatever the alias: only the edge bits are write-1-to-clear
            elif kind == "ints":
                continue  # read only
            elif dest == 0:
                if kind == "inte" and pin.irq_enable_mask != nibble:
                    pin.irq_enable_mask = nibble
                    self.rp2040.update_io_interrupt()
                elif kind == "intf" and pin.irq_force_mask != nibble:
                    pin.irq_force_mask = nibble
                    self.rp2040.update_io_interrupt()
            else:
                self._extra[dest][0 if kind == "inte" else 1][index] = nibble
