# Cython view of core/syscfg.hpp (docs/records/0096-cpp-mcu-core.md) and the declaration of the native SYSCFG shell.
# Header-only C++17, compiled into every extension that cimports it (setup.py builds them all as C++).

from libc.stdint cimport int64_t, uint32_t
from libcpp cimport bool as cppbool

from rp2040py.native._cpu cimport Cpu
from rp2040py.native._window_map cimport WindowHandler

cdef extern from "syscfg.hpp" namespace "rp2040core":
    cdef const uint32_t kSyscfgWarnRead
    cdef const uint32_t kSyscfgWarnReadAtomicArea
    cdef const uint32_t kSyscfgWarnWrite

    ctypedef void (*SyscfgWarnFn)(void* ctx, uint32_t kind, uint32_t offset, int64_t value)

    cdef cppclass SyscfgHost:
        SyscfgHost() noexcept
        SyscfgWarnFn warn
        void* ctx
        const int* failed

    cdef cppclass SyscfgBlock:
        SyscfgBlock() noexcept
        void init(Cpu* cpu, const SyscfgHost& host) noexcept
        int64_t raw_write_value() noexcept
        cppbool reset() noexcept
        uint32_t proc1_nmi_mask
        uint32_t proc_config
        uint32_t proc_in_sync_bypass
        uint32_t proc_in_sync_bypass_hi
        uint32_t dbgforce
        uint32_t mempowerdown
        uint32_t read(uint32_t offset) noexcept
        cppbool write(uint32_t offset, int64_t value) noexcept
        cppbool write_atomic(uint32_t offset, int64_t raw, uint32_t atomic_type) noexcept
        WindowHandler window_handler() noexcept

cdef class RP2040SysCfg:
    cdef SyscfgBlock _block
    cdef public object rp2040
    cdef public object name
