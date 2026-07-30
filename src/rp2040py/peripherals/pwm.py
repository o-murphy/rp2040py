import math
from enum import IntEnum
from typing import TYPE_CHECKING

from rp2040py.clock.clock import IClock
from rp2040py.irq import IRQ
from rp2040py.peripherals.dma import DREQChannel
from rp2040py.peripherals.peripheral import BasePeripheral
from rp2040py.utils.timer32 import Timer32, Timer32PeriodicAlarm, TimerMode

if TYPE_CHECKING:
    from rp2040py.rp2040 import RP2040

__all__ = ("RPPWM",)

# Control and status register
CHN_CSR = 0x00
# INT and FRAC form a fixed-point fractional number.
# Counting rate is system clock frequency divided by this number.
# Fractional division uses simple 1st-order sigma-delta.
CHN_DIV = 0x04
# Direct access to the PWM counter
CHN_CTR = 0x08
# Counter compare values
CHN_CC = 0x0C
# Counter wrap value
CHN_TOP = 0x10

# This register aliases the CSR_EN bits for all channels.
# Writing to this register allows multiple channels to be enabled
# or disabled simultaneously, so they can run in perfect sync.
# For each channel, there is only one physical EN register bit,
# which can be accessed through here or CHx_CSR.
EN = 0xA0
# Raw Interrupts
INTR = 0xA4
# Interrupt Enable
INTE = 0xA8
# Interrupt Force
INTF = 0xAC
# Interrupt status after masking & forcing
INTS = 0xB0

INT_MASK = 0xFF

# CHn_CSR bits
CSR_PH_ADV = 1 << 7
CSR_PH_RET = 1 << 6
CSR_DIVMODE_SHIFT = 4
CSR_DIVMODE_MASK = 0x3
CSR_B_INV = 1 << 3
CSR_A_INV = 1 << 2
CSR_PH_CORRECT = 1 << 1
CSR_EN = 1 << 0


class PWMDivMode(IntEnum):
    FREE_RUNNING = 0
    B_GATED = 1
    B_RISING_EDGE = 2
    B_FALLING_EDGE = 3


class PWMChannel:
    def __init__(self, pwm: "RPPWM", clock: IClock, index: int):
        self.pwm = pwm
        self.clock = clock
        self.index = index

        self.timer = Timer32(self.clock, self.pwm.clock_freq)
        self.alarm_a = Timer32PeriodicAlarm(self.timer, lambda: self.set_a(False))
        self.alarm_b = Timer32PeriodicAlarm(self.timer, lambda: self.set_b(False))
        self.alarm_bottom = Timer32PeriodicAlarm(self.timer, self._wrap)

        self.csr = 0
        self.div = 0
        self.cc = 0
        self.top = 0
        self.last_b_value = False
        self.counting_up = True
        self.cc_updated = False
        self.top_updated = False
        self.tick_counter: float = 0
        self.div_mode = PWMDivMode.FREE_RUNNING

        # GPIO pin indices: Table 525. Mapping of PWM channels to GPIO pins on RP2040
        self.pin_a1 = self.index * 2
        self.pin_b1 = self.index * 2 + 1
        self.pin_a2 = 16 + self.index * 2 if self.index < 7 else -1
        self.pin_b2 = 16 + self.index * 2 + 1 if self.index < 7 else -1

        self.alarm_a.enable = True
        self.alarm_b.enable = True
        self.alarm_bottom.enable = True

    def read_register(self, offset: int) -> int:
        if offset == CHN_CSR:
            return self.csr
        if offset == CHN_DIV:
            return self.div
        if offset == CHN_CTR:
            return self.timer.counter
        if offset == CHN_CC:
            return self.cc
        if offset == CHN_TOP:
            return self.top
        # Shouldn't get here
        return 0

    def write_register(self, offset: int, value: int) -> None:
        if offset == CHN_CSR:
            if value & CSR_EN and not (self.csr & CSR_EN):
                self._update_double_buffered()
            self.csr = value & ~(CSR_PH_ADV | CSR_PH_RET)
            if self.csr & CSR_PH_ADV:
                self.timer.advance(1)
            if self.csr & CSR_PH_RET:
                self.timer.advance(-1)
            self.div_mode = PWMDivMode((self.csr >> CSR_DIVMODE_SHIFT) & CSR_DIVMODE_MASK)
            self.set_b_direction(self.div_mode == PWMDivMode.FREE_RUNNING)
            self.update_enable()
            self.last_b_value = self.gpio_b_value
            self.timer.mode = TimerMode.ZIGZAG if value & CSR_PH_CORRECT else TimerMode.INCREMENT

        elif offset == CHN_DIV:
            self.div = value & 0x000FFFFF
            int_value = (value >> 4) & 0xFF
            frac_value = value & 0xF
            self.timer.prescaler = (int_value if int_value else 256) + frac_value / 16

        elif offset == CHN_CTR:
            self.timer.set(value & 0xFFFF)

        elif offset == CHN_CC:
            self.cc = value
            self.cc_updated = True

        elif offset == CHN_TOP:
            self.top = value & 0xFFFF
            self.top_updated = True

    def reset(self) -> None:
        self.write_register(CHN_CSR, 0)
        self.write_register(CHN_DIV, 0x01 << 4)
        self.write_register(CHN_CTR, 0)
        self.write_register(CHN_CC, 0)
        self.write_register(CHN_TOP, 0xFFFF)
        self.counting_up = True
        self.timer.enable = False
        self.timer.reset()

    def _update_double_buffered(self) -> None:
        if self.cc_updated:
            self.alarm_b.target = self.cc >> 16
            self.alarm_a.target = self.cc & 0xFFFF
            self.cc_updated = False
        if self.top_updated:
            self.timer.top = self.top
            self.top_updated = False

    def _wrap(self) -> None:
        self.pwm.channel_interrupt(self.index)
        self._update_double_buffered()
        if not (self.csr & CSR_PH_CORRECT):
            self.set_a(self.alarm_a.target > 0)
            self.set_b(self.alarm_b.target > 0)

    def set_a(self, value: bool) -> None:
        if self.csr & CSR_A_INV:
            value = not value
        self.pwm.gpio_set(self.pin_a1, value)
        if self.pin_a2 >= 0:
            self.pwm.gpio_set(self.pin_a2, value)

    def set_b(self, value: bool) -> None:
        if self.csr & CSR_B_INV:
            value = not value
        self.pwm.gpio_set(self.pin_b1, value)
        if self.pin_b2 >= 0:
            self.pwm.gpio_set(self.pin_b2, value)

    @property
    def gpio_b_value(self) -> bool:
        return bool(self.pwm.gpio_read(self.pin_b1) or (self.pwm.gpio_read(self.pin_b2) if self.pin_b2 > 0 else False))

    def set_b_direction(self, value: bool) -> None:
        self.pwm.gpio_set_dir(self.pin_b1, value)
        if self.pin_b2 >= 0:
            self.pwm.gpio_set_dir(self.pin_b2, value)

    def gpio_b_changed(self) -> None:
        value = self.gpio_b_value
        if value == self.last_b_value:
            return
        self.last_b_value = value

        if self.div_mode == PWMDivMode.B_GATED:
            self.update_enable()
        elif self.div_mode == PWMDivMode.B_RISING_EDGE:
            if value:
                self.tick_counter += 1
        elif self.div_mode == PWMDivMode.B_FALLING_EDGE:  # noqa: SIM102
            if not value:
                self.tick_counter += 1

        if self.tick_counter >= self.timer.prescaler:
            self.timer.advance(1)
            self.tick_counter -= self.timer.prescaler

    def update_enable(self) -> None:
        csr, div_mode = self.csr, self.div_mode
        enable = bool(csr & CSR_EN)
        self.timer.enable = enable and (
            div_mode == PWMDivMode.FREE_RUNNING or (div_mode == PWMDivMode.B_GATED and self.gpio_b_value)
        )

    @property
    def en(self) -> int:
        # TODO: matches upstream rp2040js, which only defines a setter for `en` - reading it
        # in JS returns `undefined`, and `undefined << n` evaluates to 0, so callers that shift
        # this value (RPPWM.read_uint32 for the EN register) always see 0. Kept as-is for
        # behavioral parity. Revisit if rp2040js fixes it upstream.
        return 0

    @en.setter
    def en(self, value: int) -> None:
        if value and not (self.csr & CSR_EN):
            self._update_double_buffered()
        if value:
            self.csr |= CSR_EN
        else:
            self.csr &= ~CSR_EN
        self.update_enable()


class RPPWM(BasePeripheral):
    def __init__(self, rp2040: "RP2040", name: str):
        super().__init__(rp2040, name)
        self.channels = [PWMChannel(self, self.rp2040.clock, i) for i in range(8)]
        self._int_raw = 0
        self._int_enable = 0
        self._int_force = 0

        self.gpio_value = 0
        self.gpio_direction = 0

    @property
    def int_status(self) -> int:
        return (self._int_raw & self._int_enable) | self._int_force

    def read_uint32(self, offset: int) -> int:
        if offset < EN:
            channel = math.floor(offset / 0x14)
            return self.channels[channel].read_register(offset % 0x14)
        if offset == EN:
            return (
                (self.channels[7].en << 7)
                | (self.channels[6].en << 6)
                | (self.channels[5].en << 5)
                | (self.channels[4].en << 4)
                | (self.channels[3].en << 3)
                | (self.channels[2].en << 2)
                | (self.channels[1].en << 1)
                | (self.channels[0].en << 0)
            )
        if offset == INTR:
            return self._int_raw
        if offset == INTE:
            return self._int_enable
        if offset == INTF:
            return self._int_force
        if offset == INTS:
            return self.int_status
        return super().read_uint32(offset)

    def write_uint32(self, offset: int, value: int) -> None:
        if offset < EN:
            channel = math.floor(offset / 0x14)
            self.channels[channel].write_register(offset % 0x14, value)
            return

        if offset == EN:
            self.channels[7].en = value & (1 << 7)
            self.channels[6].en = value & (1 << 6)
            self.channels[5].en = value & (1 << 5)
            self.channels[4].en = value & (1 << 4)
            self.channels[3].en = value & (1 << 3)
            self.channels[2].en = value & (1 << 2)
            self.channels[1].en = value & (1 << 1)
            self.channels[0].en = value & (1 << 0)
        elif offset == INTR:
            self._int_raw &= ~(value & INT_MASK)
            self.check_interrupts()
        elif offset == INTE:
            self._int_enable = value & INT_MASK
            self.check_interrupts()
        elif offset == INTF:
            self._int_force = value & INT_MASK
            self.check_interrupts()
        else:
            super().write_uint32(offset, value)

    @property
    def clock_freq(self) -> int:
        return self.rp2040.clk_sys

    def channel_interrupt(self, index: int) -> None:
        self._int_raw |= 1 << index
        self.check_interrupts()

        # We also set the DMA Request (DREQ) for the channel
        self.rp2040.dma.set_dreq(DREQChannel.DREQ_PWM_WRAP0 + index)

    def check_interrupts(self) -> None:
        self.rp2040.set_interrupt(IRQ.PWM_WRAP, bool(self.int_status))

    def gpio_set(self, index: int, value: bool) -> None:
        bit = 1 << index
        new_gpio_value = self.gpio_value | bit if value else self.gpio_value & ~bit
        if self.gpio_value != new_gpio_value:
            self.gpio_value = new_gpio_value
            self.rp2040.gpio[index].check_for_updates()

    def gpio_set_dir(self, index: int, output: bool) -> None:
        bit = 1 << index
        new_gpio_direction = self.gpio_direction | bit if output else self.gpio_direction & ~bit
        if self.gpio_direction != new_gpio_direction:
            self.gpio_direction = new_gpio_direction
            self.rp2040.gpio[index].check_for_updates()

    def gpio_read(self, index: int) -> bool:
        return self.rp2040.gpio[index].input_value

    def gpio_on_input(self, index: int) -> None:
        # TODO: matches upstream rp2040js, which uses logical AND (`&&`) here instead of
        # bitwise AND (`&`) - this reduces to "if gpio_direction is nonzero", not "if bit
        # `index` of gpio_direction is set". Kept as-is for behavioral parity. Revisit if
        # rp2040js fixes it upstream.
        if self.gpio_direction and 1 << index:
            return
        for channel in self.channels:
            if channel.pin_b1 == index or channel.pin_b2 == index:
                channel.gpio_b_changed()

    def reset(self) -> None:
        self.gpio_direction = 0xFFFFFFFF
        for channel in self.channels:
            channel.reset()
