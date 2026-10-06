# Cython view of core/sysinfo.hpp (docs/records/0096-cpp-mcu-core.md) and the declaration of the native SYSINFO shell.
# Header-only C++17, compiled into every extension that cimports it (setup.py builds them all as C++).

from libc.stdint cimport int64_t, uint32_t
from libcpp cimport bool as cppbool

from rp2040py.native._window_map cimport WindowHandler

cdef extern from "sysinfo.hpp" namespace "rp2040core":
    cdef const uint32_t kSysInfoWarnRead
    cdef const uint32_t kSysInfoWarnReadAtomicArea
    cdef const uint32_t kSysInfoWarnWrite

    ctypedef cppbool (*SysInfoRomVersionFn)(void* ctx, uint32_t* version)
    ctypedef void (*SysInfoWarnFn)(void* ctx, uint32_t kind, uint32_t offset, int64_t value)

    cdef cppclass SysInfoHost:
        SysInfoHost() noexcept
        SysInfoRomVersionFn rom_version
        SysInfoWarnFn warn
        void* ctx
        const int* failed

    cdef cppclass SysInfoBlock:
        SysInfoBlock() noexcept
        void init(const SysInfoHost& host) noexcept
        int64_t raw_write_value() noexcept
        cppbool reset() noexcept
        uint32_t read(uint32_t offset) noexcept
        cppbool write(uint32_t offset, int64_t value) noexcept
        cppbool write_atomic(uint32_t offset, int64_t raw, uint32_t atomic_type) noexcept
        WindowHandler window_handler() noexcept

cdef class RP2040SysInfo:
    cdef SysInfoBlock _block
    cdef public object rp2040
    cdef public object name
