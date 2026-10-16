# Cython view of core/tbman.hpp (docs/records/0096-cpp-mcu-core.md) and the declaration of the native TBMAN shell.
# Header-only C++17, compiled into every extension that cimports it (setup.py builds them all as C++).

from libc.stdint cimport int64_t, uint32_t
from libcpp cimport bool as cppbool

from rp2040py.native._window_map cimport WindowHandler

cdef extern from "tbman.hpp" namespace "rp2040core":

    cdef cppclass TbmanBlock:
        TbmanBlock() noexcept
        int64_t raw_write_value() noexcept
        cppbool reset() noexcept
        uint32_t read(uint32_t offset) noexcept
        cppbool write(uint32_t offset, int64_t value) noexcept
        cppbool write_atomic(uint32_t offset, int64_t raw, uint32_t atomic_type) noexcept
        WindowHandler window_handler() noexcept

cdef class RPTBMAN:
    cdef TbmanBlock* _block  # the chip's own (core/mcu.hpp): this object is its Python view
    cdef public object rp2040
    cdef public object name
