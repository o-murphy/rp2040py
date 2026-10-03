# Cython view of core/window_map.hpp (docs/records/0096-cpp-mcu-core.md, Phase 1): the peripheral window
# registry. Header-only C++17, compiled into every extension that cimports it (setup.py builds them all as C++).

from libc.stdint cimport int64_t, uint32_t

cdef extern from "window_map.hpp" namespace "rp2040core":
    cdef const int kNoWindow

    ctypedef uint32_t (*Read32Fn)(void* ctx, uint32_t offset)
    ctypedef void (*Write32Fn)(void* ctx, uint32_t offset, int64_t raw_value, uint32_t atomic_type)

    cdef cppclass WindowHandler:
        Read32Fn read32
        Write32Fn write32
        void* ctx

    cdef cppclass WindowMap:
        WindowMap() noexcept
        int attach(uint32_t address, const WindowHandler& handler) noexcept
        bint detach(uint32_t address) noexcept
        void clear() noexcept
        int count() noexcept
        bint has(uint32_t address) noexcept
        int read32(uint32_t address, uint32_t* out) noexcept
        int write32(uint32_t address, int64_t raw_value) noexcept
