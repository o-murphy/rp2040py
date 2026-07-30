from typing import TYPE_CHECKING

from rp2040py.peripherals.peripheral import BasePeripheral

if TYPE_CHECKING:
    from rp2040py.rp2040 import RP2040

__all__ = ("RPXOSC",)

# XOSC register offsets
XOSC_CTRL = 0x00
XOSC_STATUS = 0x04
XOSC_DORMANT = 0x08
XOSC_STARTUP = 0x0C
XOSC_COUNT = 0x1C

# CTRL register bits
CTRL_ENABLE_LSB = 12
CTRL_ENABLE_BITS = 0x00FFF000
CTRL_FREQ_RANGE_BITS = 0x00000FFF

# CTRL ENABLE values
CTRL_ENABLE_DISABLE = 0xD1E
CTRL_ENABLE_ENABLE = 0xFAB

# STATUS register bits
STATUS_STABLE = 0x80000000  # bit 31
STATUS_BADWRITE = 0x01000000  # bit 24
STATUS_ENABLED = 0x00001000  # bit 12
STATUS_FREQ_RANGE_BITS = 0x00000003

# DORMANT register values
DORMANT_VALUE = 0x636F6D61  # "coma" in ASCII
WAKE_VALUE = 0x77616B65  # "wake" in ASCII

# STARTUP register bits
STARTUP_X4 = 0x00100000  # bit 20
STARTUP_DELAY_BITS = 0x00003FFF


class RPXOSC(BasePeripheral):
    def __init__(self, rp2040: "RP2040", name: str):
        super().__init__(rp2040, name)
        self._ctrl = 0
        self._status = 0
        self._dormant = 0
        self._startup = 0
        self._count = 0
        self._enabled = False
        self._stable = False
        self._is_dormant = False

    def read_uint32(self, offset: int) -> int:
        if offset == XOSC_CTRL:
            return self._ctrl

        if offset == XOSC_STATUS:
            status = self._status
            if self._stable:
                status |= STATUS_STABLE
            if self._enabled:
                status |= STATUS_ENABLED
            return status

        if offset == XOSC_DORMANT:
            return self._dormant

        if offset == XOSC_STARTUP:
            return self._startup

        if offset == XOSC_COUNT:
            return self._count

        return super().read_uint32(offset)

    def write_uint32(self, offset: int, value: int) -> None:
        if offset == XOSC_CTRL:
            self._ctrl = value
            enable_value = (value & CTRL_ENABLE_BITS) >> CTRL_ENABLE_LSB

            if enable_value == CTRL_ENABLE_ENABLE:
                if not self._is_dormant:
                    self._enabled = True
                    # For simplicity, become stable immediately
                    # In real hardware, this would take time based on STARTUP register
                    self._stable = True
            elif enable_value == CTRL_ENABLE_DISABLE:
                self._enabled = False
                self._stable = False
            elif enable_value != 0:
                # Invalid write to ENABLE field
                self._status |= STATUS_BADWRITE
                self.warn(f"Invalid ENABLE value written: 0x{enable_value:x}")

        elif offset == XOSC_STATUS:
            # Clear BADWRITE bit if written as 1 (write-1-to-clear)
            if value & STATUS_BADWRITE:
                self._status &= ~STATUS_BADWRITE

        elif offset == XOSC_DORMANT:
            if value == DORMANT_VALUE:
                self._is_dormant = True
                self._stable = False
            elif value == WAKE_VALUE:
                self._is_dormant = False
                if self._enabled:
                    self._stable = True
            self._dormant = value

        elif offset == XOSC_STARTUP:
            self._startup = value & (STARTUP_X4 | STARTUP_DELAY_BITS)

        elif offset == XOSC_COUNT:
            # Writing to COUNT starts the countdown
            self._count = value & 0xFF
            # For simplicity, we don't actually implement the countdown
            # In real hardware, this would decrement at the XOSC frequency

        else:
            super().write_uint32(offset, value)
