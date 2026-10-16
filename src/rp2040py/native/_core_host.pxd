# Cython view of the chip's ChipPorts (core/core_host.hpp, docs/records/0096-cpp-mcu-core.md): what a shell fills in for a block it makes directly (a stand-in chip, a test), where the chip's own `Mcu` fills in its.

from libc.stdint cimport int64_t, uint32_t
from libcpp cimport bool

cdef extern from "core_host.hpp" namespace "rp2040core":
    cdef cppclass ChipPorts:
        ChipPorts() noexcept
        void (*warn)(void* chip, uint32_t block, uint32_t kind, uint32_t offset, int64_t value) noexcept
        void* chip
        const int* failed
        bool (*irq)(void* chip, uint32_t line, bool level) noexcept
        bool (*dreq)(void* chip, uint32_t number, bool asserted) noexcept
