# Cython view of core/xosc.hpp (docs/records/0096-cpp-mcu-core.md) and the declaration of the native XOSC shell.
# Header-only C++17, compiled into every extension that cimports it (setup.py builds them all as C++).

from libc.stdint cimport int64_t, uint32_t
from libcpp cimport bool as cppbool

from rp2040py.native._window_map cimport WindowHandler

cdef extern from "xosc.hpp" namespace "rp2040core":
    cdef const uint32_t kXoscWarnRead
    cdef const uint32_t kXoscWarnReadAtomicArea
    cdef const uint32_t kXoscWarnWrite
    cdef const uint32_t kXoscWarnInvalidFreqRange
    cdef const uint32_t kXoscWarnInvalidEnable
    cdef const uint32_t kXoscWarnInvalidDormant

    ctypedef void (*XoscWarnFn)(void* ctx, uint32_t kind, uint32_t offset, int64_t value)

    cdef cppclass XoscHost:
        XoscHost() noexcept
        XoscWarnFn warn
        void* ctx
        const int* failed

    cdef cppclass XoscBlock:
        XoscBlock() noexcept
        void init(const XoscHost& host) noexcept
        int64_t raw_write_value() noexcept
        cppbool reset() noexcept
        uint32_t ctrl
        uint32_t status
        uint32_t dormant
        uint32_t startup
        uint32_t count
        cppbool enabled
        cppbool stable
        cppbool is_dormant
        uint32_t read(uint32_t offset) noexcept
        cppbool write(uint32_t offset, int64_t value) noexcept
        cppbool write_atomic(uint32_t offset, int64_t raw, uint32_t atomic_type) noexcept
        WindowHandler window_handler() noexcept

cdef class RPXOSC:
    cdef XoscBlock _block
    cdef public object rp2040
    cdef public object name
