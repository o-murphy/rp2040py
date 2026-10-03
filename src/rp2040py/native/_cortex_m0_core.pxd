# Declaration file paired with _cortex_m0_core.pyx. `CortexM0Core` is a thin shell over the C++ `Cpu` of core/cpu.hpp
# (docs/records/0096-cpp-mcu-core.md, Phase 2 step 4b): the whole architectural state lives in `_cpu`, and the Python-visible
# fields (`registers`, flags, `pending_interrupts`, ...) are properties onto it. Other Cython modules (the batch loop in
# _simulator.pyx) reach `core._cpu` directly, as plain C++ field access.
#
# `registers` and `interrupt_priorities` are `public` typed memoryviews, as before: they are views over the C++ arrays (no copy),
# so `core.registers[i] = x` from Python writes the very words the C++ instruction loop reads.

from rp2040py.native._cpu cimport Cpu
from rp2040py.native._rp2040 cimport RP2040


cdef class CortexM0Core:
    cdef Cpu _cpu
    # Typed as the concrete native RP2040, not `object`: the bus pointer handed to the C++ core comes from it, and a native
    # CortexM0Core can never end up paired with the pure-Python RP2040 or vice versa (rp2040py.native/__init__.py imports them
    # all-or-nothing).
    cdef public RP2040 rp2040
    cdef public unsigned int[:] registers
    cdef public unsigned int[:] interrupt_priorities
    cdef object _bl_taken
    cdef object _default_bl_taken

    cpdef reset(self)
    cpdef set_interrupt(self, irq, value)
    cpdef int execute_instruction(self) except -1
