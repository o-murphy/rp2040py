# cython: language_level=3, boundscheck=False, wraparound=False, cdivision=True, freethreading_compatible=True
"""Real, fully-typed Cython port of rp2040py._rp2040.RP2040 - see that file for the semantic
reference and inline commentary.

Only the actual hot path is typed: sram/flash/usb_dpram/bootrom are C-level memoryviews (real
views into the same bytearray/array.array buffers, not copies - so external code touching
rp2040.flash[...]  etc. still works, see tests/test_ssi.py) and read_uint8/16/32 / write_uint8/16/32
branch on them directly instead of going through Python-level bytearray indexing.

Everything else - the ~30 peripheral objects (UART, I2C, DMA, PIO, GPIO, ...), the peripherals
dict, on_break, clk_sys/clk_peri - is constructed exactly as in _rp2040.py and stored as plain
Python attributes (`cdef dict __dict__` below opts this class into normal dynamic attribute
support). None of that is on the per-instruction hot path, so typing it further would add real
duplication-drift risk for no measurable benefit.

`clock` is the one exception: typed as the concrete native SimulationClock (see _rp2040.pxd),
not left in the `__dict__` bucket above, so native/_simulator.pyx's hot loop can bind it to a
statically-typed local and call tick()/nanos_to_next_alarm/has_scheduled_alarm as direct C-level
dispatch - see native/_simulation_clock.pyx's own module docstring for the full rationale.
"""

from libc.stdint cimport int64_t, uint8_t, uint32_t, uint64_t
from libcpp cimport bool

from rp2040py.native._cortex_m0_core cimport CortexM0Core
from rp2040py.native._dma cimport RPDMA
from rp2040py.native._mcu cimport kMcuBlockBusctrl, kMcuBlockPsm, kMcuBlockResets, McuMemory, kMcuBlockRtc, kMcuBlockSyscfg, kMcuBlockSysinfo, kMcuBlockTbman, kMcuBlockVreg, Mcu, McuHost, kMcuBlockAdc, kMcuBlockClocks, kMcuBlockDma, kMcuBlockI2c0, kMcuBlockI2c1, kMcuBlockIoBank0, kMcuBlockIoQspi, kMcuBlockPadsBank0, kMcuBlockPadsQspi, kMcuBlockPllSys, kMcuBlockPllUsb, kMcuBlockPpb, kMcuBlockPio0, kMcuBlockPio1, kMcuBlockPwm, kMcuBlockRosc, kMcuBlockSsi, kMcuBlockUsb, kMcuBlockXip, kMcuBlockSio, kMcuBlockSpi0, kMcuBlockSpi1, kMcuBlockTimer, kMcuBlockUart0, kMcuBlockUart1, kMcuBlockWatchdog, kMcuBlockXosc
from rp2040py.native._pending cimport park_error, pending_flag, raise_if_pending
from rp2040py.native._pio cimport PioBlock
from rp2040py.native._usb cimport UsbHost, usb_wire_fill
from rp2040py.native._bus cimport BusHost, kBusWarnInvalidRead, kBusWarnUnalignedRead
from rp2040py.native._window_map cimport Read32Fn, Write32Fn, WindowHandler
from rp2040py.native._simulation_clock cimport SimulationClock

from rp2040py.clock.clock import IClock
from rp2040py.native._gpio_pin import GPIOPin, GPIOPinBank
from rp2040py.native._gpio_pin import GPIOPinBank as _NativeGPIOPinBank
from rp2040py.native._pin_wiring import bind_pin_sources
from rp2040py.irq import IRQ
from rp2040py.memory_map import (
    APB_START_ADDRESS,
    DPRAM_START_ADDRESS,
    FLASH_END_ADDRESS,
    FLASH_START_ADDRESS,
    RAM_START_ADDRESS,
    SIO_START_ADDRESS,
)
from rp2040py.native._adc import RPADC
from rp2040py.native._busctrl import RPBUSCTRL
from rp2040py.native._clocks import RPClocks
from rp2040py.native._pll import RPPLL
from rp2040py.peripherals._dma import DREQChannel
from rp2040py.native._i2c import RPI2C
from rp2040py.native._io import RPIO
from rp2040py.native._pads import RPPADS
from rp2040py.native._vreg_and_chip_reset import HAD_POR, HAD_RUN, RPVREGAndChipReset
from rp2040py.native._xip_ctrl import RPXIPAux, RPXIPCtrl
from rp2040py.qspi_pads import QSPI_PAD_RESET_VALUES
from rp2040py.reset_cause import ResetCause
from rp2040py.native._pio import RPPIO
from rp2040py.native._ppb import RPPPB
from rp2040py.native._psm import RPPSM
from rp2040py.native._pwm import RPPWM
from rp2040py.native._reset import RPReset
from rp2040py.native._rtc import RP2040RTC
from rp2040py.native._spi import RPSPI
from rp2040py.peripherals._spi import ISPIDMAChannels
from rp2040py.native._ssi import RPSSI
from rp2040py.native._syscfg import RP2040SysCfg
from rp2040py.native._sysinfo import RP2040SysInfo
from rp2040py.native._tbman import RPTBMAN
from rp2040py.native._timer import RPTimer
from rp2040py.native._uart import RPUART
from rp2040py.peripherals._uart import IUARTDMAChannels
from rp2040py.native._usb import RPUSBController
from rp2040py.native._watchdog import RPWatchdog
from rp2040py.native._rosc import RPROSC
from rp2040py.native._xosc import RPXOSC
from rp2040py.native._sio import RPSIO
from rp2040py.native._bit cimport u32
from rp2040py.utils.logging import ConsoleLogger, LogLevel

# Deliberately NOT `from cpython cimport array`: that pulls in CPython-internal array.array
# struct access, which doesn't compile under Py_LIMITED_API (confirmed via an actual abi3 build
# attempt - GCC errors on incomplete-type access to PyTypeObject/arrayobject internals). All we
# actually do with `array` is call the plain array.array(...) constructor, which needs no special
# declaration - a normal Python-level call works identically and is limited-API-safe.
import array
import os
from rp2040py.native._register_warning import warn_register

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


cdef class _PythonWindow:
    """The context of one window handler whose peripheral is a Python object: the C++ window map calls
    the trampolines below with a pointer to this. The peripheral's methods are looked up on every call
    (never cached as bound methods), so a test or board that replaces `peripheral.read_uint32` on the
    instance after the window was attached still gets what it asked for."""

    cdef object peripheral

    def __cinit__(self, peripheral):
        self.peripheral = peripheral


cdef uint32_t _python_window_read32(void* ctx, uint32_t offset) noexcept:
    try:
        return <unsigned int> (<_PythonWindow> ctx).peripheral.read_uint32(offset)
    except BaseException as error:
        park_error(error)
        return 0


cdef void _python_window_write32(void* ctx, uint32_t offset, int64_t raw_value, uint32_t atomic_type) noexcept:
    try:
        (<_PythonWindow> ctx).peripheral.write_uint32_atomic(offset, raw_value, atomic_type)
    except BaseException as error:
        park_error(error)


cdef uint32_t _python_sio_read32(void* ctx, uint32_t offset) noexcept:
    # int(): SIO's DIV_QUOTIENT can be a fractional JS number (see sio.py) - any real JS consumer (typed-array store,
    # bitwise op) truncates it immediately via ToUint32, so truncate here rather than letting a float leak into the rest
    # of the bus/CPU/DMA read path.
    try:
        return <unsigned int> (int((<_PythonWindow> ctx).peripheral.read_uint32(offset)) & 0xFFFFFFFFU)
    except BaseException as error:
        park_error(error)
        return 0


cdef void _python_direct_write32(void* ctx, uint32_t offset, int64_t raw_value, uint32_t atomic_type) noexcept:
    # SIO and the PPB have never had an atomic-alias write path: they get `write_uint32` with the caller's own value.
    try:
        (<_PythonWindow> ctx).peripheral.write_uint32(offset, raw_value)
    except BaseException as error:
        park_error(error)


cdef void _bus_warn(void* ctx, uint32_t kind, uint32_t address) noexcept:
    cdef RP2040 chip = <RP2040> ctx
    try:
        if kind == kBusWarnUnalignedRead:
            chip.logger.warning(LOG_NAME, f"read from address {address:x}, which is not 32 bit aligned")
        elif kind == kBusWarnInvalidRead:
            chip.logger.warning(LOG_NAME, f"Read from invalid memory address: {address:x}")
        else:
            chip.logger.warning(LOG_NAME, f"Write to undefined address: {address:x}")
    except BaseException as error:
        park_error(error)


cdef void _bus_dpram_written(void* ctx, uint32_t offset, int64_t value) noexcept:
    cdef RP2040 chip = <RP2040> ctx
    try:
        chip.usb_ctrl.dpram_updated(offset, value)
    except BaseException as error:
        park_error(error)


class _PeripheralTable(dict):
    """`RP2040.peripherals`: still an ordinary dict (blocks, boards and tests read, add and replace entries
    exactly as before) - but every change to it is mirrored into the chip's C++ window registry, which is
    what the bus actually dispatches through."""

    def __init__(self, chip, items=()):
        dict.__init__(self)
        self._chip = chip
        self.update(items)

    def __setitem__(self, key, value):
        dict.__setitem__(self, key, value)
        self._chip._attach_window(key, value)

    def __delitem__(self, key):
        dict.__delitem__(self, key)
        self._chip._detach_window(key)

    def update(self, *args, **kwargs):
        for key, value in dict(*args, **kwargs).items():
            self[key] = value

    def setdefault(self, key, default=None):
        if key not in self:
            self[key] = default
        return dict.__getitem__(self, key)

    def pop(self, key, *default):
        present = key in self
        value = dict.pop(self, key, *default)
        if present:
            self._chip._detach_window(key)
        return value

    def popitem(self):
        key, value = dict.popitem(self)
        self._chip._detach_window(key)
        return key, value

    def clear(self):
        dict.clear(self)
        self._chip._clear_windows()

    def __ior__(self, other):
        self.update(other)
        return self


class _PinSourceList(list):
    """`RP2040.pio`: a list that tells the chip when one of its blocks is replaced, so the pins' direct sources follow (`RP2040._bind_pin_sources`)."""

    def __init__(self, items, owner):
        super().__init__(items)
        self._owner = owner

    def __setitem__(self, index, value):
        super().__setitem__(index, value)
        self._owner._bind_pin_sources()


cdef bool _irq_hook(void* ctx, uint32_t irq, bool level) noexcept:
    cdef RP2040 chip = <RP2040> ctx
    try:
        chip._irq_override(irq, level)
    except BaseException as error:
        park_error(error)
        return False
    return True


cdef dict _BLOCK_WINDOW = {
    kMcuBlockRtc: 0x4005C,
    kMcuBlockTbman: 0x4006C,
    kMcuBlockBusctrl: 0x40030,
    kMcuBlockPsm: 0x40010,
    kMcuBlockResets: 0x4000C,
    kMcuBlockVreg: 0x40064,
    kMcuBlockSysinfo: 0x40000,
    kMcuBlockSyscfg: 0x40004,
    kMcuBlockTimer: 0x40054,
    kMcuBlockAdc: 0x4004C,
    kMcuBlockUart0: 0x40034,
    kMcuBlockUart1: 0x40038,
    kMcuBlockSpi0: 0x4003C,
    kMcuBlockSpi1: 0x40040,
    kMcuBlockI2c0: 0x40044,
    kMcuBlockI2c1: 0x40048,
    kMcuBlockClocks: 0x40008,
    kMcuBlockPllSys: 0x40028,
    kMcuBlockPllUsb: 0x4002C,
    kMcuBlockXosc: 0x40024,
    kMcuBlockRosc: 0x40060,
    kMcuBlockWatchdog: 0x40058,
    kMcuBlockDma: 0x50000,
    kMcuBlockIoBank0: 0x40014,
    kMcuBlockIoQspi: 0x40018,
    kMcuBlockPadsBank0: 0x4001C,
    kMcuBlockPadsQspi: 0x40020,
    kMcuBlockPwm: 0x40050,
    kMcuBlockPio0: 0x50200,
    kMcuBlockPio1: 0x50300,
    kMcuBlockSsi: 0x18000,
    kMcuBlockXip: 0x14000,
    kMcuBlockUsb: 0x50110,
}  # the block the `Mcu` built -> its key in the peripherals table

# The blocks of the reset cascade and the chip attribute that holds each one's Python object (`RP2040._reset_world_mask`); the pin banks' IO / PADS blocks also follow their bank.
_RESET_BLOCKS = (
    (kMcuBlockPpb, lambda chip: chip.ppb),
    (kMcuBlockSio, lambda chip: chip.sio),
    (kMcuBlockClocks, lambda chip: chip.clocks),
    (kMcuBlockIoBank0, lambda chip: chip.io_bank0),
    (kMcuBlockPadsBank0, lambda chip: chip.pads_bank0),
    (kMcuBlockIoQspi, lambda chip: chip.io_qspi),
    (kMcuBlockPadsQspi, lambda chip: chip.pads_qspi),
    (kMcuBlockPwm, lambda chip: chip.pwm),
    (kMcuBlockDma, lambda chip: chip.dma),
    (kMcuBlockAdc, lambda chip: chip.adc),
    (kMcuBlockTimer, lambda chip: chip.timer),
    (kMcuBlockUart0, lambda chip: chip.uart[0]),
    (kMcuBlockUart1, lambda chip: chip.uart[1]),
    (kMcuBlockSpi0, lambda chip: chip.spi[0]),
    (kMcuBlockSpi1, lambda chip: chip.spi[1]),
    (kMcuBlockI2c0, lambda chip: chip.i2c[0]),
    (kMcuBlockI2c1, lambda chip: chip.i2c[1]),
    (kMcuBlockPio0, lambda chip: chip.pio[0]),
    (kMcuBlockPio1, lambda chip: chip.pio[1]),
    (kMcuBlockUsb, lambda chip: chip.usb_ctrl),
    (kMcuBlockRtc, lambda chip: chip.rtc),
    (kMcuBlockBusctrl, lambda chip: chip.busctrl),
    (kMcuBlockSyscfg, lambda chip: chip.syscfg),
    (kMcuBlockXosc, lambda chip: chip.xosc),
    (kMcuBlockRosc, lambda chip: chip.rosc),
    (kMcuBlockXip, lambda chip: chip.xip_ctrl),
    (kMcuBlockSsi, lambda chip: chip.ssi),
    (kMcuBlockWatchdog, lambda chip: chip.watchdog),
    (kMcuBlockVreg, lambda chip: chip.vreg_and_chip_reset),
)
_CAUSE_CODES = {ResetCause.POWER_ON: 0, ResetCause.RUN_PIN: 1, ResetCause.WATCHDOG: 2}  # core/mcu.hpp's McuResetCause


cdef void _mcu_warn(void* ctx, uint32_t block, uint32_t kind, uint32_t offset, int64_t value) noexcept:
    # What a block that the `Mcu` built says about a register access it does not implement: the Python view of that block (the one in the peripherals table) words it for the logger.
    cdef RP2040 chip = <RP2040> ctx
    try:
        # (the PPB and SIO are not in the peripherals table: the bus reaches them on its own)
        view = chip._ppb if block == kMcuBlockPpb else chip._sio if block == kMcuBlockSio else chip._peripherals.get(_BLOCK_WINDOW[block])
        if view is not None:
            warn_register(view, kind, offset, value)
    except BaseException as error:
        park_error(error)


cdef bool _w_uart_byte(void* ctx, uint32_t instance, uint32_t byte) noexcept:
    cdef RP2040 chip = <RP2040> ctx
    try:
        chip.uart[instance]._world_byte(byte)
    except BaseException as error:
        park_error(error)
        return False
    return True


cdef bool _w_uart_baud(void* ctx, uint32_t instance) noexcept:
    cdef RP2040 chip = <RP2040> ctx
    try:
        chip.uart[instance]._world_baud()
    except BaseException as error:
        park_error(error)
        return False
    return True


cdef bool _w_spi_transmit(void* ctx, uint32_t instance, uint32_t value) noexcept:
    cdef RP2040 chip = <RP2040> ctx
    try:
        return <bool> chip.spi[instance]._world_transmit(value)
    except BaseException as error:
        park_error(error)
        return False


XOSC_STATUS = 0x04
XOSC_STATUS_STABLE = 1 << 31


cdef object _bank_of(RP2040 chip, uint32_t bank):
    return chip._gpio_bank if bank == 0 else chip._qspi_bank


cdef bool _w_pin_source(void* ctx, uint32_t bank, uint32_t source, uint32_t* value) noexcept:
    cdef RP2040 chip = <RP2040> ctx
    try:
        value[0] = <uint32_t> _bank_of(chip, bank)._world_source(source)
    except BaseException as error:
        park_error(error)
        return False
    return True


cdef bool _w_pin_changed(void* ctx, uint32_t bank, uint32_t pin, int new_state, int old_state) noexcept:
    cdef RP2040 chip = <RP2040> ctx
    try:
        _bank_of(chip, bank)._world_changed(pin, new_state, old_state)
    except BaseException as error:
        park_error(error)
        return False
    return True


cdef bool _w_pin_input_changed(void* ctx, uint32_t bank, uint32_t pin, bool pwm, uint32_t pio_mask) noexcept:
    cdef RP2040 chip = <RP2040> ctx
    try:
        _bank_of(chip, bank)._world_input(pin, pwm, pio_mask)
    except BaseException as error:
        park_error(error)
        return False
    return True


cdef bool _w_pin_inputs(void* ctx, uint32_t bank, uint32_t* value) noexcept:
    cdef RP2040 chip = <RP2040> ctx
    try:
        value[0] = <uint32_t> (<long long> _bank_of(chip, bank).input_values())
    except BaseException as error:
        park_error(error)
        return False
    return True


cdef bool _w_pin_update(void* ctx, uint32_t bank, uint32_t pin_mask) noexcept:
    cdef RP2040 chip = <RP2040> ctx
    try:
        pins = chip.qspi if bank else chip.gpio
        for index in range(len(pins)):
            if index < 32 and pin_mask & ((<uint32_t> 1) << index):
                pins[index].check_for_updates()
    except BaseException as error:
        park_error(error)
        return False
    return True


cdef void _w_system_reset_request(void* ctx) noexcept:
    cdef RP2040 chip = <RP2040> ctx
    try:
        chip.ppb._world_system_reset()
    except BaseException as error:
        park_error(error)


cdef bool _w_watchdog_trigger(void* ctx) noexcept:
    cdef RP2040 chip = <RP2040> ctx
    try:
        chip.watchdog._world_trigger()
    except BaseException as error:
        park_error(error)
        return False
    return True


cdef bool _w_watchdog_tick(void* ctx, double tick_hz) noexcept:
    cdef RP2040 chip = <RP2040> ctx
    try:
        chip.watchdog._world_tick(tick_hz)
    except BaseException as error:
        park_error(error)
        return False
    return True


cdef double _w_xosc_hz(void* ctx) noexcept:
    # The crystal's output while a host's own XOSC block (not the chip's) is in place: the board's crystal while that block says it is stable.
    cdef RP2040 chip = <RP2040> ctx
    try:
        return <double> chip.xosc_freq if chip.xosc.read_uint32(XOSC_STATUS) & XOSC_STATUS_STABLE else 0.0
    except BaseException as error:
        park_error(error)
        return 0.0


cdef double _w_rosc_hz(void* ctx) noexcept:
    cdef RP2040 chip = <RP2040> ctx
    try:
        return <double> chip.rosc_freq
    except BaseException as error:
        park_error(error)
        return 0.0


cdef bool _w_i2c_start(void* ctx, uint32_t instance, bool repeated) noexcept:
    cdef RP2040 chip = <RP2040> ctx
    try:
        return <bool> chip.i2c[instance]._world_start(repeated)
    except BaseException as error:
        park_error(error)
        return False


cdef bool _w_i2c_connect(void* ctx, uint32_t instance, uint32_t address, uint32_t mode) noexcept:
    cdef RP2040 chip = <RP2040> ctx
    try:
        return <bool> chip.i2c[instance]._world_connect(address, mode)
    except BaseException as error:
        park_error(error)
        return False


cdef bool _w_i2c_write(void* ctx, uint32_t instance, uint32_t value) noexcept:
    cdef RP2040 chip = <RP2040> ctx
    try:
        return <bool> chip.i2c[instance]._world_write(value)
    except BaseException as error:
        park_error(error)
        return False


cdef bool _w_i2c_read(void* ctx, uint32_t instance, bool ack) noexcept:
    cdef RP2040 chip = <RP2040> ctx
    try:
        return <bool> chip.i2c[instance]._world_read(ack)
    except BaseException as error:
        park_error(error)
        return False


cdef bool _w_i2c_stop(void* ctx, uint32_t instance) noexcept:
    cdef RP2040 chip = <RP2040> ctx
    try:
        return <bool> chip.i2c[instance]._world_stop()
    except BaseException as error:
        park_error(error)
        return False


cdef bool _w_reset_block(void* ctx, uint32_t block) noexcept:
    cdef RP2040 chip = <RP2040> ctx
    try:
        chip._reset_block_python(block)
    except BaseException as error:
        park_error(error)
        return False
    return True


cdef bool _w_usb_reset(void* ctx) noexcept:
    cdef RP2040 chip = <RP2040> ctx
    try:
        wire_reset = getattr(chip.usb_ctrl, "_wire_reset", None)
        if wire_reset is not None:
            wire_reset()
    except BaseException as error:
        park_error(error)
        return False
    return True


cdef bool _w_bus_reset(void* ctx) noexcept:
    cdef RP2040 chip = <RP2040> ctx
    try:
        if chip.usb_ctrl.on_reset:
            chip.usb_ctrl.on_reset()
    except BaseException as error:
        park_error(error)
        return False
    return True


cdef bool _w_record_cause(void* ctx, uint32_t flag) noexcept:
    cdef RP2040 chip = <RP2040> ctx
    try:
        chip.vreg_and_chip_reset.record_reset_cause(flag)
    except BaseException as error:
        park_error(error)
        return False
    return True


cdef bool _w_ssi_baudr(void* ctx, double* value) noexcept:
    cdef RP2040 chip = <RP2040> ctx
    try:
        value[0] = <double> (chip.ssi.read_uint32(0x14) & 0xFFFF)  # SSI.BAUDR
    except BaseException as error:
        park_error(error)
        return False
    return True


cdef bool _w_clk_sys(void* ctx, double* hz) noexcept:
    cdef RP2040 chip = <RP2040> ctx
    try:
        hz[0] = <double> chip.clk_sys
    except BaseException as error:
        park_error(error)
        return False
    return True


cdef bool _w_pio_started(void* ctx, uint32_t index) noexcept:
    cdef RP2040 chip = <RP2040> ctx
    try:
        chip.pio[index]._world_started()
    except BaseException as error:
        park_error(error)
        return False
    return True


cdef bool _w_adc_read(void* ctx, uint32_t channel) noexcept:
    cdef RP2040 chip = <RP2040> ctx
    try:
        chip.adc._world_read(channel)
    except BaseException as error:
        park_error(error)
        return False
    return True


cdef bool _w_adc_channel_value(void* ctx, uint32_t channel, int64_t* value) noexcept:
    cdef RP2040 chip = <RP2040> ctx
    try:
        value[0] = <int64_t> chip.adc._world_channel_value(<object> channel)
    except BaseException as error:
        park_error(error)
        return False
    return True


cdef bool _tree_hook(void* ctx) noexcept:
    # A tree the host put over the chip's own (an oracle's reference): the oscillators and the PLLs tell the chip, which re-derives through whichever tree it has now.
    cdef RP2040 chip = <RP2040> ctx
    try:
        chip.update_clocks()
    except BaseException as error:
        park_error(error)
        return False
    return True


cdef dict _DREQ_BY_NUMBER = {int(channel): channel for channel in DREQChannel}


cdef bool _dreq_tap_hook(void* ctx, uint32_t number, bool asserted) noexcept:
    # The observer of `RP2040.dreq_tap`: sees a request on its way to the chip's DMA, which still gets it.
    cdef RP2040 chip = <RP2040> ctx
    try:
        chip._dreq_tap(int(number), asserted)
    except BaseException as error:
        park_error(error)
        return False
    return True


cdef bool _dreq_hook(void* ctx, uint32_t number, bool asserted) noexcept:
    cdef RP2040 chip = <RP2040> ctx
    try:
        channel = _DREQ_BY_NUMBER.get(number, number)
        if asserted:
            chip._dma.set_dreq(channel)
        else:
            chip._dma.clear_dreq(channel)
    except BaseException as error:
        park_error(error)
        return False
    return True


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
        self._attach_memory_regions()
        self._window_owners = {}
        self.core = CortexM0Core(self)
        self.core1 = CortexM0Core(self, 1)  # the second core (record 0104): the chip's C++ `cpu1`, not run until a launch
        self.cores = (self.core, self.core1)
        self.quantum_cycles = 16  # two cores only (record 0104): 16 is the F2 scheduler, 1 the exact one

    cdef void _attach_memory_regions(self):
        """Hands the C++ memory map pointers into the four buffers above (record 0096, Phase 1, D2).
        Zero-copy: the map holds the buffers' own addresses; they never move because the typed
        memoryviews pin them (a bytearray/array.array cannot be resized while exported). The
        per-region flags and mirror masks reproduce the pre-Phase-1 behaviour exactly - see
        core/memory_map.hpp and tests/test_memory_map_parity.py."""
        cdef McuMemory memory
        memory.rom = <uint8_t*> &self._bootrom[0]
        memory.rom_size = self.bootrom_byte_size
        memory.flash = <uint8_t*> &self._flash[0]
        memory.flash_size = self.flash_byte_size
        memory.sram = <uint8_t*> &self._sram[0]
        memory.sram_size = self.ram_byte_size
        memory.dpram = <uint8_t*> &self._usb_dpram[0]
        memory.dpram_size = self.dpram_byte_size
        self._mcu.set_memory(memory)

    def __init__(self, clock: IClock | None = None):
        cdef BusHost bus_host
        bus_host.warn = _bus_warn
        bus_host.dpram_written = _bus_dpram_written
        bus_host.ctx = <void*> self
        self._mcu.bus.init(bus_host)
        # NOTE: must be set before constructing any peripheral below - several of them
        # (RPPPB, RPWatchdog, RPADC, RPPWM, RPPIO, RPUSBController, RPDMA) call
        # self.clock.create_alarm(...) from their own __init__.
        self.clock = clock if clock is not None else SimulationClock()
        self._mcu.use_clock(&self.clock._clock)  # a clock handed in is used where it is: it may already hold time and alarms (the batch glue points the chip at its simulator's clock again on every run)

        # The blocks that live in the C++ `Mcu` are built by it - on this clock, with their windows on the bus and their lines on the CPU (record 0096, 3a); the host it is given is what answers for what the
        # core cannot (the logger, so far).
        cdef McuHost world
        cdef UsbHost usb_wire
        world.warn = _mcu_warn
        world.ctx = <void*> self
        world.failed = pending_flag()
        world.uart_byte = _w_uart_byte
        world.uart_baud = _w_uart_baud
        world.pin_source = _w_pin_source
        world.pin_changed = _w_pin_changed
        world.pin_input_changed = _w_pin_input_changed
        world.pin_inputs = _w_pin_inputs
        world.pin_update = _w_pin_update
        world.system_reset_request = _w_system_reset_request
        world.watchdog_trigger = _w_watchdog_trigger
        world.watchdog_tick = _w_watchdog_tick
        world.xosc_hz = _w_xosc_hz
        world.rosc_hz = _w_rosc_hz
        world.spi_transmit = _w_spi_transmit
        world.i2c_start = _w_i2c_start
        world.i2c_connect = _w_i2c_connect
        world.i2c_write = _w_i2c_write
        world.i2c_read = _w_i2c_read
        world.i2c_stop = _w_i2c_stop
        world.adc_read = _w_adc_read
        world.pio_started = _w_pio_started
        world.ssi_baudr = _w_ssi_baudr
        usb_wire_fill(&usb_wire, <void*> self)
        world.usb_wire = &usb_wire
        world.reset_block = _w_reset_block
        world.usb_reset = _w_usb_reset
        world.bus_reset = _w_bus_reset
        world.record_cause = _w_record_cause
        world.clk_sys = _w_clk_sys
        world.adc_channel_value = _w_adc_channel_value
        self._mcu.build(world)

        # Clocks
        self.clk_sys = 125 * MHZ
        self.clk_peri = 125 * MHZ
        # The oscillators the PLLs and the clock generators start from (rp2040js 1.4.0): a 12 MHz crystal, and the ring oscillator at its typical frequency.
        self.xosc_freq = 12 * MHZ
        self.rosc_freq = 6.5 * MHZ
        self._clock_listeners = set()

        self.ppb = RPPPB(self, "PPB", 0)
        self.sio = RPSIO(self, 0)
        if os.environ.get("RP2040PY_CORE1", "1").lower() in ("0", "off", "false", "no"):  # the CLI's --no-core1 sets it: the second core is on unless asked to be off
            self.set_core1_enabled(False)

        self.uart = [
            RPUART(
                self, "UART0", IRQ.UART0, IUARTDMAChannels(rx=DREQChannel.DREQ_UART0_RX, tx=DREQChannel.DREQ_UART0_TX), 0
            ),
            RPUART(
                self, "UART1", IRQ.UART1, IUARTDMAChannels(rx=DREQChannel.DREQ_UART1_RX, tx=DREQChannel.DREQ_UART1_TX), 1
            ),
        ]
        self.i2c = [RPI2C(self, "I2C0", IRQ.I2C0, 0), RPI2C(self, "I2C1", IRQ.I2C1, 1)]
        self.pwm = RPPWM(self, "PWM_BASE", 0)
        self.adc = RPADC(self, "ADC", 0)

        # The chip's pins are slots of two banks (30 GPIO pins, 6 QSPI pins). The native ones share two C++ banks: the IO_BANK0 / IO_QSPI / PADS windows work on them directly, and the
        # chip asks the bank for what is a property of all its pins at once (`gpio_values`, the IO_BANK0 interrupt line). The facade hands out the pure-Python pins and banks instead when
        # the native ones are switched off (RP2040PY_SKIP_CYTHON=1): the same interface, the pure-Python windows go with them. `gpio` / `qspi` are the banks' own pin lists.
        self._gpio_bank = GPIOPinBank(self, 30, False, 0, 0)
        self._qspi_bank = GPIOPinBank(self, 6, True, 0, 1)
        self.gpio = self._gpio_bank.shells
        for i in range(30):
            GPIOPin(self, i, bank=self._gpio_bank)

        self.qspi = self._qspi_bank.shells
        for i, name in enumerate(("SCLK", "SS", "SD0", "SD1", "SD2", "SD3")):
            GPIOPin(self, i, name, bank=self._qspi_bank)
        # GPIOPin's own constructor default is PADS_BANK0's reset value, which is wrong for this
        # bank: PADS_QSPI resets each pad differently (RP2040 datasheet 2.19.6.3), and the
        # difference is load-bearing. GPIO_QSPI_SS is the only pad with a pull-up, which is what
        # holds the flash deselected and what makes the BOOTSEL button readable (the button pulls
        # SS down). Left at the bank0 default, an undriven SS reads low forever, so firmware that
        # polls it - CircuitPython 10.x does, from RAM, during boot - never sees it go high.
        # See docs/records/0050-qspi-pad-reset-values.md.
        for _qspi_pin, _pad_reset in zip(self.qspi, QSPI_PAD_RESET_VALUES):
            _qspi_pin.reset(io=False, pad_reset=_pad_reset)

        self.dma = RPDMA(self, "DMA", 0)
        self.pio = [
            RPPIO(self, "PIO0", IRQ.PIO0_IRQ0, 0),
            RPPIO(self, "PIO1", IRQ.PIO1_IRQ0, 1),
        ]
        self.usb_ctrl = RPUSBController(self, "USB")
        self.watchdog = RPWatchdog(self, "WATCHDOG_BASE", 0)
        # Named, not just an entry in `peripherals` below: BaseDevice.hard_reset() has to
        # record *which* reset just happened in CHIP_RESET (0089 Phase 1), the same way it
        # reaches self.watchdog for REASON - a dict lookup by base address would work but
        # would be the only such reach-in in the tree.
        self.vreg_and_chip_reset = RPVREGAndChipReset(self, "VREG_AND_CHIP_RESET_BASE")
        self.spi = [
            RPSPI(self, "SPI0", IRQ.SPI0, ISPIDMAChannels(rx=DREQChannel.DREQ_SPI0_RX, tx=DREQChannel.DREQ_SPI0_TX), 0),
            RPSPI(self, "SPI1", IRQ.SPI1, ISPIDMAChannels(rx=DREQChannel.DREQ_SPI1_RX, tx=DREQChannel.DREQ_SPI1_TX), 1),
        ]

        self.logger = ConsoleLogger(LogLevel.DEBUG, True)

        # Named, not just entries in `peripherals` below - see _rp2040.py's identical block for
        # why reset() needs each of them by name (0089 Phase 5).
        self.pll_sys = RPPLL(self, "PLL_SYS_BASE", 0)
        self.pll_usb = RPPLL(self, "PLL_USB_BASE", 1)
        self.resets = RPReset(self, "RESETS_BASE")
        self.psm = RPPSM(self, "PSM_BASE")
        self.pads_bank0 = RPPADS(self, "PADS_BANK0_BASE", "bank0", 0)
        self.pads_qspi = RPPADS(self, "PADS_QSPI_BASE", "qspi", 1)
        self.timer = RPTimer(self, "TIMER_BASE", 0)
        self.rtc = RP2040RTC(self, "RTC_BASE")
        self.clocks = RPClocks(self, "CLOCKS_BASE", 0)  # after the PLLs and the blocks it drives (PPB, PWM, watchdog, RTC): the native tree points at their C++ blocks
        self.busctrl = RPBUSCTRL(self, "BUSCTRL_BASE")
        self.syscfg = RP2040SysCfg(self, "SYSCFG")
        self.xip_ctrl = RPXIPCtrl(self, "XIP_CTRL_BASE")
        self.ssi = RPSSI(self, "SSI")
        self.xosc = RPXOSC(self, "XOSC_BASE", 0)
        self.io_bank0 = RPIO(self, "IO_BANK0_BASE", None, 0)
        self.io_qspi = RPIO(self, "IO_QSPI_BASE", self.qspi, 1)
        self.rosc = RPROSC(self, "ROSC_BASE", 0)

        self.peripherals = {
            0x14000: self.xip_ctrl,
            0x50400: RPXIPAux(self, "XIP_AUX_BASE"),
            0x18000: self.ssi,
            0x40000: RP2040SysInfo(self, "SYSINFO_BASE"),
            0x40004: self.syscfg,
            0x40008: self.clocks,
            0x4000C: self.resets,
            0x40010: self.psm,
            0x40014: self.io_bank0,
            0x40018: self.io_qspi,
            0x4001C: self.pads_bank0,
            0x40020: self.pads_qspi,
            0x40024: self.xosc,
            0x40028: self.pll_sys,
            0x4002C: self.pll_usb,
            0x40030: self.busctrl,
            0x40034: self.uart[0],
            0x40038: self.uart[1],
            0x4003C: self.spi[0],
            0x40040: self.spi[1],
            0x40044: self.i2c[0],
            0x40048: self.i2c[1],
            0x4004C: self.adc,
            0x40050: self.pwm,
            0x40054: self.timer,
            0x40058: self.watchdog,
            0x4005C: self.rtc,
            0x40060: self.rosc,
            0x40064: self.vreg_and_chip_reset,
            0x4006C: RPTBMAN(self, "TBMAN_BASE"),
            0x50000: self.dma,
            0x50110: self.usb_ctrl,
            0x50200: self.pio[0],
            0x50300: self.pio[1],
        }

        # The pins read the native SIO / PIO / PWM registers directly (no Python call per level evaluation)
        self._bind_pin_sources()

        # Debugging
        self.on_break = self._default_on_break
        # Set via the `simulator` property below, by the owning Simulator's own __init__ - see
        # _rp2040.py's identical field for the full rationale.
        self._simulator = None

        # RUN pin / RESET button (0089 Phase 4) - see _rp2040.py's identical fields for the full
        # rationale, including the schematic that makes a press a level rather than a pulse.
        self._run_pin_low = False
        self.on_run_pin_held = self._default_run_pin_held
        self.on_run_pin_reset = self._default_run_pin_reset

        self.reset()

    def enter_reset(self, *, from_watchdog: bool | None = None, cause: ResetCause | None = None) -> None:
        """Put the chip *into* reset: every block this reset covers goes to its reset values, the cause is
        recorded, and the USB controller's `on_reset` fires so whatever is attached to the bus clears itself.

        The chip-level half of a reset, owned by the chip (0057's option B). `BaseDevice` used to
        run this sequence itself, which meant anything holding a bare `RP2040` - the `run`
        subcommand, a board file, an `ExternalDevice` that only ever gets `attach(rp2040)` - could
        reset the chip and leave the host side of the USB link stale. What the device layer still
        owns is the awaitable form, which needs things this object does not have.

        `cause` is why (`ResetCause`; default `RUN_PIN`, what a host-side "reset the board" is physically,
        0089's D4). It picks the WDSEL-gated domain set (`WATCHDOG` is the only one that does; see `reset()`)
        and what firmware reads back afterwards (0089 section 1.3): a `RUN_PIN` / `POWER_ON` reset clears the
        watchdog's REASON / SCRATCH and sets CHIP_RESET to `HAD_RUN` / `HAD_POR`; a `WATCHDOG` reset leaves both
        alone. The chip records it itself, *after* the reset (so the wider reset cannot wipe it), so a chip with
        no device layer reports the same cause a device does (docs/records/0096-cpp-mcu-core.md, step 1, F1).
        `from_watchdog=True` is the older spelling of `cause=ResetCause.WATCHDOG`; giving both and
        contradicting them is an error. The `on_reset` notification is **not** gated by the cause: the USB
        block is only register-reset when WDSEL selects it, but a chip reset drops the device off the host's bus
        either way, and `hard_reset()`'s guarantee (0089) must not become conditional on what a guest wrote there.
        Does not start execution again - `leave_reset()` is that, and the RUN pin needs the two
        separable because a held button stays in the first."""
        if cause is None:
            cause = ResetCause.WATCHDOG if from_watchdog else ResetCause.RUN_PIN
        elif from_watchdog is not None and from_watchdog != (cause is ResetCause.WATCHDOG):
            raise ValueError(f"enter_reset(from_watchdog={from_watchdog}, cause={cause}): they disagree")
        # The cascade, the USB link told and the cause recorded are the `Mcu`'s (the cause is recorded *after* the reset, so the wider reset cannot wipe it).
        if not self._mcu.enter_reset_code(_CAUSE_CODES[cause], self.psm.wdsel, self.resets.wdsel, self._reset_world_mask()):
            raise_if_pending()
        raise_if_pending()

    def leave_reset(self) -> None:
        """Release the chip: point the core at flash's entry point, where the bootrom would have
        left it, and let it run. The other half of `enter_reset()`."""
        self._mcu.leave_reset()

    def _default_on_break(self, code: int) -> None:
        # TODO: raise HardFault exception
        pass

    def _default_run_pin_held(self):
        """RUN pulled low with nothing installed over it: the chip resets itself. See
        _rp2040.py's twin."""
        self.enter_reset()

    def _default_run_pin_reset(self) -> None:
        """RUN released with nothing installed over it: the chip boots. The counterpart of
        `_default_run_pin_held()`."""
        self.leave_reset()

    @property
    def run_pin_low(self):
        """True while RUN is held low - the chip is in reset and executes nothing at all. Read
        once per batch by execute_batch() (native/_simulator.pyx). See _rp2040.py's twin."""
        return self._run_pin_low

    def set_run_pin(self, *, low: bool) -> None:
        """Drive the RUN pin, as a RESET button does. Both edges fire: `low=True` (press) fires
        `on_run_pin_held` - the chip enters reset and drops off the USB bus - and `low=False`
        (release) fires `on_run_pin_reset`, which boots it. Idempotent per level.
        See _rp2040.py's twin for the full contract - the two must not drift."""
        if low == self._run_pin_low:
            return
        self._run_pin_low = low
        if low:
            self.on_run_pin_held()
        else:
            self.on_run_pin_reset()

    @property
    def simulator(self):
        return self._simulator

    @simulator.setter
    def simulator(self, value) -> None:
        self._simulator = value

    def schedule_threadsafe(self, fn_or_coro) -> None:
        """Hands `fn_or_coro` off to the engine-room loop of whichever Simulator owns this RP2040 -
        see Simulator.schedule_threadsafe() for the full contract (docs/CYW43_WIFI_BACKLOG.md's
        "Concurrency model" section). Raises RuntimeError if this RP2040 has no owning Simulator."""
        if self._simulator is None:
            raise RuntimeError("RP2040.schedule_threadsafe() called on an RP2040 with no owning Simulator")
        self._simulator.schedule_threadsafe(fn_or_coro)

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

    def reset(self, *, preserve_flash: bool = False, from_watchdog: bool = False) -> None:
        """Reset the chip: the core, and every block the reset actually covers.

        `preserve_flash=True` is for a live reset (a RESET button, a guest `machine.reset()`, a
        host-side `hard_reset()`) - unlike the construction-time/`load_bootrom()` calls, that must
        not erase the firmware/filesystem currently running from flash.

        `from_watchdog=True` is what makes this faithful rather than blunt
        (docs/records/0089-one-reset-for-every-trigger.md, D5 and Phase 5). A RUN-pin or power-on
        reset covers everything; a **watchdog** reset covers only what the guest selected, via two
        registers this emulator previously stored without acting on:

        - `PSM.WDSEL` - power-manager domains (SIO, CLOCKS, the RESETS controller itself, ...).
        - `RESETS.WDSEL` - individual peripheral blocks.

        The indirect route is the one that does the work in practice: pico-sdk's
        `watchdog_reboot()` sets `PSM.WDSEL` to "everything apart from ROSC and XOSC" and never
        touches `RESETS.WDSEL`, so the peripherals are reset because the **RESETS controller** is,
        and its own reset state holds every peripheral in reset. Both routes are honoured below.

        Two things deliberately survive every path:

        - **SRAM** (0089's D6): a PSM reset resets the SRAM *controllers*, not the array, and
          firmware re-initialises what it relies on. `bootrom` likewise - it is ROM.
        - **Wiring, everywhere.** Each block's own `reset()` restores registers but not callbacks,
          GPIO listeners, DMA/DREQ identity or analog inputs: a chip reset does not unsolder the
          LED, unplug the UART consumer or change the voltage on ADC0. That split is what makes
          resetting in place safe for the `ExternalDevice`s and the `USBCDC` holding references
          into this object.

        What is still *not* reset, split by why:

        - **`rosc`** - not a special case any more: a `PSM.WDSEL` domain like XOSC, handled by the same gate.
        - **`xosc`** - not a special case at all any more: it is a `PSM.WDSEL` domain like SIO and
          CLOCKS, so the existing gate gets it exactly right on both paths. `watchdog_reboot()`
          selects "everything apart from ROSC and XOSC" - the oscillators produce the clock the
          reset logic and the booting CPU run on, and stopping them mid-reboot would stop the
          chip's own clock - while a RUN-pin/power-on reset has no such exemption and does reset it.
        - **The TIMER's count** - it reads the simulation clock, which is shared with USB SOF and
          every other peripheral's alarms and must not be rewound. The block keeps its own epoch
          instead, so the count does restart; what is not modelled is anything else reading that
          clock noticing.

        `SYSINFO`/`TBMAN` are covered by not needing it - they hold no instance state at
        all, so `BasePeripheral`'s default no-op is their correct implementation.
        """
        if not self._mcu.reset(1 if from_watchdog else 0, 1 if preserve_flash else 0, self.psm.wdsel, self.resets.wdsel, self._reset_world_mask()):
            raise_if_pending()
        raise_if_pending()

    def _reset_world_mask(self):
        """Which blocks of the cascade the chip does not hold itself (an oracle's pure-Python reference, a test's stand-in): bit `block` of the mask, for the `Mcu` to hand to `_reset_block_python`."""
        cdef uint64_t mask = 0
        for block, getter in _RESET_BLOCKS:
            if not getattr(getter(self), "_from_mcu", False):
                mask |= (<uint64_t> 1) << block
        # a pin bank the host holds takes its IO / PADS blocks with it
        if not getattr(self._gpio_bank, "_from_mcu", False):
            mask |= ((<uint64_t> 1) << kMcuBlockIoBank0) | ((<uint64_t> 1) << kMcuBlockPadsBank0)
        if not getattr(self._qspi_bank, "_from_mcu", False):
            mask |= ((<uint64_t> 1) << kMcuBlockIoQspi) | ((<uint64_t> 1) << kMcuBlockPadsQspi)
        return mask

    def _reset_block_python(self, block):
        """The reference's reset of one block of the cascade, for a block the chip does not hold (the `Mcu` calls it instead of resetting its own)."""
        if block == kMcuBlockPpb:
            self.ppb.reset()
        elif block == kMcuBlockSio:
            self.sio.reset()
        elif block == kMcuBlockClocks:
            self.clocks.reset()
            self.clocks.reset_tree()
        elif block == kMcuBlockIoBank0:
            for pin in self.gpio:
                pin.reset(pads=False)
            self.io_bank0.reset()
        elif block == kMcuBlockPadsBank0:
            for pin in self.gpio:
                pin.reset(io=False)
            self.pads_bank0.reset()
        elif block == kMcuBlockIoQspi:
            for qspi_pin in self.qspi:
                qspi_pin.reset(pads=False)
            self.io_qspi.reset()
        elif block == kMcuBlockPadsQspi:
            # PADS_QSPI does not share BANK0's reset value - GPIO_QSPI_SS's pull-up is what holds the flash deselected and what makes BOOTSEL readable (record 0050)
            for qspi_pin, pad_reset in zip(self.qspi, QSPI_PAD_RESET_VALUES):
                qspi_pin.reset(io=False, pad_reset=pad_reset)
            self.pads_qspi.reset()
        elif block == kMcuBlockPwm:
            self.pwm.reset()
        elif block == kMcuBlockDma:
            self.dma.reset()
        elif block == kMcuBlockAdc:
            self.adc.reset()
        elif block == kMcuBlockTimer:
            self.timer.reset()
        elif block == kMcuBlockUart0:
            self.uart[0].reset()
        elif block == kMcuBlockUart1:
            self.uart[1].reset()
        elif block == kMcuBlockSpi0:
            self.spi[0].reset()
        elif block == kMcuBlockSpi1:
            self.spi[1].reset()
        elif block == kMcuBlockI2c0:
            self.i2c[0].reset()
        elif block == kMcuBlockI2c1:
            self.i2c[1].reset()
        elif block == kMcuBlockPio0:
            self.pio[0].reset()
        elif block == kMcuBlockPio1:
            self.pio[1].reset()
        elif block == kMcuBlockUsb:
            self.usb_ctrl.reset()
        elif block == kMcuBlockRtc:
            self.rtc.reset()
        elif block == kMcuBlockBusctrl:
            self.busctrl.reset()
        elif block == kMcuBlockSyscfg:
            self.syscfg.reset()
        elif block == kMcuBlockXosc:
            self.xosc.reset()
        elif block == kMcuBlockRosc:
            self.rosc.reset()
        elif block == kMcuBlockXip:
            self.xip_ctrl.reset()
        elif block == kMcuBlockSsi:
            self.ssi.reset()
        elif block == kMcuBlockWatchdog:
            self.watchdog.reset()

    def update_clocks(self):
        """Re-derives `clk_sys`/`clk_peri` from the PLL and CLOCKS registers and retunes what runs from them (see `peripherals.clocks.update_clocks`)."""
        self.clocks.update()

    def add_clock_listener(self, listener):
        """Calls `listener(clk_sys, old_clk_sys)` whenever clk_sys changes - for anything that derives a rate from it (a PIO clock divider, say). Returns the
        function that unsubscribes it."""
        self._clock_listeners.add(listener)

        def unsubscribe():
            self._clock_listeners.discard(listener)

        return unsubscribe

    cpdef unsigned int read_uint32(self, long long address) except? 0:
        cdef unsigned int word = self._mcu.bus.read32(<unsigned int> (address & 0xFFFFFFFFU))
        raise_if_pending()
        return word

    def _bind_pin_sources(self):
        """Points the chip's native pin banks at the registers of the native SIO / PIO / PWM blocks, where there are such (core/pin.hpp's direct sources); callable again whenever one is replaced."""
        # A PWM or a PIO that is the host's own (not the `Mcu`'s) is told of a pin input that moved by the host, not inside (`Mcu::set_pwm_world` / `set_pio_world`).
        pwm_block = self._pwm_block  # (a cdef field: not reachable by getattr)
        if pwm_block is not None:
            self._mcu.set_pwm_world(not getattr(pwm_block, "_from_mcu", False))
        pio_blocks = self._pio_blocks
        if pio_blocks is not None:
            for index in range(2):
                self._mcu.set_pio_world(index, not (index < len(pio_blocks) and getattr(pio_blocks[index], "_from_mcu", False)))
        for bank in (getattr(self, "_gpio_bank", None), getattr(self, "_qspi_bank", None)):
            if isinstance(bank, _NativeGPIOPinBank):  # a pure-Python bank (an oracle's reference pins) reads the blocks itself
                bind_pin_sources(bank, self)

    # The pins read the registers of these three blocks directly (see `_bind_pin_sources`), so replacing one has to re-point them: every way of replacing is a property or a list that does it.
    @property
    def pwm(self):
        return self._pwm_block

    @pwm.setter
    def pwm(self, value) -> None:
        self._pwm_block = value
        self._bind_pin_sources()
        self._rebind_clock_sinks()

    @property
    def pio(self):
        return self._pio_blocks

    @pio.setter
    def pio(self, value) -> None:
        self._pio_blocks = _PinSourceList(value, self)
        self._bind_pin_sources()

    @property
    def sio(self):
        return self._sio

    @sio.setter
    def sio(self, value) -> None:
        self._sio = value
        self._mcu.bus.set_sio(self._direct_handler(value, True))
        self._bind_pin_sources()

    @property
    def ppb(self):
        return self._ppb

    @ppb.setter
    def ppb(self, value) -> None:
        self._ppb = value
        self._mcu.bus.set_ppb(self._direct_handler(value, False))
        self._rebind_clock_sinks()

    # The clock tree tells these five (with the DMA) of a change directly when they are native blocks (core/clock_sinks.hpp), so replacing one has to re-point the tree: every way of replacing is a property.
    @property
    def watchdog(self):
        return self._watchdog

    @watchdog.setter
    def watchdog(self, value) -> None:
        self._watchdog = value
        self._rebind_clock_sinks()

    @property
    def clocks(self):
        return self._clocks

    @clocks.setter
    def clocks(self, value) -> None:
        old = self._clocks
        self._clocks = value
        if old is not None and old is not value and hasattr(old, "release_consumers"):
            old.release_consumers()  # (the blocks it drove go back to asking the chip, and it stops telling them)

    @property
    def xosc(self):
        return self._xosc

    @xosc.setter
    def xosc(self, value) -> None:
        self._xosc = value
        self._rebind_clock_sinks()

    @property
    def rosc(self):
        return self._rosc

    @rosc.setter
    def rosc(self, value) -> None:
        self._rosc = value
        self._rebind_clock_sinks()

    @property
    def xosc_freq(self):
        """The board's crystal (12 MHz on a Pico): a parameter of the world, which the clock tree reads (`RPClocks.set_crystal_hz`)."""
        return self._xosc_freq

    @xosc_freq.setter
    def xosc_freq(self, value) -> None:
        self._xosc_freq = value
        clocks = self._clocks
        if clocks is not None and hasattr(clocks, "set_crystal_hz"):
            clocks.set_crystal_hz(value)
            self.update_clocks()

    @property
    def rosc_freq(self):
        """The ring oscillator's output: the frequency its block last announced (a native tree reads it from the C++ block, and nothing writes this any more); a reference block that
        assigns it (an oracle's pure-Python ROSC) is read back as assigned."""
        rosc = self._rosc
        return getattr(rosc, "_hz", self._rosc_freq) if rosc is not None else self._rosc_freq

    @rosc_freq.setter
    def rosc_freq(self, value) -> None:
        self._rosc_freq = value

    @property
    def dma(self):
        return self._dma

    @dma.setter
    def dma(self, value) -> None:
        self._dma = value
        # The DMA requests of the blocks go to `Mcu::set_dreq` (core/mcu.hpp). The chip's own native DMA is attached to it, so a request reaches its C++ block with no Python in
        # between; anything else (a recorder, a test double, a pure-Python DMA) gets the requests through a hook, instead of the DMA, as it always did.
        if type(value) is RPDMA:
            self._mcu.set_dreq_hook(NULL, NULL)
            self._mcu.attach_dma((<RPDMA> value)._block)
        else:
            self._mcu.attach_dma(NULL)
            self._mcu.set_dreq_hook(_dreq_hook, <void*> self)
        self._rebind_clock_sinks()
        self._install_dreq_tap()

    @property
    def dreq_tap(self):
        """An observer of the DMA requests that reach the chip's DMA: `f(number, asserted)`, called for every request, the redundant ones too, before the DMA gets it (the DMA still does). `None` takes it away.
        Same on the pure-Python chip; it is how a test or a debugger sees the order of the requests without standing in for the DMA."""
        return self._dreq_tap

    @dreq_tap.setter
    def dreq_tap(self, value) -> None:
        self._dreq_tap = value
        self._install_dreq_tap()

    cdef _install_dreq_tap(self):
        # Only the chip's own native DMA has the C++ tap; a stand-in for the DMA (a recorder) receives the requests itself and has no use for it.
        if type(self._dma) is RPDMA:
            if self._dreq_tap is None:
                (<RPDMA> self._dma)._block.dreq_tap = NULL
            else:
                (<RPDMA> self._dma)._block.dreq_tap = _dreq_tap_hook
                (<RPDMA> self._dma)._block.dreq_tap_ctx = <void*> self

    @property
    def rtc(self):
        return self._rtc

    @rtc.setter
    def rtc(self, value) -> None:
        self._rtc = value
        self._rebind_clock_sinks()

    def _rebind_clock_sinks(self):
        clocks = self._clocks  # (None while the chip is being built)
        if clocks is not None and hasattr(clocks, "rebind_consumers"):
            clocks.rebind_consumers()

    def _native_pio_address(self, index):
        """The address of this chip's PIO block `index` (0 or 1), for the native PIO shell that is its Python view; None if there is no such block."""
        cdef PioBlock* block = self._mcu.pio_block(index)
        return <size_t> block if block != NULL else None

    @staticmethod
    def _native_run_flags(chip):
        """The chip's stop byte and its "a device waits on the real world" byte as two one-byte buffers (what `Simulator._stop_flag` / `_real_io_flag` are over a native chip): the
        batch loop reads the same bytes on every iteration. Looked up on the TYPE, so a stand-in for the chip does not answer. The buffers do not keep the chip alive: whoever holds
        them (the Simulator) holds the chip too."""
        cdef RP2040 rp = <RP2040> chip
        cdef unsigned char[::1] stop = <unsigned char[:1]> rp._mcu.stop_address()
        cdef unsigned char[::1] real_io = <unsigned char[:1]> rp._mcu.real_io_address()
        return stop, real_io

    def _native_bus_address(self):
        """The address of this chip's C++ bus, for a native block that works on it directly (the DMA). Looked up as an
        attribute of the chip, so a chip without a C++ bus (the pure-Python one) simply does not answer."""
        return <size_t> &self._mcu.bus

    cdef WindowHandler _direct_handler(self, object block, bint is_sio):
        """The handler the bus calls for SIO or the PPB. A block with `_native_window` on its TYPE lends its own C++
        functions (a recorder/profiler that forwards attributes must still see every access, and a Mock must not answer
        for every name); anything else is reached through a Python trampoline that looks its methods up on every call."""
        cdef WindowHandler handler
        cdef _PythonWindow owner
        native_window = getattr(type(block), "_native_window", None)
        if native_window is not None:
            read_ptr, write_ptr, ctx_ptr = native_window(block)
            handler.read32 = <Read32Fn> <size_t> read_ptr
            handler.write32 = <Write32Fn> <size_t> write_ptr
            handler.ctx = <void*> <size_t> ctx_ptr
            owner_object = block  # keeps the block (and so the context pointer) alive
        else:
            owner = _PythonWindow(block)
            handler.read32 = _python_sio_read32 if is_sio else _python_window_read32
            handler.write32 = _python_direct_write32
            handler.ctx = <void*> owner
            owner_object = owner
        if is_sio:
            self._sio_owner = owner_object
        else:
            self._ppb_owner = owner_object
        return handler

    @property
    def peripherals(self):
        return self._peripherals

    @peripherals.setter
    def peripherals(self, value) -> None:
        # Whatever mapping is assigned becomes the chip's table (and the window registry is rebuilt from
        # it), so `chip.peripherals = {...}` keeps meaning what it always did.
        self._clear_windows()
        self._peripherals = _PeripheralTable(self, value)

    def _attach_window(self, key, peripheral) -> None:
        """Mirrors `peripherals[key] = peripheral` into the C++ window registry. `key` is the bus's own
        dictionary key, `(address >> 14) << 2`; one that no address can produce was never reachable and is
        not registered."""
        cdef WindowHandler handler
        cdef _PythonWindow owner
        cdef unsigned long long k
        if not isinstance(key, int) or key < 0 or key >= (1 << 20) or (key & 3):
            return
        k = key
        # A native block (a C++ block behind a Cython shell, e.g. the TIMER) offers its own C++ read/write functions
        # through `_native_window`, and the window is served without any Python on the access path. Looked up on the
        # TYPE, never the instance: a recorder or profiler that forwards attributes with __getattr__ must not lend its
        # target's fast path (it would be bypassed), and a Mock must not answer for every name.
        native_window = getattr(type(peripheral), "_native_window", None)
        if native_window is not None:
            read_ptr, write_ptr, ctx_ptr = native_window(peripheral)
            handler.read32 = <Read32Fn> <size_t> read_ptr
            handler.write32 = <Write32Fn> <size_t> write_ptr
            handler.ctx = <void*> <size_t> ctx_ptr
            self._window_owners[k] = peripheral  # keeps the block (and so the context pointer) alive
        else:
            owner = _PythonWindow(peripheral)
            self._window_owners[k] = owner
            handler.read32 = _python_window_read32
            handler.write32 = _python_window_write32
            handler.ctx = <void*> owner
        self._mcu.bus.windows.attach(<uint32_t> (k << 12), handler)

    def _detach_window(self, key) -> None:
        if not isinstance(key, int) or key < 0 or key >= (1 << 20) or (key & 3):
            return
        self._mcu.bus.windows.detach(<uint32_t> (<unsigned long long> key << 12))
        self._window_owners.pop(key, None)

    def _clear_windows(self) -> None:
        self._mcu.bus.windows.clear()
        self._window_owners.clear()

    def find_peripheral(self, address):
        return self.peripherals.get((u32(address) >> 14) << 2)

    cpdef unsigned int read_uint16(self, long long address):
        """We assume the address is 16-bit aligned."""
        cdef unsigned int value = self._mcu.bus.read16(<unsigned int> (address & 0xFFFFFFFFU))
        raise_if_pending()
        return value

    cpdef unsigned int read_uint8(self, long long address):
        cdef unsigned int value = self._mcu.bus.read8(<unsigned int> (address & 0xFFFFFFFFU))
        raise_if_pending()
        return value

    cpdef write_uint32(self, long long address, long long value):
        # The handlers get the ORIGINAL `value`, not a masked copy: sio.py's divider emulation relies on the true signed
        # Python int (see core/bus.hpp).
        self._mcu.bus.write32(<unsigned int> (address & 0xFFFFFFFFU), value)
        raise_if_pending()

    cpdef write_uint8(self, long long address, long long value):
        self._mcu.bus.write8(<unsigned int> (address & 0xFFFFFFFFU), <unsigned int> (value & 0xFF))
        raise_if_pending()

    cpdef write_uint16(self, long long address, long long value):
        # we assume that address is 16-bit aligned.
        # Ideally we should generate a fault if not!
        self._mcu.bus.write16(<unsigned int> (address & 0xFFFFFFFFU), <unsigned int> (value & 0xFFFF))
        raise_if_pending()

    @property
    def gpio_values(self) -> int:
        return self._gpio_bank.input_values()

    def _native_resets_address(self):
        return <size_t> &self._mcu.resets()

    def _native_psm_address(self):
        return <size_t> &self._mcu.psm()

    def _native_busctrl_address(self):
        """The address of the BUSCTRL block the `Mcu` built, for the shell that is its Python view."""
        return <size_t> &self._mcu.busctrl()

    def _native_vreg_address(self):
        """The address of the VREG block the `Mcu` built, for the shell that is its Python view."""
        return <size_t> &self._mcu.vreg()

    def _native_sysinfo_address(self):
        """The address of the SYSINFO block the `Mcu` built, for the shell that is its Python view."""
        return <size_t> &self._mcu.sysinfo()

    def _native_syscfg_address(self):
        """The address of the SYSCFG block the `Mcu` built, for the shell that is its Python view."""
        return <size_t> &self._mcu.syscfg()

    def _native_timer_address(self):
        """The address of the TIMER block the `Mcu` built, for the shell that is its Python view."""
        return <size_t> &self._mcu.timer()

    def _native_adc_address(self):
        return <size_t> &self._mcu.adc()

    def _native_uart_address(self, index):
        return <size_t> &self._mcu.uart(<uint32_t> index)

    def _native_spi_address(self, index):
        return <size_t> &self._mcu.spi(<uint32_t> index)

    def _native_i2c_address(self, index):
        return <size_t> &self._mcu.i2c(<uint32_t> index)

    # The chip's two pin banks: the `Mcu`'s own (views of its C++ banks) or a host's (an oracle's reference banks). SIO reads the pins' inputs and updates the pins in C++ for the first kind and asks the
    # host for the second.
    @property
    def _gpio_bank(self):
        return self._gpio_bank_object

    @_gpio_bank.setter
    def _gpio_bank(self, bank) -> None:
        self._gpio_bank_object = bank
        self._mcu.set_pin_bank_world(0, not getattr(bank, "_from_mcu", False))

    @property
    def _qspi_bank(self):
        return self._qspi_bank_object

    @_qspi_bank.setter
    def _qspi_bank(self, bank) -> None:
        self._qspi_bank_object = bank
        self._mcu.set_pin_bank_world(1, not getattr(bank, "_from_mcu", False))

    @property
    def clk_peri(self):
        """The peripheral clock, in Hz: the C++ clock tree's value (the UARTs' and SPIs' bit times follow it). Assigning sets that value, as a host or a test that fixes the rate does."""
        return self._mcu.clocks().clk_peri()

    @clk_peri.setter
    def clk_peri(self, value) -> None:
        self._mcu.clocks().force_clk_peri(<double> value)

    def _native_pins_address(self, bank):
        return <size_t> &self._mcu.pins(<uint32_t> bank)

    def _native_io_address(self, bank):
        return <size_t> &self._mcu.io(<uint32_t> bank)

    def _native_pads_address(self, bank):
        return <size_t> &self._mcu.pads(<uint32_t> bank)

    def _native_ssi_address(self):
        return <size_t> &self._mcu.ssi()

    def _native_usb_address(self):
        return <size_t> &self._mcu.usb()

    def _native_xip_address(self):
        return <size_t> &self._mcu.xip()

    # The SSI is the `Mcu`'s own, or a host's (an oracle's reference): the `Mcu`'s listens to the chip-select pin and gives the XIP stream its BAUDR; a host's does both itself.
    @property
    def ssi(self):
        return self._ssi_object

    @ssi.setter
    def ssi(self, block) -> None:
        self._ssi_object = block
        self._mcu.set_ssi_world(not getattr(block, "_from_mcu", False))

    def _native_pwm_address(self):
        return <size_t> &self._mcu.pwm()

    def _native_sio_address(self):
        return <size_t> &self._mcu.sio()

    def _native_ppb_address(self, core=0):
        return <size_t> &self._mcu.ppb(<uint8_t> core)

    def _native_watchdog_address(self):
        return <size_t> &self._mcu.watchdog()

    def _native_dma_address(self):
        return <size_t> &self._mcu.dma()

    def _native_clocks_address(self):
        """The address of the CLOCKS block the `Mcu` built (the tree), for the shell that is its Python view."""
        return <size_t> &self._mcu.clocks()

    def _native_pll_address(self, index):
        return <size_t> &self._mcu.pll(<uint32_t> index)

    def _native_xosc_address(self):
        return <size_t> &self._mcu.xosc()

    def _native_rosc_address(self):
        return <size_t> &self._mcu.rosc()

    def _native_tree(self, native):
        """Which tree the oscillators and the PLLs tell: the `Mcu`'s own (`native`), or the chip's Python `update_clocks()` (a tree that replaced it)."""
        if native:
            self._mcu.set_tree_hook(NULL, NULL)
        else:
            self._mcu.set_tree_hook(_tree_hook, <void*> self)

    def _native_tbman_address(self):
        """The address of the TBMAN block the `Mcu` built, for the shell that is its Python view."""
        return <size_t> &self._mcu.tbman()

    def _native_rtc_address(self):
        """The address of the RTC block the `Mcu` built, for the shell that is its Python view."""
        return <size_t> &self._mcu.rtc()

    def _native_mcu_address(self):
        """The address of this chip's C++ `Mcu`, for a native block that raises its interrupt line straight on it. Looked up as an attribute of the chip, so a chip without one
        (the pure-Python chip, a stand-in) simply does not answer."""
        return <size_t> &self._mcu

    def _native_set_interrupt(self, irq: int, value: bool) -> None:
        if not self._mcu.set_interrupt_unhooked(<uint32_t> irq, True if value else False):  # (not through a hook: a spy calls this to let the line through) every NVIC (core 1's once it runs) gets the system lines; waking a core took an exception that failed
            raise_if_pending()

    def set_core1_enabled(self, on):
        """The inter-core FIFOs and the boot ROM's launch handshake (which the SIO answers): on by default; off gives back the old stub (the registers warn and read as 0xFFFFFFFF)."""
        self._mcu.set_core1_enabled(True if on else False)

    @property
    def core1_running(self):
        return self._mcu.core1_running()

    @property
    def set_interrupt(self):
        """The interrupt line entry point: `Mcu::set_interrupt` is the one the blocks call, and this is the Python method with the same effect. Assigning to it (a test that spies on
        the lines, a host that wants to see them) puts the assigned function *over* the CPU for every block: the line goes to it instead, as it always did on a chip whose
        `set_interrupt` was replaced; `del` takes it away again."""
        return self._irq_override if self._irq_override is not None else self._native_set_interrupt

    @set_interrupt.setter
    def set_interrupt(self, function) -> None:
        self._irq_override = function
        self._mcu.set_irq_hook(_irq_hook, <void*> self)

    @set_interrupt.deleter
    def set_interrupt(self) -> None:
        self._irq_override = None
        self._mcu.set_irq_hook(NULL, NULL)

    def update_io_interrupt(self) -> None:
        self.set_interrupt(IRQ.IO_BANK0, self._gpio_bank.any_irq_value())

    def step(self) -> None:
        self.core.execute_instruction()
