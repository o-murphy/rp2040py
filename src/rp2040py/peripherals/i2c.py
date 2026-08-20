from collections.abc import Callable
from enum import IntEnum
from typing import TYPE_CHECKING

from rp2040py.peripherals.peripheral import BasePeripheral
from rp2040py.utils.fifo import FIFO

if TYPE_CHECKING:
    from rp2040py.rp2040 import RP2040

__all__ = (
    "RPI2C",
    "I2CMode",
    "I2CSpeed",
)

IC_CON = 0x00  # I2C Control Register
IC_TAR = 0x04  # I2C Target Address Register
IC_SAR = 0x08  # I2C Slave Address Register
IC_DATA_CMD = 0x10  # I2C Rx/Tx Data Buffer and Command Register
IC_SS_SCL_HCNT = 0x14  # Standard Speed I2C Clock SCL High Count Register
IC_SS_SCL_LCNT = 0x18  # Standard Speed I2C Clock SCL Low Count Register
IC_FS_SCL_HCNT = 0x1C  # Fast Mode or Fast Mode Plus I2C Clock SCL High Count Register
IC_FS_SCL_LCNT = 0x20  # Fast Mode or Fast Mode Plus I2C Clock SCL Low Count Register
IC_INTR_STAT = 0x2C  # I2C Interrupt Status Register
IC_INTR_MASK = 0x30  # I2C Interrupt Mask Register
IC_RAW_INTR_STAT = 0x34  # I2C Raw Interrupt Status Register
IC_RX_TL = 0x38  # I2C Receive FIFO Threshold Register
IC_TX_TL = 0x3C  # I2C Transmit FIFO Threshold Register
IC_CLR_INTR = 0x40  # Clear Combined and Individual Interrupt Register
IC_CLR_RX_UNDER = 0x44  # Clear RX_UNDER Interrupt Register
IC_CLR_RX_OVER = 0x48  # Clear RX_OVER Interrupt Register
IC_CLR_TX_OVER = 0x4C  # Clear TX_OVER Interrupt Register
IC_CLR_RD_REQ = 0x50  # Clear RD_REQ Interrupt Register
IC_CLR_TX_ABRT = 0x54  # Clear TX_ABRT Interrupt Register
IC_CLR_RX_DONE = 0x58  # Clear RX_DONE Interrupt Register
IC_CLR_ACTIVITY = 0x5C  # Clear ACTIVITY Interrupt Register
IC_CLR_STOP_DET = 0x60  # Clear STOP_DET Interrupt Register
IC_CLR_START_DET = 0x64  # Clear START_DET Interrupt Register
IC_CLR_GEN_CALL = 0x68  # Clear GEN_CALL Interrupt Register
IC_ENABLE = 0x6C  # I2C ENABLE Register
IC_STATUS = 0x70  # I2C STATUS Register
IC_TXFLR = 0x74  # I2C Transmit FIFO Level Register
IC_RXFLR = 0x78  # I2C Receive FIFO Level Register
IC_SDA_HOLD = 0x7C  # I2C SDA Hold Time Length Register
IC_TX_ABRT_SOURCE = 0x80  # I2C Transmit Abort Source Register
IC_SLV_DATA_NACK_ONLY = 0x84  # Generate Slave Data NACK Register
IC_DMA_CR = 0x88  # DMA Control Register
IC_DMA_TDLR = 0x8C  # DMA Transmit Data Level Register
IC_DMA_RDLR = 0x90  # DMA Transmit Data Level Register
IC_SDA_SETUP = 0x94  # I2C SDA Setup Register
IC_ACK_GENERAL_CALL = 0x98  # I2C ACK General Call Register
IC_ENABLE_STATUS = 0x9C  # I2C Enable Status Register
IC_FS_SPKLEN = 0xA0  # I2C SS, FS or FM+ spike suppression limit
IC_CLR_RESTART_DET = 0xA8  # Clear RESTART_DET Interrupt Register
IC_COMP_PARAM_1 = 0xF4  # Component Parameter Register 1
IC_COMP_VERSION = 0xF8  # I2C Component Version Register
IC_COMP_TYPE = 0xFC  # I2C Component Type Register

# IC_CON bits:
STOP_DET_IF_MASTER_ACTIVE = 1 << 10
RX_FIFO_FULL_HLD_CTRL = 1 << 9
TX_EMPTY_CTRL = 1 << 8
STOP_DET_IFADDRESSED = 1 << 7
IC_SLAVE_DISABLE = 1 << 6
IC_RESTART_EN = 1 << 5
IC_10BITADDR_MASTER = 1 << 4
IC_10BITADDR_SLAVE = 1 << 3
SPEED_SHIFT = 1
SPEED_MASK = 0x3
MASTER_MODE = 1 << 0

# IC_TAR bits:
SPECIAL = 1 << 11
GC_OR_START = 1 << 10

# IC_STATUS bits:
SLV_ACTIVITY = 1 << 6
MST_ACTIVITY = 1 << 5
RFF = 1 << 4
RFNE = 1 << 3
TFE = 1 << 2
TFNF = 1 << 1
ACTIVITY = 1 << 0

# IC_ENABLE bits:
TX_CMD_BLOCK = 1 << 2
ABORT = 1 << 1
ENABLE = 1 << 0

# IC_TX_ABRT_SOURCE bits:
TX_FLUSH_CNT_MASK = 0x1FF
TX_FLUSH_CNT_SHIFT = 23
ABRT_USER_ABRT = 1 << 16
ABRT_SLVRD_INT = 1 << 15
ABRT_SLV_ARBLOST = 1 << 14
ABRT_SLVFLUSH_TXFIFO = 1 << 13
ARB_LOST = 1 << 12
ABRT_MASTER_DIS = 1 << 11
ABRT_10B_RD_NORSTRT = 1 << 10
ABRT_SBYTE_NORSTRT = 1 << 9
ABRT_HS_NORSTRT = 1 << 8
ABRT_SBYTE_ACKDET = 1 << 7
ABRT_HS_ACKDET = 1 << 6
ABRT_GCALL_READ = 1 << 5
ABRT_GCALL_NOACK = 1 << 4
ABRT_TXDATA_NOACK = 1 << 3
ABRT_10ADDR2_NOACK = 1 << 2
ABRT_10ADDR1_NOACK = 1 << 1
ABRT_7B_ADDR_NOACK = 1 << 0


# Connection parameters
class I2CMode(IntEnum):
    WRITE = 0
    READ = 1


class I2CSpeed(IntEnum):
    INVALID = 0
    STANDARD = 1  # standard mode (100 kbit/s)
    FAST_MODE = 2  # fast mode (<=400 kbit/s) or fast mode plus (<=1000Kbit/s)
    HIGH_SPEED_MODE = 3  # high speed mode (3.4 Mbit/s)


class I2CState(IntEnum):
    IDLE = 0
    START = 1
    CONNECT = 2
    CONNECTED = 3
    STOP = 4


# Interrupts
R_RESTART_DET = 1 << 12  # Slave mode only
R_GEN_CALL = 1 << 11
R_START_DET = 1 << 10
R_STOP_DET = 1 << 9
R_ACTIVITY = 1 << 8
R_RX_DONE = 1 << 7
R_TX_ABRT = 1 << 6
R_RD_REQ = 1 << 5
R_TX_EMPTY = 1 << 4
R_TX_OVER = 1 << 3
R_RX_FULL = 1 << 2
R_RX_OVER = 1 << 1
R_RX_UNDER = 1 << 0

# FIFO entry bits
FIRST_DATA_BYTE = 1 << 10
RESTART = 1 << 10
STOP = 1 << 9
CMD = 1 << 8  # 0 for write, 1 for read


class RPI2C(BasePeripheral):
    def __init__(self, rp2040: "RP2040", name: str, irq: int):
        super().__init__(rp2040, name)
        self.irq = irq

        self._state = I2CState.IDLE
        self._busy = False
        self._stop = False
        self._pending_restart = False
        self._first_byte = False
        self._rx_fifo = FIFO(16)
        self._tx_fifo = FIFO(16)

        # user provided callbacks
        self.on_start: Callable[[bool], None] = lambda repeated_start: self.complete_start()
        self.on_connect: Callable[[int, I2CMode], None] = lambda address, mode: self.complete_connect(False)
        self.on_write_byte: Callable[[int], None] = lambda value: self.complete_write(False)
        self.on_read_byte: Callable[[bool], None] = lambda ack: self.complete_read(0xFF)
        self.on_stop: Callable[[], None] = self.complete_stop

        self.enable = 0
        self.rx_threshold = 0
        self.tx_threshold = 0
        self.control = IC_SLAVE_DISABLE | IC_RESTART_EN | (I2CSpeed.FAST_MODE << SPEED_SHIFT) | MASTER_MODE
        self.ss_clock_high_period = 0x0028
        self.ss_clock_low_period = 0x002F
        self.fs_clock_high_period = 0x0006
        self.fs_clock_low_period = 0x000D
        self.target_address = 0x55
        self.slave_address = 0x55
        self.abort_source = 0
        self.int_raw = 0
        self.int_enable = 0
        self._spikelen = 0x07

    def reset(self) -> None:
        """Registers, FIFOs and the bus state machine, back to power-on (0089 Phase 5). The five
        `on_*` callbacks are wiring - whatever is on the bus - and are left alone."""
        self._state = I2CState.IDLE
        self._busy = False
        self._stop = False
        self._pending_restart = False
        self._first_byte = False
        self._rx_fifo.reset()
        self._tx_fifo.reset()
        self.enable = 0
        self.rx_threshold = 0
        self.tx_threshold = 0
        self.control = IC_SLAVE_DISABLE | IC_RESTART_EN | (I2CSpeed.FAST_MODE << SPEED_SHIFT) | MASTER_MODE
        self.ss_clock_high_period = 0x0028
        self.ss_clock_low_period = 0x002F
        self.fs_clock_high_period = 0x0006
        self.fs_clock_low_period = 0x000D
        self.target_address = 0x55
        self.slave_address = 0x55
        self.abort_source = 0
        self.int_raw = 0
        self.int_enable = 0
        self._spikelen = 0x07
        self.rp2040.set_interrupt(self.irq, False)

    @property
    def int_status(self) -> int:
        return self.int_raw & self.int_enable

    @property
    def speed(self) -> I2CSpeed:
        return I2CSpeed((self.control >> SPEED_SHIFT) & SPEED_MASK)

    @property
    def scl_low_period(self) -> int:
        return self.ss_clock_low_period if self.speed == I2CSpeed.STANDARD else self.fs_clock_low_period

    @property
    def scl_high_period(self) -> int:
        return self.ss_clock_high_period if self.speed == I2CSpeed.STANDARD else self.fs_clock_high_period

    @property
    def master_bits(self) -> int:
        return 10 if self.control & IC_10BITADDR_MASTER else 7

    def check_interrupts(self) -> None:
        self.rp2040.set_interrupt(self.irq, bool(self.int_status))

    def _clear_interrupts(self, mask: int) -> int:
        if self.int_raw & mask:
            self.int_raw &= ~mask
            self.check_interrupts()
            return 1
        return 0

    def _set_interrupts(self, mask: int) -> None:
        if not (self.int_raw & mask):
            self.int_raw |= mask
            self.check_interrupts()

    def _abort(self, reason: int) -> None:
        self.abort_source &= ~TX_FLUSH_CNT_MASK
        self.abort_source |= reason | (self._tx_fifo.item_count << TX_FLUSH_CNT_SHIFT)
        self._tx_fifo.reset()
        self._set_interrupts(R_TX_ABRT)

    def _next_command(self) -> None:
        enabled = self.enable & ENABLE
        blocked = self.enable & TX_CMD_BLOCK
        if self._tx_fifo.empty or self._busy or blocked or not enabled:
            return
        self._busy = True
        restart = bool(self._tx_fifo.peek() & RESTART) and not self._pending_restart and not self._stop
        if self._state == I2CState.IDLE or restart:
            self._pending_restart = restart
            self._stop = False
            self._state = I2CState.START
            self.on_start(restart)
            return
        self._pending_restart = False
        cmd = self._tx_fifo.pull()
        read_mode = bool(cmd & CMD)
        self._stop = bool(cmd & STOP)
        if read_mode:
            self.on_read_byte(not self._stop)
        else:
            self.on_write_byte(cmd & 0xFF)
        if self._tx_fifo.item_count <= self.tx_threshold:
            self._set_interrupts(R_TX_EMPTY)

    def _push_rx(self, value: int) -> None:
        if self._rx_fifo.full:
            self._set_interrupts(R_RX_OVER)
            return
        self._rx_fifo.push(value)
        if self._rx_fifo.item_count > self.rx_threshold:
            self._set_interrupts(R_RX_FULL)

    def complete_start(self) -> None:
        if self._tx_fifo.empty or self._state != I2CState.START or self._stop:
            self.on_stop()
            return
        mode = I2CMode.READ if self._tx_fifo.peek() & CMD else I2CMode.WRITE
        self._state = I2CState.CONNECT
        self._set_interrupts(R_START_DET)
        address_mask = 0x3FF if self.master_bits == 10 else 0xFF
        self.on_connect(self.target_address & address_mask, mode)

    def complete_connect(self, ack: bool, nack_byte: int = 0) -> None:
        if not ack or self._stop:
            if not ack:
                if not self.target_address:
                    self._abort(ABRT_GCALL_NOACK)
                elif self.control & IC_10BITADDR_MASTER:
                    self._abort(ABRT_10ADDR1_NOACK if nack_byte == 0 else ABRT_10ADDR2_NOACK)
                else:
                    self._abort(ABRT_7B_ADDR_NOACK)
            self._state = I2CState.STOP
            self.on_stop()
            return

        self._state = I2CState.CONNECTED
        self._busy = False
        self._first_byte = True
        self._next_command()

    def complete_write(self, ack: bool) -> None:
        if not ack or self._stop:
            if not ack:
                self._abort(ABRT_TXDATA_NOACK)
            self._state = I2CState.STOP
            self.on_stop()
            return

        self._busy = False
        self._next_command()

    def complete_read(self, value: int) -> None:
        self._push_rx(value | (FIRST_DATA_BYTE if self._first_byte else 0))
        if self._stop:
            self._state = I2CState.STOP
            self.on_stop()
            return
        self._first_byte = False
        self._busy = False
        self._next_command()

    def complete_stop(self) -> None:
        self._state = I2CState.IDLE
        self._set_interrupts(R_STOP_DET)
        self._busy = False
        self._pending_restart = False
        if self.enable & ABORT:
            self.enable &= ~ABORT
        else:
            self._next_command()

    def arbitration_lost(self) -> None:
        self._state = I2CState.IDLE
        self._busy = False
        self._abort(ARB_LOST)

    def read_uint32(self, offset: int) -> int:
        if offset == IC_CON:
            return self.control
        if offset == IC_TAR:
            return self.target_address
        if offset == IC_SAR:
            return self.slave_address
        if offset == IC_DATA_CMD:
            if self._rx_fifo.empty:
                self._set_interrupts(R_RX_UNDER)
                return 0
            self._clear_interrupts(R_RX_FULL)
            return self._rx_fifo.pull()
        if offset == IC_SS_SCL_HCNT:
            return self.ss_clock_high_period
        if offset == IC_SS_SCL_LCNT:
            return self.ss_clock_low_period
        if offset == IC_FS_SCL_HCNT:
            return self.fs_clock_high_period
        if offset == IC_FS_SCL_LCNT:
            return self.fs_clock_low_period
        if offset == IC_INTR_STAT:
            return self.int_status
        if offset == IC_INTR_MASK:
            return self.int_enable
        if offset == IC_RAW_INTR_STAT:
            return self.int_raw
        if offset == IC_RX_TL:
            return self.rx_threshold
        if offset == IC_TX_TL:
            return self.tx_threshold
        if offset == IC_CLR_INTR:
            self.abort_source &= ABRT_SBYTE_NORSTRT  # Clear IC_TX_ABRT_SOURCE, expect for bit 9
            return self._clear_interrupts(
                R_RX_UNDER
                | R_RX_OVER
                | R_TX_OVER
                | R_RD_REQ
                | R_TX_ABRT
                | R_RX_DONE
                | R_ACTIVITY
                | R_STOP_DET
                | R_START_DET
                | R_GEN_CALL
            )
        if offset == IC_CLR_RX_UNDER:
            return self._clear_interrupts(R_RX_UNDER)
        if offset == IC_CLR_RX_OVER:
            return self._clear_interrupts(R_RX_OVER)
        if offset == IC_CLR_TX_OVER:
            return self._clear_interrupts(R_TX_OVER)
        if offset == IC_CLR_RD_REQ:
            return self._clear_interrupts(R_RD_REQ)
        if offset == IC_CLR_TX_ABRT:
            self.abort_source &= ABRT_SBYTE_NORSTRT  # Clear IC_TX_ABRT_SOURCE, expect for bit 9
            return self._clear_interrupts(R_TX_ABRT)
        if offset == IC_CLR_RX_DONE:
            return self._clear_interrupts(R_RX_DONE)
        if offset == IC_CLR_ACTIVITY:
            return self._clear_interrupts(R_ACTIVITY)
        if offset == IC_CLR_STOP_DET:
            return self._clear_interrupts(R_STOP_DET)
        if offset == IC_CLR_START_DET:
            return self._clear_interrupts(R_START_DET)
        if offset == IC_CLR_GEN_CALL:
            return self._clear_interrupts(R_GEN_CALL)
        if offset == IC_ENABLE:
            return self.enable
        if offset == IC_STATUS:
            return (
                (MST_ACTIVITY | ACTIVITY if self._state != I2CState.IDLE else 0)
                | (RFF if self._rx_fifo.full else 0)
                | (RFNE if not self._rx_fifo.empty else 0)
                | (TFE if self._tx_fifo.empty else 0)
                | (TFNF if not self._tx_fifo.full else 0)
            )
        if offset == IC_TXFLR:
            return self._tx_fifo.item_count
        if offset == IC_RXFLR:
            return self._rx_fifo.item_count
        if offset == IC_SDA_HOLD:
            return 0x01
        if offset == IC_TX_ABRT_SOURCE:
            value = self.abort_source
            self.abort_source &= ABRT_SBYTE_NORSTRT  # Clear IC_TX_ABRT_SOURCE, expect for bit 9
            return value
        if offset == IC_ENABLE_STATUS:
            # I2C status - read only. bit 0 reflects IC_ENABLE, bit 1,2 relate to i2c slave mode.
            return self.enable & 0x1
        if offset == IC_FS_SPKLEN:
            return self._spikelen & 0xFF
        if offset == IC_COMP_PARAM_1:
            # From the datasheet:
            # Note This register is not implemented and therefore reads as 0. If it was
            # implemented it would be a constant read-only register that contains encoded
            # information about the component's parameter settings.
            return 0
        if offset == IC_COMP_VERSION:
            return 0x3230312A
        if offset == IC_COMP_TYPE:
            return 0x44570140
        return super().read_uint32(offset)

    def write_uint32(self, offset: int, value: int) -> None:
        if offset == IC_CON:
            if ((value >> SPEED_SHIFT) & SPEED_MASK) == I2CSpeed.INVALID:
                value = (value & ~(SPEED_MASK << SPEED_SHIFT)) | (I2CSpeed.HIGH_SPEED_MODE << SPEED_SHIFT)
            self.control = value

        elif offset == IC_TAR:
            self.target_address = value & 0x3FF

        elif offset == IC_SAR:
            self.slave_address = value & 0x3FF

        elif offset == IC_DATA_CMD:
            if self._tx_fifo.full:
                self._set_interrupts(R_TX_OVER)
            else:
                self._tx_fifo.push(value)
                self._clear_interrupts(R_TX_EMPTY)
                self._next_command()

        elif offset == IC_SS_SCL_HCNT:
            self.ss_clock_high_period = value & 0xFFFF

        elif offset == IC_SS_SCL_LCNT:
            self.ss_clock_low_period = value & 0xFFFF

        elif offset == IC_FS_SCL_HCNT:
            self.fs_clock_high_period = value & 0xFFFF

        elif offset == IC_FS_SCL_LCNT:
            self.fs_clock_low_period = value & 0xFFFF

        elif offset == IC_SDA_HOLD:
            if not (value & ENABLE) and value != 0x1:
                self.warn("Unimplemented write to IC_SDA_HOLD")

        elif offset == IC_RX_TL:
            self.rx_threshold = value & 0xFF
            self.rx_threshold = min(self.rx_threshold, self._rx_fifo.size)

        elif offset == IC_TX_TL:
            self.tx_threshold = value & 0xFF
            self.tx_threshold = min(self.tx_threshold, self._tx_fifo.size)

        elif offset == IC_ENABLE:
            # ABORT bit can only be set by software, not cleared.
            value |= self.enable & ABORT
            if value & ABORT:
                if self._state == I2CState.IDLE:
                    value &= ~ABORT
                else:
                    self._abort(ABRT_USER_ABRT)
                    self._stop = True
            if not (value & ENABLE):
                self._tx_fifo.reset()
                self._rx_fifo.reset()
            self.enable = value
            self._next_command()  # TX_CMD_BLOCK may have changed

        elif offset == IC_FS_SPKLEN:
            if not (value & ENABLE) and value > 0:
                self._spikelen = value

        else:
            super().write_uint32(offset, value)
