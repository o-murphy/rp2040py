from collections.abc import Callable
from typing import TYPE_CHECKING

from rp2040py.irq import IRQ
from rp2040py.peripherals.dma import DREQChannel
from rp2040py.peripherals.peripheral import BasePeripheral
from rp2040py.utils.fifo import FIFO

if TYPE_CHECKING:
    from rp2040py.rp2040 import RP2040

__all__ = ("RPADC",)

CS = 0x00  # ADC Control and Status
RESULT = 0x04  # Result of most recent ADC conversion
FCS = 0x08  # FIFO control and status
FIFO_REG = 0x0C  # Conversion result FIFO
DIV = 0x10  # Clock divider.0x14 INTR Raw Interrupts
INTR = 0x14  # Raw Interrupts
INTE = 0x18  # Interrupt Enable
INTF = 0x1C  # Interrupt Force
INTS = 0x20  # Interrupt status after masking & forcing

# CS bits
CS_RROBIN_MASK = 0x1F
CS_RROBIN_SHIFT = 16
CS_AINSEL_MASK = 0x7
CS_AINSEL_SHIFT = 12
CS_ERR_STICKY = 1 << 10
CS_ERR = 1 << 9
CS_READY = 1 << 8
CS_START_MANY = 1 << 3
CS_START_ONE = 1 << 2
CS_TS_EN = 1 << 1
CS_EN = 1 << 0
CS_WRITE_MASK = (
    (CS_RROBIN_MASK << CS_RROBIN_SHIFT)
    | (CS_AINSEL_MASK << CS_AINSEL_SHIFT)
    | CS_START_MANY
    | CS_START_ONE
    | CS_TS_EN
    | CS_EN
)

# FCS bits
FCS_THRES_MASK = 0xF
FCS_THRESH_SHIFT = 24
FCS_LEVEL_MASK = 0xF
FCS_LEVEL_SHIFT = 16
FCS_OVER = 1 << 11
FCS_UNDER = 1 << 10
FCS_FULL = 1 << 9
FCS_EMPTY = 1 << 8
FCS_DREQ_EN = 1 << 3
FCS_ERR = 1 << 2
FCS_SHIFT = 1 << 1
FCS_EN = 1 << 0
FCS_WRITE_MASK = (FCS_THRES_MASK << FCS_THRESH_SHIFT) | FCS_DREQ_EN | FCS_ERR | FCS_SHIFT | FCS_EN

# FIFO_REG bits
FIFO_ERR = 1 << 15

# DIV bits
DIV_INT_MASK = 0xFFFF
DIV_INT_SHIFT = 8
DIV_FRAC_MASK = 0xFF
DIV_FRAC_SHIFT = 0

# Interrupt bits
FIFO_INT = 1 << 0


class RPADC(BasePeripheral):
    def __init__(self, rp2040: "RP2040", name: str):
        super().__init__(rp2040, name)

        # Number of ADC channels
        self.num_channels = 5

        # ADC resolution (in bits)
        self.resolution = 12

        # Time to read a single sample, in microseconds
        self.sample_time = 2

        # ADC Channel values. Channels 0...3 are connected to GPIO 26...29, and channel 4 is
        # connected to the built-in temperature sensor: T=27-(ADC_voltage-0.706)/0.001721.
        #
        # Changing the values will change the ADC reading, unless you override on_adc_read()
        # with a custom implementation.
        self.channel_values = [0, 0, 0, 0, 0]

        # Invoked whenever the emulated code performs an ADC read.
        #
        # The default implementation reads the result from the `channel_values` list, and then
        # calls complete_adc_read() after `sample_time` microseconds.
        #
        # If you override the default implementation, make sure to call `complete_adc_read()`
        # after `sample_time` microseconds (or else the ADC read will never complete).
        self.on_adc_read: Callable[[int], None] = self._default_on_adc_read

        self.fifo = FIFO(4)
        self.dreq = DREQChannel.DREQ_ADC

        # Registers
        self.cs = 0
        self.fcs = 0
        self.clock_div = 0
        self.int_enable = 0
        self.int_force = 0
        self.result = 0

        # Status
        self.busy = False
        self.err = False

        self.current_channel = 0

        # Used to simulate ADC sample time
        self.sample_alarm = self.rp2040.clock.create_alarm(self._on_sample_alarm)

        # For scheduling multi-shot ADC capture
        self.multi_shot_alarm = self.rp2040.clock.create_alarm(self._on_multi_shot_alarm)

    def _default_on_adc_read(self, channel: int) -> None:
        self.current_channel = channel
        self.sample_alarm.schedule(self.sample_time * 1000)

    def _on_sample_alarm(self) -> None:
        self.complete_adc_read(self.channel_values[self.current_channel], False)

    def _on_multi_shot_alarm(self) -> None:
        if self.cs & CS_START_MANY:
            self.start_adc_read()

    @property
    def temperature_enable(self) -> int:
        return self.cs & CS_TS_EN

    @property
    def enabled(self) -> int:
        return self.cs & CS_EN

    @property
    def divider(self) -> float:
        return (
            1
            + ((self.clock_div >> DIV_INT_SHIFT) & DIV_INT_MASK)
            + ((self.clock_div >> DIV_FRAC_SHIFT) & DIV_FRAC_MASK) / 256
        )

    @property
    def int_raw(self) -> int:
        thres = (self.fcs >> FCS_THRESH_SHIFT) & FCS_THRES_MASK
        return FIFO_INT if self.fifo.item_count >= thres else 0

    @property
    def int_status(self) -> int:
        return (self.int_raw & self.int_enable) | self.int_force

    @property
    def _active_channel(self) -> int:
        return (self.cs >> CS_AINSEL_SHIFT) & CS_AINSEL_MASK

    @_active_channel.setter
    def _active_channel(self, channel: int) -> None:
        self.cs &= ~(CS_AINSEL_MASK << CS_AINSEL_SHIFT)
        # TODO: matches upstream rp2040js, which masks `channel` with CS_AINSEL_SHIFT (12)
        # instead of CS_AINSEL_MASK (0x7) here - almost certainly an upstream bug, kept as-is
        # for behavioral parity. Revisit if rp2040js fixes it upstream.
        self.cs |= (channel & CS_AINSEL_SHIFT) << CS_AINSEL_SHIFT

    def check_interrupts(self) -> None:
        self.rp2040.set_interrupt(IRQ.ADC_FIFO, bool(self.int_status))

    def start_adc_read(self) -> None:
        self.busy = True
        self.on_adc_read(self._active_channel)

    def _update_dma(self) -> None:
        if self.fcs & FCS_DREQ_EN:
            thres = (self.fcs >> FCS_THRESH_SHIFT) & FCS_THRES_MASK
            if self.fifo.item_count >= thres:
                self.rp2040.dma.set_dreq(self.dreq)
            else:
                self.rp2040.dma.clear_dreq(self.dreq)

    def complete_adc_read(self, value: int, error: bool) -> None:
        self.busy = False
        self.result = value
        if error:
            self.cs |= CS_ERR_STICKY | CS_ERR
        else:
            self.cs &= ~CS_ERR

        # FIFO
        if self.fcs & FCS_EN:
            if self.fifo.full:
                self.fcs |= FCS_OVER
            else:
                value &= 0xFFF  # 12 bits
                if self.fcs & FCS_SHIFT:
                    value >>= 4
                if error and self.fcs & FCS_ERR:
                    value |= FIFO_ERR
                self.fifo.push(value)
                self._update_dma()
                self.check_interrupts()

        # Round-robin
        round_mask = (self.cs >> CS_RROBIN_SHIFT) & CS_RROBIN_MASK
        if round_mask:
            channel = self._active_channel + 1
            while not (round_mask & (1 << channel)):
                channel = (channel + 1) % self.num_channels
            self._active_channel = channel

        # Multi-shot conversions
        if self.cs & CS_START_MANY:
            clock_mhz = 48
            sample_ticks = clock_mhz * self.sample_time
            if self.divider > sample_ticks:
                # clock runs at 48MHz, subtract 2uS
                micros = (self.divider - sample_ticks) / clock_mhz
                self.multi_shot_alarm.schedule(micros * 1000)
            else:
                self.start_adc_read()

    def read_uint32(self, offset: int) -> int:
        if offset == CS:
            return self.cs | (CS_ERR if self.err else 0) | (0 if self.busy else CS_READY)
        if offset == RESULT:
            return self.result
        if offset == FCS:
            return (
                self.fcs
                | ((self.fifo.item_count & FCS_LEVEL_MASK) << FCS_LEVEL_SHIFT)
                | (FCS_FULL if self.fifo.full else 0)
                | (FCS_EMPTY if self.fifo.empty else 0)
            )
        if offset == FIFO_REG:
            if self.fifo.empty:
                self.fcs |= FCS_UNDER
                return 0
            value = self.fifo.pull()
            self._update_dma()
            return value
        if offset == DIV:
            return self.clock_div
        if offset == INTR:
            return self.int_raw
        if offset == INTE:
            return self.int_enable
        if offset == INTF:
            return self.int_force
        if offset == INTS:
            return self.int_status
        return super().read_uint32(offset)

    def write_uint32(self, offset: int, value: int) -> None:
        if offset == CS:
            self.fcs &= ~(value & CS_ERR_STICKY)  # Write-clear bits
            self.cs = (self.cs & ~CS_WRITE_MASK) | (value & CS_WRITE_MASK)
            if value & CS_EN and not self.busy and (value & CS_START_ONE or value & CS_START_MANY):
                self.start_adc_read()

        elif offset == FCS:
            self.fcs &= ~(value & (FCS_OVER | FCS_UNDER))  # Write-clear bits
            self.fcs = (self.fcs & ~FCS_WRITE_MASK) | (value & FCS_WRITE_MASK)
            self.check_interrupts()

        elif offset == DIV:
            self.clock_div = value

        elif offset == INTE:
            self.int_enable = value & FIFO_INT
            self.check_interrupts()

        elif offset == INTF:
            self.int_force = value & FIFO_INT
            self.check_interrupts()

        else:
            super().write_uint32(offset, value)
