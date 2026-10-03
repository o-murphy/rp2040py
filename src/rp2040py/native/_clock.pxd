# Cython view of core/clock.hpp (docs/records/0096-cpp-mcu-core.md, Phase 2): the C++ clock and alarm scheduler.
# Header-only C++17, compiled into every extension that cimports it (setup.py builds them all as C++).

from libcpp cimport bool

cdef extern from "clock.hpp" namespace "rp2040core":
    # `bool`, not `bint`: Cython's bint is a C int, which is a different function-pointer type from the core's bool.
    ctypedef bool (*AlarmFireFn)(void* ctx)

    cdef cppclass Alarm:
        Alarm* next
        double nanos
        bint scheduled
        AlarmFireFn fire
        void* ctx

    cdef cppclass Clock:
        double frequency
        double nanos() noexcept
        bint has_alarm() noexcept
        double nanos_to_next_alarm() noexcept
        void link(Alarm* alarm, double delta) noexcept
        bint unlink(Alarm* alarm) noexcept
        void schedule(Alarm* alarm, double delta) noexcept
        void cancel(Alarm* alarm) noexcept
        bint tick(double delta) noexcept
