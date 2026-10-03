# Cython view of core/bus.hpp (docs/records/0096-cpp-mcu-core.md, Phase 2 step 4a): the system bus. Header-only C++17.

from libc.stdint cimport int64_t, uint32_t

from rp2040py.native._memory_map cimport MemoryMap
from rp2040py.native._window_map cimport WindowHandler, WindowMap

cdef extern from "bus.hpp" namespace "rp2040core":
    ctypedef void (*BusWarnFn)(void* ctx, uint32_t kind, uint32_t address)
    ctypedef void (*BusDpramFn)(void* ctx, uint32_t offset, int64_t value)

    cdef const uint32_t kBusWarnUnalignedRead
    cdef const uint32_t kBusWarnInvalidRead
    cdef const uint32_t kBusWarnUndefinedWrite

    cdef cppclass BusHost:
        BusWarnFn warn
        BusDpramFn dpram_written
        void* ctx

    cdef cppclass Bus:
        MemoryMap mem
        WindowMap windows
        Bus() noexcept
        void init(const BusHost& host) noexcept
        void set_sio(const WindowHandler& handler) noexcept
        void set_ppb(const WindowHandler& handler) noexcept
        uint32_t read32(uint32_t addr) noexcept
        uint32_t read16(uint32_t addr) noexcept
        uint32_t read8(uint32_t addr) noexcept
        void write32(uint32_t addr, int64_t value) noexcept
        void write16(uint32_t addr, uint32_t value) noexcept
        void write8(uint32_t addr, uint32_t value) noexcept
