# cython: language_level=3, boundscheck=False, wraparound=False, cdivision=True, freethreading_compatible=True
"""The native RP2040 SIO: a Python-facing shell over the C++ `SioBlock` of `core/sio.hpp`
(docs/records/0096-cpp-mcu-core.md, Phase 2). `_sio.py` is the pure-Python reference, kept as the oracle; `sio.py` is the
facade that picks between them, as for the other native ports.

SIO is not a peripheral-table window (its range starts at 0xD0000000), so the chip learns about this block through its
`sio` property: when the object assigned has a `_native_window` on its type, the bus calls the block's C++ functions
directly - a spinlock, a GPIO_OUT_SET or an interpolator POP never touches Python on the access path.

What stays Python is the edge of the block, reached through trampolines whose failures are parked in the shared slot of
`_pending.pyx` and re-raised by whoever called in: the pins' input levels (`rp2040.gpio_values`, the QSPI pads), telling
the pads their SIO-driven state changed (`GPIOPin.check_for_updates`), charging the divider's cycles to the core, and the
logger. The messages are the pure block's, word for word.

The registers the rest of the chip and the tests read and write directly (`gpio_value`, `gpio_output_enable`, the QSPI
pair, the divider's operands) are properties; `interp0`/`interp1` are views of the C++ interpolators with the pure
`Interpolator`'s attribute names.
"""

from libc.stdint cimport int64_t, uint32_t
from libcpp cimport bool

from rp2040py.native._pending cimport park_error, pending_flag, raise_if_pending
from rp2040py.native._window_map cimport WindowHandler

__all__ = ("RPSIO",)

_GPIO_PINS = 30


cdef uint32_t _gpio_in_trampoline(void* ctx) noexcept:
    cdef RPSIO sio = <RPSIO> ctx
    try:
        return <uint32_t> (<int64_t> sio.rp2040.gpio_values)
    except BaseException as error:
        park_error(error)
        return 0


cdef uint32_t _qspi_in_trampoline(void* ctx) noexcept:
    cdef RPSIO sio = <RPSIO> ctx
    cdef uint32_t result = 0
    try:
        qspi = sio.rp2040.qspi
        for index in range(len(qspi)):
            if qspi[index].input_value:
                result |= (<uint32_t> 1) << index
    except BaseException as error:
        park_error(error)
        return 0
    return result


cdef bool _update_pins_trampoline(void* ctx, uint32_t pin_mask, bool qspi) noexcept:
    cdef RPSIO sio = <RPSIO> ctx
    try:
        gpio = sio.rp2040.qspi if qspi else sio.rp2040.gpio
        for index in range(len(gpio)):
            if index < 32 and pin_mask & ((<uint32_t> 1) << index):
                gpio[index].check_for_updates()
    except BaseException as error:
        park_error(error)
        return False
    return True


cdef void _cycles_trampoline(void* ctx, uint32_t cycles) noexcept:
    cdef RPSIO sio = <RPSIO> ctx
    try:
        sio.rp2040.core.cycles += cycles
    except BaseException as error:
        park_error(error)


cdef void _ports_warn(void* chip, uint32_t block, uint32_t kind, uint32_t offset, int64_t value) noexcept:
    cdef RPSIO sio = <RPSIO> chip
    try:
        sio._warn_kind(kind, offset, value)
    except BaseException as error:
        park_error(error)


cdef bool _ports_irq(void* chip, uint32_t line, bool level) noexcept:
    # a block made on its own (a stand-in chip, a test): the FIFO lines go to the chip's Python `set_interrupt`, as a pure-Python SIO does
    cdef RPSIO sio = <RPSIO> chip
    try:
        sio.rp2040.set_interrupt(line, True if level else False)
    except BaseException as error:
        park_error(error)
        return False
    return True


cdef class RPSIO:
    def __init__(self, rp2040, mcu_index=-1):
        cdef SioHost host
        self.rp2040 = rp2040
        self.name = "SIO"
        self._from_mcu = mcu_index >= 0
        if mcu_index >= 0:
            # the chip's C++ `Mcu` holds this block (the pins' sources and the bus are already bound to it): this object is the Python view of it
            self._block = <SioBlock*> <size_t> rp2040._native_sio_address()
        else:
            self._block = &self._own
            host.gpio_in = _gpio_in_trampoline
            host.qspi_in = _qspi_in_trampoline
            host.update_pins = _update_pins_trampoline
            host.add_cycles = _cycles_trampoline
            host.ctx = <void*> self
            self._ports.chip = <void*> self
            self._ports.warn = _ports_warn
            self._ports.irq = _ports_irq
            self._ports.failed = pending_flag()
            self._own.init(&self._ports, 0, host)

    def _warn_kind(self, kind, offset, value):
        """What the block reports (a warning, or the failure of a division by zero), worded for the logger or raised (the chip's host calls this for the block it built)."""
        if kind == kSioWarnFifo:
            self.rp2040.logger.warning(
                "SIO",
                "Inter-core FIFO (0x50-0x58) is not implemented. core1/_thread is unsupported "
                "(see docs/records/0053-core1-and-inter-core-fifo.md)",
            )
        elif kind == kSioWarnReadInvalid:
            self.rp2040.logger.warning("SIO", f"Read from invalid SIO address: {offset:x}")
        elif kind == kSioWarnWriteInvalid:
            self.rp2040.logger.warning("SIO", f"Write to invalid SIO address: {offset:x}, value={value:x}")
        else:  # kSioFailDivideByZero: what `dividend / divisor` raises in the pure block for a divisor that is 0 as 32 bits
            raise ZeroDivisionError("division by zero")

    # --- the pure block's attributes --------------------------------------------------------------

    @property
    def gpio_value(self):
        return self._block.gpio_value

    @gpio_value.setter
    def gpio_value(self, value):
        self._block.gpio_value = <uint32_t> (<int64_t> value)

    @property
    def gpio_output_enable(self):
        return self._block.gpio_output_enable

    @gpio_output_enable.setter
    def gpio_output_enable(self, value):
        self._block.gpio_output_enable = <uint32_t> (<int64_t> value)

    @property
    def qspi_gpio_value(self):
        return self._block.qspi_gpio_value

    @qspi_gpio_value.setter
    def qspi_gpio_value(self, value):
        self._block.qspi_gpio_value = <uint32_t> (<int64_t> value)

    @property
    def qspi_gpio_output_enable(self):
        return self._block.qspi_gpio_output_enable

    @qspi_gpio_output_enable.setter
    def qspi_gpio_output_enable(self, value):
        self._block.qspi_gpio_output_enable = <uint32_t> (<int64_t> value)

    def snapshot(self):
        """The block's state in one call: the four GPIO words, core 0's divider, the spin locks and both interpolators (`i0.accum0` ...)."""
        cdef SioBlock* b = self._block
        cdef int index
        state = {
            "gpio_value": b.gpio_value,
            "gpio_output_enable": b.gpio_output_enable,
            "qspi_gpio_value": b.qspi_gpio_value,
            "qspi_gpio_output_enable": b.qspi_gpio_output_enable,
            "div_dividend": b.core(0).div_dividend,
            "div_divisor": b.core(0).div_divisor,
            "div_quotient": b.core(0).div_quotient,
            "div_remainder": b.core(0).div_remainder,
            "div_csr": b.core(0).div_csr,
            "spin_lock": b.spin_lock,
        }
        for index in range(2):
            state[f"i{index}.accum0"] = b.interp(index).accum0
            state[f"i{index}.accum1"] = b.interp(index).accum1
            state[f"i{index}.base0"] = b.interp(index).base0
            state[f"i{index}.base1"] = b.interp(index).base1
            state[f"i{index}.base2"] = b.interp(index).base2
            state[f"i{index}.ctrl0"] = b.interp(index).ctrl0
            state[f"i{index}.ctrl1"] = b.interp(index).ctrl1
            state[f"i{index}.result0"] = b.interp(index).result0
            state[f"i{index}.result1"] = b.interp(index).result1
            state[f"i{index}.result2"] = b.interp(index).result2
            state[f"i{index}.smresult0"] = b.interp(index).smresult0
            state[f"i{index}.smresult1"] = b.interp(index).smresult1
        return state

    # --- the pure block's methods ------------------------------------------------------------------

    def reset(self):
        self._block.reset()

    def update_hardware_divider(self, signed_division):
        if not self._block.update_hardware_divider(<bool> (1 if signed_division else 0)):
            raise_if_pending()

    def read_uint32(self, offset):
        """The register as the pure block returns it: an int, or the divider's fractional quotient as a float."""
        cdef double value = self._block.read_wide(<uint32_t> offset)
        raise_if_pending()
        if value == <int64_t> value:
            return <int64_t> value
        return value

    def write_uint32(self, offset, value):
        if not self._block.write32(<uint32_t> offset, <int64_t> value):
            raise_if_pending()

    # --- the native bus protocol ------------------------------------------------------------------

    def _native_window(self):
        """What `RP2040` registers for the SIO range instead of calling this object: the addresses of the block's own
        read/write functions and its context. Looked up on the *type* by the bus, so a recorder that forwards
        attributes is not bypassed."""
        cdef WindowHandler handler = self._block.window_handler()
        return (<size_t> handler.read32, <size_t> handler.write32, <size_t> handler.ctx)
