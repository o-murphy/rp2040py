# cython: language_level=3, boundscheck=False, wraparound=False, cdivision=True, freethreading_compatible=True
"""Real, fully-typed Cython port of rp2040py._rp2040.RP2040 - see that file for the semantic
reference and inline commentary.

Only the actual hot path is typed: sram/flash/usb_dpram/bootrom are C-level memoryviews (real
views into the same bytearray/array.array buffers, not copies - so external code touching
rp2040.flash[...]  etc. still works, see tests/test_ssi.py) and read_uint8/16/32 / write_uint8/16/32
branch on them directly instead of going through Python-level bytearray indexing.

Everything else - the ~30 peripheral objects (UART, I2C, DMA, PIO, GPIO, ...), the clock, the
peripherals dict, on_break, clk_sys/clk_peri - is constructed exactly as in _rp2040.py and stored
as plain Python attributes (`cdef dict __dict__` below opts this class into normal dynamic
attribute support). None of that is on the per-instruction hot path, so typing it further would
add real duplication-drift risk for no measurable benefit.
"""

from rp2040py.native._cortex_m0_core cimport CortexM0Core

from rp2040py.clock.clock import IClock
from rp2040py.clock.simulation_clock import SimulationClock
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
from rp2040py.peripherals.peripheral import UnimplementedPeripheral
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
from rp2040py.native._bit cimport read_uint16_le, read_uint32_le, u32, write_uint16_le, write_uint32_le
from rp2040py.utils.logging import ConsoleLogger, LogLevel

# Deliberately NOT `from cpython cimport array`: that pulls in CPython-internal array.array
# struct access, which doesn't compile under Py_LIMITED_API (confirmed via an actual abi3 build
# attempt - GCC errors on incomplete-type access to PyTypeObject/arrayobject internals). All we
# actually do with `array` is call the plain array.array(...) constructor, which needs no special
# declaration - a normal Python-level call works identically and is limited-API-safe.
import array

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

cdef const unsigned int KB = 1024
cdef unsigned int MB = 1024 * KB
cdef const unsigned int MHZ = 1_000_000

# Not `const`: Cython requires a const global's initializer to be a compile-time constant, and
# these are cast from Python-level values imported from rp2040py.memory_map at module load.
cdef unsigned int RAM_START = <unsigned int> RAM_START_ADDRESS
cdef unsigned int FLASH_START = <unsigned int> FLASH_START_ADDRESS
cdef unsigned int FLASH_END = <unsigned int> FLASH_END_ADDRESS
cdef unsigned int DPRAM_START = <unsigned int> DPRAM_START_ADDRESS
cdef unsigned int SIO_START = <unsigned int> SIO_START_ADDRESS


cdef class RP2040:
    def __cinit__(self):
        # Buffer-backed memoryview fields are allocated here, not __init__: __cinit__ is
        # guaranteed to run exactly once as part of object allocation (before any subclass
        # __init__ could skip calling super().__init__() and leave these fields unset), per
        # Cython's __cinit__/__init__ split. Ignores the `clock` constructor arg entirely -
        # Cython passes it through but a no-args-besides-self __cinit__ just ignores it.
        #
        # 4096 32-bit words = 16KB, matching the real RP2040 boot ROM size. A typed memoryview
        # (not a plain list, unlike _rp2040.py's pure-Python bootrom) since this module's whole
        # point is C-level access on the hot read_uint32/write_uint32 path.
        self._bootrom = array.array("I", [0] * (4 * KB))
        self.bootrom_byte_size = len(self._bootrom) * 4
        self._sram = bytearray(264 * KB)
        self.ram_byte_size = len(self._sram)
        self._flash = bytearray(16 * MB)
        self.flash_byte_size = len(self._flash)
        self._usb_dpram = bytearray(4 * KB)
        self.dpram_byte_size = len(self._usb_dpram)
        self.core = CortexM0Core(self)

    def __init__(self, clock: IClock | None = None):
        # NOTE: must be set before constructing any peripheral below - several of them
        # (RPPPB, RPWatchdog, RPADC, RPPWM, RPPIO, RPUSBController, RPDMA) call
        # self.clock.create_alarm(...) from their own __init__.
        self.clock = clock if clock is not None else SimulationClock()

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

        self.logger = ConsoleLogger(LogLevel.DEBUG, True)

        self.peripherals = {
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
        self.on_break = self._default_on_break

        self.reset()

    def _default_on_break(self, code: int) -> None:
        # TODO: raise HardFault exception
        pass

    # Wrapped in a real builtin memoryview() rather than exposed as `cdef public`: Cython's own
    # typed-memoryview-slice object doesn't support content-based `==` against bytes/bytearray
    # (falls back to identity comparison), which real bytearray/memoryview slices do - and
    # existing callers (peripherals/usb.py, device/load_flash.py, tests/test_ssi.py,
    # tests/test_kaluma_device.py) compare/slice/assign these as if they were still the plain
    # bytearrays _rp2040.py uses. memoryview(cython_memoryview) is still a live view onto the
    # same backing buffer, not a copy, so mutation through it is unaffected.
    @property
    def sram(self):
        return memoryview(self._sram)

    @property
    def flash(self):
        return memoryview(self._flash)

    @property
    def usb_dpram(self):
        return memoryview(self._usb_dpram)

    @property
    def bootrom(self):
        return memoryview(self._bootrom)

    def load_bootrom(self, bootrom_data) -> None:
        # Cython memoryview slice-assignment needs the RHS explicitly typed as a memoryview too -
        # a plain array.array/bytearray/bytes only auto-coerces when passed as a *parameter*, not
        # as a slice-assignment RHS (confirmed via isolated repro; raises "TypeError: an integer
        # is required" otherwise).
        cdef unsigned int[:] materialized = array.array("I", (value & 0xFFFFFFFFU for value in bootrom_data))
        self._bootrom[: len(bootrom_data)] = materialized
        self.reset()

    def reset(self) -> None:
        cdef unsigned char[:] filler
        self.core.reset()
        self.pwm.reset()
        filler = bytearray(b"\xff" * len(self._flash))
        self._flash[:] = filler

    cpdef unsigned int read_uint32(self, long long address) except? 0:
        cdef unsigned int addr = <unsigned int> (address & 0xFFFFFFFFU)
        cdef unsigned int offset
        if addr & 0x3:
            self.logger.error(LOG_NAME, f"read from address {addr:x}, which is not 32 bit aligned")

        if addr < self.bootrom_byte_size:
            return self._bootrom[addr // 4]
        if FLASH_START <= addr < FLASH_END:
            # Flash is mirrored four times:
            # - 0x10000000 XIP
            # - 0x11000000 XIP_NOALLOC
            # - 0x12000000 XIP_NOCACHE
            # - 0x13000000 XIP_NOCACHE_NOALLOC
            offset = addr & 0x00FFFFFF
            return read_uint32_le(self._flash, offset)
        if RAM_START <= addr < RAM_START + self.ram_byte_size:
            return read_uint32_le(self._sram, addr - RAM_START)
        if DPRAM_START <= addr < DPRAM_START + self.dpram_byte_size:
            return read_uint32_le(self._usb_dpram, addr - DPRAM_START)
        if (addr >> 12) == 0xE000E:
            return <unsigned int> self.ppb.read_uint32(addr & 0xFFF)
        if SIO_START <= addr < SIO_START + 0x10000000:
            # int(): SIO's DIV_QUOTIENT can be a fractional JS number (see sio.py) - any real JS
            # consumer (typed-array store, bitwise op) truncates it immediately via ToUint32, so
            # truncate here rather than letting a float leak into the rest of the bus/CPU/DMA
            # read path.
            return <unsigned int> (int(self.sio.read_uint32(addr - SIO_START)) & 0xFFFFFFFFU)

        peripheral = self.find_peripheral(addr)
        if peripheral is not None:
            return <unsigned int> peripheral.read_uint32(addr & 0x3FFF)

        self.logger.warning(LOG_NAME, f"Read from invalid memory address: {addr:x}")
        return 0xFFFFFFFFU

    def find_peripheral(self, address):
        return self.peripherals.get((u32(address) >> 14) << 2)

    cpdef unsigned int read_uint16(self, long long address):
        """We assume the address is 16-bit aligned."""
        cdef unsigned int addr = <unsigned int> (address & 0xFFFFFFFFU)
        cdef unsigned int offset
        cdef unsigned int value
        if FLASH_START <= addr < FLASH_START + self.flash_byte_size:
            offset = addr - FLASH_START
            return read_uint16_le(self._flash, offset)
        if RAM_START <= addr < RAM_START + self.ram_byte_size:
            offset = addr - RAM_START
            return read_uint16_le(self._sram, offset)

        value = self.read_uint32(addr & 0xFFFFFFFCU)
        return (value & 0xFFFF0000U) >> 16 if (addr & 0x2) else (value & 0xFFFF)

    cpdef unsigned int read_uint8(self, long long address):
        cdef unsigned int addr = <unsigned int> (address & 0xFFFFFFFFU)
        cdef unsigned int value
        if FLASH_START <= addr < FLASH_START + self.flash_byte_size:
            return self._flash[addr - FLASH_START]
        if RAM_START <= addr < RAM_START + self.ram_byte_size:
            return self._sram[addr - RAM_START]

        value = self.read_uint16(addr & 0xFFFFFFFEU)
        return <unsigned int> ((value & 0xFF00) >> 8 if (addr & 0x1) else (value & 0xFF))

    cpdef write_uint32(self, long long address, long long value):
        cdef unsigned int addr = <unsigned int> (address & 0xFFFFFFFFU)
        cdef unsigned int val = <unsigned int> (value & 0xFFFFFFFFU)
        cdef unsigned int atomic_type
        cdef unsigned int offset
        # Same range order as read_uint32() - RAM/flash/bootrom checked first via cheap integer
        # comparisons, find_peripheral() (a dict lookup) only as the fallback for what's left.
        #
        # sio/ppb/peripheral get the ORIGINAL `value` object, not the masked `val` - matching
        # _rp2040.py exactly. sio.py's hardware-divider emulation (see update_hardware_divider's
        # `self.div_dividend > 0` check) relies on receiving the true signed Python int, not its
        # unsigned 32-bit truncation; s32()/u32() are idempotent either way, but a raw `> 0`
        # comparison on a pre-masked value is not.
        if addr < self.bootrom_byte_size:
            self._bootrom[addr // 4] = val
        elif FLASH_START <= addr < FLASH_START + self.flash_byte_size:
            write_uint32_le(self._flash, addr - FLASH_START, val)
        elif RAM_START <= addr < RAM_START + self.ram_byte_size:
            write_uint32_le(self._sram, addr - RAM_START, val)
        elif DPRAM_START <= addr < DPRAM_START + self.dpram_byte_size:
            offset = addr - DPRAM_START
            write_uint32_le(self._usb_dpram, offset, val)
            self.usb_ctrl.dpram_updated(offset, value)
        elif SIO_START <= addr < SIO_START + 0x10000000:
            self.sio.write_uint32(addr - SIO_START, value)
        elif (addr >> 12) == 0xE000E:
            self.ppb.write_uint32(addr & 0xFFF, value)
        else:
            peripheral = self.find_peripheral(addr)
            if peripheral is not None:
                atomic_type = (addr & 0x3000) >> 12
                offset = addr & 0xFFF
                peripheral.write_uint32_atomic(offset, value, atomic_type)
            else:
                self.logger.warning(LOG_NAME, f"Write to undefined address: {addr:x}")

    cpdef write_uint8(self, long long address, long long value):
        cdef unsigned int addr = <unsigned int> (address & 0xFFFFFFFFU)
        cdef unsigned int val = <unsigned int> (value & 0xFF)
        cdef unsigned int aligned_address
        cdef unsigned int offset
        cdef unsigned int atomic_type
        cdef unsigned int peripheral_offset
        if RAM_START <= addr < RAM_START + self.ram_byte_size:
            self._sram[addr - RAM_START] = val
            return

        aligned_address = addr & 0xFFFFFFFCU
        offset = addr & 0x3
        peripheral = self.find_peripheral(addr)
        if peripheral is not None:
            atomic_type = (aligned_address & 0x3000) >> 12
            peripheral_offset = aligned_address & 0xFFF
            peripheral.write_uint32_atomic(
                peripheral_offset,
                val | (val << 8) | (val << 16) | (val << 24),
                atomic_type,
            )
            return
        original_value = self.read_uint32(aligned_address)
        patched = bytearray((<unsigned int> original_value).to_bytes(4, "little"))
        patched[offset] = val
        self.write_uint32(aligned_address, int.from_bytes(patched, "little"))

    cpdef write_uint16(self, long long address, long long value):
        # we assume that address is 16-bit aligned.
        # Ideally we should generate a fault if not!
        cdef unsigned int addr = <unsigned int> (address & 0xFFFFFFFFU)
        cdef unsigned int val = <unsigned int> (value & 0xFFFF)
        cdef unsigned int aligned_address
        cdef unsigned int offset
        cdef unsigned int atomic_type
        cdef unsigned int peripheral_offset
        if RAM_START <= addr < RAM_START + self.ram_byte_size:
            write_uint16_le(self._sram, addr - RAM_START, val)
            return

        aligned_address = addr & 0xFFFFFFFCU
        offset = addr & 0x3
        peripheral = self.find_peripheral(addr)
        if peripheral is not None:
            atomic_type = (aligned_address & 0x3000) >> 12
            peripheral_offset = aligned_address & 0xFFF
            peripheral.write_uint32_atomic(peripheral_offset, val | (val << 16), atomic_type)
            return
        original_value = self.read_uint32(aligned_address)
        patched = bytearray((<unsigned int> original_value).to_bytes(4, "little"))
        write_uint16_le(patched, offset, val)
        self.write_uint32(aligned_address, int.from_bytes(patched, "little"))

    @property
    def gpio_values(self) -> int:
        cdef unsigned int result = 0
        cdef unsigned int gpio_index
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
