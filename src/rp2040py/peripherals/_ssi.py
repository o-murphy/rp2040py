from collections import deque
from typing import TYPE_CHECKING

from rp2040py.gpio_pin import GPIOPinState
from rp2040py.peripherals.peripheral import BasePeripheral

if TYPE_CHECKING:
    from rp2040py.rp2040 import RP2040

__all__ = ("RPSSI",)

# See RP2040 datasheet sect 4.10.13
SSI_CTRLR0 = 0x00000000
SSI_CTRLR1 = 0x00000004
SSI_SSIENR = 0x00000008
SSI_MWCR = 0x0000000C
SSI_SER = 0x00000010
SSI_BAUDR = 0x00000014
SSI_TXFTLR = 0x00000018
SSI_RXFTLR = 0x0000001C
SSI_TXFLR = 0x00000020
SSI_RXFLR = 0x00000024
SSI_SR = 0x00000028
SSI_SR_TFNF_BITS = 0x00000002
SSI_SR_TFE_BITS = 0x00000004
SSI_SR_RFNE_BITS = 0x00000008
SSI_IMR = 0x0000002C
SSI_ISR = 0x00000030
SSI_RISR = 0x00000034
SSI_TXOICR = 0x00000038
SSI_RXOICR = 0x0000003C
SSI_RXUICR = 0x00000040
SSI_MSTICR = 0x00000044
SSI_ICR = 0x00000048
SSI_DMACR = 0x0000004C
SSI_DMATDLR = 0x00000050
SSI_DMARDLR = 0x00000054
# Identification register
SSI_IDR = 0x00000058
SSI_VERSION_ID = 0x0000005C
SSI_DR0 = 0x00000060
SSI_RX_SAMPLE_DLY = 0x000000F0
SSI_SPI_CTRL_R0 = 0x000000F4
SSI_TXD_DRIVE_EDGE = 0x000000F8

# JEDEC-standard SPI NOR flash commands - the subset the RP2040 bootrom's ROM_FUNC_FLASH_*
# helpers (called by the Pico SDK's flash_range_erase()/flash_range_program(), themselves called
# by MicroPython's rp2.Flash) actually issue over this peripheral. XIP reads never reach here -
# RP2040.read_uint32()/read_uint16() serve those directly from the `flash` bytearray - so this
# only needs to cover what real flash-*writing* software drives.
CMD_WRITE_ENABLE = 0x06
CMD_WRITE_DISABLE = 0x04
CMD_READ_STATUS_1 = 0x05
CMD_READ_STATUS_2 = 0x35
CMD_WRITE_STATUS = 0x01
CMD_PAGE_PROGRAM = 0x02
CMD_SECTOR_ERASE = 0x20  # 4 KB
CMD_BLOCK_ERASE = 0xD8  # 64 KB
CMD_READ_DATA = 0x03
CMD_READ_JEDEC_ID = 0x9F

FLASH_PAGE_SIZE = 256
FLASH_SECTOR_SIZE = 4096
FLASH_BLOCK_SIZE = 65536

STATUS_WEL_BIT = 0x02  # write-enable-latch (status register 1)
STATUS2_QE_BIT = 0x02  # quad-enable (status register 2) - reported permanently set (see
# CMD_READ_STATUS_2 below): connect_internal_flash() reads this before deciding whether it needs
# to issue CMD_WRITE_STATUS to turn quad mode on - reporting it already enabled lets that check
# succeed immediately rather than retrying a WRSR sequence this peripheral doesn't need to act on
# (nothing here actually depends on quad vs standard SPI framing either way).

# Arbitrary but plausible Winbond-shaped ID (manufacturer, memory type, capacity) - nothing in the
# boot/flash path is known to hard-require a specific real chip's exact ID, just *a* consistent
# 3-byte response to CMD_READ_JEDEC_ID.
JEDEC_ID = (0xEF, 0x40, 0x15)


class RPSSI(BasePeripheral):
    def __init__(self, rp2040: "RP2040", name: str):
        super().__init__(rp2040, name)
        self._txflr = 0
        self._baudr = 0
        self._crtlr0 = 0
        self._crtlr1 = 0
        self._ssienr = 0
        self._spictlr0 = 0
        self._rxsampldly = 0
        self._txddriveedge = 0

        # Software-driven SPI NOR flash command state (see the module docstring above): `_tx_buffer`
        # accumulates the bytes shifted out via DR0 since chip-select was last asserted - real
        # flash commands are framed by chip-select, not by anything visible in the byte stream
        # itself. On RP2040, chip-select is *not* SSI's own SER/SSIENR - the Pico SDK's
        # flash_cs_force() bit-bangs the QSPI_SS pin's IO_QSPI override directly instead ("in case
        # RAM-resident IRQs are still running... the bootrom does the same", per its own comment
        # in pico-sdk's flash.c), bypassing SSI's chip-select machinery entirely. So framing here
        # keys off `rp2040.qspi[1]` (QSPI_SS, active-low) actually changing value, not SSIENR.
        # `_rx_queue` holds bytes shifted *in* (the flash chip's response) waiting to be read back
        # via DR0 - necessary because a real SPI transfer is full-duplex (every outgoing byte has a
        # corresponding incoming one), and multi-byte responses (JEDEC ID, read-status, page reads)
        # need to come back in the right order across several DR0 reads.
        self._write_enabled = False
        # Synced to the pin's actual resolved value at construction time, not hardcoded False -
        # QSPI_SS's own reset state (see gpio_pin.py: `always_output_enabled` pins with no
        # function-select driving them yet resolve `.value` to LOW, i.e. *asserted*) can already be
        # "asserted" before any real edge ever happens. Hardcoding False here desyncs this flag from
        # the pin: the bootrom's first-ever `flash_cs_force(low)` is then a no-op (LOW -> LOW, no
        # edge fires `_on_cs_pin_changed`), so `_cs_asserted` would stay wrongly False forever and
        # every byte of that first command would be silently dropped by `write_uint32`'s
        # `self._ssienr and self._cs_asserted` guard - starving the bootrom's flash_do_cmd_cs() TX/RX
        # FIFO-drain loop of the RX bytes it's waiting for and hanging it forever.
        self._cs_asserted = rp2040.qspi[1].value == GPIOPinState.LOW
        self._tx_buffer = bytearray()
        self._rx_queue: deque[int] = deque()
        rp2040.qspi[1].add_listener(self._on_cs_pin_changed)

    def reset(self) -> None:
        """The SSI half of the XIP domain (`PSM.WDSEL`'s XIP bit - there is no `RESETS_RESET_SSI`;
        0089 Phase 5). A real watchdog reboot resets it too, and the bootrom re-initialises it on
        the way back up, which is what makes clearing an in-flight flash command safe here.

        Two things are deliberately not reset, and both are landmines the constructor above already
        documents:

        - **The QSPI_SS listener is not re-added.** `_listeners` is a set of *bound methods*, and a
          fresh `self._on_cs_pin_changed` is a new object each time - re-adding would leave two
          listeners firing per edge.
        - **`_cs_asserted` is re-synced from the pin, never hardcoded `False`.** An
          `always_output_enabled` pad with nothing driving it resolves to LOW, i.e. already
          asserted; hardcoding False desyncs the flag from the pin, the bootrom's first
          `flash_cs_force(low)` then fires no edge, and its flash command hangs forever waiting for
          RX bytes that are silently dropped. Read the constructor's own comment before touching
          this.
        """
        self._txflr = 0
        self._baudr = 0
        self._crtlr0 = 0
        self._crtlr1 = 0
        self._ssienr = 0
        self._spictlr0 = 0
        self._rxsampldly = 0
        self._txddriveedge = 0
        self._write_enabled = False
        self._cs_asserted = self.rp2040.qspi[1].value == GPIOPinState.LOW
        self._tx_buffer = bytearray()
        self._rx_queue.clear()

    def read_uint32(self, offset: int) -> int:
        if offset == SSI_TXFLR:
            return self._txflr
        if offset == SSI_RXFLR:
            return len(self._rx_queue)
        if offset == SSI_CTRLR0:
            return self._crtlr0  # & 0x017FFFFF = b23,b25..31 reserved
        if offset == SSI_CTRLR1:
            return self._crtlr1
        if offset == SSI_SSIENR:
            return self._ssienr
        if offset == SSI_BAUDR:
            return self._baudr
        if offset == SSI_SR:
            rfne = SSI_SR_RFNE_BITS if self._rx_queue else 0
            return SSI_SR_TFE_BITS | SSI_SR_TFNF_BITS | rfne
        if offset == SSI_IDR:
            return 0x51535049
        if offset == SSI_VERSION_ID:
            return 0x3430312A
        if offset == SSI_RX_SAMPLE_DLY:
            return self._rxsampldly
        if offset == SSI_TXD_DRIVE_EDGE:
            return self._txddriveedge
        if offset == SSI_SPI_CTRL_R0:
            return self._spictlr0  # b6,7,10,19..23 reserved
        if offset == SSI_DR0:
            return self._rx_queue.popleft() if self._rx_queue else 0
        return super().read_uint32(offset)

    def write_uint32(self, offset: int, value: int) -> None:
        if offset == SSI_TXFLR:
            self._txflr = value
        elif offset == SSI_RXFLR:
            pass  # real hardware: read-only FIFO-level status, write is a no-op
        elif offset == SSI_CTRLR0:
            self._crtlr0 = value  # & 0x017FFFFF = b23,b25..31 reserved
        elif offset == SSI_CTRLR1:
            self._crtlr1 = value
        elif offset == SSI_SSIENR:
            self._ssienr = value
        elif offset == SSI_BAUDR:
            self._baudr = value
        elif offset == SSI_RX_SAMPLE_DLY:
            self._rxsampldly = value & 0xFF
        elif offset == SSI_TXD_DRIVE_EDGE:
            self._txddriveedge = value & 0xFF
        elif offset == SSI_SPI_CTRL_R0:
            self._spictlr0 = value
        elif offset == SSI_DR0:
            if self._ssienr:
                if self._cs_asserted:
                    self._rx_queue.append(self._shift_byte(value & 0xFF))
                else:
                    # SSI's shift register/FIFOs are wired independently of the QSPI_SS GPIO pin -
                    # real hardware keeps shifting bytes through DR0 even while software has forced
                    # chip-select high (e.g. flash_exit_xip()'s deliberate CS-high dummy-clock
                    # compatibility sequence, pico-bootrom's program_flash_generic.c). The virtual
                    # flash chip isn't "listening" in that state, so there's nothing meaningful to
                    # shift back - 0xFF (idle bus), same fallback `_shift_byte()` uses for an
                    # unrecognized opcode - but the RX FIFO must still gain an entry, or firmware's
                    # TXFLR/RXFLR-driven flow-control loop (bootrom's flash_put_get()) spins forever
                    # waiting for bytes that will never arrive.
                    self._rx_queue.append(0xFF)
        else:
            super().write_uint32(offset, value)

    def _on_cs_pin_changed(self, value: GPIOPinState, _last_value: GPIOPinState) -> None:
        # QSPI_SS is active-low: LOW means the flash chip is selected.
        now_asserted = value == GPIOPinState.LOW
        if now_asserted and not self._cs_asserted:
            # Chip-select asserting: start a fresh command.
            self._tx_buffer = bytearray()
            self._rx_queue = deque()
        elif self._cs_asserted and not now_asserted:
            # Chip-select deasserting: the command (and, for PAGE_PROGRAM, however many data bytes
            # were shifted - not knowable in advance, only by where chip-select ends up deasserted)
            # is now complete - apply whatever it was to the actual flash bytes.
            self._apply_command()
        self._cs_asserted = now_asserted

    def _shift_byte(self, byte_out: int) -> int:
        """One SPI clock's worth of full-duplex exchange: `byte_out` is being shifted into the
        (virtual) flash chip, and this returns whatever it shifts back out in response. Only
        WRITE_ENABLE/WRITE_DISABLE take effect immediately (single-byte commands, nothing to wait
        for); erase/program are deferred to `_apply_command()` at chip-select deassert, since real
        flash chips apply them atomically once the whole command+address+data has been clocked in,
        not byte-by-byte as they arrive.
        """
        self._tx_buffer.append(byte_out)
        pos = len(self._tx_buffer) - 1
        opcode = self._tx_buffer[0]

        if pos == 0:
            if opcode == CMD_WRITE_ENABLE:
                self._write_enabled = True
            elif opcode == CMD_WRITE_DISABLE:
                self._write_enabled = False
            return 0xFF

        if opcode == CMD_READ_STATUS_1:
            return STATUS_WEL_BIT if self._write_enabled else 0

        if opcode == CMD_READ_STATUS_2:
            return STATUS2_QE_BIT

        if opcode == CMD_WRITE_STATUS:
            return 0  # accepted and ignored - see STATUS2_QE_BIT above

        if opcode == CMD_READ_JEDEC_ID:
            index = pos - 1
            return JEDEC_ID[index] if index < len(JEDEC_ID) else 0

        if opcode == CMD_READ_DATA and pos >= 4:
            address = self._address_from_buffer()
            target = address + (pos - 4)
            flash = self.rp2040.flash
            return flash[target] if 0 <= target < len(flash) else 0xFF

        # Unrecognized opcode (e.g. a dummy/mode-continuation byte in a quad-I/O read sequence, or
        # a probe this peripheral doesn't need to model): 0xFF, not 0x00 - matching what a real
        # floating/idle SPI bus reads back, the least likely value to look like "a real but wrong"
        # answer to whatever's checking it.
        return 0xFF

    def _address_from_buffer(self) -> int:
        buf = self._tx_buffer
        return (buf[1] << 16) | (buf[2] << 8) | buf[3]

    def _apply_command(self) -> None:
        if not self._tx_buffer:
            return
        opcode = self._tx_buffer[0]
        flash = self.rp2040.flash

        if opcode in (CMD_SECTOR_ERASE, CMD_BLOCK_ERASE) and len(self._tx_buffer) >= 4:
            if self._write_enabled:
                size = FLASH_SECTOR_SIZE if opcode == CMD_SECTOR_ERASE else FLASH_BLOCK_SIZE
                address = self._address_from_buffer() & ~(size - 1)
                flash[address : address + size] = b"\xff" * size
            self._write_enabled = False

        elif opcode == CMD_PAGE_PROGRAM and len(self._tx_buffer) > 4:
            if self._write_enabled:
                address = self._address_from_buffer()
                data = self._tx_buffer[4 : 4 + FLASH_PAGE_SIZE]
                for i, byte in enumerate(data):
                    target = address + i
                    if 0 <= target < len(flash):
                        # NOR flash program can only clear bits (1 -> 0), never set them - AND
                        # rather than overwrite, matching real hardware (and catching software
                        # that programs without erasing first the same way real flash would).
                        flash[target] &= byte
            self._write_enabled = False

        elif opcode == CMD_WRITE_STATUS:
            self._write_enabled = False

        self._tx_buffer = bytearray()
