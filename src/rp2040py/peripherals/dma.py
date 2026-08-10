from collections.abc import Callable
from enum import IntEnum
from typing import TYPE_CHECKING

from rp2040py.irq import IRQ
from rp2040py.peripherals.peripheral import BasePeripheral
from rp2040py.utils.bit import urshift

if TYPE_CHECKING:
    from rp2040py.rp2040 import RP2040


__all__ = (
    "RPDMA",
    "DREQChannel",
    "RPDMAChannel",
)


class DREQChannel(IntEnum):
    DREQ_PIO0_TX0 = 0
    DREQ_PIO0_TX1 = 1
    DREQ_PIO0_TX2 = 2
    DREQ_PIO0_TX3 = 3
    DREQ_PIO0_RX0 = 4
    DREQ_PIO0_RX1 = 5
    DREQ_PIO0_RX2 = 6
    DREQ_PIO0_RX3 = 7
    DREQ_PIO1_TX0 = 8
    DREQ_PIO1_TX1 = 9
    DREQ_PIO1_TX2 = 10
    DREQ_PIO1_TX3 = 11
    DREQ_PIO1_RX0 = 12
    DREQ_PIO1_RX1 = 13
    DREQ_PIO1_RX2 = 14
    DREQ_PIO1_RX3 = 15
    DREQ_SPI0_TX = 16
    DREQ_SPI0_RX = 17
    DREQ_SPI1_TX = 18
    DREQ_SPI1_RX = 19
    DREQ_UART0_TX = 20
    DREQ_UART0_RX = 21
    DREQ_UART1_TX = 22
    DREQ_UART1_RX = 23
    DREQ_PWM_WRAP0 = 24
    DREQ_PWM_WRAP1 = 25
    DREQ_PWM_WRAP2 = 26
    DREQ_PWM_WRAP3 = 27
    DREQ_PWM_WRAP4 = 28
    DREQ_PWM_WRAP5 = 29
    DREQ_PWM_WRAP6 = 30
    DREQ_PWM_WRAP7 = 31
    DREQ_I2C0_TX = 32
    DREQ_I2C0_RX = 33
    DREQ_I2C1_TX = 34
    DREQ_I2C1_RX = 35
    DREQ_ADC = 36
    DREQ_XIP_STREAM = 37
    DREQ_XIP_SSITX = 38
    DREQ_XIP_SSIRX = 39
    DREQ_MAX = 40


class TREQ(IntEnum):
    TIMER0 = 0x3B
    TIMER1 = 0x3C
    TIMER2 = 0x3D
    TIMER3 = 0x3E
    PERMANENT = 0x3F


# Per-channel registers
CHN_READ_ADDR = 0x000  # DMA Channel n Read Address pointer
CHN_WRITE_ADDR = 0x004  # DMA Channel n Write Address pointer
CHN_TRANS_COUNT = 0x008  # DMA Channel n Transfer Count
CHN_CTRL_TRIG = 0x00C  # DMA Channel n Control and Status
CHN_AL1_CTRL = 0x010  # Alias for channel n CTRL register
CHN_AL1_READ_ADDR = 0x014  # Alias for channel n READ_ADDR register
CHN_AL1_WRITE_ADDR = 0x018  # Alias for channel n WRITE_ADDR register
CHN_AL1_TRANS_COUNT_TRIG = 0x01C  # Alias for channel n TRANS_COUNT register
CHN_AL2_CTRL = 0x020  # Alias for channel n CTRL register
CHN_AL2_TRANS_COUNT = 0x024  # Alias for channel n TRANS_COUNT register
CHN_AL2_READ_ADDR = 0x028  # Alias for channel n READ_ADDR register
CHN_AL2_WRITE_ADDR_TRIG = 0x02C  # Alias for channel n WRITE_ADDR register
CHN_AL3_CTRL = 0x030  # Alias for channel n CTRL register
CHN_AL3_WRITE_ADDR = 0x034  # Alias for channel n WRITE_ADDR register
CHN_AL3_TRANS_COUNT = 0x038  # Alias for channel n TRANS_COUNT register
CHN_AL3_READ_ADDR_TRIG = 0x03C  # Alias for channel n READ_ADDR register
CHN_DBG_CTDREQ = 0x800
CHN_DBG_TCR = 0x804
CHANNEL_REGISTERS_SIZE = 12 * 0x40
CHANNEL_REGISTERS_MASK = 0x83F

READ_ADDR_REGS = (CHN_READ_ADDR, CHN_AL1_READ_ADDR, CHN_AL2_READ_ADDR, CHN_AL3_READ_ADDR_TRIG)
WRITE_ADDR_REGS = (CHN_WRITE_ADDR, CHN_AL1_WRITE_ADDR, CHN_AL2_WRITE_ADDR_TRIG, CHN_AL3_WRITE_ADDR)
TRANS_COUNT_REGS = (
    CHN_TRANS_COUNT,
    CHN_AL1_TRANS_COUNT_TRIG,
    CHN_AL2_TRANS_COUNT,
    CHN_AL3_TRANS_COUNT,
)
CTRL_REGS = (CHN_CTRL_TRIG, CHN_AL1_CTRL, CHN_AL2_CTRL, CHN_AL3_CTRL)
TRIGGER_REGS = (CHN_AL3_READ_ADDR_TRIG, CHN_AL2_WRITE_ADDR_TRIG, CHN_AL1_TRANS_COUNT_TRIG, CHN_CTRL_TRIG)

# General DMA registers
INTR = 0x400  # Interrupt Status (raw)
INTE0 = 0x404  # Interrupt Enables for IRQ 0
INTF0 = 0x408  # Force Interrupts
INTS0 = 0x40C  # Interrupt Status for IRQ 0
INTE1 = 0x414  # Interrupt Enables for IRQ 1
INTF1 = 0x418  # Force Interrupts for IRQ 1
INTS1 = 0x41C  # Interrupt Status (masked) for IRQ 1
TIMER0 = 0x420  # Pacing (X/Y) Fractional Timer
TIMER1 = 0x424  # Pacing (X/Y) Fractional Timer
TIMER2 = 0x428  # Pacing (X/Y) Fractional Timer
TIMER3 = 0x42C  # Pacing (X/Y) Fractional Timer
MULTI_CHAN_TRIGGER = 0x430  # Trigger one or more channels simultaneously
SNIFF_CTRL = 0x434  # Sniffer Control
SNIFF_DATA = 0x438  # Data accumulator for sniff hardware
FIFO_LEVELS = 0x440  # Debug RAF, WAF, TDF levels
CHAN_ABORT = 0x444  # Abort an in-progress transfer sequence on one or more channels
N_CHANNELS = 0x448

# CHn_CTRL_TRIG bits
AHB_ERROR = 1 << 31
READ_ERROR = 1 << 30
WRITE_ERROR = 1 << 29
BUSY = 1 << 24
SNIFF_EN = 1 << 23
BSWAP = 1 << 22
IRQ_QUIET = 1 << 21
TREQ_SEL_MASK = 0x3F
TREQ_SEL_SHIFT = 15
CHAIN_TO_MASK = 0xF
CHAIN_TO_SHIFT = 11
RING_SEL = 1 << 10
RING_SIZE_MASK = 0xF
RING_SIZE_SHIFT = 6
INCR_WRITE = 1 << 5
INCR_READ = 1 << 4
DATA_SIZE_MASK = 0x3
DATA_SIZE_SHIFT = 2
HIGH_PRIORITY = 1 << 1
EN = 1 << 0
CHN_CTRL_TRIG_WRITE_MASK = 0xFFFFFF
CHN_CTRL_TRIG_WC_MASK = READ_ERROR | WRITE_ERROR


class RPDMAChannel:
    def __init__(self, dma: "RPDMA", rp2040: "RP2040", index: int):
        self.dma = dma
        self.rp2040 = rp2040
        self.index = index

        self._ctrl = 0
        self._read_addr = 0
        self._write_addr = 0
        self._trans_count = 0
        self._dreq_counter = 0
        self._trans_count_reload = 0
        self._treq_value = 0
        self._data_size = 1
        self._chain_to = 0
        self._ring_mask = 0
        self._transfer_fn: Callable[[], None] = self.transfer8

        self.transfer_alarm = rp2040.clock.create_alarm(self.transfer)
        self.reset()

    def start(self) -> None:
        if not (self._ctrl & EN) or self._ctrl & BUSY:
            return
        self._ctrl |= BUSY
        self._trans_count = self._trans_count_reload
        if self._trans_count:
            self.schedule_transfer()

    @property
    def treq(self) -> int:
        return self._treq_value

    @property
    def active(self) -> bool:
        return bool(self._ctrl & EN and self._ctrl & BUSY)

    def transfer8(self) -> None:
        rp2040 = self.rp2040
        rp2040.write_uint8(self._write_addr, rp2040.read_uint8(self._read_addr))

    def transfer16(self) -> None:
        rp2040 = self.rp2040
        rp2040.write_uint16(self._write_addr, rp2040.read_uint16(self._read_addr))

    def transfer_swap16(self) -> None:
        rp2040 = self.rp2040
        input_value = rp2040.read_uint16(self._read_addr)
        rp2040.write_uint16(self._write_addr, ((input_value & 0xFF) << 8) | (input_value >> 8))

    def transfer32(self) -> None:
        rp2040 = self.rp2040
        rp2040.write_uint32(self._write_addr, rp2040.read_uint32(self._read_addr))

    def transfer_swap32(self) -> None:
        rp2040 = self.rp2040
        input_value = rp2040.read_uint32(self._read_addr)
        rp2040.write_uint32(
            self._write_addr,
            ((input_value & 0x000000FF) << 24)
            | ((input_value & 0x0000FF00) << 8)
            | ((input_value & 0x00FF0000) >> 8)
            | ((input_value >> 24) & 0xFF),
        )

    def transfer(self) -> None:
        ctrl, data_size, ring_mask = self._ctrl, self._data_size, self._ring_mask
        self._transfer_fn()
        if ctrl & INCR_READ:
            if ring_mask and not (ctrl & RING_SEL):
                self._read_addr = (self._read_addr & ~ring_mask) | ((self._read_addr + data_size) & ring_mask)
            else:
                self._read_addr += data_size
        if ctrl & INCR_WRITE:
            if ring_mask and ctrl & RING_SEL:
                self._write_addr = (self._write_addr & ~ring_mask) | ((self._write_addr + data_size) & ring_mask)
            else:
                self._write_addr += data_size
        self._trans_count -= 1
        if self._trans_count > 0:
            self.schedule_transfer()
        else:
            self._ctrl &= ~BUSY
            if not (self._ctrl & IRQ_QUIET):
                self.dma.int_raw |= 1 << self.index
                self.dma.check_interrupts()
            if self._chain_to != self.index:
                channels = self.dma.channels
                if 0 <= self._chain_to < len(channels):
                    channels[self._chain_to].start()

    def schedule_transfer(self) -> None:
        if self.dma.dreq.get(self._treq_value, False) or self._treq_value == TREQ.PERMANENT:
            self.transfer_alarm.schedule(0)
        else:
            delay = self.dma.get_timer(self._treq_value)
            if delay:
                self.transfer_alarm.schedule(delay * 1000)

    def abort(self) -> None:
        self._ctrl &= ~BUSY
        self.transfer_alarm.cancel()

    def read_uint32(self, offset: int) -> int:
        if offset in READ_ADDR_REGS:
            return self._read_addr

        if offset in WRITE_ADDR_REGS:
            return self._write_addr

        if offset in TRANS_COUNT_REGS:
            return self._trans_count

        if offset in CTRL_REGS:
            return self._ctrl

        if offset == CHN_DBG_CTDREQ:
            return self._dreq_counter

        if offset == CHN_DBG_TCR:
            return self._trans_count_reload

        return 0

    def write_uint32(self, offset: int, value: int) -> None:
        if offset in READ_ADDR_REGS:
            self._read_addr = value

        elif offset in WRITE_ADDR_REGS:
            self._write_addr = value

        elif offset in TRANS_COUNT_REGS:
            self._trans_count_reload = value

        elif offset in CTRL_REGS:
            self._ctrl = (self._ctrl & ~CHN_CTRL_TRIG_WRITE_MASK) | (value & CHN_CTRL_TRIG_WRITE_MASK)
            self._ctrl &= ~(value & CHN_CTRL_TRIG_WC_MASK)  # Handle write-clear (WC) bits
            self._treq_value = (self._ctrl >> TREQ_SEL_SHIFT) & TREQ_SEL_MASK
            self._chain_to = (self._ctrl >> CHAIN_TO_SHIFT) & CHAIN_TO_MASK
            ring_size = (self._ctrl >> RING_SIZE_SHIFT) & RING_SIZE_MASK
            self._ring_mask = (1 << ring_size) - 1 if ring_size else 0
            data_size_sel = (self._ctrl >> DATA_SIZE_SHIFT) & DATA_SIZE_MASK
            if data_size_sel == 1:
                self._data_size = 2
                self._transfer_fn = self.transfer_swap16 if self._ctrl & BSWAP else self.transfer16
            elif data_size_sel == 2:
                self._data_size = 4
                self._transfer_fn = self.transfer_swap32 if self._ctrl & BSWAP else self.transfer32
            else:
                self._transfer_fn = self.transfer8
                self._data_size = 1
            if self._ctrl & EN and self._ctrl & BUSY:
                self.schedule_transfer()
            if not (self._ctrl & EN):
                self.transfer_alarm.cancel()

        elif offset == CHN_DBG_CTDREQ:
            self._dreq_counter = 0

        if offset in TRIGGER_REGS:
            if value:
                self.start()
            elif self._ctrl & IRQ_QUIET:
                # Null trigger interrupts
                self.dma.int_raw |= 1 << self.index
                self.dma.check_interrupts()

    def reset(self) -> None:
        self.write_uint32(CHN_CTRL_TRIG, self.index << CHAIN_TO_SHIFT)


class RPDMA(BasePeripheral):
    def __init__(self, rp2040: "RP2040", name: str):
        super().__init__(rp2040, name)
        self.channels = [RPDMAChannel(self, self.rp2040, i) for i in range(12)]

        self.int_raw = 0
        self._int_enable0 = 0
        self._int_force0 = 0
        self._int_enable1 = 0
        self._int_force1 = 0
        self._timer0 = 0
        self._timer1 = 0
        self._timer2 = 0
        self._timer3 = 0

        self.dreq: dict[int, bool] = {}

    def reset(self) -> None:
        for channel in self.channels:
            channel.transfer_alarm.cancel()
            channel.reset()
        self.int_raw = 0
        self._int_enable0 = 0
        self._int_force0 = 0
        self._int_enable1 = 0
        self._int_force1 = 0
        self._timer0 = 0
        self._timer1 = 0
        self._timer2 = 0
        self._timer3 = 0
        self.dreq.clear()
        self.check_interrupts()

    @property
    def int_status0(self) -> int:
        return (self.int_raw & self._int_enable0) | self._int_force0

    @property
    def int_status1(self) -> int:
        return (self.int_raw & self._int_enable1) | self._int_force1

    def read_uint32(self, offset: int) -> int:
        if (offset & 0x7FF) < CHANNEL_REGISTERS_SIZE:
            channel_index = (offset & 0x7FF) >> 6
            return self.channels[channel_index].read_uint32(offset & CHANNEL_REGISTERS_MASK)

        if offset == TIMER0:
            return self._timer0
        if offset == TIMER1:
            return self._timer1
        if offset == TIMER2:
            return self._timer2
        if offset == TIMER3:
            return self._timer3
        if offset == INTR:
            return self.int_raw
        if offset == INTE0:
            return self._int_enable0
        if offset == INTF0:
            return self._int_force0
        if offset == INTS0:
            return self.int_status0
        if offset == INTE1:
            return self._int_enable1
        if offset == INTF1:
            return self._int_force1
        if offset == INTS1:
            return self.int_status1
        if offset == N_CHANNELS:
            return len(self.channels)
        return super().read_uint32(offset)

    def write_uint32(self, offset: int, value: int) -> None:
        if (offset & 0x7FF) < CHANNEL_REGISTERS_SIZE:
            channel_index = (offset & 0x7FF) >> 6
            self.channels[channel_index].write_uint32(offset & CHANNEL_REGISTERS_MASK, value)
            return

        if offset == TIMER0:
            self._timer0 = value
            return
        if offset == TIMER1:
            self._timer1 = value
            return
        if offset == TIMER2:
            self._timer2 = value
            return
        if offset == TIMER3:
            self._timer3 = value
            return
        if offset in (INTR, INTS0, INTS1):
            self.int_raw &= ~self.raw_write_value
            self.check_interrupts()
            return
        if offset == INTE0:
            self._int_enable0 = value & 0xFFFF
            self.check_interrupts()
            return
        if offset == INTF0:
            self._int_force0 = value & 0xFFFF
            self.check_interrupts()
            return
        if offset == INTE1:
            self._int_enable1 = value & 0xFFFF
            self.check_interrupts()
            return
        if offset == INTF1:
            self._int_force1 = value & 0xFFFF
            self.check_interrupts()
            return
        if offset == MULTI_CHAN_TRIGGER:
            for chan in self.channels:
                if value & (1 << chan.index):
                    chan.start()
            return
        if offset == CHAN_ABORT:
            for chan in self.channels:
                if value & (1 << chan.index):
                    chan.abort()
            return
        super().write_uint32(offset, value)

    def set_dreq(self, dreq_channel: int) -> None:
        if not self.dreq.get(dreq_channel, False):
            self.dreq[dreq_channel] = True
            for channel in self.channels:
                if channel.treq == dreq_channel and channel.active:
                    channel.schedule_transfer()

    def clear_dreq(self, dreq_channel: int) -> None:
        self.dreq[dreq_channel] = False

    def get_timer(self, treq: int) -> float:
        """Returns the number of microseconds for a cycle of the given DMA timer, or 0 if the timer is disabled."""
        dividend = 0
        divisor = 1
        if treq == TREQ.PERMANENT:
            dividend = 1
            divisor = 1
        elif treq == TREQ.TIMER0:
            dividend = self._timer0 >> 16
            divisor = self._timer0 & 0xFFFF
        elif treq == TREQ.TIMER1:
            dividend = self._timer1 >> 16
            divisor = self._timer1 & 0xFFFF
        elif treq == TREQ.TIMER2:
            dividend = self._timer2 >> 16
            divisor = self._timer2 & 0xFFFF
        elif treq == TREQ.TIMER3:
            # TODO: matches upstream rp2040js, which shifts by 36 (JS masks shift amounts to
            # 0-31, so `>>> 36` behaves like `>>> 4`, not `>>> 16` like the other timers).
            # Almost certainly an upstream bug, kept as-is for behavioral parity. Revisit if
            # rp2040js fixes it upstream.
            dividend = urshift(self._timer3, 36)
            divisor = self._timer3 & 0xFFFF

        if divisor == 0:
            return 0
        return ((dividend / divisor) * 1e6) / self.rp2040.clk_sys

    def check_interrupts(self) -> None:
        self.rp2040.set_interrupt(IRQ.DMA_IRQ0, bool(self.int_status0))
        self.rp2040.set_interrupt(IRQ.DMA_IRQ1, bool(self.int_status1))
