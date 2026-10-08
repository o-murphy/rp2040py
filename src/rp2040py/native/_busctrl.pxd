# Cython view of core/busctrl.hpp (docs/records/0096-cpp-mcu-core.md) and the declaration of the native BUSCTRL shell.
# Header-only C++17, compiled into every extension that cimports it (setup.py builds them all as C++).

from libc.stdint cimport int64_t, uint32_t
from libcpp cimport bool as cppbool

from rp2040py.native._window_map cimport WindowHandler

cdef extern from "busctrl.hpp" namespace "rp2040core":
    cdef const uint32_t kBusctrlWarnRead
    cdef const uint32_t kBusctrlWarnReadAtomicArea
    cdef const uint32_t kBusctrlWarnWrite

    ctypedef void (*BusctrlWarnFn)(void* ctx, uint32_t kind, uint32_t offset, int64_t value)

    cdef cppclass BusctrlHost:
        BusctrlHost() noexcept
        BusctrlWarnFn warn
        void* ctx
        const int* failed

    cdef cppclass BusctrlBlock:
        BusctrlBlock() noexcept
        void init(const BusctrlHost& host) noexcept
        int64_t raw_write_value() noexcept
        uint32_t bus_priority
        uint32_t perf_ctr[4]
        uint32_t perf_sel[4]
        cppbool reset() noexcept
        uint32_t read(uint32_t offset) noexcept
        cppbool write(uint32_t offset, int64_t value) noexcept
        cppbool write_atomic(uint32_t offset, int64_t raw, uint32_t atomic_type) noexcept
        WindowHandler window_handler() noexcept

cdef class RPBUSCTRL:
    cdef BusctrlBlock _block
    cdef public object rp2040
    cdef public object name
    cdef public object voltage_select
    cdef public object perf_ctr
    cdef public object perf_sel
