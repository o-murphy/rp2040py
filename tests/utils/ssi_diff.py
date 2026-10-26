"""A lockstep differential oracle for the SSI (the flash command path): the same generated stimulus drives two chips and everything observable is compared after every step.

docs/records/0096-cpp-mcu-core.md, the flash-path design note. The method is the PIO's and the DMA's (tests/utils/pio_diff.py, dma_diff.py): one chip whose SSI is the
*pure-Python* reference (``peripherals/_ssi.py``, built explicitly because the facade would hand out the native one) and one whose SSI is whatever the facade gives
(today the same class, later the C++ one), fed one stream of operations:

* writes and reads of **every register offset** through the bus (so the unimplemented ones - DR1..DR35, anything past the block - and their warnings are compared), with
  the side-effect-free ones read after every step and DR0 read as an operation of its own;
* **flash commands** the way the bootrom and the Pico SDK drive them: QSPI_SS forced low through the pin's ``ctrl`` override (what ``flash_cs_force()`` does), each byte
  written to DR0 and read straight back (the shift is full duplex), QSPI_SS forced high - which is when an erase or a program is applied. Every opcode the reference knows
  (write enable/disable, status 1 and 2, write status, page program, 4K and 64K erase, read data, JEDEC ID) with random addresses, data of any length up to 300 bytes
  (the reference keeps 256 data bytes of a program and indexes a read by position, whatever the length) and random bytes of no command at all;
* raw DR0 traffic with chip-select asserted, deasserted, and SSIENR clear; CS toggles with an empty command; CS released to the pad's own level;
* ``reset()``, and pokes of the flash around the addresses used (erased, programmed, patterned) so that an erase, an AND-program and a read each have something to find.

* the **FIFO, interrupt and DMA behaviour** (datasheet 4.10.5-4.10.9, docs/records/0098 "XIP_CTRL and the XIP subsystem"): bursts of DR writes that overflow the 16-entry RX FIFO, reads
  of an empty FIFO, the thresholds (TXFTLR, RXFTLR, DMATDLR, DMARDLR) and the interrupt mask (IMR) kept small so that they matter, the clear-on-read registers, ``CTRLR0.TMOD``,
  DMACR; the combined interrupt (``XIP_IRQ``) and the two DMA requests are recorded as they are announced, so *when* the chip is told is compared as well.

After **each** step: the register file (bar the clear-on-read ones, which are operations of their own), the RX FIFO's level *and contents*, the held interrupts and the raw and masked
status, what the chip was last told, the command state (write-enable latch, chip-select flag, the command's length and its first 260 bytes), the flash bytes of the windows the
stimulus works in, and the ordered log of warnings, interrupt-line and DMA-request calls; an exception on one side and not the other is a difference like any other.

The one trap of the rig: both chips' SSIs listen to their own QSPI_SS, and the pure rig *replaces* ``chip.ssi`` after construction - the replaced SSI's listener is taken
off the pin (it would apply every command to the same flash a second time).

"""

import random
from collections.abc import Callable
from typing import Any

from rp2040py.peripherals import _ssi as S
from utils.twin import new_chip

SSI_BASE = 0x18000000
FLASH_WINDOWS = ((0x0, 0x40000), (0xFFE000, 0x1000000))  # the first 256 KiB and the last 8 KiB of the 16 MiB flash

# GPIOPin.ctrl: output_override (bits 9:8) 2 = low, 3 = high; output-enable override (bits 13:12) 3 = forced on - what pico-sdk's flash_cs_force() leaves in place.
_OE_FORCE_ENABLED = 3 << 12
CS_LOW = _OE_FORCE_ENABLED | (2 << 8)
CS_HIGH = _OE_FORCE_ENABLED | (3 << 8)
CS_RELEASED = 0  # no override at all: the pad's own level (an always-output-enabled pin nothing drives reads LOW)

REGISTERS = (
    S.SSI_CTRLR0, S.SSI_CTRLR1, S.SSI_SSIENR, S.SSI_MWCR, S.SSI_SER, S.SSI_BAUDR, S.SSI_TXFTLR, S.SSI_RXFTLR, S.SSI_TXFLR, S.SSI_RXFLR, S.SSI_SR, S.SSI_IMR, S.SSI_ISR,
    S.SSI_RISR, S.SSI_TXOICR, S.SSI_RXOICR, S.SSI_RXUICR, S.SSI_MSTICR, S.SSI_ICR, S.SSI_DMACR, S.SSI_DMATDLR, S.SSI_DMARDLR, S.SSI_IDR, S.SSI_VERSION_ID,
    S.SSI_RX_SAMPLE_DLY, S.SSI_SPI_CTRL_R0, S.SSI_TXD_DRIVE_EDGE,
)  # fmt: skip
WRITABLE = (
    S.SSI_CTRLR0, S.SSI_CTRLR1, S.SSI_BAUDR, S.SSI_TXFLR, S.SSI_RXFLR, S.SSI_RX_SAMPLE_DLY, S.SSI_SPI_CTRL_R0, S.SSI_TXD_DRIVE_EDGE, S.SSI_SSIENR, S.SSI_MWCR, S.SSI_SER,
    S.SSI_TXFTLR, S.SSI_RXFTLR, S.SSI_IMR, S.SSI_DMACR, S.SSI_DMATDLR, S.SSI_DMARDLR, S.SSI_ISR, S.SSI_ICR,
)  # fmt: skip
UNIMPLEMENTED = (0xFC, 0x100, 0x200)
DR_ALIASES = (0x64, 0x68, 0xA0, 0xEC)  # DR1..DR35 are the data register too
OPCODES = (
    S.CMD_WRITE_ENABLE, S.CMD_WRITE_DISABLE, S.CMD_READ_STATUS_1, S.CMD_READ_STATUS_2, S.CMD_WRITE_STATUS, S.CMD_PAGE_PROGRAM, S.CMD_SECTOR_ERASE, S.CMD_BLOCK_ERASE,
    S.CMD_READ_DATA, S.CMD_READ_JEDEC_ID,
)  # fmt: skip


class Rig:
    """One chip plus the log of what its SSI warned about."""

    def __init__(self, kind: str) -> None:
        self.kind = kind
        self.chip = new_chip(kind)
        self.log: list[tuple] = []
        self._tap()

    def _tap(self) -> None:
        chip, log = self.chip, self.log

        # The observer of the requests that reach the chip's (real) DMA: it sees the order of them and the DMA still gets each one.
        def dreq_tap(number: int, asserted: bool) -> None:
            log.append(("dreq", int(number), int(asserted)))

        chip.dreq_tap = dreq_tap
        original = chip.set_interrupt

        def set_interrupt(irq: int, value: bool) -> None:
            log.append(("irq", int(irq), int(bool(value))))
            original(irq, value)

        chip.set_interrupt = set_interrupt  # type: ignore[method-assign]
        for method in ("warning", "error", "info", "debug"):
            setattr(chip.logger, method, lambda name, message, _m=method: log.append((_m, str(name), str(message))))

    def _replace_the_ssi(self, factory: Callable[[Any, str], Any]) -> None:
        old = self.chip.ssi
        if hasattr(old, "_detach"):
            old._detach()
        else:
            self.chip.qspi[1]._listeners.discard(
                old._on_cs_pin_changed
            )  # a bound method compares equal to the one the SSI registered
        new = factory(self.chip, "SSI")
        self.chip.ssi = new
        self.chip.peripherals[SSI_BASE >> 12] = new

    # --- the operations --------------------------------------------------------------------------------------------------

    def _cs(self, ctrl: int) -> None:
        pin = self.chip.qspi[1]
        pin.ctrl = ctrl
        pin.check_for_updates()

    def apply(self, op: tuple) -> Any:
        kind = op[0]
        chip = self.chip
        if kind == "write":
            chip.write_uint32(SSI_BASE + op[1], op[2] & 0xFFFFFFFF)
        elif kind == "read":
            return int(chip.read_uint32(SSI_BASE + op[1]))
        elif kind == "dr0_write":
            chip.write_uint32(SSI_BASE + (op[2] if len(op) > 2 else S.SSI_DR0), op[1])
        elif kind == "dr0_read":
            return int(chip.read_uint32(SSI_BASE + (op[1] if len(op) > 1 else S.SSI_DR0)))
        elif kind == "cs":
            self._cs(op[1])
        elif kind == "command":  # one whole transaction, the way the bootrom does it
            _, data = op
            chip.write_uint32(SSI_BASE + S.SSI_SSIENR, 1)
            self._cs(CS_LOW)
            received = []
            for byte in data:
                chip.write_uint32(SSI_BASE + S.SSI_DR0, byte)
                received.append(int(chip.read_uint32(SSI_BASE + S.SSI_DR0)))
            self._cs(CS_HIGH)
            return tuple(received)
        elif kind == "reset":
            chip.ssi.reset()
        elif kind == "poke":
            _, address, data = op
            chip.flash[address : address + len(data)] = data
        else:
            raise ValueError(f"unknown op {op!r}")
        return None

    # --- the observation -------------------------------------------------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        chip, ssi = self.chip, self.chip.ssi
        out: dict[str, Any] = {
            "regs": tuple(
                int(chip.read_uint32(SSI_BASE + offset))
                for offset in REGISTERS
                if offset != S.SSI_DR0 and offset not in S.CLEAR_ON_READ
            )
        }
        out["state"] = tuple(sorted(ssi.snapshot().items()))
        out["flash"] = tuple(bytes(chip.flash[a:b]) for a, b in FLASH_WINDOWS)
        return out


# --- logic mutants of the reference: what the oracle must see ----------------------------------------------------------------


def mutant_rig(name: str) -> Rig:
    """A rig whose SSI is the pure-Python one with one piece of its logic changed, to prove the comparison sees a change of *logic*."""

    class Mutant(S.RPSSI):
        def _shift_byte(self, byte_out: int) -> int:
            pos = len(self._tx_buffer)
            if pos > 0:
                opcode = self._tx_buffer[0]
                if name == "status2_zero" and opcode == S.CMD_READ_STATUS_2:
                    self._tx_buffer.append(byte_out)
                    return 0
                if name == "jedec_wrong" and opcode == S.CMD_READ_JEDEC_ID and pos == 2:
                    self._tx_buffer.append(byte_out)
                    return 0x41
                if name == "read_data_offset" and opcode == S.CMD_READ_DATA and pos >= 4:
                    self._tx_buffer.append(byte_out)
                    address = self._address_from_buffer()
                    target = address + pos - 3
                    return self.rp2040.flash[target] if 0 <= target < len(self.rp2040.flash) else 0xFF
                if name == "unknown_reads_zero" and opcode not in OPCODES:
                    self._tx_buffer.append(byte_out)
                    return 0
            return super()._shift_byte(byte_out)

        def _apply_command(self) -> None:
            if not self._tx_buffer:
                return
            opcode = self._tx_buffer[0]
            flash = self.rp2040.flash
            if (
                name == "erase_ignores_wel"
                and opcode in (S.CMD_SECTOR_ERASE, S.CMD_BLOCK_ERASE)
                and len(self._tx_buffer) >= 4
            ):
                size = S.FLASH_SECTOR_SIZE if opcode == S.CMD_SECTOR_ERASE else S.FLASH_BLOCK_SIZE
                address = self._address_from_buffer() & ~(size - 1)
                flash[address : address + size] = b"\xff" * size
                self._write_enabled = False
                self._tx_buffer = bytearray()
                return
            if name == "erase_not_aligned" and opcode == S.CMD_SECTOR_ERASE and len(self._tx_buffer) >= 4:
                if self._write_enabled:
                    address = self._address_from_buffer()
                    flash[address : address + S.FLASH_SECTOR_SIZE] = b"\xff" * S.FLASH_SECTOR_SIZE
                self._write_enabled = False
                self._tx_buffer = bytearray()
                return
            if name == "program_overwrites" and opcode == S.CMD_PAGE_PROGRAM and len(self._tx_buffer) > 4:
                if self._write_enabled:
                    address = self._address_from_buffer()
                    for i, byte in enumerate(self._tx_buffer[4 : 4 + S.FLASH_PAGE_SIZE]):
                        if 0 <= address + i < len(flash):
                            flash[address + i] = byte
                self._write_enabled = False
                self._tx_buffer = bytearray()
                return
            if name == "program_keeps_wel" and opcode == S.CMD_PAGE_PROGRAM:
                keep = self._write_enabled
                super()._apply_command()
                self._write_enabled = keep
                return
            if name == "program_page_257" and opcode == S.CMD_PAGE_PROGRAM and len(self._tx_buffer) > 4:
                if self._write_enabled:
                    address = self._address_from_buffer()
                    for i, byte in enumerate(self._tx_buffer[4 : 4 + S.FLASH_PAGE_SIZE + 1]):
                        if 0 <= address + i < len(flash):
                            flash[address + i] &= byte
                self._write_enabled = False
                self._tx_buffer = bytearray()
                return
            super()._apply_command()

        def _on_cs_pin_changed(self, value: Any, _last_value: Any) -> None:
            if name == "rx_kept_on_assert":
                from rp2040py.gpio_pin import GPIOPinState

                now = value == GPIOPinState.LOW
                if now and not self._cs_asserted:
                    self._tx_buffer = bytearray()
                elif self._cs_asserted and not now:
                    self._apply_command()
                self._cs_asserted = now
                return
            super()._on_cs_pin_changed(value, _last_value)

        def read_uint32(self, offset: int) -> int:
            mutated = self._mutated_read(offset)
            if mutated is not None:
                return mutated
            if name == "dr_aliases_unimplemented" and S.is_data_register(offset) and offset != S.SSI_DR0:
                return super(S.RPSSI, self).read_uint32(offset)
            return super().read_uint32(offset)

        def write_uint32(self, offset: int, value: int) -> None:
            if name == "dr_aliases_unimplemented" and S.is_data_register(offset) and offset != S.SSI_DR0:
                super(S.RPSSI, self).write_uint32(offset, value)
                return
            if name == "deasserted_pushes_nothing" and offset == S.SSI_DR0 and self._ssienr and not self._cs_asserted:
                return
            if name == "ctrlr0_unmasked" and offset == S.SSI_CTRLR0:
                self._crtlr0 = value
                return
            if name == "ctrlr1_unmasked" and offset == S.SSI_CTRLR1:
                self._crtlr1 = value
                return
            if name == "baudr_unmasked" and offset == S.SSI_BAUDR:
                self._baudr = value
                return
            if name == "spi_ctrlr0_unmasked" and offset == S.SSI_SPI_CTRL_R0:
                self._spictlr0 = value
                return
            if name == "ssienr_unmasked" and offset == S.SSI_SSIENR:
                self._ssienr = value
                return
            if name == "txflr_writable" and offset == S.SSI_TXFLR:
                self._txflr = value
                return
            if name == "imr_unmasked" and offset == S.SSI_IMR:
                self._stored[offset] = value
                return
            if name == "stored_not_stored" and offset in S.STORED_REGISTERS:
                return
            if name == "ssienr_ignored" and offset == S.SSI_DR0 and not self._ssienr:
                self._rx_queue.append(self._shift_byte(value & 0xFF))
                return
            if name == "disable_keeps_the_levels" and offset == S.SSI_SSIENR:
                self._ssienr = value & 1
                if not self._ssienr:
                    self._rx_queue.clear()
                return
            if name == "stored_write_not_announced" and offset in S.STORED_REGISTERS:
                self._stored[offset] = value & S.STORED_REGISTERS[offset]
                return
            super().write_uint32(offset, value)

        @property
        def _risr(self) -> int:
            value = super()._risr
            if name == "rxf_threshold_off_by_one":
                value &= ~S.INT_RXF
                value |= S.INT_RXF if len(self._rx_queue) >= self._stored[S.SSI_RXFTLR] else 0
            if name == "txe_never":
                value &= ~S.INT_TXE
            if name == "txe_strict":
                value &= ~S.INT_TXE
                value |= S.INT_TXE if self._txflr < self._stored[S.SSI_TXFTLR] else 0
            if name == "rxu_not_raw":
                value &= ~S.INT_RXU
            return value

        @property
        def _isr(self) -> int:
            if name == "isr_unmasked":
                return self._risr
            return super()._isr

        def _receive(self, frame: int) -> None:
            if name == "rx_overflow_dropped_silently" and len(self._rx_queue) >= S.FIFO_DEPTH:
                return
            if name == "rx_overflow_replaces_oldest" and len(self._rx_queue) >= S.FIFO_DEPTH:
                self._sticky |= S.INT_RXO
                self._rx_queue.popleft()
                self._rx_queue.append(frame)
                return
            if name == "fifo_is_17_deep" and len(self._rx_queue) == S.FIFO_DEPTH:
                self._rx_queue.append(frame)
                return
            if name == "tx_only_stores":
                if len(self._rx_queue) >= S.FIFO_DEPTH:
                    self._sticky |= S.INT_RXO
                else:
                    self._rx_queue.append(frame)
                return
            if name == "rx_only_drops" and (self._crtlr0 >> 8) & 3 == 2:
                return
            if name == "tmod_bit_9_only" and (self._crtlr0 >> 8) & 2:
                return
            super()._receive(frame)

        def _update(self) -> None:
            if name == "irq_every_time":
                self._irq_level = self._isr == 0  # force an announcement every time
            if name == "dreq_channels_swapped":
                saved = (S.DREQ_XIP_SSITX, S.DREQ_XIP_SSIRX)
                S.DREQ_XIP_SSITX, S.DREQ_XIP_SSIRX = saved[1], saved[0]
                try:
                    super()._update()
                finally:
                    S.DREQ_XIP_SSITX, S.DREQ_XIP_SSIRX = saved
                return
            if name == "dreq_rx_at_dmardlr" and self.rp2040.dma is not None:
                from rp2040py.irq import IRQ

                level = self._isr != 0
                if level != self._irq_level:
                    self._irq_level = level
                    self.rp2040.set_interrupt(IRQ.XIP, level)
                request = (
                    bool(self._stored[S.SSI_DMACR] & S.SSI_DMACR_RDMAE)
                    and len(self._rx_queue) >= self._stored[S.SSI_DMARDLR]
                )
                if request != self._dreq_rx:
                    self._dreq_rx = request
                    (self.rp2040.dma.set_dreq if request else self.rp2040.dma.clear_dreq)(S.DREQ_XIP_SSIRX)
                return
            if name == "dreq_tx_ignores_dmacr":
                saved_dmacr = self._stored[S.SSI_DMACR]
                self._stored[S.SSI_DMACR] = saved_dmacr | S.SSI_DMACR_TDMAE
                try:
                    super()._update()
                finally:
                    self._stored[S.SSI_DMACR] = saved_dmacr
                return
            super()._update()

        def _mutated_read(self, offset: int) -> "int | None":
            if name == "empty_read_not_held" and offset == S.SSI_DR0 and not self._rx_queue:
                return 0
            if name == "txoicr_clears_rxo" and offset == S.SSI_TXOICR:
                self._sticky &= ~S.INT_RXO
                self._update()
                return 0
            if name == "icr_clears_nothing" and offset == S.SSI_ICR:
                return 0
            if name == "icr_clears_only_rxo" and offset == S.SSI_ICR:
                self._sticky &= ~S.INT_RXO
                self._update()
                return 0
            if name == "clear_register_returns_status" and offset == S.SSI_RXOICR:
                value = self._sticky & S.INT_RXO
                self._sticky &= ~S.INT_RXO
                self._update()
                return value
            if name == "rff_missing" and offset == S.SSI_SR:
                return super().read_uint32(offset) & ~S.SSI_SR_RFF_BITS
            if name == "rff_at_15" and offset == S.SSI_SR:
                value = super().read_uint32(offset) & ~S.SSI_SR_RFF_BITS
                return value | (S.SSI_SR_RFF_BITS if len(self._rx_queue) >= S.FIFO_DEPTH - 1 else 0)
            if name == "pop_not_announced" and offset == S.SSI_DR0 and self._rx_queue:
                self._update = lambda: None  # type: ignore[method-assign]
                try:
                    return super().read_uint32(offset)
                finally:
                    del self._update
            return None

        def reset(self) -> None:
            super().reset()
            if name == "reset_keeps_sticky":
                self._sticky = S.INT_RXO
            if name == "reset_cs_false":
                self._cs_asserted = False
            if name == "reset_keeps_wel":
                self._write_enabled = True
            if name == "spi_ctrlr0_reset_zero":
                self._spictlr0 = 0
            if name == "reset_keeps_stored":
                self._stored[S.SSI_IMR] = 1

    rig = Rig("pure")
    rig._replace_the_ssi(Mutant)
    return rig


MUTANTS = (
    "status2_zero", "jedec_wrong", "read_data_offset", "unknown_reads_zero", "erase_ignores_wel", "erase_not_aligned", "program_overwrites", "program_keeps_wel",
    "program_page_257", "rx_kept_on_assert", "deasserted_pushes_nothing", "ssienr_ignored", "reset_cs_false", "reset_keeps_wel",
    "ctrlr0_unmasked", "ctrlr1_unmasked", "baudr_unmasked", "spi_ctrlr0_unmasked", "ssienr_unmasked", "txflr_writable", "imr_unmasked", "stored_not_stored", "spi_ctrlr0_reset_zero",
    "reset_keeps_stored", "dr_aliases_unimplemented",
    "rxf_threshold_off_by_one", "txe_never", "txe_strict", "rxu_not_raw", "isr_unmasked", "rx_overflow_dropped_silently", "rx_overflow_replaces_oldest", "fifo_is_17_deep",
    "tx_only_stores", "rx_only_drops", "tmod_bit_9_only", "irq_every_time", "dreq_channels_swapped", "dreq_rx_at_dmardlr", "dreq_tx_ignores_dmacr", "empty_read_not_held",
    "txoicr_clears_rxo", "icr_clears_nothing", "icr_clears_only_rxo", "clear_register_returns_status", "rff_missing", "rff_at_15", "pop_not_announced", "reset_keeps_sticky",
    "disable_keeps_the_levels", "stored_write_not_announced",
)  # fmt: skip


# --- the stimulus ----------------------------------------------------------------------------------------------------------


def _address(r: random.Random) -> int:
    roll = r.random()
    if roll < 0.7:
        return r.randrange(0, 0x40000)
    if roll < 0.85:
        return 0xFFE000 + r.randrange(0, 0x2000)
    return r.choice(
        (0xFFFFFF, 0xFFFFF0, 0x3FFFF, 0x3FF00, 0x1000, 0xFFF, 0x10000, 0xFFFF)
    )  # edges: past the end, around sector and block boundaries


def _command(r: random.Random) -> tuple:
    opcode = r.choice(OPCODES) if r.random() < 0.9 else r.getrandbits(8)
    address = _address(r)
    head = [opcode, (address >> 16) & 0xFF, (address >> 8) & 0xFF, address & 0xFF]
    if opcode == S.CMD_PAGE_PROGRAM:
        length = r.choice((1, 4, 16, 64, 255, 256, 257, 300, 0))
        tail = [r.getrandbits(8) for _ in range(length)]
    elif opcode == S.CMD_READ_DATA:
        tail = [0] * r.choice((1, 4, 16, 64, 255, 260, 300))
    elif opcode == S.CMD_READ_JEDEC_ID:
        tail = [0] * r.choice((0, 1, 2, 3, 4, 6))
        head = head[:1]
    elif opcode in (S.CMD_WRITE_ENABLE, S.CMD_WRITE_DISABLE):
        head = head[: r.choice((1, 1, 1, 2))]
        tail = []
    elif opcode in (S.CMD_READ_STATUS_1, S.CMD_READ_STATUS_2, S.CMD_WRITE_STATUS):
        head = head[:1]
        tail = [r.getrandbits(8) for _ in range(r.choice((1, 1, 2, 3)))]
    elif opcode in (S.CMD_SECTOR_ERASE, S.CMD_BLOCK_ERASE):
        head = head[: r.choice((4, 4, 4, 3, 1))]  # a short one is not applied
        tail = []
    else:
        tail = [r.getrandbits(8) for _ in range(r.choice((0, 1, 3, 8)))]
    return ("command", tuple(head + tail))


def _poke(r: random.Random) -> tuple:
    address = _address(r) & ~0xFF
    size = r.choice((16, 256, 4096))
    kind = r.random()
    if kind < 0.4:
        data = bytes(r.getrandbits(8) for _ in range(min(size, 256))) * (size // min(size, 256))
    elif kind < 0.7:
        data = b"\xff" * size
    else:
        data = bytes((address + i) & 0xFF for i in range(size))
    limit = FLASH_WINDOWS[1][1] if address >= FLASH_WINDOWS[1][0] else FLASH_WINDOWS[0][1]
    return ("poke", address, data[: max(0, limit - address)])


def generate(seed: int, steps: int) -> list[tuple]:
    r = random.Random(seed)
    ops: list[tuple] = [("write", S.SSI_SSIENR, 1)]
    for _ in range(6):
        ops.append(_poke(r))
    while len(ops) < steps:
        roll = r.random()
        if roll < 0.40:
            if r.random() < 0.35:  # write enable first, so that the erase or program that follows has its latch
                ops.append(("command", (S.CMD_WRITE_ENABLE,)))
                ops.append(_command(r))
            else:
                ops.append(_command(r))
        elif (
            roll < 0.42
        ):  # a command cut short: chip-select asserted, a few bytes, and then a reset, a release or nothing
            ops.extend(
                [
                    ("write", S.SSI_SSIENR, 1),
                    ("cs", CS_LOW),
                    *[("dr0_write", r.getrandbits(8)) for _ in range(r.randrange(1, 7))],
                ]
            )
            ops.append(r.choice((("reset",), ("cs", CS_HIGH), ("cs", CS_RELEASED), ("dr0_read",))))
        elif roll < 0.48:
            ops.append(_poke(r))
        elif roll < 0.58:
            ops.append(("write", S.SSI_SSIENR, r.choice((0, 1, 1, 1, 2))))
        elif roll < 0.68:
            register = r.choice(WRITABLE)
            if register in (S.SSI_TXFTLR, S.SSI_RXFTLR, S.SSI_DMATDLR, S.SSI_DMARDLR) and r.random() < 0.8:
                value = r.randrange(0, 18)  # a threshold that matters: the FIFOs are 16 deep
            elif register in (S.SSI_IMR, S.SSI_DMACR) and r.random() < 0.8:
                value = r.getrandbits(6) if register == S.SSI_IMR else r.getrandbits(2)
            elif register == S.SSI_CTRLR0 and r.random() < 0.7:
                value = (r.randrange(4) << 8) | r.getrandbits(4)  # the transfer mode, and little else
            else:
                value = r.getrandbits(32)
            ops.append(("write", register, value))
        elif roll < 0.70:
            if (
                r.random() < 0.6
            ):  # ... with a READ_DATA in flight, so the frames that are kept or lost are different bytes
                address = _address(r)
                ops.extend([("write", S.SSI_SSIENR, 1), ("cs", CS_LOW)])
                ops.extend(
                    ("dr0_write", byte)
                    for byte in (S.CMD_READ_DATA, (address >> 16) & 0xFF, (address >> 8) & 0xFF, address & 0xFF)
                )
                ops.append(("read", S.SSI_RXOICR))
            for _ in range(
                r.randrange(8, 26)
            ):  # a burst that overflows the 16-entry receive FIFO, then some reads and a clear
                ops.append(("dr0_write", r.getrandbits(8)))
            ops.extend(("dr0_read",) for _ in range(r.randrange(0, 20)))
            ops.append(("read", r.choice(tuple(S.CLEAR_ON_READ))))
        elif roll < 0.74:
            ops.append(("read", r.choice(REGISTERS + UNIMPLEMENTED)))
        elif roll < 0.78:
            ops.append(("write", r.choice(UNIMPLEMENTED + (S.SSI_SR, S.SSI_IDR)), r.getrandbits(32)))
        elif roll < 0.86:
            for _ in range(
                r.randrange(1, 6)
            ):  # raw DR0 traffic: with whatever chip-select state and SSIENR the stream left
                ops.append(("dr0_write", r.getrandbits(8), r.choice((S.SSI_DR0, S.SSI_DR0, *DR_ALIASES))))
                if r.random() < 0.5:
                    ops.append(("dr0_read", r.choice((S.SSI_DR0, S.SSI_DR0, *DR_ALIASES))))
        elif roll < 0.93:
            ops.append(("cs", r.choice((CS_LOW, CS_HIGH, CS_HIGH, CS_RELEASED))))
        elif roll < 0.96:
            ops.append(("dr0_read", r.choice((S.SSI_DR0, *DR_ALIASES))))
        elif roll < 0.985:
            ops.append(("reset",))
        else:
            ops.append(
                ("write", 0x1000 + r.choice(WRITABLE), r.getrandbits(32))
            )  # the atomic-alias window of a register
    return ops[:steps]


# --- the comparison --------------------------------------------------------------------------------------------------------


class Divergence:
    def __init__(self, step: int, op: tuple, what: str) -> None:
        self.step, self.op, self.what = step, op, what

    def __str__(self) -> str:
        shown = tuple(x if not isinstance(x, (bytes, tuple)) or len(x) < 12 else f"<{len(x)} items>" for x in self.op)
        return f"step {self.step} {shown}: {self.what}"

    def __repr__(self) -> str:
        return f"Divergence({self})"


def _step(rig: Rig, op: tuple) -> tuple:
    """(result, exception name) of one operation: an exception on one side and not on the other is a difference like any other."""
    try:
        return rig.apply(op), None
    except Exception as error:  # noqa: BLE001 - what is compared is *that* it raised and what, not how it is handled
        return None, f"{type(error).__name__}: {error}"


def diff_snapshots(a: dict[str, Any], b: dict[str, Any]) -> str:
    parts = []
    for key, value_a in a.items():
        value_b = b[key]
        if value_a == value_b:
            continue
        if isinstance(value_a, tuple) and len(value_a) == len(value_b):
            where = [i for i, (x, y) in enumerate(zip(value_a, value_b, strict=True)) if x != y]
            parts.append(
                f"{key}[{where[:6]}] {[str(value_a[i])[:60] for i in where[:6]]} vs {[str(value_b[i])[:60] for i in where[:6]]}"
            )
        else:
            parts.append(f"{key}: {str(value_a)[:80]} vs {str(value_b)[:80]}")
    return "; ".join(parts)


def run_pair(
    ops: list[tuple],
    *,
    reference: Callable[[], Rig] = lambda: Rig("pure"),
    candidate: Callable[[], Rig] = lambda: Rig("default"),
    perturb: "Callable[[Rig, int], None] | None" = None,
) -> "Divergence | None":
    """Runs `ops` on both rigs and returns the first difference, or None. `perturb(candidate, step)` lets a test damage the candidate to prove the oracle sees it."""
    a, b = reference(), candidate()
    log_a = log_b = 0
    for step, op in enumerate(ops):
        result_a, error_a = _step(a, op)
        result_b, error_b = _step(b, op)
        if perturb is not None:
            perturb(b, step)
        if (result_a, error_a) != (result_b, error_b):
            return Divergence(step, op, f"result {str(result_a)[:80]!r}/{error_a} vs {str(result_b)[:80]!r}/{error_b}")
        new_a, new_b = a.log[log_a:], b.log[log_b:]
        log_a, log_b = len(a.log), len(b.log)
        if new_a != new_b:
            return Divergence(step, op, f"warnings: {new_a[:3]} vs {new_b[:3]}")
        snapshot_a, snapshot_b = a.snapshot(), b.snapshot()
        if snapshot_a != snapshot_b:
            return Divergence(step, op, diff_snapshots(snapshot_a, snapshot_b))
    return None


def coverage(ops: list[tuple]) -> dict[str, int]:
    """What the run exercised, measured on the reference: the counts a test asserts so that a green run means something."""
    counts: dict[str, int] = {}

    def count(name: str, amount: int = 1) -> None:
        counts[name] = counts.get(name, 0) + amount

    cls = S.RPSSI
    original_shift, original_apply, original_cs, original_write = (
        cls._shift_byte,
        cls._apply_command,
        cls._on_cs_pin_changed,
        cls.write_uint32,
    )

    def shift(self: Any, byte_out: int) -> int:
        if not self._tx_buffer:
            known = {S.CMD_WRITE_ENABLE: "wren", S.CMD_WRITE_DISABLE: "wrdi"}.get(byte_out, "first")
            count(f"opcode.{known if byte_out in OPCODES else 'unknown'}")
        pos = len(self._tx_buffer)
        if pos >= 260:
            count("shift.past_260")
        if pos >= 4 and self._tx_buffer[0] == S.CMD_READ_DATA:
            address = self._address_from_buffer() + pos - 4
            count("read.past_the_end" if address >= len(self.rp2040.flash) else "read.in_range")
        return original_shift(self, byte_out)

    def apply(self: Any) -> None:
        if self._tx_buffer:
            opcode = self._tx_buffer[0]
            if opcode in (S.CMD_SECTOR_ERASE, S.CMD_BLOCK_ERASE):
                count(
                    f"erase{'.wel' if self._write_enabled else '.no_wel'}{'.short' if len(self._tx_buffer) < 4 else ''}"
                )
            elif opcode == S.CMD_PAGE_PROGRAM:
                count(
                    f"program{'.wel' if self._write_enabled else '.no_wel'}{'.long' if len(self._tx_buffer) > 4 + S.FLASH_PAGE_SIZE else ''}"
                )
            else:
                count("apply.other")
        else:
            count("apply.empty")
        original_apply(self)

    def on_cs(self: Any, value: Any, last: Any) -> None:
        from rp2040py.gpio_pin import GPIOPinState

        now = value == GPIOPinState.LOW
        count(
            "cs.assert"
            if now and not self._cs_asserted
            else ("cs.deassert" if self._cs_asserted and not now else "cs.same")
        )
        original_cs(self, value, last)

    def write(self: Any, offset: int, value: int) -> None:
        if S.is_data_register(offset):
            if offset != S.SSI_DR0:
                count("dr.alias")
            count(
                "dr0.cs_asserted"
                if self._ssienr and self._cs_asserted
                else ("dr0.cs_deasserted" if self._ssienr else "dr0.ssienr_clear")
            )
        original_write(self, offset, value)

    cls._shift_byte, cls._apply_command, cls._on_cs_pin_changed, cls.write_uint32 = shift, apply, on_cs, write  # type: ignore[method-assign]
    try:
        rig = Rig("pure")
        for op in ops:
            if op[0] == "reset":
                count("reset.mid_command" if rig.chip.ssi._tx_buffer else "reset")
            _, error = _step(rig, op)
            if error:
                count("exception")
            for event in rig.log:
                count(event[0] if event[0] in ("irq", "dreq") else "warning")
            rig.log.clear()
            ssi = rig.chip.ssi
            for held, label in ((S.INT_RXO, "rxo"), (S.INT_RXU, "rxu")):
                if ssi._sticky & held:
                    count(f"held.{label}")
            if len(ssi._rx_queue) >= S.FIFO_DEPTH:
                count("rx.full")
            if (ssi._crtlr0 >> S.SSI_CTRLR0_TMOD_SHIFT) & 3 == S.TMOD_TX_ONLY:
                count("tmod.tx_only")
            if ssi._isr:
                count("isr.pending")
            if ssi._dreq_rx:
                count("dreq.rx_high")
            if ssi._dreq_tx:
                count("dreq.tx_high")
    finally:
        cls._shift_byte, cls._apply_command, cls._on_cs_pin_changed, cls.write_uint32 = (
            original_shift,
            original_apply,
            original_cs,
            original_write,
        )  # type: ignore[method-assign]
    return counts
