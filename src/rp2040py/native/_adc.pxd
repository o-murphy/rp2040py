# Cython view of core/adc.hpp and core/fifo.hpp (docs/records/0096-cpp-mcu-core.md, Phase 4) and the declaration of the native ADC shell.
# Header-only C++17, compiled into every extension that cimports it (setup.py builds them all as C++).

from libc.stdint cimport int64_t, uint32_t
from libcpp cimport bool as cppbool


from rp2040py.native._clock cimport Alarm, Clock
from rp2040py.native._window_map cimport WindowHandler

cdef extern from "fifo.hpp" namespace "rp2040core":
    cdef cppclass Fifo8 "rp2040core::Fifo<8>":
        uint32_t size() noexcept
        uint32_t count() noexcept
        cppbool empty() noexcept
        cppbool full() noexcept
        uint32_t at(uint32_t index) noexcept
        void push(uint32_t value) noexcept
        uint32_t pull() noexcept
        uint32_t peek() noexcept
        void reset() noexcept

cdef extern from "adc.hpp" namespace "rp2040core":

    ctypedef cppbool (*AdcReadFn)(void* ctx, uint32_t channel)
    ctypedef cppbool (*AdcChannelValueFn)(void* ctx, uint32_t channel, int64_t* value)

    cdef cppclass AdcBlock:
        uint32_t cs
        uint32_t fcs
        uint32_t clock_div
        uint32_t int_enable
        uint32_t int_force
        int64_t result
        cppbool busy
        uint32_t current_channel
        int64_t num_channels
        double sample_time
        Fifo8 fifo
        Alarm sample_alarm
        Alarm multi_shot_alarm
        AdcBlock() noexcept
        void detach() noexcept
        Clock* clock() noexcept
        int64_t raw_write_value() noexcept
        double divider() noexcept
        uint32_t int_raw() noexcept
        uint32_t int_status() noexcept
        uint32_t active_channel() noexcept
        void set_active_channel(int64_t channel) noexcept
        cppbool reset() noexcept
        cppbool check_interrupts() noexcept
        cppbool start_adc_read() noexcept
        void default_adc_read(uint32_t channel) noexcept
        cppbool complete_adc_read(int64_t value, cppbool error) noexcept
        uint32_t read(uint32_t offset) noexcept
        cppbool write(uint32_t offset, int64_t value) noexcept
        cppbool write_atomic(uint32_t offset, int64_t raw, uint32_t atomic_type) noexcept
        WindowHandler window_handler() noexcept

cdef class RPADC:
    cdef public bint _from_mcu  # this is the view of the `Mcu`'s block
    cdef AdcBlock* _block  # the chip's own (core/mcu.hpp): this object is its Python view
    cdef public object rp2040
    cdef public object name
    cdef public object clock
    cdef public object channel_values
    cdef public object resolution
    cdef public object dreq
    cdef object _on_adc_read
    cdef object _sample_time
