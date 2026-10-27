# Cython view of core/gspi.hpp (docs/records/0096-cpp-mcu-core.md, Phase 3) and the declaration of the native gSPI shifter shell.
# Header-only C++17, compiled into every extension that cimports it (setup.py builds them all as C++).

from libc.stdint cimport uint8_t, uint32_t
from libcpp cimport bool as cppbool

from rp2040py.native._gpio_pin cimport GPIOPin
from rp2040py.native._pin cimport PinBank

cdef extern from "gspi.hpp" namespace "rp2040core":
    ctypedef cppbool (*GspiWordFn)(void* ctx, uint32_t word)
    ctypedef cppbool (*GspiCsFn)(void* ctx, cppbool selected)

    cdef cppclass GspiHost:
        GspiHost() noexcept
        GspiWordFn on_word
        GspiCsFn on_cs
        void* ctx

    cdef cppclass GspiShifterCore "rp2040core::GspiShifter":
        GspiShifterCore() noexcept
        cppbool attach(PinBank* clk, PinBank* data, PinBank* cs, const GspiHost& host, uint32_t clk_slot, uint32_t data_slot, uint32_t cs_slot) noexcept
        void detach() noexcept
        cppbool start_response(const uint8_t* bytes, uint32_t length) noexcept
        void reset() noexcept


cdef class GspiShifter:
    cdef GspiShifterCore _core
    cdef object _bus
    cdef object _clk
    cdef object _data
    cdef object _cs
    cdef bint _attached

