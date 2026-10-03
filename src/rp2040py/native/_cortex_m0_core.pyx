# cython: language_level=3, boundscheck=False, wraparound=False, cdivision=True, freethreading_compatible=True
"""The native Cortex-M0+ core: a Python-facing shell over the C++ `Cpu` of `core/cpu.hpp` (docs/records/0096-cpp-mcu-core.md,
Phase 2 step 4b). The instruction decode, the ~90 handlers, the exception model and the interrupt priorities all run in C++;
`_cortex_m0_core.py` stays the pure-Python reference and `tests/test_cpu_parity.py` holds the two to identical behaviour.

The C++ core calls the bus (`core/bus.hpp`, owned by `RP2040`) directly. What it cannot do itself goes through a `CpuHost` of
trampolines: `RP2040.on_break` (BKPT/UDF), the call-tracing hook `bl_taken` (called only while one is installed - the default no-op
costs nothing per BL/BLX any more) and the logger. A Python error raised inside any of them, or inside a peripheral the bus
reaches, is parked in the shared slot of `_pending.pyx`; the C++ core notices the flag after the call, stops the instruction
at that point and returns a fault, and `execute_instruction()` re-raises. Machine state is left as the Python exception left
it: earlier effects of the instruction stay, later ones (the destination register of a failed load, the cycle count) do not happen.

The architectural state is exposed field by field as properties (same names, same masking as the Python core's attributes);
`registers` and `interrupt_priorities` are memoryviews over the C++ arrays.
"""

from libc.stdint cimport int64_t, uint32_t
from libcpp cimport bool

from rp2040py.native._cpu cimport (
    CpuHost,
    kCpuFault,
    kCpuLogInstrUnimplemented,
    kCpuLogMrsUnimplemented,
    kCpuLogSev,
    kCpuLogYield,
)
from rp2040py.native._pending cimport park_error, pending_flag, raise_if_pending

from rp2040py.irq import MAX_HARDWARE_IRQ

__all__ = ("SYSM_CONTROL", "SYSM_MSP", "SYSM_PRIMASK", "SYSM_PSP", "CortexM0Core")

# Plain module globals: part of the public API mirrored from _cortex_m0_core.py (rp2040py.native/__init__.py re-exports these).
SYSM_MSP = 8
SYSM_PSP = 9
SYSM_PRIMASK = 16
SYSM_CONTROL = 20

LOG_NAME = "CortexM0Core"

cdef const int MODE_THREAD = 0
cdef const int SP_MAIN = 0
cdef unsigned int MAX_HW_IRQ = <unsigned int> MAX_HARDWARE_IRQ


cdef void _break_trampoline(void* ctx, uint32_t imm) noexcept:
    cdef CortexM0Core core = <CortexM0Core> ctx
    try:
        core.rp2040.on_break(imm)
    except BaseException as error:
        park_error(error)


cdef void _bl_trampoline(void* ctx, bool blx) noexcept:
    cdef CortexM0Core core = <CortexM0Core> ctx
    try:
        core._bl_taken(core, True if blx else False)
    except BaseException as error:
        park_error(error)


cdef void _log_trampoline(void* ctx, uint32_t kind, uint32_t a, uint32_t b, uint32_t c) noexcept:
    cdef CortexM0Core core = <CortexM0Core> ctx
    try:
        if kind == kCpuLogMrsUnimplemented:
            core.rp2040.logger.warning(LOG_NAME, f"MRS with unimplemented SYSm value: {a}")
        elif kind == kCpuLogInstrUnimplemented:
            core.rp2040.logger.warning(LOG_NAME, f"Warning: Instruction at {a:x} is not implemented yet!")
            core.rp2040.logger.warning(LOG_NAME, f"Opcode: 0x{b:x} (0x{c:x})")
        elif kind == kCpuLogSev:
            core.rp2040.logger.info(LOG_NAME, "SEV")
        elif kind == kCpuLogYield:
            core.rp2040.logger.info(LOG_NAME, "Yield")
    except BaseException as error:
        park_error(error)


cdef class CortexM0Core:
    def __cinit__(self, *args, **kwargs):
        # The views are over the C++ arrays inside this very object: no allocation, no copy, valid as long as it lives.
        self.registers = <unsigned int[:16]> self._cpu.registers
        self.interrupt_priorities = <unsigned int[:4]> self._cpu.interrupt_priorities

    def __init__(self, rp2040):
        cdef CpuHost host
        self.rp2040 = rp2040
        self._default_bl_taken = lambda core, blx: None
        self._bl_taken = self._default_bl_taken
        host.on_break = _break_trampoline
        host.bl_taken = _bl_trampoline
        host.log = _log_trampoline
        host.failed = pending_flag()
        host.ctx = <void*> self
        self._cpu.init(&self.rp2040._bus, host, MAX_HW_IRQ)
        self._cpu.registers[13] = 0xFFFFFFFCU
        self._cpu.banked_sp = 0xFFFFFFFCU

    @property
    def logger(self):
        return self.rp2040.logger

    @property
    def bl_taken(self):
        return self._bl_taken

    @bl_taken.setter
    def bl_taken(self, value):
        # Only a real hook is called from the instruction loop; the default no-op never leaves C++.
        self._bl_taken = value
        self._cpu.bl_hook_enabled = value is not self._default_bl_taken

    cpdef reset(self):
        # Full architectural reset - mirrors _cortex_m0_core.py's reset() (RPWatchdog.on_watchdog_trigger reuses it for a
        # live, mid-execution reset, not just construction-time setup).
        if not self._cpu.reset():
            raise_if_pending()

    @property
    def banked_sp(self):
        return self._cpu.banked_sp

    @banked_sp.setter
    def banked_sp(self, value):
        self._cpu.banked_sp = <uint32_t> (value & 0xFFFFFFFFU)

    @property
    def break_rewind(self):
        return self._cpu.break_rewind

    @break_rewind.setter
    def break_rewind(self, value):
        self._cpu.break_rewind = <uint32_t> (value & 0xFFFFFFFFU)

    @property
    def ipsr(self):
        return self._cpu.ipsr

    @ipsr.setter
    def ipsr(self, value):
        self._cpu.ipsr = <uint32_t> (value & 0xFFFFFFFFU)

    @property
    def interrupt_nmi_mask(self):
        return self._cpu.interrupt_nmi_mask

    @interrupt_nmi_mask.setter
    def interrupt_nmi_mask(self, value):
        self._cpu.interrupt_nmi_mask = <uint32_t> (value & 0xFFFFFFFFU)

    @property
    def pending_interrupts(self):
        return self._cpu.pending_interrupts

    @pending_interrupts.setter
    def pending_interrupts(self, value):
        self._cpu.pending_interrupts = <uint32_t> (value & 0xFFFFFFFFU)

    @property
    def enabled_interrupts(self):
        return self._cpu.enabled_interrupts

    @enabled_interrupts.setter
    def enabled_interrupts(self, value):
        self._cpu.enabled_interrupts = <uint32_t> (value & 0xFFFFFFFFU)

    @property
    def vtor(self):
        return self._cpu.vtor

    @vtor.setter
    def vtor(self, value):
        self._cpu.vtor = <uint32_t> (value & 0xFFFFFFFFU)

    @property
    def shpr2(self):
        return self._cpu.shpr2

    @shpr2.setter
    def shpr2(self, value):
        self._cpu.shpr2 = <uint32_t> (value & 0xFFFFFFFFU)

    @property
    def shpr3(self):
        return self._cpu.shpr3

    @shpr3.setter
    def shpr3(self, value):
        self._cpu.shpr3 = <uint32_t> (value & 0xFFFFFFFFU)

    @property
    def event_registered(self):
        return self._cpu.event_registered

    @event_registered.setter
    def event_registered(self, value):
        self._cpu.event_registered = True if value else False

    @property
    def waiting(self):
        return self._cpu.waiting

    @waiting.setter
    def waiting(self, value):
        self._cpu.waiting = True if value else False

    @property
    def n(self):
        return self._cpu.n

    @n.setter
    def n(self, value):
        self._cpu.n = True if value else False

    @property
    def c(self):
        return self._cpu.c

    @c.setter
    def c(self, value):
        self._cpu.c = True if value else False

    @property
    def z(self):
        return self._cpu.z

    @z.setter
    def z(self, value):
        self._cpu.z = True if value else False

    @property
    def v(self):
        return self._cpu.v

    @v.setter
    def v(self, value):
        self._cpu.v = True if value else False

    @property
    def pm(self):
        return self._cpu.pm

    @pm.setter
    def pm(self, value):
        self._cpu.pm = True if value else False

    @property
    def n_priv(self):
        return self._cpu.n_priv

    @n_priv.setter
    def n_priv(self, value):
        self._cpu.n_priv = True if value else False

    @property
    def pending_nmi(self):
        return self._cpu.pending_nmi

    @pending_nmi.setter
    def pending_nmi(self, value):
        self._cpu.pending_nmi = True if value else False

    @property
    def pending_pend_sv(self):
        return self._cpu.pending_pend_sv

    @pending_pend_sv.setter
    def pending_pend_sv(self, value):
        self._cpu.pending_pend_sv = True if value else False

    @property
    def pending_svcall(self):
        return self._cpu.pending_svcall

    @pending_svcall.setter
    def pending_svcall(self, value):
        self._cpu.pending_svcall = True if value else False

    @property
    def pending_systick(self):
        return self._cpu.pending_systick

    @pending_systick.setter
    def pending_systick(self, value):
        self._cpu.pending_systick = True if value else False

    @property
    def interrupts_updated(self):
        return self._cpu.interrupts_updated

    @interrupts_updated.setter
    def interrupts_updated(self, value):
        self._cpu.interrupts_updated = True if value else False

    @property
    def sp_sel(self):
        return self._cpu.sp_sel

    @sp_sel.setter
    def sp_sel(self, value):
        self._cpu.sp_sel = <int> value

    @property
    def current_mode(self):
        return self._cpu.current_mode

    @current_mode.setter
    def current_mode(self, value):
        self._cpu.current_mode = <int> value

    # --- registers and status, with the Python properties' masking ----------------------------------------------

    @property
    def sp(self):
        return self._cpu.registers[13]

    @sp.setter
    def sp(self, value):
        self._cpu.set_sp(<uint32_t> (value & 0xFFFFFFFFU))

    @property
    def lr(self):
        return self._cpu.registers[14]

    @lr.setter
    def lr(self, value):
        self._cpu.registers[14] = <uint32_t> (value & 0xFFFFFFFFU)

    @property
    def pc(self):
        return self._cpu.registers[15]

    @pc.setter
    def pc(self, value):
        self._cpu.registers[15] = <uint32_t> (value & 0xFFFFFFFFU)

    @property
    def cycles(self):
        return self._cpu.cycles

    @cycles.setter
    def cycles(self, value):
        self._cpu.cycles = <unsigned long long> value

    @property
    def apsr(self):
        return self._cpu.apsr()

    @apsr.setter
    def apsr(self, value):
        self._cpu.set_apsr(<uint32_t> (value & 0xFFFFFFFFU))

    @property
    def x_psr(self):
        return self._cpu.x_psr()

    @x_psr.setter
    def x_psr(self, value):
        self._cpu.set_x_psr(<uint32_t> (value & 0xFFFFFFFFU))

    @property
    def sp_process(self):
        return self._cpu.sp_process()

    @sp_process.setter
    def sp_process(self, value):
        self._cpu.set_sp_process(<uint32_t> (value & 0xFFFFFFFFU))

    @property
    def sp_main(self):
        return self._cpu.sp_main()

    @sp_main.setter
    def sp_main(self, value):
        self._cpu.set_sp_main(<uint32_t> (value & 0xFFFFFFFFU))

    @property
    def pend_sv_priority(self):
        return self._cpu.pend_sv_priority()

    @property
    def sv_call_priority(self):
        return self._cpu.sv_call_priority()

    @property
    def systick_priority(self):
        return self._cpu.systick_priority()

    @property
    def vect_pending(self):
        return self._cpu.vect_pending()

    def check_condition(self, cond) -> bool:
        return self._cpu.check_condition(<uint32_t> cond)

    def switch_stack(self, stack) -> None:
        self._cpu.switch_stack(<int> stack)

    def exception_entry(self, exception_number) -> None:
        if not self._cpu.exception_entry(<uint32_t> exception_number):
            raise_if_pending()

    def exception_return(self, exc_return) -> None:
        if not self._cpu.exception_return(<uint32_t> exc_return):
            raise_if_pending()

    def exception_priority(self, n) -> int:
        return self._cpu.exception_priority(<uint32_t> n)

    cpdef set_interrupt(self, irq, value):
        if not self._cpu.set_interrupt(<uint32_t> irq, True if value else False):
            raise_if_pending()

    def check_for_interrupts(self) -> bool:
        cdef int result = self._cpu.check_for_interrupts()
        if result < 0:
            raise_if_pending()
        return result > 0

    def read_special_register(self, sysm) -> int:
        cdef uint32_t value
        if not self._cpu.read_special_register(<uint32_t> sysm, &value):
            raise_if_pending()
        return value

    def write_special_register(self, sysm, value) -> None:
        if not self._cpu.write_special_register(<uint32_t> sysm, <uint32_t> (value & 0xFFFFFFFFU)):
            raise_if_pending()

    cpdef int execute_instruction(self) except -1:
        cdef int delta = self._cpu.execute()
        if delta < 0:
            raise_if_pending()
            raise RuntimeError("the C++ core faulted without a parked Python error")
        return delta
