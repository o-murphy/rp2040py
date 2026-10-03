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

from rp2040py.native._pending cimport park_error, raise_if_pending
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


cdef bool _update_pins_trampoline(void* ctx, uint32_t pin_mask) noexcept:
    cdef RPSIO sio = <RPSIO> ctx
    try:
        gpio = sio.rp2040.gpio
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


cdef void _warn_trampoline(void* ctx, uint32_t kind, uint32_t offset, int64_t value) noexcept:
    cdef RPSIO sio = <RPSIO> ctx
    try:
        if kind == kSioWarnFifo:
            sio.rp2040.logger.warning(
                "SIO",
                "Inter-core FIFO (0x50-0x58) is not implemented. core1/_thread is unsupported "
                "(see docs/records/0053-core1-and-inter-core-fifo.md)",
            )
        elif kind == kSioWarnReadInvalid:
            sio.rp2040.logger.warning("SIO", f"Read from invalid SIO address: {offset:x}")
        elif kind == kSioWarnWriteInvalid:
            sio.rp2040.logger.warning("SIO", f"Write to invalid SIO address: {offset:x}, value={value:x}")
        else:  # kSioFailDivideByZero: what `dividend / divisor` raises in the pure block for a divisor that is 0 as 32 bits
            park_error(ZeroDivisionError("division by zero"))
    except BaseException as error:
        park_error(error)


cdef class _InterpolatorView:
    """One of the C++ interpolators with the attribute names of the pure `interpolator.Interpolator`."""

    def __cinit__(self, RPSIO owner, int index):
        self._owner = owner
        self._index = index

    @property
    def accum0(self):
        return self._owner._block.interp(self._index).accum0

    @accum0.setter
    def accum0(self, value):
        self._owner._block.interp(self._index).accum0 = <int64_t> value

    @property
    def accum1(self):
        return self._owner._block.interp(self._index).accum1

    @accum1.setter
    def accum1(self, value):
        self._owner._block.interp(self._index).accum1 = <int64_t> value

    @property
    def base0(self):
        return self._owner._block.interp(self._index).base0

    @base0.setter
    def base0(self, value):
        self._owner._block.interp(self._index).base0 = <int64_t> value

    @property
    def base1(self):
        return self._owner._block.interp(self._index).base1

    @base1.setter
    def base1(self, value):
        self._owner._block.interp(self._index).base1 = <int64_t> value

    @property
    def base2(self):
        return self._owner._block.interp(self._index).base2

    @base2.setter
    def base2(self, value):
        self._owner._block.interp(self._index).base2 = <int64_t> value

    @property
    def ctrl0(self):
        return self._owner._block.interp(self._index).ctrl0

    @ctrl0.setter
    def ctrl0(self, value):
        self._owner._block.interp(self._index).ctrl0 = <int64_t> value

    @property
    def ctrl1(self):
        return self._owner._block.interp(self._index).ctrl1

    @ctrl1.setter
    def ctrl1(self, value):
        self._owner._block.interp(self._index).ctrl1 = <int64_t> value

    @property
    def result0(self):
        return self._owner._block.interp(self._index).result0

    @property
    def result1(self):
        return self._owner._block.interp(self._index).result1

    @property
    def result2(self):
        return self._owner._block.interp(self._index).result2

    @property
    def smresult0(self):
        return self._owner._block.interp(self._index).smresult0

    @property
    def smresult1(self):
        return self._owner._block.interp(self._index).smresult1

    def update(self):
        self._owner._block.interp(self._index).update()

    def writeback(self):
        self._owner._block.interp(self._index).writeback()

    def set_base01(self, value):
        self._owner._block.interp(self._index).set_base01(<int64_t> value)

    def reset(self):
        self._owner._block.interp(self._index).reset()


cdef class RPSIO:
    def __init__(self, rp2040):
        cdef SioHost host
        self.rp2040 = rp2040
        self.name = "SIO"
        host.gpio_in = _gpio_in_trampoline
        host.qspi_in = _qspi_in_trampoline
        host.update_pins = _update_pins_trampoline
        host.add_cycles = _cycles_trampoline
        host.warn = _warn_trampoline
        host.ctx = <void*> self
        self._block.init(host)
        self._interp0 = _InterpolatorView(self, 0)
        self._interp1 = _InterpolatorView(self, 1)

    # --- the pure block's attributes --------------------------------------------------------------

    @property
    def interp0(self):
        return self._interp0

    @property
    def interp1(self):
        return self._interp1

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

    @property
    def div_dividend(self):
        return self._block.div_dividend

    @div_dividend.setter
    def div_dividend(self, value):
        self._block.div_dividend = <int64_t> value

    @property
    def div_divisor(self):
        return self._block.div_divisor

    @div_divisor.setter
    def div_divisor(self, value):
        self._block.div_divisor = <int64_t> value

    @property
    def div_quotient(self):
        return self._block.div_quotient

    @div_quotient.setter
    def div_quotient(self, value):
        self._block.div_quotient = <double> value

    @property
    def div_remainder(self):
        return self._block.div_remainder

    @div_remainder.setter
    def div_remainder(self, value):
        self._block.div_remainder = <int64_t> value

    @property
    def div_csr(self):
        return self._block.div_csr

    @div_csr.setter
    def div_csr(self, value):
        self._block.div_csr = <uint32_t> (<int64_t> value)

    @property
    def spin_lock(self):
        return self._block.spin_lock

    @spin_lock.setter
    def spin_lock(self, value):
        self._block.spin_lock = <uint32_t> (<int64_t> value)

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
