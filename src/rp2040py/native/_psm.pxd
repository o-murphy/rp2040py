# Cython view of core/psm.hpp (docs/records/0096-cpp-mcu-core.md) and the declaration of the native PSM shell.
# Header-only C++17, compiled into every extension that cimports it (setup.py builds them all as C++).

from libc.stdint cimport int64_t, uint32_t
from libcpp cimport bool as cppbool

from rp2040py.native._window_map cimport WindowHandler

cdef extern from "psm.hpp" namespace "rp2040core":
    cdef const uint32_t kPsmWarnRead
    cdef const uint32_t kPsmWarnReadAtomicArea
    cdef const uint32_t kPsmWarnWrite

    ctypedef void (*PsmWarnFn)(void* ctx, uint32_t kind, uint32_t offset, int64_t value)

    cdef cppclass PsmHost:
        PsmHost() noexcept
        PsmWarnFn warn
        void* ctx
        const int* failed

    cdef cppclass PsmBlock:
        PsmBlock() noexcept
        void init(const PsmHost& host) noexcept
        int64_t raw_write_value() noexcept
        uint32_t frce_on
        uint32_t frce_off
        uint32_t wdsel
        uint32_t read(uint32_t offset) noexcept
        cppbool write(uint32_t offset, int64_t value) noexcept
        cppbool write_atomic(uint32_t offset, int64_t raw, uint32_t atomic_type) noexcept
        WindowHandler window_handler() noexcept

cdef class RPPSM:
    cdef PsmBlock _block
    cdef public object rp2040
    cdef public object name
