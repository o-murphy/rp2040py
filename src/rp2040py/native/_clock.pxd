# Cython view of core/clock.hpp (docs/records/0096-cpp-mcu-core.md, Phase 2): the C++ clock and alarm scheduler.
# Header-only C++17, compiled into every extension that cimports it (setup.py builds them all as C++).

from libc.stdint cimport int64_t
from libcpp cimport bool

cdef extern from "clock.hpp" namespace "rp2040core":
    # `bool`, not `bint`: Cython's bint is a C int, which is a different function-pointer type from the core's bool.
    ctypedef bool (*AlarmFireFn)(void* ctx)
    ctypedef void (*RebaseFn)(void* ctx, int64_t delta)

    cdef cppclass RebaseListener:
        RebaseListener* next
        Clock* owner

    cdef cppclass Alarm:
        Alarm* next
        Clock* owner
        int64_t ticks
        bint scheduled
        AlarmFireFn fire
        void* ctx

    cdef cppclass Clock:
        double frequency
        int64_t ticks() noexcept
        double nanos() noexcept
        bint has_alarm() noexcept
        int64_t ticks_to_next_alarm() noexcept
        double nanos_to_next_alarm() noexcept
        bint saturated() noexcept
        double rebased_ticks() noexcept
        void add_rebase_listener(RebaseListener* listener, RebaseFn fn, void* ctx) noexcept
        void rebase(int64_t delta) noexcept
        void link(Alarm* alarm, double delta_nanos) noexcept
        void link_ticks(Alarm* alarm, int64_t delta) noexcept
        bint unlink(Alarm* alarm) noexcept
        void schedule(Alarm* alarm, double delta_nanos) noexcept
        void schedule_ticks(Alarm* alarm, int64_t delta) noexcept
        void cancel(Alarm* alarm) noexcept
        bint tick(double delta_nanos) noexcept
        bint tick_ticks(int64_t delta) noexcept
        bint defer_ticks(int64_t delta) noexcept
        bint advance_cycles(int64_t cycles, double ticks_per_cycle) noexcept

    int64_t nanos_to_ticks(double nanos) noexcept
    cdef const int64_t kTicksPerNano
    cdef const int64_t kMaxTicks

    void cancel_alarm(Alarm* alarm) noexcept
    void remove_rebase_listener(RebaseListener* listener) noexcept
