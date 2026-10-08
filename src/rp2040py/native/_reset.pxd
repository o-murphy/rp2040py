# Cython view of core/resets.hpp (docs/records/0096-cpp-mcu-core.md) and the declaration of the native RESETS shell.
# Header-only C++17, compiled into every extension that cimports it (setup.py builds them all as C++).

from libc.stdint cimport int64_t, uint32_t
from libcpp cimport bool as cppbool

from rp2040py.native._window_map cimport WindowHandler

cdef extern from "resets.hpp" namespace "rp2040core":
    cdef const uint32_t kResetsWarnRead
    cdef const uint32_t kResetsWarnReadAtomicArea
    cdef const uint32_t kResetsWarnWrite

    ctypedef void (*ResetsWarnFn)(void* ctx, uint32_t kind, uint32_t offset, int64_t value)

    cdef cppclass ResetsHost:
        ResetsHost() noexcept
        ResetsWarnFn warn
        void* ctx
        const int* failed

    cdef cppclass ResetsBlock:
        ResetsBlock() noexcept
        void init(const ResetsHost& host) noexcept
        int64_t raw_write_value() noexcept
        uint32_t reset_bits
        uint32_t wdsel
        uint32_t read(uint32_t offset) noexcept
        cppbool write(uint32_t offset, int64_t value) noexcept
        cppbool write_atomic(uint32_t offset, int64_t raw, uint32_t atomic_type) noexcept
        WindowHandler window_handler() noexcept

cdef class RPReset:
    cdef ResetsBlock _block
    cdef public object rp2040
    cdef public object name
