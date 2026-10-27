# Cython view of core/xip_ctrl.hpp (docs/records/0096-cpp-mcu-core.md, docs/records/0098-datasheet-conformance-audit.md "XIP_CTRL and the XIP subsystem") and the declaration of the native XIP shell.
# Header-only C++17, compiled into every extension that cimports it (setup.py builds them all as C++).

from libc.stdint cimport int16_t, int64_t, uint8_t, uint32_t
from libcpp cimport bool as cppbool

from rp2040py.native._clock cimport Clock
from rp2040py.native._memory_map cimport XipHooks
from rp2040py.native._native_block cimport NativeBlock
from rp2040py.native._window_map cimport WindowHandler

cdef extern from "xip_ctrl.hpp" namespace "rp2040core":
    cdef const uint32_t kXipWarnBadWrite
    cdef const uint32_t kXipWarnSramRead
    cdef const uint32_t kXipWarnSramWrite

    ctypedef cppbool (*XipRateFn)(void* ctx, double* value)

    cdef cppclass XipBlock:
        XipBlock() noexcept
        void detach() noexcept
        int64_t raw_write_value() noexcept
        uint32_t ctrl
        uint32_t ctr_hit
        uint32_t ctr_acc
        uint32_t stream_addr
        uint32_t stream_ctr
        uint32_t fifo[2]
        uint32_t fifo_len
        cppbool stream_in_flight
        int64_t flush_done_ticks
        int16_t tags[1024][2]
        uint8_t recent[1024]
        uint8_t sram[16384]
        cppbool reset() noexcept
        cppbool flush_ready() noexcept
        uint32_t stat() noexcept
        uint32_t sram_read(uint32_t address) noexcept
        void sram_write(uint32_t address, uint32_t value) noexcept
        uint32_t read(uint32_t offset) noexcept
        cppbool write(uint32_t offset, int64_t value) noexcept
        cppbool write_atomic(uint32_t offset, int64_t raw, uint32_t atomic_type) noexcept
        WindowHandler window_handler() noexcept
        WindowHandler aux_window_handler() noexcept
        XipHooks hooks() noexcept


cdef class RPXIPCtrl(NativeBlock):
    cdef XipBlock* _block  # the chip's own (core/mcu.hpp): this object is its Python view
    cdef public object clock
