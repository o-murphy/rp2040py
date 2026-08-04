# cython: language_level=3, boundscheck=False, wraparound=False, cdivision=True
"""Real, fully-typed Cython port of rp2040py._cortex_m0_core.CortexM0Core.

Every method parameter and local that's on the per-instruction hot path is genuinely C-typed
(not just class fields, see docs/PORTING.md's Cython section) and instruction dispatch goes
through a real C function-pointer table (DISPATCH_TABLE), not a Python-level list of bound
methods - both were measured necessary to get a real speedup (a field-only port measured ~2-9%;
this pattern measured ~11x on a realistic instruction mix in isolation).

self.rp2040 stays a plain Python object reference: bus reads/writes (read_uint32 etc.) are still
regular Python method calls from here. Porting the RP2040 bus's own hot paths (rp2040py/native/
rp2040.pyx) narrows that boundary further, but every instruction fetch crosses it regardless, so
this alone does not reach the isolated ~11x figure - see docs/BACKLOG.md for real, measured
full-boot numbers.

Semantics (flag formulas, cycle counts, PC handling, shift-by-0/32 edge cases) are transcribed
1:1 from _cortex_m0_core.py - that file remains the readable reference; comments here are limited
to why a particular C-level construct was necessary to preserve Python's arbitrary-precision
integer semantics safely (see u32/s32, cimported below from rp2040py.native._bit).
"""

from rp2040py.native._bit cimport s32, u32

from rp2040py.irq import MAX_HARDWARE_IRQ
from rp2040py.memory_map import APB_START_ADDRESS, SIO_START_ADDRESS

# Deliberately NOT `from cpython cimport array`: that pulls in CPython-internal array.array
# struct access, which doesn't compile under Py_LIMITED_API (confirmed via an actual abi3 build
# attempt - GCC errors on incomplete-type access to PyTypeObject/arrayobject internals). All we
# actually do with `array` is call the plain array.array(...) constructor, which needs no special
# declaration - a normal Python-level call works identically and is limited-API-safe.
import array

# ---------------------------------------------------------------------------------------------
# Module constants (mirrors _cortex_m0_core.py's module-level constants)
# ---------------------------------------------------------------------------------------------

cdef const unsigned int EXC_RESET = 1
cdef const unsigned int EXC_NMI = 2
cdef const unsigned int EXC_HARDFAULT = 3
cdef const unsigned int EXC_SVCALL = 11
cdef const unsigned int EXC_PENDSV = 14
cdef const unsigned int EXC_SYSTICK = 15

cdef const unsigned int SYSM_APSR = 0
cdef const unsigned int SYSM_XPSR = 3
cdef const unsigned int SYSM_IPSR = 5

# Plain (not cdef) module globals: part of the public API mirrored from _cortex_m0_core.py
# (rp2040py.native/__init__.py re-exports these), so they need to be real Python attributes,
# not C-only constants.
SYSM_MSP = 8
SYSM_PSP = 9
SYSM_PRIMASK = 16
SYSM_CONTROL = 20

cdef const unsigned int LOWEST_PRIORITY = 4

cdef const int MODE_THREAD = 0
cdef const int MODE_HANDLER = 1

cdef const unsigned int SP_REGISTER = 13
cdef const unsigned int PC_REGISTER = 15

cdef const int SP_MAIN = 0
cdef const int SP_PROCESS = 1

cdef const unsigned int WIDE_RANGE_START = 0xF000
cdef const unsigned int WIDE_RANGE_END = 0xF800

# Not `const`: Cython requires a const global's initializer to be a compile-time constant, and
# these are cast from Python-level values imported from rp2040py.memory_map/irq at module load.
cdef unsigned int SIO_START = <unsigned int> SIO_START_ADDRESS
cdef unsigned int APB_START = <unsigned int> APB_START_ADDRESS
cdef unsigned int MAX_HW_IRQ = <unsigned int> MAX_HARDWARE_IRQ

LOG_NAME = "CortexM0Core"


# ---------------------------------------------------------------------------------------------
# Narrow-to-32-bit helpers.
#
# Register-width arithmetic (add/sub/shift) is done in `long long`/`int` C locals rather than
# `unsigned int` throughout, because several of the source formulas rely on Python's
# arbitrary-precision `&`/`>>` behaving correctly on transiently negative or >32-bit
# intermediates (e.g. _subtract_update_flags's `result = minuend - subtrahend`, which can go
# negative before the caller masks it). u32/s32 (cimported above from rp2040py.native._bit)
# reproduce Python's `& 0xFFFFFFFF` / signed 32-bit reinterpretation on a `long long` explicitly,
# rather than relying on C's implementation-defined-pre-C23 signed-negative bitwise-AND behavior.
# ---------------------------------------------------------------------------------------------

cdef inline int sign_extend8(unsigned int value) noexcept:
    # s32 is cpdef (Python-callable too), so it requires the GIL - can't be nogil here.
    cdef unsigned int shifted = (value << 24) & 0xFFFFFFFFU
    return s32(<long long> shifted) >> 24


cdef inline int sign_extend16(unsigned int value) noexcept:
    cdef unsigned int shifted = (value << 16) & 0xFFFFFFFFU
    return s32(<long long> shifted) >> 16


cdef class CortexM0Core:
    def __cinit__(self):
        # Buffer-backed memoryview fields are allocated here, not __init__: __cinit__ is
        # guaranteed to run exactly once as part of object allocation (before any subclass
        # __init__ could skip calling super().__init__() and leave these fields unset), per
        # Cython's __cinit__/__init__ split. Ignores the `rp2040` constructor arg entirely -
        # Cython passes it through but a no-args-besides-self __cinit__ just ignores it.
        self.registers = array.array("I", [0] * 16)
        self.interrupt_priorities = array.array("I", [0xFFFFFFFFU, 0, 0, 0])

    def __init__(self, rp2040):
        self.rp2040 = rp2040
        self.banked_sp = 0
        self.cycles = 0

        self.event_registered = False
        self.waiting = False

        self.n = False
        self.c = False
        self.z = False
        self.v = False

        self.break_rewind = 0

        self.pm = False

        self.sp_sel = SP_MAIN
        self.n_priv = False

        self.current_mode = MODE_THREAD
        self.ipsr = 0
        self.interrupt_nmi_mask = 0
        self.pending_interrupts = 0
        self.enabled_interrupts = 0
        self.pending_nmi = False
        self.pending_pend_sv = False
        self.pending_svcall = False
        self.pending_systick = False
        self.interrupts_updated = False
        self.vtor = 0
        self.shpr2 = 0
        self.shpr3 = 0

        self.bl_taken = lambda core, blx: None

        self.registers[13] = 0xFFFFFFFCU
        self.banked_sp = 0xFFFFFFFCU

    @property
    def logger(self):
        return self.rp2040.logger

    cpdef reset(self):
        self.registers[13] = (<unsigned int> self.rp2040.read_uint32(self.vtor)) & 0xFFFFFFFCU
        self.registers[15] = (<unsigned int> self.rp2040.read_uint32(self.vtor + 4)) & 0xFFFFFFFEU
        self.cycles = 0

    @property
    def sp(self):
        return self.registers[13]
    
    @sp.setter
    def sp(self, value):
        self.registers[13] = <unsigned int> (value & 0xFFFFFFFCU)

    @property
    def lr(self):
        return self.registers[14]
        
    @lr.setter
    def lr(self, value):
        self.registers[14] = <unsigned int> (value & 0xFFFFFFFFU)

    @property
    def pc(self):
        return self.registers[15]
    
    @pc.setter    
    def pc(self, value):
        self.registers[15] = <unsigned int> (value & 0xFFFFFFFFU)

    @property
    def apsr(self):
        cdef unsigned int result = 0
        if self.n:
            result |= 0x80000000U
        if self.z:
            result |= 0x40000000
        if self.c:
            result |= 0x20000000
        if self.v:
            result |= 0x10000000
        return result

    @apsr.setter
    def apsr(self, value):
        cdef unsigned int v = <unsigned int> (value & 0xFFFFFFFFU)
        self.n = bool(v & 0x80000000U)
        self.z = bool(v & 0x40000000)
        self.c = bool(v & 0x20000000)
        self.v = bool(v & 0x10000000)

    @property
    def x_psr(self):
        return self.apsr | self.ipsr | (1 << 24)
    
    @x_psr.setter
    def x_psr(self, value):
        self.apsr = value
        self.ipsr = <unsigned int> (value & 0x3F)

    def check_condition(self, cond) -> bool:
        cdef unsigned int c = <unsigned int> cond
        cdef unsigned int base = c >> 1
        cdef bint result = False
        if base == 0b000:
            result = self.z
        elif base == 0b001:
            result = self.c
        elif base == 0b010:
            result = self.n
        elif base == 0b011:
            result = self.v
        elif base == 0b100:
            result = self.c and not self.z
        elif base == 0b101:
            result = self.n == self.v
        elif base == 0b110:
            result = (self.n == self.v) and not self.z
        elif base == 0b111:
            result = True
        if (c & 0b1) and c != 0b1111:
            return not result
        return result

    def switch_stack(self, stack) -> None:
        cdef unsigned int temp
        if self.sp_sel != stack:
            temp = self.registers[13]
            self.registers[13] = self.banked_sp
            self.banked_sp = temp
            self.sp_sel = stack

    @property
    def sp_process(self):
        return self.sp if self.sp_sel == SP_PROCESS else self.banked_sp
    
    @sp_process.setter
    def sp_process(self, value):
        if self.sp_sel == SP_PROCESS:
            self.sp = value
        else:
            self.banked_sp = <unsigned int> (value & 0xFFFFFFFFU)

    @property
    def sp_main(self):
        return self.sp if self.sp_sel == SP_MAIN else self.banked_sp

    @sp_main.setter
    def sp_main(self, value):
        if self.sp_sel == SP_MAIN:
            self.sp = value
        else:
            self.banked_sp = <unsigned int> (value & 0xFFFFFFFFU)

    def exception_entry(self, exception_number) -> None:
        cdef unsigned int frame_ptr = 0
        cdef unsigned int frame_ptr_align = 0
        if self.sp_sel and self.current_mode == MODE_THREAD:
            frame_ptr_align = 1 if (self.sp_process & 0b100) else 0
            self.sp_process = (self.sp_process - 0x20) & ~0b100
            frame_ptr = self.sp_process
        else:
            frame_ptr_align = 1 if (self.sp_main & 0b100) else 0
            self.sp_main = (self.sp_main - 0x20) & ~0b100
            frame_ptr = self.sp_main
        # only the stack locations, not the store order, are architected
        self.rp2040.write_uint32(frame_ptr, self.registers[0])
        self.rp2040.write_uint32(frame_ptr + 0x4, self.registers[1])
        self.rp2040.write_uint32(frame_ptr + 0x8, self.registers[2])
        self.rp2040.write_uint32(frame_ptr + 0xC, self.registers[3])
        self.rp2040.write_uint32(frame_ptr + 0x10, self.registers[12])
        self.rp2040.write_uint32(frame_ptr + 0x14, self.lr)
        self.rp2040.write_uint32(frame_ptr + 0x18, self.pc & ~1)  # ReturnAddress(ExceptionType);
        self.rp2040.write_uint32(frame_ptr + 0x1C, (self.x_psr & ~(1 << 9)) | (frame_ptr_align << 9))
        if self.current_mode == MODE_HANDLER:
            self.lr = 0xFFFFFFF1U
        elif not self.sp_sel:
            self.lr = 0xFFFFFFF9U
        else:
            self.lr = 0xFFFFFFFDU
        # ExceptionTaken:
        self.current_mode = MODE_HANDLER  # Enter Handler Mode, now Privileged
        self.ipsr = exception_number
        self.switch_stack(SP_MAIN)
        self.event_registered = True
        vector_table = self.vtor
        self.pc = self.rp2040.read_uint32(vector_table + 4 * exception_number)

    def exception_return(self, exc_return) -> None:
        cdef unsigned int frame_ptr = self.sp_main
        cdef unsigned int selector = exc_return & 0xF
        cdef unsigned int psr
        cdef unsigned int frame_ptr_align
        cdef bint force_thread
        if selector == 0b0001:  # Return to Handler
            self.current_mode = MODE_HANDLER
            self.switch_stack(SP_MAIN)
        elif selector == 0b1001:  # Return to Thread using Main stack
            self.current_mode = MODE_THREAD
            self.switch_stack(SP_MAIN)
        elif selector == 0b1101:  # Return to Thread using Process stack
            frame_ptr = self.sp_process
            self.current_mode = MODE_THREAD
            self.switch_stack(SP_PROCESS)
        # Assigning current_mode to MODE_THREAD causes a drop in privilege
        # if CONTROL.nPRIV is set to 1

        # PopStack:
        self.registers[0] = self.rp2040.read_uint32(frame_ptr)
        self.registers[1] = self.rp2040.read_uint32(frame_ptr + 0x4)
        self.registers[2] = self.rp2040.read_uint32(frame_ptr + 0x8)
        self.registers[3] = self.rp2040.read_uint32(frame_ptr + 0xC)
        self.registers[12] = self.rp2040.read_uint32(frame_ptr + 0x10)
        self.lr = self.rp2040.read_uint32(frame_ptr + 0x14)
        self.pc = self.rp2040.read_uint32(frame_ptr + 0x18)
        psr = self.rp2040.read_uint32(frame_ptr + 0x1C)

        frame_ptr_align = 0b100 if (psr & (1 << 9)) else 0

        if selector == 0b0001 or selector == 0b1001:
            self.sp_main = (self.sp_main + 0x20) | frame_ptr_align
        elif selector == 0b1101:
            self.sp_process = (self.sp_process + 0x20) | frame_ptr_align

        self.apsr = psr & 0xF0000000U
        force_thread = (self.current_mode == MODE_THREAD) and self.n_priv
        self.ipsr = 0 if force_thread else (psr & 0x3F)
        self.interrupts_updated = True
        # Thumb bit should always be one! EPSR<24> = psr<24>; // Load valid EPSR bits from memory
        self.event_registered = True

    @property
    def pend_sv_priority(self):
        return (self.shpr3 >> 22) & 0x3

    @property
    def sv_call_priority(self):
        return self.shpr2 >> 30

    @property
    def systick_priority(self):
        return self.shpr3 >> 30

    def exception_priority(self, n) -> int:
        cdef unsigned int int_num
        cdef unsigned int priority
        if n == EXC_RESET:
            return -3
        if n == EXC_NMI:
            return -2
        if n == EXC_HARDFAULT:
            return -1
        if n == EXC_SVCALL:
            return self.sv_call_priority
        if n == EXC_PENDSV:
            return self.pend_sv_priority
        if n == EXC_SYSTICK:
            return self.systick_priority
        if n < 16:
            return LOWEST_PRIORITY
        int_num = n - 16
        for priority in range(4):
            if self.interrupt_priorities[priority] & (1 << int_num):
                return priority
        return LOWEST_PRIORITY

    @property
    def vect_pending(self):
        cdef unsigned int sv_call_priority, systick_priority, pend_sv_priority
        cdef unsigned int pending_interrupts, priority, level_interrupts, interrupt_number
        if self.pending_nmi:
            return EXC_NMI
        sv_call_priority = self.sv_call_priority
        systick_priority = self.systick_priority
        pend_sv_priority = self.pend_sv_priority
        pending_interrupts = self.pending_interrupts
        for priority in range(LOWEST_PRIORITY):
            level_interrupts = pending_interrupts & self.interrupt_priorities[priority]
            if self.pending_svcall and priority == sv_call_priority:
                return EXC_SVCALL
            if self.pending_pend_sv and priority == pend_sv_priority:
                return EXC_PENDSV
            if self.pending_systick and priority == systick_priority:
                return EXC_SYSTICK
            if level_interrupts:
                for interrupt_number in range(32):
                    if level_interrupts & (1 << interrupt_number):
                        return 16 + interrupt_number
        return 0

    cpdef set_interrupt(self, irq, value):
        cdef unsigned int irq_bit = <unsigned int> (1 << irq)
        if value and not (self.pending_interrupts & irq_bit):
            self.pending_interrupts |= irq_bit
            self.interrupts_updated = True
            if self.waiting and self.check_for_interrupts():
                self.waiting = False
        elif not value:
            self.pending_interrupts &= ~irq_bit

    def check_for_interrupts(self) -> bool:
        cdef int current_priority
        cdef int p
        cdef unsigned int interrupt_set, sv_call_priority, systick_priority, pend_sv_priority
        cdef unsigned int priority, level_interrupts, interrupt_number
        if self.waiting:
            current_priority = self.exception_priority(self.ipsr) if self.pm else LOWEST_PRIORITY
        else:
            current_priority = min(self.exception_priority(self.ipsr), 0 if self.pm else LOWEST_PRIORITY)
        interrupt_set = self.pending_interrupts & self.enabled_interrupts
        sv_call_priority = self.sv_call_priority
        systick_priority = self.systick_priority
        pend_sv_priority = self.pend_sv_priority
        if self.pending_nmi:
            self.pending_nmi = False
            self.exception_entry(EXC_NMI)
            return True
        for p in range(current_priority):
            priority = <unsigned int> p
            level_interrupts = interrupt_set & self.interrupt_priorities[priority]
            if self.pending_svcall and priority == sv_call_priority:
                self.pending_svcall = False
                self.exception_entry(EXC_SVCALL)
                return True
            if self.pending_pend_sv and priority == pend_sv_priority:
                self.pending_pend_sv = False
                self.exception_entry(EXC_PENDSV)
                return True
            if self.pending_systick and priority == systick_priority:
                self.pending_systick = False
                self.exception_entry(EXC_SYSTICK)
                return True
            if level_interrupts:
                for interrupt_number in range(32):
                    if level_interrupts & (1 << interrupt_number):
                        if interrupt_number > MAX_HW_IRQ:
                            self.pending_interrupts &= ~(<unsigned int> (1 << interrupt_number))
                        self.exception_entry(16 + interrupt_number)
                        return True
        self.interrupts_updated = False
        return False

    def read_special_register(self, sysm) -> int:
        if sysm == SYSM_APSR:
            return self.apsr
        if sysm == SYSM_XPSR:
            return self.x_psr
        if sysm == SYSM_IPSR:
            return self.ipsr
        if sysm == SYSM_PRIMASK:
            return 1 if self.pm else 0
        if sysm == SYSM_MSP:
            return self.sp_main
        if sysm == SYSM_PSP:
            return self.sp_process
        if sysm == SYSM_CONTROL:
            return (2 if self.sp_sel == SP_PROCESS else 0) | (1 if self.n_priv else 0)
        self.rp2040.logger.warning(LOG_NAME, f"MRS with unimplemented SYSm value: {sysm}")
        return 0

    def write_special_register(self, sysm, value) -> None:
        if sysm == SYSM_APSR:
            self.apsr = value
        elif sysm == SYSM_XPSR:
            self.x_psr = value
        elif sysm == SYSM_IPSR:
            self.ipsr = value
        elif sysm == SYSM_PRIMASK:
            self.pm = bool(value & 1)
            self.interrupts_updated = True
        elif sysm == SYSM_MSP:
            self.sp_main = value
        elif sysm == SYSM_PSP:
            self.sp_process = value
        elif sysm == SYSM_CONTROL:
            self.n_priv = bool(value & 1)
            if self.current_mode == MODE_THREAD:
                self.switch_stack(SP_PROCESS if (value & 2) else SP_MAIN)
        else:
            self.rp2040.logger.warning(LOG_NAME, f"MRS with unimplemented SYSm value: {sysm}")

    cpdef int execute_instruction(self) except -1:
        cdef unsigned int opcode_pc, opcode, opcode2
        cdef bint wide_instruction
        cdef OpHandler handler
        cdef int delta_cycles
        if self.interrupts_updated and self.check_for_interrupts():
            self.waiting = False
        # ARM Thumb instruction encoding - 16 bits / 2 bytes
        opcode_pc = self.registers[15] & 0xFFFFFFFEU  # ensure no LSB set PC are executed
        opcode = self.rp2040.read_uint16(opcode_pc)
        wide_instruction = (opcode >> 12) == 0b1111 or (opcode >> 11) == 0b11101
        opcode2 = self.rp2040.read_uint16(opcode_pc + 2) if wide_instruction else 0
        self.registers[15] = (self.registers[15] + 2) & 0xFFFFFFFFU

        if WIDE_RANGE_START <= opcode < WIDE_RANGE_END:
            handler = resolve_wide(opcode, opcode2)
        else:
            handler = DISPATCH_TABLE[opcode]

        if handler != NULL:
            delta_cycles = handler(self, opcode, opcode2, opcode_pc)
        else:
            delta_cycles = 1
            self.rp2040.logger.warning(LOG_NAME, f"Warning: Instruction at {opcode_pc:x} is not implemented yet!")
            self.rp2040.logger.warning(LOG_NAME, f"Opcode: 0x{opcode:x} (0x{opcode2:x})")

        self.cycles += delta_cycles
        return delta_cycles


# ---------------------------------------------------------------------------------------------
# Module-level helpers used by op_* handlers below. Free functions rather than bound methods so
# they can sit behind a real C function pointer in DISPATCH_TABLE (see docs/PORTING.md) and so
# calling them from an op_* handler is a direct C call, not a Python-level method lookup.
# ---------------------------------------------------------------------------------------------

cdef inline bint check_condition(CortexM0Core core, unsigned int cond) noexcept:
    return core.check_condition(cond)


cdef inline int cycles_io(unsigned int addr, bint write) noexcept:
    if SIO_START <= addr < SIO_START + 0x10000000:
        return 0
    if APB_START <= addr < APB_START + 0x10000000:
        return 4 if write else 3
    return 1


cdef void bx_write_pc(CortexM0Core core, unsigned int address) except *:
    if core.current_mode == MODE_HANDLER and (address >> 28) == 0xF:
        core.exception_return(address & 0x0FFFFFFF)
    else:
        core.registers[15] = address & 0xFFFFFFFEU


cdef inline long long add_update_flags(CortexM0Core core, long long addend1, long long addend2) noexcept:
    cdef long long raw_result = addend1 + addend2
    cdef unsigned int unsigned_sum = u32(raw_result)
    cdef long long signed_sum = (<long long> s32(addend1)) + (<long long> s32(addend2))
    core.n = (raw_result & 0x80000000U) != 0
    core.z = (raw_result & 0xFFFFFFFFLL) == 0
    core.c = raw_result != (<long long> unsigned_sum)
    core.v = s32(raw_result) != signed_sum
    return <long long> unsigned_sum


cdef inline long long subtract_update_flags(CortexM0Core core, long long minuend, long long subtrahend) noexcept:
    cdef long long result = minuend - subtrahend
    cdef unsigned int result_u = u32(result)
    cdef unsigned int minuend_u = u32(minuend)
    cdef unsigned int subtrahend_u = u32(subtrahend)
    core.n = (result_u & 0x80000000U) != 0
    core.z = result_u == 0
    core.c = minuend >= subtrahend
    core.v = (
        ((result_u & 0x80000000U) != 0 and (minuend_u & 0x80000000U) == 0 and (subtrahend_u & 0x80000000U) != 0)
        or ((result_u & 0x80000000U) == 0 and (minuend_u & 0x80000000U) != 0 and (subtrahend_u & 0x80000000U) == 0)
    )
    return result


# ---------------------------------------------------------------------------------------------
# Instruction handlers. Transcribed 1:1 from _cortex_m0_core.py's _op_* methods - see that file
# for the semantic reference and inline commentary on ARM edge cases (shift-by-0/32 etc). The
# only behavioral additions here are the C-level shift-amount guards noted per-handler, needed
# because C's `<<`/`>>` by >= the operand width is undefined behavior where Python's is not.
# ---------------------------------------------------------------------------------------------

cdef int op_adcs(CortexM0Core core, unsigned int opcode, unsigned int opcode2, unsigned int opcode_pc) except -1:
    # ADCS
    cdef unsigned int rm = (opcode >> 3) & 0x7
    cdef unsigned int rdn = opcode & 0x7
    core.registers[rdn] = <unsigned int> add_update_flags(
        core, core.registers[rm], core.registers[rdn] + (1 if core.c else 0)
    )
    return 1


cdef int op_add_register_sp_plus_immediate(
    CortexM0Core core, unsigned int opcode, unsigned int opcode2, unsigned int opcode_pc
) except -1:
    # ADD (register = SP plus immediate)
    cdef unsigned int imm8 = opcode & 0xFF
    cdef unsigned int rd = (opcode >> 8) & 0x7
    core.registers[rd] = core.registers[13] + (imm8 << 2)
    return 1


cdef int op_add_sp_plus_immediate(
    CortexM0Core core, unsigned int opcode, unsigned int opcode2, unsigned int opcode_pc
) except -1:
    # ADD (SP plus immediate)
    cdef unsigned int imm32 = (opcode & 0x7F) << 2
    core.registers[13] = (core.registers[13] + imm32) & 0xFFFFFFFCU
    return 1


cdef int op_adds_encoding_t1(
    CortexM0Core core, unsigned int opcode, unsigned int opcode2, unsigned int opcode_pc
) except -1:
    # ADDS (Encoding T1)
    cdef unsigned int imm3 = (opcode >> 6) & 0x7
    cdef unsigned int rn = (opcode >> 3) & 0x7
    cdef unsigned int rd = opcode & 0x7
    core.registers[rd] = <unsigned int> add_update_flags(core, core.registers[rn], imm3)
    return 1


cdef int op_adds_encoding_t2(
    CortexM0Core core, unsigned int opcode, unsigned int opcode2, unsigned int opcode_pc
) except -1:
    # ADDS (Encoding T2)
    cdef unsigned int imm8 = opcode & 0xFF
    cdef unsigned int rdn = (opcode >> 8) & 0x7
    core.registers[rdn] = <unsigned int> add_update_flags(core, core.registers[rdn], imm8)
    return 1


cdef int op_adds_register(CortexM0Core core, unsigned int opcode, unsigned int opcode2, unsigned int opcode_pc) except -1:
    # ADDS (register)
    cdef unsigned int rm = (opcode >> 6) & 0x7
    cdef unsigned int rn = (opcode >> 3) & 0x7
    cdef unsigned int rd = opcode & 0x7
    core.registers[rd] = <unsigned int> add_update_flags(core, core.registers[rn], core.registers[rm])
    return 1


cdef int op_add_register(CortexM0Core core, unsigned int opcode, unsigned int opcode2, unsigned int opcode_pc) except -1:
    # ADD (register)
    cdef int delta_cycles = 1
    cdef unsigned int rm = (opcode >> 3) & 0xF
    cdef unsigned int rdn = ((opcode & 0x80) >> 4) | (opcode & 0x7)
    cdef unsigned int left_value = core.registers[15] + 2 if rdn == PC_REGISTER else core.registers[rdn]
    cdef unsigned int right_value = core.registers[15] + 2 if rm == PC_REGISTER else core.registers[rm]
    cdef unsigned int result = left_value + right_value
    if rdn != SP_REGISTER and rdn != PC_REGISTER:
        core.registers[rdn] = result
    elif rdn == PC_REGISTER:
        core.registers[rdn] = result & 0xFFFFFFFEU
        delta_cycles += 1
    elif rdn == SP_REGISTER:
        core.registers[rdn] = result & 0xFFFFFFFCU
    return delta_cycles


cdef int op_adr(CortexM0Core core, unsigned int opcode, unsigned int opcode2, unsigned int opcode_pc) except -1:
    # ADR
    cdef unsigned int imm8 = opcode & 0xFF
    cdef unsigned int rd = (opcode >> 8) & 0x7
    core.registers[rd] = (opcode_pc & 0xFFFFFFFCU) + 4 + (imm8 << 2)
    return 1


cdef int op_ands_encoding_t2(CortexM0Core core, unsigned int opcode, unsigned int opcode2, unsigned int opcode_pc) except -1:
    # ANDS (Encoding T2)
    cdef unsigned int rm = (opcode >> 3) & 0x7
    cdef unsigned int rdn = opcode & 0x7
    cdef unsigned int result = core.registers[rdn] & core.registers[rm]
    core.registers[rdn] = result
    core.n = (result & 0x80000000U) != 0
    core.z = result == 0
    return 1


cdef int op_asrs_immediate(CortexM0Core core, unsigned int opcode, unsigned int opcode2, unsigned int opcode_pc) except -1:
    # ASRS (immediate)
    cdef unsigned int imm5 = (opcode >> 6) & 0x1F
    cdef unsigned int rm = (opcode >> 3) & 0x7
    cdef unsigned int rd = opcode & 0x7
    cdef unsigned int input_value = core.registers[rm]
    cdef unsigned int shift_n = imm5 if imm5 else 32
    cdef int result
    if shift_n < 32:
        result = s32(<long long> input_value) >> shift_n
    else:
        result = -1 if (input_value & 0x80000000U) else 0
    core.registers[rd] = <unsigned int> result
    core.n = (result & 0x80000000U) != 0
    core.z = u32(<long long> result) == 0
    core.c = (input_value & (<unsigned int> (1 << (shift_n - 1)))) != 0
    return 1


cdef int op_asrs_register(CortexM0Core core, unsigned int opcode, unsigned int opcode2, unsigned int opcode_pc) except -1:
    # ASRS (register)
    cdef unsigned int rm = (opcode >> 3) & 0x7
    cdef unsigned int rdn = opcode & 0x7
    cdef unsigned int input_value = core.registers[rdn]
    cdef unsigned int shift_n = min(32, core.registers[rm] & 255)
    cdef int result
    if shift_n < 32:
        result = s32(<long long> input_value) >> shift_n
    else:
        result = -1 if (input_value & 0x80000000U) else 0
    core.registers[rdn] = <unsigned int> result
    core.n = (result & 0x80000000U) != 0
    core.z = u32(<long long> result) == 0
    # NOTE: shift_n can be 0 here (unlike the immediate form above); `& 31` replicates the
    # source's JS-shift-wraparound-emulating mask so the C shift amount stays in [0, 31].
    core.c = (input_value & (<unsigned int> (1 << ((shift_n - 1) & 31)))) != 0
    return 1


cdef int op_b_with_cond(CortexM0Core core, unsigned int opcode, unsigned int opcode2, unsigned int opcode_pc) except -1:
    # B (with cond)
    cdef int delta_cycles = 1
    cdef int imm8 = <int> ((opcode & 0xFF) << 1)
    cdef unsigned int cond = (opcode >> 8) & 0xF
    if imm8 & (1 << 8):
        imm8 = (imm8 & 0x1FF) - 0x200
    if check_condition(core, cond):
        core.registers[15] = <unsigned int> (<long long> core.registers[15] + imm8 + 2)
        delta_cycles += 1
    return delta_cycles


cdef int op_b(CortexM0Core core, unsigned int opcode, unsigned int opcode2, unsigned int opcode_pc) except -1:
    # B
    cdef int imm11 = <int> ((opcode & 0x7FF) << 1)
    if imm11 & (1 << 11):
        imm11 = (imm11 & 0x7FF) - 0x800
    core.registers[15] = <unsigned int> (<long long> core.registers[15] + imm11 + 2)
    return 2


cdef int op_bics(CortexM0Core core, unsigned int opcode, unsigned int opcode2, unsigned int opcode_pc) except -1:
    # BICS
    cdef unsigned int rm = (opcode >> 3) & 0x7
    cdef unsigned int rdn = opcode & 0x7
    cdef unsigned int result = core.registers[rdn] & ~core.registers[rm]
    core.registers[rdn] = result
    core.n = (result & 0x80000000U) != 0
    core.z = result == 0
    return 1


cdef int op_bkpt(CortexM0Core core, unsigned int opcode, unsigned int opcode2, unsigned int opcode_pc) except -1:
    # BKPT
    cdef unsigned int imm8 = opcode & 0xFF
    core.break_rewind = 2
    core.rp2040.on_break(imm8)
    return 1


cdef int op_bl(CortexM0Core core, unsigned int opcode, unsigned int opcode2, unsigned int opcode_pc) except -1:
    # BL
    cdef unsigned int imm11 = opcode2 & 0x7FF
    cdef unsigned int j2 = (opcode2 >> 11) & 0x1
    cdef unsigned int j1 = (opcode2 >> 13) & 0x1
    cdef unsigned int imm10 = opcode & 0x3FF
    cdef unsigned int s = (opcode >> 10) & 0x1
    cdef unsigned int i1 = 1 - (s ^ j1)
    cdef unsigned int i2 = 1 - (s ^ j2)
    cdef unsigned int imm32 = (
        ((0xFF if s else 0) << 24) | ((i1 << 23) | (i2 << 22) | (imm10 << 12) | (imm11 << 1))
    )
    core.registers[14] = (core.registers[15] + 2) | 0x1
    core.registers[15] = core.registers[15] + 2 + imm32
    core.bl_taken(core, False)
    return 3


cdef int op_blx(CortexM0Core core, unsigned int opcode, unsigned int opcode2, unsigned int opcode_pc) except -1:
    # BLX
    cdef unsigned int rm = (opcode >> 3) & 0xF
    core.registers[14] = core.registers[15] | 0x1
    core.registers[15] = core.registers[rm] & ~1
    core.bl_taken(core, True)
    return 2


cdef int op_bx(CortexM0Core core, unsigned int opcode, unsigned int opcode2, unsigned int opcode_pc) except -1:
    # BX
    cdef unsigned int rm = (opcode >> 3) & 0xF
    bx_write_pc(core, core.registers[rm])
    return 2


cdef int op_cmn_register(CortexM0Core core, unsigned int opcode, unsigned int opcode2, unsigned int opcode_pc) except -1:
    # CMN (register)
    cdef unsigned int rm = (opcode >> 3) & 0x7
    cdef unsigned int rn = opcode & 0x7
    add_update_flags(core, core.registers[rn], core.registers[rm])
    return 1


cdef int op_cmp_immediate(CortexM0Core core, unsigned int opcode, unsigned int opcode2, unsigned int opcode_pc) except -1:
    # CMP immediate
    cdef unsigned int rn = (opcode >> 8) & 0x7
    cdef unsigned int imm8 = opcode & 0xFF
    subtract_update_flags(core, core.registers[rn], imm8)
    return 1


cdef int op_cmp_register(CortexM0Core core, unsigned int opcode, unsigned int opcode2, unsigned int opcode_pc) except -1:
    # CMP (register)
    cdef unsigned int rm = (opcode >> 3) & 0x7
    cdef unsigned int rn = opcode & 0x7
    subtract_update_flags(core, core.registers[rn], core.registers[rm])
    return 1


cdef int op_cmp_register_encoding_t2(
    CortexM0Core core, unsigned int opcode, unsigned int opcode2, unsigned int opcode_pc
) except -1:
    # CMP (register) encoding T2
    cdef unsigned int rm = (opcode >> 3) & 0xF
    cdef unsigned int rn = ((opcode >> 4) & 0x8) | (opcode & 0x7)
    subtract_update_flags(core, core.registers[rn], core.registers[rm])
    return 1


cdef int op_cpsid_i(CortexM0Core core, unsigned int opcode, unsigned int opcode2, unsigned int opcode_pc) except -1:
    # CPSID i
    core.pm = True
    return 1


cdef int op_cpsie_i(CortexM0Core core, unsigned int opcode, unsigned int opcode2, unsigned int opcode_pc) except -1:
    # CPSIE i
    core.pm = False
    core.interrupts_updated = True
    return 1


cdef int op_dmb_sy(CortexM0Core core, unsigned int opcode, unsigned int opcode2, unsigned int opcode_pc) except -1:
    # DMB SY
    core.registers[15] = (core.registers[15] + 2) & 0xFFFFFFFFU
    return 3


cdef int op_dsb_sy(CortexM0Core core, unsigned int opcode, unsigned int opcode2, unsigned int opcode_pc) except -1:
    # DSB SY
    core.registers[15] = (core.registers[15] + 2) & 0xFFFFFFFFU
    return 3


cdef int op_eors(CortexM0Core core, unsigned int opcode, unsigned int opcode2, unsigned int opcode_pc) except -1:
    # EORS
    cdef unsigned int rm = (opcode >> 3) & 0x7
    cdef unsigned int rdn = opcode & 0x7
    cdef unsigned int result = core.registers[rm] ^ core.registers[rdn]
    core.registers[rdn] = result
    core.n = (result & 0x80000000U) != 0
    core.z = result == 0
    return 1


cdef int op_isb_sy(CortexM0Core core, unsigned int opcode, unsigned int opcode2, unsigned int opcode_pc) except -1:
    # ISB SY
    core.registers[15] = (core.registers[15] + 2) & 0xFFFFFFFFU
    return 3


cdef int op_ldmia(CortexM0Core core, unsigned int opcode, unsigned int opcode2, unsigned int opcode_pc) except -1:
    # LDMIA
    cdef int delta_cycles = 1
    cdef unsigned int rn = (opcode >> 8) & 0x7
    cdef unsigned int reg_list = opcode & 0xFF
    cdef unsigned int address = core.registers[rn]
    cdef unsigned int i
    for i in range(8):
        if reg_list & (1 << i):
            core.registers[i] = core.rp2040.read_uint32(address)
            address += 4
            delta_cycles += 1
    # Write back
    if not (reg_list & (1 << rn)):
        core.registers[rn] = address
    return delta_cycles


cdef int op_ldr_immediate(CortexM0Core core, unsigned int opcode, unsigned int opcode2, unsigned int opcode_pc) except -1:
    # LDR (immediate)
    cdef unsigned int imm5 = ((opcode >> 6) & 0x1F) << 2
    cdef unsigned int rn = (opcode >> 3) & 0x7
    cdef unsigned int rt = opcode & 0x7
    cdef unsigned int addr = core.registers[rn] + imm5
    cdef int delta_cycles = 1 + cycles_io(addr, False)
    core.registers[rt] = core.rp2040.read_uint32(addr)
    return delta_cycles


cdef int op_ldr_sp_immediate(CortexM0Core core, unsigned int opcode, unsigned int opcode2, unsigned int opcode_pc) except -1:
    # LDR (sp + immediate)
    cdef unsigned int rt = (opcode >> 8) & 0x7
    cdef unsigned int imm8 = opcode & 0xFF
    cdef unsigned int addr = core.registers[13] + (imm8 << 2)
    cdef int delta_cycles = 1 + cycles_io(addr, False)
    core.registers[rt] = core.rp2040.read_uint32(addr)
    return delta_cycles


cdef int op_ldr_literal(CortexM0Core core, unsigned int opcode, unsigned int opcode2, unsigned int opcode_pc) except -1:
    # LDR (literal)
    cdef unsigned int imm8 = (opcode & 0xFF) << 2
    cdef unsigned int rt = (opcode >> 8) & 7
    cdef unsigned int next_pc = core.registers[15] + 2
    cdef unsigned int addr = (next_pc & 0xFFFFFFFCU) + imm8
    cdef int delta_cycles = 1 + cycles_io(addr, False)
    core.registers[rt] = core.rp2040.read_uint32(addr)
    return delta_cycles


cdef int op_ldr_register(CortexM0Core core, unsigned int opcode, unsigned int opcode2, unsigned int opcode_pc) except -1:
    # LDR (register)
    cdef unsigned int rm = (opcode >> 6) & 0x7
    cdef unsigned int rn = (opcode >> 3) & 0x7
    cdef unsigned int rt = opcode & 0x7
    cdef unsigned int addr = core.registers[rm] + core.registers[rn]
    cdef int delta_cycles = 1 + cycles_io(addr, False)
    core.registers[rt] = core.rp2040.read_uint32(addr)
    return delta_cycles


cdef int op_ldrb_immediate(CortexM0Core core, unsigned int opcode, unsigned int opcode2, unsigned int opcode_pc) except -1:
    # LDRB (immediate)
    cdef unsigned int imm5 = (opcode >> 6) & 0x1F
    cdef unsigned int rn = (opcode >> 3) & 0x7
    cdef unsigned int rt = opcode & 0x7
    cdef unsigned int addr = core.registers[rn] + imm5
    cdef int delta_cycles = 1 + cycles_io(addr, False)
    core.registers[rt] = core.rp2040.read_uint8(addr)
    return delta_cycles


cdef int op_ldrb_register(CortexM0Core core, unsigned int opcode, unsigned int opcode2, unsigned int opcode_pc) except -1:
    # LDRB (register)
    cdef unsigned int rm = (opcode >> 6) & 0x7
    cdef unsigned int rn = (opcode >> 3) & 0x7
    cdef unsigned int rt = opcode & 0x7
    cdef unsigned int addr = core.registers[rm] + core.registers[rn]
    cdef int delta_cycles = 1 + cycles_io(addr, False)
    core.registers[rt] = core.rp2040.read_uint8(addr)
    return delta_cycles


cdef int op_ldrh_immediate(CortexM0Core core, unsigned int opcode, unsigned int opcode2, unsigned int opcode_pc) except -1:
    # LDRH (immediate)
    cdef unsigned int imm5 = (opcode >> 6) & 0x1F
    cdef unsigned int rn = (opcode >> 3) & 0x7
    cdef unsigned int rt = opcode & 0x7
    cdef unsigned int addr = core.registers[rn] + (imm5 << 1)
    cdef int delta_cycles = 1 + cycles_io(addr, False)
    core.registers[rt] = core.rp2040.read_uint16(addr)
    return delta_cycles


cdef int op_ldrh_register(CortexM0Core core, unsigned int opcode, unsigned int opcode2, unsigned int opcode_pc) except -1:
    # LDRH (register)
    cdef unsigned int rm = (opcode >> 6) & 0x7
    cdef unsigned int rn = (opcode >> 3) & 0x7
    cdef unsigned int rt = opcode & 0x7
    cdef unsigned int addr = core.registers[rm] + core.registers[rn]
    cdef int delta_cycles = 1 + cycles_io(addr, False)
    core.registers[rt] = core.rp2040.read_uint16(addr)
    return delta_cycles


cdef int op_ldrsb(CortexM0Core core, unsigned int opcode, unsigned int opcode2, unsigned int opcode_pc) except -1:
    # LDRSB
    cdef unsigned int rm = (opcode >> 6) & 0x7
    cdef unsigned int rn = (opcode >> 3) & 0x7
    cdef unsigned int rt = opcode & 0x7
    cdef unsigned int addr = core.registers[rm] + core.registers[rn]
    cdef int delta_cycles = 1 + cycles_io(addr, False)
    core.registers[rt] = <unsigned int> sign_extend8(<unsigned int> core.rp2040.read_uint8(addr))
    return delta_cycles


cdef int op_ldrsh(CortexM0Core core, unsigned int opcode, unsigned int opcode2, unsigned int opcode_pc) except -1:
    # LDRSH
    cdef unsigned int rm = (opcode >> 6) & 0x7
    cdef unsigned int rn = (opcode >> 3) & 0x7
    cdef unsigned int rt = opcode & 0x7
    cdef unsigned int addr = core.registers[rm] + core.registers[rn]
    cdef int delta_cycles = 1 + cycles_io(addr, False)
    core.registers[rt] = <unsigned int> sign_extend16(<unsigned int> core.rp2040.read_uint16(addr))
    return delta_cycles


cdef int op_lsls_immediate(CortexM0Core core, unsigned int opcode, unsigned int opcode2, unsigned int opcode_pc) except -1:
    # LSLS (immediate)
    cdef unsigned int imm5 = (opcode >> 6) & 0x1F
    cdef unsigned int rm = (opcode >> 3) & 0x7
    cdef unsigned int rd = opcode & 0x7
    cdef unsigned int input_value = core.registers[rm]
    cdef unsigned int result = input_value << imm5
    core.registers[rd] = result
    core.n = (result & 0x80000000U) != 0
    core.z = result == 0
    if imm5:
        core.c = (input_value & (<unsigned int> (1 << (32 - imm5)))) != 0
    return 1


cdef int op_lsls_register(CortexM0Core core, unsigned int opcode, unsigned int opcode2, unsigned int opcode_pc) except -1:
    # LSLS (register)
    cdef unsigned int rm = (opcode >> 3) & 0x7
    cdef unsigned int rdn = opcode & 0x7
    cdef unsigned int input_value = core.registers[rdn]
    cdef unsigned int shift_count = core.registers[rm] & 0xFF
    cdef unsigned int result = 0 if shift_count >= 32 else (input_value << shift_count)
    core.registers[rdn] = result
    core.n = (result & 0x80000000U) != 0
    core.z = result == 0
    # NOTE: shift_count ranges 0-255 here; `& 31` replicates the source's mod-32 shift-amount
    # wraparound so the C shift amount stays in [0, 31] instead of triggering UB.
    if shift_count:
        core.c = (input_value & (<unsigned int> (1 << ((32 - shift_count) & 31)))) != 0
    return 1


cdef int op_lsrs_immediate(CortexM0Core core, unsigned int opcode, unsigned int opcode2, unsigned int opcode_pc) except -1:
    # LSRS (immediate)
    cdef unsigned int imm5 = (opcode >> 6) & 0x1F
    cdef unsigned int rm = (opcode >> 3) & 0x7
    cdef unsigned int rd = opcode & 0x7
    cdef unsigned int input_value = core.registers[rm]
    cdef unsigned int result = (input_value >> imm5) if imm5 else 0
    core.registers[rd] = result
    core.n = (result & 0x80000000U) != 0
    core.z = result == 0
    core.c = ((input_value >> (imm5 - 1 if imm5 else 31)) & 0x1) != 0
    return 1


cdef int op_lsrs_register(CortexM0Core core, unsigned int opcode, unsigned int opcode2, unsigned int opcode_pc) except -1:
    # LSRS (register)
    cdef unsigned int rm = (opcode >> 3) & 0x7
    cdef unsigned int rdn = opcode & 0x7
    cdef unsigned int shift_amount = core.registers[rm] & 0xFF
    cdef unsigned int input_value = core.registers[rdn]
    cdef unsigned int result = (input_value >> shift_amount) if shift_amount < 32 else 0
    core.registers[rdn] = result
    core.n = (result & 0x80000000U) != 0
    core.z = result == 0
    # NOTE: shift_amount can be 0 here, making `shift_amount - 1` negative if done unmasked;
    # `& 31` replicates the source's mod-32 wraparound instead of triggering C shift UB.
    if shift_amount <= 32:
        core.c = ((input_value >> ((shift_amount - 1) & 31)) & 0x1) != 0
    else:
        core.c = False
    return 1


cdef int op_mov(CortexM0Core core, unsigned int opcode, unsigned int opcode2, unsigned int opcode_pc) except -1:
    # MOV
    cdef int delta_cycles = 1
    cdef unsigned int rm = (opcode >> 3) & 0xF
    cdef unsigned int rd = ((opcode >> 4) & 0x8) | (opcode & 0x7)
    cdef unsigned int value = core.registers[15] + 2 if rm == PC_REGISTER else core.registers[rm]
    if rd == PC_REGISTER:
        delta_cycles += 1
        value &= ~1
    elif rd == SP_REGISTER:
        value &= ~3
    core.registers[rd] = value
    return delta_cycles


cdef int op_movs(CortexM0Core core, unsigned int opcode, unsigned int opcode2, unsigned int opcode_pc) except -1:
    # MOVS
    cdef unsigned int value = opcode & 0xFF
    cdef unsigned int rd = (opcode >> 8) & 7
    core.registers[rd] = value
    core.n = (value & 0x80000000U) != 0
    core.z = value == 0
    return 1


cdef int op_mrs(CortexM0Core core, unsigned int opcode, unsigned int opcode2, unsigned int opcode_pc) except -1:
    # MRS
    cdef unsigned int sysm = opcode2 & 0xFF
    cdef unsigned int rd = (opcode2 >> 8) & 0xF
    core.registers[rd] = core.read_special_register(sysm)
    core.registers[15] = (core.registers[15] + 2) & 0xFFFFFFFFU
    return 3


cdef int op_msr(CortexM0Core core, unsigned int opcode, unsigned int opcode2, unsigned int opcode_pc) except -1:
    # MSR
    cdef unsigned int sysm = opcode2 & 0xFF
    cdef unsigned int rn = opcode & 0xF
    core.write_special_register(sysm, core.registers[rn])
    core.registers[15] = (core.registers[15] + 2) & 0xFFFFFFFFU
    return 3


cdef int op_muls(CortexM0Core core, unsigned int opcode, unsigned int opcode2, unsigned int opcode_pc) except -1:
    # MULS
    cdef unsigned int rn = (opcode >> 3) & 0x7
    cdef unsigned int rdm = opcode & 0x7
    cdef unsigned int result = <unsigned int> ((<unsigned long long> core.registers[rn] * core.registers[rdm]) & 0xFFFFFFFFU)
    core.registers[rdm] = result
    core.n = (result & 0x80000000U) != 0
    core.z = result == 0
    return 1


cdef int op_mvns(CortexM0Core core, unsigned int opcode, unsigned int opcode2, unsigned int opcode_pc) except -1:
    # MVNS
    cdef unsigned int rm = (opcode >> 3) & 7
    cdef unsigned int rd = opcode & 7
    cdef unsigned int result = ~core.registers[rm]
    core.registers[rd] = result
    core.n = (result & 0x80000000U) != 0
    core.z = result == 0
    return 1


cdef int op_orrs_encoding_t2(CortexM0Core core, unsigned int opcode, unsigned int opcode2, unsigned int opcode_pc) except -1:
    # ORRS (Encoding T2)
    cdef unsigned int rm = (opcode >> 3) & 0x7
    cdef unsigned int rdn = opcode & 0x7
    cdef unsigned int result = core.registers[rdn] | core.registers[rm]
    core.registers[rdn] = result
    core.n = (result & 0x80000000U) != 0
    core.z = result == 0
    return 1


cdef int op_pop(CortexM0Core core, unsigned int opcode, unsigned int opcode2, unsigned int opcode_pc) except -1:
    # POP
    cdef int delta_cycles = 1
    cdef unsigned int p = (opcode >> 8) & 1
    cdef unsigned int address = core.registers[13]
    cdef unsigned int i
    for i in range(8):
        if opcode & (1 << i):
            core.registers[i] = core.rp2040.read_uint32(address)
            address += 4
            delta_cycles += 1
    if p:
        core.registers[13] = (address + 4) & 0xFFFFFFFCU
        bx_write_pc(core, core.rp2040.read_uint32(address))
        delta_cycles += 2
    else:
        core.registers[13] = address & 0xFFFFFFFCU
    return delta_cycles


cdef int op_push(CortexM0Core core, unsigned int opcode, unsigned int opcode2, unsigned int opcode_pc) except -1:
    # PUSH
    cdef int delta_cycles = 1
    cdef unsigned int bit_count = 0
    cdef unsigned int i
    for i in range(9):
        if opcode & (1 << i):
            bit_count += 1
    cdef unsigned int address = core.registers[13] - 4 * bit_count
    for i in range(8):
        if opcode & (1 << i):
            core.rp2040.write_uint32(address, core.registers[i])
            delta_cycles += 1
            address += 4
    if opcode & (1 << 8):
        core.rp2040.write_uint32(address, core.registers[14])
    core.registers[13] = (core.registers[13] - 4 * bit_count) & 0xFFFFFFFCU
    return delta_cycles


cdef int op_rev(CortexM0Core core, unsigned int opcode, unsigned int opcode2, unsigned int opcode_pc) except -1:
    # REV
    cdef unsigned int rm = (opcode >> 3) & 0x7
    cdef unsigned int rd = opcode & 0x7
    cdef unsigned int input_value = core.registers[rm]
    core.registers[rd] = (
        ((input_value & 0xFF) << 24)
        | (((input_value >> 8) & 0xFF) << 16)
        | (((input_value >> 16) & 0xFF) << 8)
        | ((input_value >> 24) & 0xFF)
    )
    return 1


cdef int op_rev16(CortexM0Core core, unsigned int opcode, unsigned int opcode2, unsigned int opcode_pc) except -1:
    # REV16
    cdef unsigned int rm = (opcode >> 3) & 0x7
    cdef unsigned int rd = opcode & 0x7
    cdef unsigned int input_value = core.registers[rm]
    core.registers[rd] = (
        (((input_value >> 16) & 0xFF) << 24)
        | (((input_value >> 24) & 0xFF) << 16)
        | ((input_value & 0xFF) << 8)
        | ((input_value >> 8) & 0xFF)
    )
    return 1


cdef int op_revsh(CortexM0Core core, unsigned int opcode, unsigned int opcode2, unsigned int opcode_pc) except -1:
    # REVSH
    cdef unsigned int rm = (opcode >> 3) & 0x7
    cdef unsigned int rd = opcode & 0x7
    cdef unsigned int input_value = core.registers[rm]
    core.registers[rd] = <unsigned int> sign_extend16(
        ((input_value & 0xFF) << 8) | ((input_value >> 8) & 0xFF)
    )
    return 1


cdef int op_ror(CortexM0Core core, unsigned int opcode, unsigned int opcode2, unsigned int opcode_pc) except -1:
    # ROR
    cdef unsigned int rm = (opcode >> 3) & 0x7
    cdef unsigned int rdn = opcode & 0x7
    cdef unsigned int input_value = core.registers[rdn]
    cdef unsigned int shift = (core.registers[rm] & 0xFF) % 32
    cdef unsigned int result
    if shift == 0:
        # input_value << 32 is C UB (shift == operand width); the source's Python `<< 32` term
        # always vanishes after the eventual `& 0xFFFFFFFF` anyway, so just skip it.
        result = input_value
    else:
        result = (input_value >> shift) | (input_value << (32 - shift))
    core.registers[rdn] = result
    core.n = (result & 0x80000000U) != 0
    core.z = result == 0
    core.c = (result & 0x80000000U) != 0
    return 1


cdef int op_negs_rsbs(CortexM0Core core, unsigned int opcode, unsigned int opcode2, unsigned int opcode_pc) except -1:
    # NEGS / RSBS
    cdef unsigned int rn = (opcode >> 3) & 0x7
    cdef unsigned int rd = opcode & 0x7
    core.registers[rd] = <unsigned int> subtract_update_flags(core, 0, core.registers[rn])
    return 1


cdef int op_nop(CortexM0Core core, unsigned int opcode, unsigned int opcode2, unsigned int opcode_pc) except -1:
    # NOP
    return 1


cdef int op_sbcs_encoding_t1(CortexM0Core core, unsigned int opcode, unsigned int opcode2, unsigned int opcode_pc) except -1:
    # SBCS (Encoding T1)
    cdef unsigned int rm = (opcode >> 3) & 0x7
    cdef unsigned int rdn = opcode & 0x7
    # `<long long>` on registers[rm], not left to Cython to infer from subtract_update_flags's own
    # `long long subtrahend` parameter type: the pure-Python reference (_cortex_m0_core.py's
    # _op_sbcs_encoding_t1) relies on Python's arbitrary-precision `registers[rm] + (1 - carry)`
    # never wrapping - e.g. rm=0xFFFFFFFF, carry=False needs the *unwrapped* 0x100000000 reaching
    # subtract_update_flags for its `minuend >= subtrahend` borrow check to come out right. Without
    # the explicit cast, whether Cython computes this addition in 32-bit `unsigned int` (silently
    # wrapping 0xFFFFFFFF + 1 to 0, losing the borrow) or widens to `long long` first based on the
    # call target's declared parameter type is Cython-version-dependent, not part of any documented
    # contract - confirmed by a real, reproduced CI failure (test_should_execute_a_sbcs_r0_r3_
    # instruction_2 passed locally on Cython 3.2.9 but failed on whatever `cibuildwheel`'s
    # unpinned `cython>=3.2.9` build-isolation environment resolved). The cast makes the widening
    # happen first, explicitly, so the result can't depend on that inference either way.
    core.registers[rdn] = <unsigned int> subtract_update_flags(
        core, core.registers[rdn], (<long long> core.registers[rm]) + (1 - (1 if core.c else 0))
    )
    return 1


cdef int op_sev(CortexM0Core core, unsigned int opcode, unsigned int opcode2, unsigned int opcode_pc) except -1:
    # SEV
    core.rp2040.logger.info(LOG_NAME, "SEV")
    return 1


cdef int op_stmia(CortexM0Core core, unsigned int opcode, unsigned int opcode2, unsigned int opcode_pc) except -1:
    # STMIA
    cdef int delta_cycles = 1
    cdef unsigned int rn = (opcode >> 8) & 0x7
    cdef unsigned int reg_list = opcode & 0xFF
    cdef unsigned int address = core.registers[rn]
    cdef unsigned int i
    for i in range(8):
        if reg_list & (1 << i):
            core.rp2040.write_uint32(address, core.registers[i])
            address += 4
            delta_cycles += 1
    # Write back
    if not (reg_list & (1 << rn)):
        core.registers[rn] = address
    return delta_cycles


cdef int op_str_immediate(CortexM0Core core, unsigned int opcode, unsigned int opcode2, unsigned int opcode_pc) except -1:
    # STR (immediate)
    cdef unsigned int imm5 = ((opcode >> 6) & 0x1F) << 2
    cdef unsigned int rn = (opcode >> 3) & 0x7
    cdef unsigned int rt = opcode & 0x7
    cdef unsigned int address = core.registers[rn] + imm5
    cdef int delta_cycles = 1 + cycles_io(address, True)
    core.rp2040.write_uint32(address, core.registers[rt])
    return delta_cycles


cdef int op_str_sp_immediate(CortexM0Core core, unsigned int opcode, unsigned int opcode2, unsigned int opcode_pc) except -1:
    # STR (sp + immediate)
    cdef unsigned int rt = (opcode >> 8) & 0x7
    cdef unsigned int imm8 = opcode & 0xFF
    cdef unsigned int address = core.registers[13] + (imm8 << 2)
    cdef int delta_cycles = 1 + cycles_io(address, True)
    core.rp2040.write_uint32(address, core.registers[rt])
    return delta_cycles


cdef int op_str_register(CortexM0Core core, unsigned int opcode, unsigned int opcode2, unsigned int opcode_pc) except -1:
    # STR (register)
    cdef unsigned int rm = (opcode >> 6) & 0x7
    cdef unsigned int rn = (opcode >> 3) & 0x7
    cdef unsigned int rt = opcode & 0x7
    cdef unsigned int address = core.registers[rm] + core.registers[rn]
    cdef int delta_cycles = 1 + cycles_io(address, True)
    core.rp2040.write_uint32(address, core.registers[rt])
    return delta_cycles


cdef int op_strb_immediate(CortexM0Core core, unsigned int opcode, unsigned int opcode2, unsigned int opcode_pc) except -1:
    # STRB (immediate)
    cdef unsigned int imm5 = (opcode >> 6) & 0x1F
    cdef unsigned int rn = (opcode >> 3) & 0x7
    cdef unsigned int rt = opcode & 0x7
    cdef unsigned int address = core.registers[rn] + imm5
    cdef int delta_cycles = 1 + cycles_io(address, True)
    core.rp2040.write_uint8(address, core.registers[rt])
    return delta_cycles


cdef int op_strb_register(CortexM0Core core, unsigned int opcode, unsigned int opcode2, unsigned int opcode_pc) except -1:
    # STRB (register)
    cdef unsigned int rm = (opcode >> 6) & 0x7
    cdef unsigned int rn = (opcode >> 3) & 0x7
    cdef unsigned int rt = opcode & 0x7
    cdef unsigned int address = core.registers[rm] + core.registers[rn]
    cdef int delta_cycles = 1 + cycles_io(address, True)
    core.rp2040.write_uint8(address, core.registers[rt])
    return delta_cycles


cdef int op_strh_immediate(CortexM0Core core, unsigned int opcode, unsigned int opcode2, unsigned int opcode_pc) except -1:
    # STRH (immediate)
    cdef unsigned int imm5 = ((opcode >> 6) & 0x1F) << 1
    cdef unsigned int rn = (opcode >> 3) & 0x7
    cdef unsigned int rt = opcode & 0x7
    cdef unsigned int address = core.registers[rn] + imm5
    cdef int delta_cycles = 1 + cycles_io(address, True)
    core.rp2040.write_uint16(address, core.registers[rt])
    return delta_cycles


cdef int op_strh_register(CortexM0Core core, unsigned int opcode, unsigned int opcode2, unsigned int opcode_pc) except -1:
    # STRH (register)
    cdef unsigned int rm = (opcode >> 6) & 0x7
    cdef unsigned int rn = (opcode >> 3) & 0x7
    cdef unsigned int rt = opcode & 0x7
    cdef unsigned int address = core.registers[rm] + core.registers[rn]
    cdef int delta_cycles = 1 + cycles_io(address, True)
    core.rp2040.write_uint16(address, core.registers[rt])
    return delta_cycles


cdef int op_sub_sp_minus_immediate(
    CortexM0Core core, unsigned int opcode, unsigned int opcode2, unsigned int opcode_pc
) except -1:
    # SUB (SP minus immediate)
    cdef unsigned int imm32 = (opcode & 0x7F) << 2
    core.registers[13] = (core.registers[13] - imm32) & 0xFFFFFFFCU
    return 1


cdef int op_subs_encoding_t1(CortexM0Core core, unsigned int opcode, unsigned int opcode2, unsigned int opcode_pc) except -1:
    # SUBS (Encoding T1)
    cdef unsigned int imm3 = (opcode >> 6) & 0x7
    cdef unsigned int rn = (opcode >> 3) & 0x7
    cdef unsigned int rd = opcode & 0x7
    core.registers[rd] = <unsigned int> subtract_update_flags(core, core.registers[rn], imm3)
    return 1


cdef int op_subs_encoding_t2(CortexM0Core core, unsigned int opcode, unsigned int opcode2, unsigned int opcode_pc) except -1:
    # SUBS (Encoding T2)
    cdef unsigned int imm8 = opcode & 0xFF
    cdef unsigned int rdn = (opcode >> 8) & 0x7
    core.registers[rdn] = <unsigned int> subtract_update_flags(core, core.registers[rdn], imm8)
    return 1


cdef int op_subs_register(CortexM0Core core, unsigned int opcode, unsigned int opcode2, unsigned int opcode_pc) except -1:
    # SUBS (register)
    cdef unsigned int rm = (opcode >> 6) & 0x7
    cdef unsigned int rn = (opcode >> 3) & 0x7
    cdef unsigned int rd = opcode & 0x7
    core.registers[rd] = <unsigned int> subtract_update_flags(core, core.registers[rn], core.registers[rm])
    return 1


cdef int op_svc(CortexM0Core core, unsigned int opcode, unsigned int opcode2, unsigned int opcode_pc) except -1:
    # SVC
    core.pending_svcall = True
    core.interrupts_updated = True
    return 1


cdef int op_sxtb(CortexM0Core core, unsigned int opcode, unsigned int opcode2, unsigned int opcode_pc) except -1:
    # SXTB
    cdef unsigned int rm = (opcode >> 3) & 0x7
    cdef unsigned int rd = opcode & 0x7
    core.registers[rd] = <unsigned int> sign_extend8(core.registers[rm])
    return 1


cdef int op_sxth(CortexM0Core core, unsigned int opcode, unsigned int opcode2, unsigned int opcode_pc) except -1:
    # SXTH
    cdef unsigned int rm = (opcode >> 3) & 0x7
    cdef unsigned int rd = opcode & 0x7
    core.registers[rd] = <unsigned int> sign_extend16(core.registers[rm])
    return 1


cdef int op_tst(CortexM0Core core, unsigned int opcode, unsigned int opcode2, unsigned int opcode_pc) except -1:
    # TST
    cdef unsigned int rm = (opcode >> 3) & 0x7
    cdef unsigned int rn = opcode & 0x7
    cdef unsigned int result = core.registers[rn] & core.registers[rm]
    core.n = (result & 0x80000000U) != 0
    core.z = result == 0
    return 1


cdef int op_udf(CortexM0Core core, unsigned int opcode, unsigned int opcode2, unsigned int opcode_pc) except -1:
    # UDF
    cdef unsigned int imm8 = opcode & 0xFF
    core.break_rewind = 2
    core.rp2040.on_break(imm8)
    return 1


cdef int op_udf_encoding_t2(CortexM0Core core, unsigned int opcode, unsigned int opcode2, unsigned int opcode_pc) except -1:
    # UDF (Encoding T2)
    cdef unsigned int imm4 = opcode & 0xF
    cdef unsigned int imm12 = opcode2 & 0xFFF
    core.break_rewind = 4
    core.rp2040.on_break((imm4 << 12) | imm12)
    core.registers[15] = (core.registers[15] + 2) & 0xFFFFFFFFU
    return 1


cdef int op_uxtb(CortexM0Core core, unsigned int opcode, unsigned int opcode2, unsigned int opcode_pc) except -1:
    # UXTB
    cdef unsigned int rm = (opcode >> 3) & 0x7
    cdef unsigned int rd = opcode & 0x7
    core.registers[rd] = core.registers[rm] & 0xFF
    return 1


cdef int op_uxth(CortexM0Core core, unsigned int opcode, unsigned int opcode2, unsigned int opcode_pc) except -1:
    # UXTH
    cdef unsigned int rm = (opcode >> 3) & 0x7
    cdef unsigned int rd = opcode & 0x7
    core.registers[rd] = core.registers[rm] & 0xFFFF
    return 1


cdef int op_wfe(CortexM0Core core, unsigned int opcode, unsigned int opcode2, unsigned int opcode_pc) except -1:
    # WFE
    if core.event_registered:
        core.event_registered = False
    else:
        core.waiting = True
    return 2


cdef int op_wfi(CortexM0Core core, unsigned int opcode, unsigned int opcode2, unsigned int opcode_pc) except -1:
    # WFI
    core.waiting = True
    return 2


cdef int op_yield_(CortexM0Core core, unsigned int opcode, unsigned int opcode2, unsigned int opcode_pc) except -1:
    # YIELD
    # do nothing for now. Wait for event!
    core.rp2040.logger.info(LOG_NAME, "Yield")
    return 1


cdef OpHandler resolve_wide(unsigned int opcode, unsigned int opcode2) noexcept:
    # Opcode range 0xF000-0xF7FF is exclusively owned by these 7 opcode2-dependent conditions
    # (verified by the assertion after DISPATCH_TABLE's construction below, mirroring
    # _cortex_m0_core.py's _resolve_wide/_build_dispatch_table). Order matters and mirrors the
    # original priority.
    if (opcode >> 11) == 0b11110 and (opcode2 >> 14) == 0b11 and ((opcode2 >> 12) & 0x1) == 1:
        return op_bl
    elif opcode == 0xF3BF and (opcode2 & 0xFFF0) == 0x8F50:
        return op_dmb_sy
    elif opcode == 0xF3BF and (opcode2 & 0xFFF0) == 0x8F40:
        return op_dsb_sy
    elif opcode == 0xF3BF and (opcode2 & 0xFFF0) == 0x8F60:
        return op_isb_sy
    elif opcode == 0b1111001111101111 and (opcode2 >> 12) == 0b1000:
        return op_mrs
    elif (opcode >> 4) == 0b111100111000 and (opcode2 >> 8) == 0b10001000:
        return op_msr
    elif (opcode >> 4) == 0b111101111111 and (opcode2 >> 12) == 0b1010:
        return op_udf_encoding_t2
    return NULL


cdef OpHandler match_pattern(unsigned int opcode) noexcept:
    if opcode >> 6 == 0b0100000101:
        return op_adcs
    if opcode >> 11 == 0b10101:
        return op_add_register_sp_plus_immediate
    if opcode >> 7 == 0b101100000:
        return op_add_sp_plus_immediate
    if opcode >> 9 == 0b0001110:
        return op_adds_encoding_t1
    if opcode >> 11 == 0b00110:
        return op_adds_encoding_t2
    if opcode >> 9 == 0b0001100:
        return op_adds_register
    if opcode >> 8 == 0b01000100:
        return op_add_register
    if opcode >> 11 == 0b10100:
        return op_adr
    if opcode >> 6 == 0b0100000000:
        return op_ands_encoding_t2
    if opcode >> 11 == 0b00010:
        return op_asrs_immediate
    if opcode >> 6 == 0b0100000100:
        return op_asrs_register
    if opcode >> 12 == 0b1101 and ((opcode >> 9) & 0x7) != 0b111:
        return op_b_with_cond
    if opcode >> 11 == 0b11100:
        return op_b
    if opcode >> 6 == 0b0100001110:
        return op_bics
    if opcode >> 8 == 0b10111110:
        return op_bkpt
    if opcode >> 7 == 0b010001111 and (opcode & 0x7) == 0:
        return op_blx
    if opcode >> 7 == 0b010001110 and (opcode & 0x7) == 0:
        return op_bx
    if opcode >> 6 == 0b0100001011:
        return op_cmn_register
    if opcode >> 11 == 0b00101:
        return op_cmp_immediate
    if opcode >> 6 == 0b0100001010:
        return op_cmp_register
    if opcode >> 8 == 0b01000101:
        return op_cmp_register_encoding_t2
    if opcode == 0xB672:
        return op_cpsid_i
    if opcode == 0xB662:
        return op_cpsie_i
    if opcode >> 6 == 0b0100000001:
        return op_eors
    if opcode >> 11 == 0b11001:
        return op_ldmia
    if opcode >> 11 == 0b01101:
        return op_ldr_immediate
    if opcode >> 11 == 0b10011:
        return op_ldr_sp_immediate
    if opcode >> 11 == 0b01001:
        return op_ldr_literal
    if opcode >> 9 == 0b0101100:
        return op_ldr_register
    if opcode >> 11 == 0b01111:
        return op_ldrb_immediate
    if opcode >> 9 == 0b0101110:
        return op_ldrb_register
    if opcode >> 11 == 0b10001:
        return op_ldrh_immediate
    if opcode >> 9 == 0b0101101:
        return op_ldrh_register
    if opcode >> 9 == 0b0101011:
        return op_ldrsb
    if opcode >> 9 == 0b0101111:
        return op_ldrsh
    if opcode >> 11 == 0b00000:
        return op_lsls_immediate
    if opcode >> 6 == 0b0100000010:
        return op_lsls_register
    if opcode >> 11 == 0b00001:
        return op_lsrs_immediate
    if opcode >> 6 == 0b0100000011:
        return op_lsrs_register
    if opcode >> 8 == 0b01000110:
        return op_mov
    if opcode >> 11 == 0b00100:
        return op_movs
    if opcode >> 6 == 0b0100001101:
        return op_muls
    if opcode >> 6 == 0b0100001111:
        return op_mvns
    if opcode >> 6 == 0b0100001100:
        return op_orrs_encoding_t2
    if opcode >> 9 == 0b1011110:
        return op_pop
    if opcode >> 9 == 0b1011010:
        return op_push
    if opcode >> 6 == 0b1011101000:
        return op_rev
    if opcode >> 6 == 0b1011101001:
        return op_rev16
    if opcode >> 6 == 0b1011101011:
        return op_revsh
    if opcode >> 6 == 0b0100000111:
        return op_ror
    if opcode >> 6 == 0b0100001001:
        return op_negs_rsbs
    if opcode == 0b1011111100000000:
        return op_nop
    if opcode >> 6 == 0b0100000110:
        return op_sbcs_encoding_t1
    if opcode == 0b1011111101000000:
        return op_sev
    if opcode >> 11 == 0b11000:
        return op_stmia
    if opcode >> 11 == 0b01100:
        return op_str_immediate
    if opcode >> 11 == 0b10010:
        return op_str_sp_immediate
    if opcode >> 9 == 0b0101000:
        return op_str_register
    if opcode >> 11 == 0b01110:
        return op_strb_immediate
    if opcode >> 9 == 0b0101010:
        return op_strb_register
    if opcode >> 11 == 0b10000:
        return op_strh_immediate
    if opcode >> 9 == 0b0101001:
        return op_strh_register
    if opcode >> 7 == 0b101100001:
        return op_sub_sp_minus_immediate
    if opcode >> 9 == 0b0001111:
        return op_subs_encoding_t1
    if opcode >> 11 == 0b00111:
        return op_subs_encoding_t2
    if opcode >> 9 == 0b0001101:
        return op_subs_register
    if opcode >> 8 == 0b11011111:
        return op_svc
    if opcode >> 6 == 0b1011001001:
        return op_sxtb
    if opcode >> 6 == 0b1011001000:
        return op_sxth
    if opcode >> 6 == 0b0100001000:
        return op_tst
    if opcode >> 8 == 0b11011110:
        return op_udf
    if opcode >> 6 == 0b1011001011:
        return op_uxtb
    if opcode >> 6 == 0b1011001010:
        return op_uxth
    if opcode == 0b1011111100100000:
        return op_wfe
    if opcode == 0b1011111100110000:
        return op_wfi
    if opcode == 0b1011111100010000:
        return op_yield_
    return NULL


cdef void _build_dispatch_table():
    cdef unsigned int opcode
    for opcode in range(0x10000):
        if WIDE_RANGE_START <= opcode < WIDE_RANGE_END:
            continue
        DISPATCH_TABLE[opcode] = match_pattern(opcode)


_build_dispatch_table()

for _opcode in range(WIDE_RANGE_START, WIDE_RANGE_END):
    assert DISPATCH_TABLE[_opcode] == NULL, (
        "A non-opcode2-dependent instruction pattern now matches an opcode in 0xF000-0xF7FF, "
        "which resolve_wide() assumes it exclusively owns (see docs/PORTING.md). Add the new "
        "pattern to resolve_wide() too, in the correct priority position relative to the "
        "existing opcode2-dependent patterns there."
    )
