# Cython view of core/rosc.hpp (docs/records/0096-cpp-mcu-core.md) and the declaration of the native ROSC shell.
# Header-only C++17, compiled into every extension that cimports it (setup.py builds them all as C++).

from libc.stdint cimport int64_t, uint32_t
from libcpp cimport bool as cppbool

from rp2040py.native._clock cimport Clock
from rp2040py.native._window_map cimport WindowHandler

cdef extern from "rosc.hpp" namespace "rp2040core":
    cdef const uint32_t kRoscWarnRead
    cdef const uint32_t kRoscWarnReadAtomicArea
    cdef const uint32_t kRoscWarnWrite
    cdef const uint32_t kRoscWarnInvalidFreqRange
    cdef const uint32_t kRoscWarnInvalidEnable
    cdef const uint32_t kRoscWarnInvalidFreqPasswd
    cdef const uint32_t kRoscWarnInvalidDormant
    cdef const uint32_t kRoscWarnInvalidDiv
    cdef const uint32_t kRoscWarnInvalidPhasePasswd

    ctypedef void (*RoscWarnFn)(void* ctx, uint32_t kind, uint32_t offset, int64_t value)
    ctypedef cppbool (*RoscFrequencyFn)(void* ctx, double hz)
    ctypedef cppbool (*RoscBitFn)(void* ctx, uint32_t* bit)

    cdef cppclass RoscHost:
        RoscHost() noexcept
        RoscWarnFn warn
        RoscFrequencyFn frequency_changed
        RoscBitFn random_bit
        void* random_ctx
        void* ctx
        const int* failed

    cdef cppclass RoscBlock:
        RoscBlock() noexcept
        void init(Clock* clock, const RoscHost& host) noexcept
        void detach() noexcept
        void bind_frequency_listener(RoscFrequencyFn fn, void* ctx) noexcept
        void bind_random_source(RoscBitFn fn, void* ctx) noexcept
        int64_t raw_write_value() noexcept
        cppbool reset() noexcept
        double frequency() noexcept
        uint32_t count_now() noexcept
        uint32_t ctrl
        uint32_t freqa
        uint32_t freqb
        uint32_t dormant
        uint32_t div
        uint32_t phase
        cppbool badwrite
        cppbool enabled
        uint32_t last_bit
        uint32_t count
        int64_t count_epoch
        double hz
        cppbool read(uint32_t offset, uint32_t* out) noexcept
        cppbool write(uint32_t offset, int64_t value) noexcept
        cppbool write_atomic(uint32_t offset, int64_t raw, uint32_t atomic_type) noexcept
        WindowHandler window_handler() noexcept

cdef class RPROSC:
    cdef public bint _from_mcu  # this is the view of the `Mcu`'s block
    cdef RoscBlock* _block  # the chip's own (core/mcu.hpp): this object is its Python view
    cdef public object rp2040
    cdef public object name
    cdef public object clock
    cdef object _random_bit
    cdef object _rng
    cdef object _bound_tree  # the native clock tree this block tells directly (None: it tells the chip through Python)
