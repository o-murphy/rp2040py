import os
import struct
from collections.abc import Callable
from typing import TYPE_CHECKING

from rp2040py.clock.clock import IClock
from rp2040py.clock.simulation_clock import SimulationClock
from rp2040py.cortex_m0_core import CortexM0Core
from rp2040py.gpio_pin import GPIOPin
from rp2040py.irq import IRQ
from rp2040py.memory_map import (
    APB_START_ADDRESS,
    DPRAM_START_ADDRESS,
    FLASH_END_ADDRESS,
    FLASH_START_ADDRESS,
    RAM_START_ADDRESS,
    SIO_START_ADDRESS,
)
from rp2040py.peripherals.adc import RPADC
from rp2040py.peripherals.busctrl import RPBUSCTRL
from rp2040py.peripherals.clocks import RPClocks
from rp2040py.peripherals.dma import RPDMA, DREQChannel
from rp2040py.peripherals.i2c import RPI2C
from rp2040py.peripherals.io import RPIO
from rp2040py.peripherals.pads import RPPADS
from rp2040py.peripherals.peripheral import Peripheral, UnimplementedPeripheral
from rp2040py.peripherals.pio import RPPIO
from rp2040py.peripherals.ppb import RPPPB
from rp2040py.peripherals.psm import RPPSM
from rp2040py.peripherals.pwm import RPPWM
from rp2040py.peripherals.reset import RPReset
from rp2040py.peripherals.rtc import RP2040RTC
from rp2040py.peripherals.spi import RPSPI, ISPIDMAChannels
from rp2040py.peripherals.ssi import RPSSI
from rp2040py.peripherals.syscfg import RP2040SysCfg
from rp2040py.peripherals.sysinfo import RP2040SysInfo
from rp2040py.peripherals.tbman import RPTBMAN
from rp2040py.peripherals.timer import RPTimer
from rp2040py.peripherals.uart import RPUART, IUARTDMAChannels
from rp2040py.peripherals.usb import RPUSBController
from rp2040py.peripherals.watchdog import RPWatchdog
from rp2040py.peripherals.xosc import RPXOSC
from rp2040py.sio import RPSIO
from rp2040py.utils.bit import (
    read_uint16_le,
    read_uint32_le,
    u32,
    write_uint16_le,
    write_uint32_le,
)
from rp2040py.utils.logging import ConsoleLogger, Logger, LogLevel

if TYPE_CHECKING:
    from rp2040py.jit import JITEngine

__all__ = (
    "APB_START_ADDRESS",
    "DPRAM_START_ADDRESS",
    "FLASH_END_ADDRESS",
    "FLASH_START_ADDRESS",
    "RAM_START_ADDRESS",
    "RP2040",
    "SIO_START_ADDRESS",
)

LOG_NAME = "RP2040"

KB = 1024
MB = 1024 * KB
MHZ = 1_000_000


def _bootrom_bytes(words: "list[int]", byte_offset: int, length: int) -> bytes:
    word_start = byte_offset // 4
    word_count = (length + (byte_offset - word_start * 4) + 3) // 4
    chunk = words[word_start : word_start + word_count]
    data = b"".join(struct.pack("<I", w & 0xFFFFFFFF) for w in chunk)
    inner_offset = byte_offset - word_start * 4
    return data[inner_offset : inner_offset + length]


# HLE (high-level emulation) hook for bootrom's __memcpy/__memcpy_44 (see CortexM0Core._hle_memcpy)
# - these two entry points, confirmed against the real bootrom source (raspberrypi/pico-bootrom's
# bootrom_misc.S, see docs/BACKLOG.md's 1.21-vs-1.28 investigation), are what real firmware's own
# memcpy() calls route through (TinyUSB's tu_fifo_read/write, littlefs's lfs2_bd_read() - both
# confirmed as the dominant real-world callers in that same investigation).
#
# Detected by scanning the *whole* loaded bootrom for these two signatures, rather than checking
# fixed offsets: confirmed (by downloading b0.elf/b2.elf via --bootrom and searching for BOOTROM_B1's
# exact signature bytes in each) that __memcpy/__memcpy_44's own machine code is byte-for-byte
# identical across B0/B1/B2 - only its *position* moves between revisions (B0: 0x2888/0x28a0, B1:
# 0x2628/0x2640, B2: 0x2604/0x261c), presumably because other ROM code shifted around it, not
# because the routine itself changed. A whole-bootrom scan (cheap - one bytes.find() over 16KB,
# once per load_bootrom() call, nowhere near the hot per-instruction path) finds the right offset
# for any of the three automatically, and for any future revision that also happens to carry the
# same routine unchanged - versus hardcoding a per-revision offset table that needs a manual update
# every time a new bootrom revision ships. A signature that isn't found at all (a hypothetical
# revision where the routine's bytes genuinely differ) leaves the hook safely inert for that image
# rather than misfiring against code it wasn't verified against.
_HLE_MEMCPY_SIGNATURE_LEN = 20
_HLE_MEMCPY_SIGNATURE_OFFSETS_IN_B1 = (0x2628, 0x2640)  # __memcpy_44, __memcpy - source of the
# signature bytes only; actual detection below scans the whole loaded bootrom, not these offsets.
# Computed lazily (on first load_bootrom() call, not at import time): rp2040py.device.bootrom is
# only a data module, but importing it still runs rp2040py.device's own __init__.py first (Python
# always initializes a parent package before a submodule), which imports back to this module -
# fine once everything else has already finished importing, a real cycle at module-import time.
_hle_memcpy_signatures: "tuple[bytes, ...] | None" = None


def _get_hle_memcpy_signatures() -> "tuple[bytes, ...]":
    global _hle_memcpy_signatures
    if _hle_memcpy_signatures is None:
        from rp2040py.device.bootrom import BOOTROM_B1

        _hle_memcpy_signatures = tuple(
            _bootrom_bytes(BOOTROM_B1, offset, _HLE_MEMCPY_SIGNATURE_LEN)
            for offset in _HLE_MEMCPY_SIGNATURE_OFFSETS_IN_B1
        )
    return _hle_memcpy_signatures


def _find_hle_memcpy_entries(bootrom_words: "list[int]") -> "frozenset[int]":
    data = _bootrom_bytes(bootrom_words, 0, len(bootrom_words) * 4)
    entries = set()
    for signature in _get_hle_memcpy_signatures():
        offset = data.find(signature)
        if offset >= 0:
            entries.add(offset)
    return frozenset(entries)


class RP2040:
    def __init__(self, clock: IClock | None = None):
        # NOTE: must be set before constructing any peripheral below - several of them
        # (RPPPB, RPWatchdog, RPADC, RPPWM, RPPIO, RPUSBController, RPDMA) call
        # self.clock.create_alarm(...) from their own __init__.
        self.clock = clock if clock is not None else SimulationClock()

        # A plain list, not Uint32Array (same rationale as CortexM0Core.registers - see
        # docs/PORTING.md's performance section): bootrom is on the hot read_uint32 path (real
        # ROM routines like the bootrom's own memcpy helper get called frequently during flash/USB
        # operations), and a raw list index is a C-level bytecode op versus Uint32Array's
        # Python-level __getitem__/__setitem__. The two write sites (load_bootrom() below,
        # write_uint32()'s bootrom branch) mask explicitly with `& 0xFFFFFFFF` instead of relying
        # on the wrapper for it.
        self.bootrom: list[int] = [0] * (4 * KB)
        # Cached rather than recomputed via len(self.bootrom) on every bus access: bootrom size
        # never changes after construction, and this matters on the hot read_uint32/write_uint32
        # path regardless of container type.
        self.bootrom_byte_size = len(self.bootrom) * 4
        # Empty until load_bootrom() confirms (by signature) this bootrom actually has __memcpy/
        # __memcpy_44 at the expected offsets - see _HLE_MEMCPY_SIGNATURES above.
        self.hle_memcpy_entries: frozenset[int] = frozenset()
        # Basic-block-fusion mini-JIT (Phase 0: scaffolding only, see docs/JIT_BACKLOG.md) - None
        # by default, so CortexM0Core.execute_instruction()'s hook is a single cheap identity
        # check with the src/rp2040py/jit package never even imported, unless explicitly enabled.
        self.jit: "JITEngine | None" = None  # noqa: UP037 - real name only bound in the branch below
        if os.environ.get("RP2040PY_ENABLE_JIT"):
            from rp2040py.jit import JITEngine

            self.jit = JITEngine()
        self.sram = bytearray(264 * KB)
        # Same reasoning as bootrom_byte_size above: sram/usb_dpram sizes never change after
        # construction, and read_uint16()/read_uint8()/write_uint8()/write_uint16() are all on the
        # hot bus-access path (read_uint16() alone runs on essentially every instruction fetch).
        self.ram_byte_size = len(self.sram)
        self.flash = bytearray(16 * MB)
        # NOT the same as FLASH_END_ADDRESS - FLASH_START_ADDRESS (that spans all four XIP mirror
        # regions read_uint32() folds back onto this same backing array; this is just the base
        # region's own size, matching what read_uint16()/read_uint8()/write_uint32() already check).
        self.flash_byte_size = len(self.flash)
        self.usb_dpram = bytearray(4 * KB)
        self.dpram_byte_size = len(self.usb_dpram)

        self.core = CortexM0Core(self)

        # Clocks
        self.clk_sys = 125 * MHZ
        self.clk_peri = 125 * MHZ

        self.ppb = RPPPB(self, "PPB")
        self.sio = RPSIO(self)

        self.uart = [
            RPUART(
                self, "UART0", IRQ.UART0, IUARTDMAChannels(rx=DREQChannel.DREQ_UART0_RX, tx=DREQChannel.DREQ_UART0_TX)
            ),
            RPUART(
                self, "UART1", IRQ.UART1, IUARTDMAChannels(rx=DREQChannel.DREQ_UART1_RX, tx=DREQChannel.DREQ_UART1_TX)
            ),
        ]
        self.i2c = [RPI2C(self, "I2C0", IRQ.I2C0), RPI2C(self, "I2C1", IRQ.I2C1)]
        self.pwm = RPPWM(self, "PWM_BASE")
        self.adc = RPADC(self, "ADC")

        self.gpio = [GPIOPin(self, i) for i in range(30)]

        self.qspi = [
            GPIOPin(self, 0, "SCLK", always_output_enabled=True),
            GPIOPin(self, 1, "SS", always_output_enabled=True),
            GPIOPin(self, 2, "SD0", always_output_enabled=True),
            GPIOPin(self, 3, "SD1", always_output_enabled=True),
            GPIOPin(self, 4, "SD2", always_output_enabled=True),
            GPIOPin(self, 5, "SD3", always_output_enabled=True),
        ]

        self.dma = RPDMA(self, "DMA")
        self.pio = [
            RPPIO(self, "PIO0", IRQ.PIO0_IRQ0, 0),
            RPPIO(self, "PIO1", IRQ.PIO1_IRQ0, 1),
        ]
        self.usb_ctrl = RPUSBController(self, "USB")
        self.spi = [
            RPSPI(self, "SPI0", IRQ.SPI0, ISPIDMAChannels(rx=DREQChannel.DREQ_SPI0_RX, tx=DREQChannel.DREQ_SPI0_TX)),
            RPSPI(self, "SPI1", IRQ.SPI1, ISPIDMAChannels(rx=DREQChannel.DREQ_SPI1_RX, tx=DREQChannel.DREQ_SPI1_TX)),
        ]

        self.logger: Logger = ConsoleLogger(LogLevel.DEBUG, True)

        self.peripherals: dict[int, Peripheral] = {
            0x18000: RPSSI(self, "SSI"),
            0x40000: RP2040SysInfo(self, "SYSINFO_BASE"),
            0x40004: RP2040SysCfg(self, "SYSCFG"),
            0x40008: RPClocks(self, "CLOCKS_BASE"),
            0x4000C: RPReset(self, "RESETS_BASE"),
            0x40010: RPPSM(self, "PSM_BASE"),
            0x40014: RPIO(self, "IO_BANK0_BASE"),
            0x40018: RPIO(self, "IO_QSPI_BASE", pins=self.qspi),
            0x4001C: RPPADS(self, "PADS_BANK0_BASE", "bank0"),
            0x40020: RPPADS(self, "PADS_QSPI_BASE", "qspi"),
            0x40024: RPXOSC(self, "XOSC_BASE"),
            0x40028: UnimplementedPeripheral(self, "PLL_SYS_BASE"),
            0x4002C: UnimplementedPeripheral(self, "PLL_USB_BASE"),
            0x40030: RPBUSCTRL(self, "BUSCTRL_BASE"),
            0x40034: self.uart[0],
            0x40038: self.uart[1],
            0x4003C: self.spi[0],
            0x40040: self.spi[1],
            0x40044: self.i2c[0],
            0x40048: self.i2c[1],
            0x4004C: self.adc,
            0x40050: self.pwm,
            0x40054: RPTimer(self, "TIMER_BASE"),
            0x40058: RPWatchdog(self, "WATCHDOG_BASE"),
            0x4005C: RP2040RTC(self, "RTC_BASE"),
            0x40060: UnimplementedPeripheral(self, "ROSC_BASE"),
            0x40064: UnimplementedPeripheral(self, "VREG_AND_CHIP_RESET_BASE"),
            0x4006C: RPTBMAN(self, "TBMAN_BASE"),
            0x50000: self.dma,
            0x50110: self.usb_ctrl,
            0x50200: self.pio[0],
            0x50300: self.pio[1],
        }

        # Debugging
        self.on_break: Callable[[int], None] = self._default_on_break

        self.reset()

    def _default_on_break(self, code: int) -> None:
        # TODO: raise HardFault exception
        pass

    def load_bootrom(self, bootrom_data: list[int]) -> None:
        self.bootrom[: len(bootrom_data)] = (value & 0xFFFFFFFF for value in bootrom_data)
        # Opt-in, not opt-out: RP2040PY_ENABLE_HLE_MEMCPY=1 is required to activate the hook at
        # all. Measured effect is still unconfirmed (no measurable win on a fast 1.21 boot, full
        # 1.28 boot-and-run A/B not measured yet as of this commit - see docs/BACKLOG.md), so this
        # stays off by default until both a real speedup and no regressions are confirmed.
        if os.environ.get("RP2040PY_ENABLE_HLE_MEMCPY"):
            self.hle_memcpy_entries = _find_hle_memcpy_entries(self.bootrom)
        else:
            self.hle_memcpy_entries = frozenset()
        self.reset()

    def reset(self) -> None:
        self.core.reset()
        self.pwm.reset()
        self.flash[:] = b"\xff" * len(self.flash)

    def read_uint32(self, address: int) -> int:
        address = u32(address)
        if address & 0x3:
            self.logger.error(LOG_NAME, f"read from address {address:x}, which is not 32 bit aligned")

        bootrom = self.bootrom
        if address < self.bootrom_byte_size:
            return bootrom[address // 4]
        if FLASH_START_ADDRESS <= address < FLASH_END_ADDRESS:
            # Flash is mirrored four times:
            # - 0x10000000 XIP
            # - 0x11000000 XIP_NOALLOC
            # - 0x12000000 XIP_NOCACHE
            # - 0x13000000 XIP_NOCACHE_NOALLOC
            offset = address & 0x00FFFFFF
            return read_uint32_le(self.flash, offset)
        if RAM_START_ADDRESS <= address < RAM_START_ADDRESS + self.ram_byte_size:
            return read_uint32_le(self.sram, address - RAM_START_ADDRESS)
        if DPRAM_START_ADDRESS <= address < DPRAM_START_ADDRESS + self.dpram_byte_size:
            return read_uint32_le(self.usb_dpram, address - DPRAM_START_ADDRESS)
        if address >> 12 == 0xE000E:
            return self.ppb.read_uint32(address & 0xFFF)
        if SIO_START_ADDRESS <= address < SIO_START_ADDRESS + 0x10000000:
            # int(): SIO's DIV_QUOTIENT can be a fractional JS number (see sio.py) - any real JS
            # consumer (typed-array store, bitwise op) truncates it immediately via ToUint32, so
            # truncate here rather than letting a float leak into the rest of the bus/CPU/DMA
            # read path.
            return int(self.sio.read_uint32(address - SIO_START_ADDRESS)) & 0xFFFFFFFF

        peripheral = self.find_peripheral(address)
        if peripheral:
            return peripheral.read_uint32(address & 0x3FFF)

        self.logger.warning(LOG_NAME, f"Read from invalid memory address: {address:x}")
        return 0xFFFFFFFF

    def find_peripheral(self, address: int) -> Peripheral | None:
        return self.peripherals.get((u32(address) >> 14) << 2)

    def read_uint16(self, address: int) -> int:
        """We assume the address is 16-bit aligned."""
        if FLASH_START_ADDRESS <= address < FLASH_START_ADDRESS + self.flash_byte_size:
            return read_uint16_le(self.flash, address - FLASH_START_ADDRESS)
        if RAM_START_ADDRESS <= address < RAM_START_ADDRESS + self.ram_byte_size:
            return read_uint16_le(self.sram, address - RAM_START_ADDRESS)

        value = self.read_uint32(address & 0xFFFFFFFC)
        return (value & 0xFFFF0000) >> 16 if address & 0x2 else value & 0xFFFF

    def read_uint8(self, address: int) -> int:
        if FLASH_START_ADDRESS <= address < FLASH_START_ADDRESS + self.flash_byte_size:
            return self.flash[address - FLASH_START_ADDRESS]
        if RAM_START_ADDRESS <= address < RAM_START_ADDRESS + self.ram_byte_size:
            return self.sram[address - RAM_START_ADDRESS]

        value = self.read_uint16(address & 0xFFFFFFFE)
        return u32((value & 0xFF00) >> 8 if address & 0x1 else value & 0xFF)

    def write_uint32(self, address: int, value: int) -> None:
        address = u32(address)
        bootrom = self.bootrom
        # Same range order as read_uint32() - RAM/flash/bootrom checked first via cheap integer
        # comparisons, find_peripheral() (a dict lookup) only as the fallback for what's left.
        # This branch used to check find_peripheral() unconditionally *first*, so every RAM write
        # (the overwhelmingly common case - stack spills, GC, locals) paid for a dict .get() that
        # was always going to miss; write_uint8()/write_uint16() never had this problem.
        if address < self.bootrom_byte_size:
            bootrom[address // 4] = value & 0xFFFFFFFF
        elif FLASH_START_ADDRESS <= address < FLASH_START_ADDRESS + self.flash_byte_size:
            write_uint32_le(self.flash, address - FLASH_START_ADDRESS, value)
        elif RAM_START_ADDRESS <= address < RAM_START_ADDRESS + self.ram_byte_size:
            write_uint32_le(self.sram, address - RAM_START_ADDRESS, value)
        elif DPRAM_START_ADDRESS <= address < DPRAM_START_ADDRESS + self.dpram_byte_size:
            offset = address - DPRAM_START_ADDRESS
            write_uint32_le(self.usb_dpram, offset, value)
            self.usb_ctrl.dpram_updated(offset, value)
        elif SIO_START_ADDRESS <= address < SIO_START_ADDRESS + 0x10000000:
            self.sio.write_uint32(address - SIO_START_ADDRESS, value)
        elif address >> 12 == 0xE000E:
            self.ppb.write_uint32(address & 0xFFF, value)
        else:
            peripheral = self.find_peripheral(address)
            if peripheral:
                atomic_type = (address & 0x3000) >> 12
                offset = address & 0xFFF
                peripheral.write_uint32_atomic(offset, value, atomic_type)
            else:
                self.logger.warning(LOG_NAME, f"Write to undefined address: {address:x}")

        if self.jit is not None:
            self.jit.on_write(address, 4)

    def write_uint8(self, address: int, value: int) -> None:
        if RAM_START_ADDRESS <= address < RAM_START_ADDRESS + self.ram_byte_size:
            self.sram[address - RAM_START_ADDRESS] = value & 0xFF
            if self.jit is not None:
                self.jit.on_write(address, 1)
            return

        aligned_address = u32(address & 0xFFFFFFFC)
        offset = address & 0x3
        peripheral = self.find_peripheral(address)
        if peripheral:
            atomic_type = (aligned_address & 0x3000) >> 12
            peripheral_offset = aligned_address & 0xFFF
            peripheral.write_uint32_atomic(
                peripheral_offset,
                (value & 0xFF) | ((value & 0xFF) << 8) | ((value & 0xFF) << 16) | ((value & 0xFF) << 24),
                atomic_type,
            )
            if self.jit is not None:
                self.jit.on_write(aligned_address, 4)
            return
        original_value = self.read_uint32(aligned_address)
        patched = bytearray(u32(original_value).to_bytes(4, "little"))
        patched[offset] = value & 0xFF
        self.write_uint32(aligned_address, int.from_bytes(patched, "little"))

    def write_uint16(self, address: int, value: int) -> None:
        # we assume that address is 16-bit aligned.
        # Ideally we should generate a fault if not!

        if RAM_START_ADDRESS <= address < RAM_START_ADDRESS + self.ram_byte_size:
            write_uint16_le(self.sram, address - RAM_START_ADDRESS, value)
            if self.jit is not None:
                self.jit.on_write(address, 2)
            return

        aligned_address = u32(address & 0xFFFFFFFC)
        offset = address & 0x3
        peripheral = self.find_peripheral(address)
        if peripheral:
            atomic_type = (aligned_address & 0x3000) >> 12
            peripheral_offset = aligned_address & 0xFFF
            peripheral.write_uint32_atomic(peripheral_offset, (value & 0xFFFF) | ((value & 0xFFFF) << 16), atomic_type)
            if self.jit is not None:
                self.jit.on_write(aligned_address, 4)
            return
        original_value = self.read_uint32(aligned_address)
        patched = bytearray(u32(original_value).to_bytes(4, "little"))
        write_uint16_le(patched, offset, value)
        self.write_uint32(aligned_address, int.from_bytes(patched, "little"))

    def bus_copy(self, dst: int, src: int, n: int) -> None:
        """memcpy(dst, src, n)-equivalent bulk copy, used only by CortexM0Core._hle_memcpy() (the
        bootrom-__memcpy HLE hook - see hle_memcpy_entries above) - not part of the normal bus API,
        and not cycle-accurate the way individual read/write_uint*() calls are meant to be.

        Fast path: a single bytearray slice copy when both ends land in RAM/flash (by far the
        common case for real firmware's own memcpy() calls - TinyUSB's tu_fifo_read/write,
        littlefs's lfs2_bd_read(), see docs/BACKLOG.md). Slice assignment handles overlap
        correctly here (the source slice is materialized as an independent copy before the
        assignment happens), matching real memcpy - well, memmove - semantics regardless of
        overlap direction.

        Fallback: a plain byte-by-byte bus copy for anything else (bootrom-to-somewhere,
        peripheral space, crossing between RAM and flash) - still correct, just not the fast
        path, since no real firmware routes a memcpy() through peripheral registers but this
        doesn't assume that's impossible.
        """
        if n <= 0:
            return
        dst = u32(dst)
        src = u32(src)

        dst_buf, dst_off = self._ram_or_flash(dst)
        src_buf, src_off = self._ram_or_flash(src)
        if dst_buf is not None and src_buf is not None and dst_off + n <= len(dst_buf) and src_off + n <= len(src_buf):
            dst_buf[dst_off : dst_off + n] = src_buf[src_off : src_off + n]
            if self.jit is not None:
                self.jit.on_write(dst, n)
            return

        data = bytes(self.read_uint8(src + i) for i in range(n))
        for i in range(n):
            self.write_uint8(dst + i, data[i])

    def _ram_or_flash(self, address: int) -> "tuple[bytearray, int] | tuple[None, int]":
        if RAM_START_ADDRESS <= address < RAM_START_ADDRESS + self.ram_byte_size:
            return self.sram, address - RAM_START_ADDRESS
        if FLASH_START_ADDRESS <= address < FLASH_START_ADDRESS + self.flash_byte_size:
            return self.flash, address - FLASH_START_ADDRESS
        return None, 0

    @property
    def gpio_values(self) -> int:
        result = 0
        for gpio_index in range(len(self.gpio)):
            if self.gpio[gpio_index].input_value:
                result |= 1 << gpio_index
        return result

    def set_interrupt(self, irq: int, value: bool) -> None:
        self.core.set_interrupt(irq, value)

    def update_io_interrupt(self) -> None:
        interrupt_value = False
        for pin in self.gpio:
            if pin.irq_value:
                interrupt_value = True
        self.set_interrupt(IRQ.IO_BANK0, interrupt_value)

    def step(self) -> None:
        self.core.execute_instruction()
