# Cython view of core/io.hpp (docs/records/0096-cpp-mcu-core.md) and the declaration of the native IO_BANK0 / IO_QSPI shell.
# Header-only C++17, compiled into every extension that cimports it (setup.py builds them all as C++).

from libc.stdint cimport int64_t, uint32_t
from libcpp cimport bool as cppbool

from rp2040py.native._gpio_pin cimport GPIOPinBank
from rp2040py.native._pin cimport PinBank
from rp2040py.native._window_map cimport WindowHandler

cdef extern from "io.hpp" namespace "rp2040core":
    cdef cppclass IoBlock:
        IoBlock() noexcept
        int64_t raw_write_value() noexcept
        void reset() noexcept
        uint32_t extra(uint32_t dest, cppbool force, uint32_t pin) noexcept
        cppbool read(uint32_t offset, uint32_t* out) noexcept
        cppbool write(uint32_t offset, int64_t value) noexcept
        cppbool write_atomic(uint32_t offset, int64_t raw, uint32_t atomic_type) noexcept
        WindowHandler window_handler() noexcept

cdef class RPIO:
    cdef public bint _from_mcu  # this is the view of the `Mcu`'s block
    cdef IoBlock* _block  # the chip's own (core/mcu.hpp): this object is its Python view
    cdef public object rp2040
    cdef public object name
    cdef public list pins
    cdef GPIOPinBank _owner
    cdef uint32_t _count
