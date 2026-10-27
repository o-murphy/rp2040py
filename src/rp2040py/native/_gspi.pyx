# cython: language_level=3
"""The native gSPI bit shifter of the CYW43 model: a Python-facing shell over the C++ `GspiShifter` of `core/gspi.hpp`
(docs/records/0096-cpp-mcu-core.md, Phase 3). `external/cyw43/bus.py`'s `GSPIBus` stays the protocol - register access, SDPCM
framing, scan/join events, the NAT bridge - and keeps its pure-Python edge methods as the oracle and as the path when no native pins
are present; this object takes over only the *edges*.

The shifter is registered as a direct listener of the CLK and CS pins, so an edge never enters Python. What reaches Python is one call
per completed 32-bit word (`GSPIBus._on_word`, whose response bytes are moved into the C++ shifter) and one per CS change
(`GSPIBus._on_cs_change`, which also drives DATA with the chip's IRQ level, as before). Failures of those calls are parked in the shared
slot of `_pending.pyx` and re-raised by whoever caused the edge.

The sources of the three pins' level (both PIOs, SIO, the PWM) are the chip's own: the `Mcu` bound them to the pin banks when it was built, so reading CLK/DATA/CS
never makes a Python attribute read, and this object binds nothing.
"""

from libc.stdint cimport uint8_t, uint32_t
from libcpp cimport bool as cppbool

from rp2040py.native._gpio_pin cimport GPIOPin
from rp2040py.native._pending cimport park_error

__all__ = ("GspiShifter", "can_attach")


def can_attach(clk, data, cs):
    """True if all three pins are the native `GPIOPin`: the shifter reaches them as C++ objects."""
    return isinstance(clk, GPIOPin) and isinstance(data, GPIOPin) and isinstance(cs, GPIOPin)


cdef cppbool _word_trampoline(void* ctx, uint32_t word) noexcept:
    cdef GspiShifter shifter = <GspiShifter> ctx
    cdef bytes response
    cdef const uint8_t* data
    try:
        bus = shifter._bus
        bus._on_word(word)
        # `_start_response()` leaves the answer in the bus; the shifter owns it from here, so the bus is "not answering" in Python's eyes.
        response = bus._response_bytes
        if response:
            bus._response_bytes = b""
            bus._response_bit_index = 0
            data = <const uint8_t*> <char*> response
            if not shifter._core.start_response(data, <uint32_t> len(response)):
                raise ValueError(f"gSPI response of {len(response)} bytes does not fit the native shifter's buffer")
    except BaseException as error:
        park_error(error)
        return False
    return True


cdef cppbool _cs_trampoline(void* ctx, cppbool selected) noexcept:
    cdef GspiShifter shifter = <GspiShifter> ctx
    try:
        shifter._bus._on_cs_change(selected)
    except BaseException as error:
        park_error(error)
        return False
    return True


cdef class GspiShifter:
    def __init__(self, bus, GPIOPin clk, GPIOPin data, GPIOPin cs):
        cdef GspiHost host
        self._bus = bus
        self._clk = clk
        self._data = data
        self._cs = cs
        host.on_word = _word_trampoline
        host.on_cs = _cs_trampoline
        host.ctx = <void*> self
        if not self._core.attach(clk.bank_ptr(), data.bank_ptr(), cs.bank_ptr(), host, clk._slot, data._slot, cs._slot):
            raise RuntimeError("the CLK/CS pin has no room for another direct listener")
        self._attached = True
        # The pins call into this object by raw pointer: they keep it alive (and it keeps them alive - an ordinary reference cycle).
        clk._direct_refs.append(self)
        cs._direct_refs.append(self)

    def detach(self):
        """Unregisters from the CLK and CS pins (the pins' sources are the chip's own: the `Mcu` bound them when it was built)."""
        if self._attached:
            self._core.detach()
            self._attached = False

    def reset(self):
        """`GSPIBus.power_off()`'s share of the bit-level state: CS deasserted, nothing shifted, no response."""
        self._core.reset()

