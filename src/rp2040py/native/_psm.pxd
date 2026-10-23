# Cython view of core/psm.hpp (docs/records/0096-cpp-mcu-core.md) and the declaration of the native PSM shell.
# Header-only C++17, compiled into every extension that cimports it (setup.py builds them all as C++).

from libc.stdint cimport int64_t, uint32_t
from libcpp cimport bool as cppbool

from rp2040py.native._native_block cimport NativeBlock
from rp2040py.native._window_map cimport WindowHandler

cdef extern from "psm.hpp" namespace "rp2040core":

    cdef cppclass PsmBlock:
        PsmBlock() noexcept
        int64_t raw_write_value() noexcept
        uint32_t wdsel
        void snapshot(uint32_t* out) noexcept
        uint32_t read(uint32_t offset) noexcept
        cppbool write(uint32_t offset, int64_t value) noexcept
        cppbool write_atomic(uint32_t offset, int64_t raw, uint32_t atomic_type) noexcept
        WindowHandler window_handler() noexcept

cdef class RPPSM(NativeBlock):
    cdef PsmBlock* _block  # the chip's own (core/mcu.hpp): this object is its Python view
