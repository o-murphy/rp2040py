# Declaration file paired with _cortex_m0_core.pyx. Cython processes a .pxd ahead of its
# matching .pyx body, which is what lets the op_* handler functions and DISPATCH_TABLE (both
# defined "before" the class, textually, in the .pyx) see CortexM0Core's C-level field layout,
# and what lets CortexM0Core.execute_instruction (defined "after" them) reference DISPATCH_TABLE
# and resolve_wide - a plain forward-declare-then-define-later cdef function isn't supported
# within a single .pyx, so the prototypes live here instead.

from rp2040py.native._rp2040 cimport RP2040

ctypedef int (*OpHandler)(CortexM0Core, unsigned int, unsigned int, unsigned int) except -1

cdef OpHandler DISPATCH_TABLE[0x10000]

cdef OpHandler resolve_wide(unsigned int opcode, unsigned int opcode2) noexcept


cdef class CortexM0Core:
    # Typed as the concrete native RP2040, not `object`: op_* handlers below call
    # core.rp2040.read_uint32(...) etc. on essentially every load/store instruction, and this
    # lets those resolve as direct C-level cpdef calls instead of a Python attribute lookup +
    # method call. Safe because rp2040py.native/__init__.py imports bit+cortex_m0_core+rp2040
    # together, all-or-nothing (see that file) - a native CortexM0Core can never end up paired
    # with the pure-Python RP2040 or vice versa.
    cdef public RP2040 rp2040
    cdef public unsigned int[:] registers
    cdef public unsigned int banked_sp
    cdef public unsigned long long cycles

    cdef public bint event_registered
    cdef public bint waiting

    cdef public bint n
    cdef public bint c
    cdef public bint z
    cdef public bint v

    cdef public unsigned int break_rewind

    cdef public bint pm

    cdef public int sp_sel
    cdef public bint n_priv

    cdef public int current_mode
    cdef public unsigned int ipsr
    cdef public unsigned int interrupt_nmi_mask
    cdef public unsigned int pending_interrupts
    cdef public unsigned int enabled_interrupts
    cdef public unsigned int[:] interrupt_priorities
    cdef public bint pending_nmi
    cdef public bint pending_pend_sv
    cdef public bint pending_svcall
    cdef public bint pending_systick
    cdef public bint interrupts_updated
    cdef public unsigned int vtor
    cdef public unsigned int shpr2
    cdef public unsigned int shpr3

    cdef public object bl_taken

    cpdef reset(self)
    cpdef set_interrupt(self, irq, value)
    cpdef int execute_instruction(self) except -1
