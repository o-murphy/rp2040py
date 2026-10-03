# The one slot where a C++ frame's failure waits for its Python caller (docs/records/0096-cpp-mcu-core.md).
#
# A callback that a C++ core calls - an alarm, an interrupt-line change, a window handler - cannot let a Python
# exception unwind through the C++ frames above it. It parks the exception here and reports failure; whoever
# called into C++ checks right after the call returns and re-raises. The slot is shared by every native module
# (the clock, the bus, each native peripheral) because the thing that fails and the thing that re-raises are
# usually in different modules: a TIMER's interrupt callback fails inside `SimulationClock.tick()`.
cdef void park_error(object error) noexcept
cdef bint has_pending_error() noexcept
cdef int raise_if_pending() except -1
# The address of a flag that is nonzero exactly while an error is parked: what a C++ core polls after a bus access or a
# callback instead of calling `has_pending_error()` (a Python-module function it has no way to reach). Valid for the life of
# the process.
cdef const int* pending_flag() noexcept
