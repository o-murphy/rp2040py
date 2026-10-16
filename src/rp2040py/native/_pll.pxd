# Cython view of core/pll.hpp (docs/records/0096-cpp-mcu-core.md) and the declaration of the native PLL shell.
# Header-only C++17, compiled into every extension that cimports it (setup.py builds them all as C++).

from libc.stdint cimport int64_t, uint32_t
from libcpp cimport bool as cppbool

from rp2040py.native._core_host cimport ChipPorts
from rp2040py.native._window_map cimport WindowHandler

cdef extern from "pll.hpp" namespace "rp2040core":

    ctypedef double (*PllRefFn)(void* ctx)
    ctypedef cppbool (*PllChangedFn)(void* ctx)

    cdef cppclass PllBlock:
        PllBlock() noexcept
        void bind_reference(PllRefFn fn, void* ctx) noexcept
        int64_t raw_write_value() noexcept
        void reset() noexcept
        uint32_t refdiv() noexcept
        uint32_t fbdiv() noexcept
        uint32_t postdiv1() noexcept
        uint32_t postdiv2() noexcept
        double reference() noexcept
        cppbool locked() noexcept
        double frequency() noexcept
        uint32_t cs
        uint32_t pwr
        uint32_t fbdiv_int
        uint32_t prim
        uint32_t read(uint32_t offset) noexcept
        cppbool write(uint32_t offset, int64_t value) noexcept
        cppbool write_atomic(uint32_t offset, int64_t raw, uint32_t atomic_type) noexcept
        WindowHandler window_handler() noexcept

cdef class RPPLL:
    cdef PllBlock* _block  # the chip's own (core/mcu.hpp): this object is its Python view
    cdef object _bound_tree  # the native clock tree this PLL takes its reference from (None: it asks the chip through Python)
    cdef public object rp2040
    cdef public object name
