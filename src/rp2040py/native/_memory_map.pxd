# Cython view of core/memory_map.hpp (docs/records/0096-cpp-mcu-core.md, Phase 1). Header-only C++17: it
# is compiled into every extension that cimports it, so setup.py builds all native modules as C++.

from libc.stdint cimport uint8_t, uint32_t

cdef extern from "memory_map.hpp" namespace "rp2040core":
    cdef const int kNotHandled

    ctypedef void (*XipAccessFn)(void* ctx, uint32_t address) noexcept
    ctypedef uint32_t (*XipSramReadFn)(void* ctx, uint32_t address) noexcept
    ctypedef void (*XipSramWriteFn)(void* ctx, uint32_t address, uint32_t value) noexcept

    cdef cppclass XipHooks:
        XipHooks() noexcept
        XipAccessFn read_access
        XipAccessFn write_access
        XipSramReadFn sram_read
        XipSramWriteFn sram_write
        void* ctx

    cdef cppclass Region:
        uint32_t base
        uint32_t window
        uint32_t size
        uint32_t mask
        uint8_t* data
        uint32_t flags

    cdef cppclass MemoryMap:
        MemoryMap() noexcept
        int attach(const Region&) noexcept
        void clear() noexcept
        void set_xip(const XipHooks&) noexcept
        int count() noexcept
        const Region& region(int) noexcept
        int read32(uint32_t, uint32_t*) noexcept
        int read16(uint32_t, uint32_t*) noexcept
        int read8(uint32_t, uint32_t*) noexcept
        int write32(uint32_t, uint32_t) noexcept
        int write16(uint32_t, uint32_t) noexcept
        int write8(uint32_t, uint32_t) noexcept
