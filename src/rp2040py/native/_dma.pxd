# Cython view of core/dma.hpp (docs/records/0096-cpp-mcu-core.md, Phase 4) and the declaration of the native DMA shell.
# Header-only C++17, compiled into every extension that cimports it (setup.py builds them all as C++).

from libc.stdint cimport int64_t, uint32_t, uint64_t
from libcpp cimport bool as cppbool


from rp2040py.native._bus cimport Bus
from rp2040py.native._clock cimport Clock
from rp2040py.native._core_host cimport ChipPorts
from rp2040py.native._native_block cimport NativeBlock
from rp2040py.native._window_map cimport WindowHandler

cdef extern from "dma.hpp" namespace "rp2040core":

    ctypedef double (*DmaClockFn)(void* ctx)
    ctypedef cppbool (*DmaDreqTap)(void* ctx, uint32_t number, cppbool asserted)

    cdef cppclass DmaChannel:
        int index
        uint32_t ctrl
        uint32_t read_addr
        uint32_t write_addr
        int64_t trans_count
        uint32_t trans_count_reload
        uint32_t dreq_counter
        uint32_t treq
        uint32_t data_size
        uint32_t chain_to
        uint32_t ring_mask
        cppbool active() noexcept
        cppbool start() noexcept
        cppbool schedule_transfer() noexcept
        void abort() noexcept
        uint32_t read(uint32_t offset) noexcept
        cppbool write(uint32_t offset, int64_t value) noexcept
        cppbool reset() noexcept

    cdef cppclass DmaBlock:
        DmaChannel channels[12]
        uint32_t int_raw
        double transfer_ns
        DmaDreqTap dreq_tap
        void* dreq_tap_ctx
        DmaBlock() noexcept
        void detach() noexcept
        uint32_t int_status0() noexcept
        uint32_t int_status1() noexcept
        int64_t raw_write_value() noexcept
        uint32_t timer(int index) noexcept
        uint64_t dreq_mask() noexcept
        cppbool dreq(uint32_t number) noexcept
        cppbool set_dreq(uint32_t number) noexcept
        void clear_dreq(uint32_t number) noexcept
        cppbool check_interrupts() noexcept
        cppbool reset() noexcept
        uint32_t read(uint32_t offset) noexcept
        cppbool write(uint32_t offset, int64_t value) noexcept
        cppbool write_atomic(uint32_t offset, int64_t raw, uint32_t atomic_type) noexcept
        WindowHandler window_handler() noexcept
        void bind_clock(DmaClockFn fn, void* ctx, double clk_sys_hz) noexcept
        void clk_sys_changed(double hz) noexcept


cdef class RPDMA(NativeBlock):
    cdef DmaBlock* _block  # the chip's own (core/mcu.hpp): this object is its Python view
    cdef public object clock
