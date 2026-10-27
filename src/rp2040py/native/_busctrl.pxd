# Cython view of core/busctrl.hpp (docs/records/0096-cpp-mcu-core.md) and the declaration of the native BUSCTRL shell.
# Header-only C++17, compiled into every extension that cimports it (setup.py builds them all as C++).

from libc.stdint cimport int64_t, uint32_t
from libcpp cimport bool as cppbool

from rp2040py.native._native_block cimport NativeBlock
from rp2040py.native._window_map cimport WindowHandler

cdef extern from "busctrl.hpp" namespace "rp2040core":

    cdef cppclass BusctrlBlock:
        BusctrlBlock() noexcept
        int64_t raw_write_value() noexcept
        void snapshot(uint32_t* out) noexcept
        void set_perf_counter(uint32_t index, uint32_t value) noexcept
        cppbool reset() noexcept
        uint32_t read(uint32_t offset) noexcept
        cppbool write(uint32_t offset, int64_t value) noexcept
        cppbool write_atomic(uint32_t offset, int64_t raw, uint32_t atomic_type) noexcept
        WindowHandler window_handler() noexcept

cdef class RPBUSCTRL(NativeBlock):
    cdef BusctrlBlock* _block  # the chip's own (core/mcu.hpp): this object is its Python view
