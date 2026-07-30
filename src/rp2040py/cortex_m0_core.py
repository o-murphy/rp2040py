from collections.abc import Callable
from enum import IntEnum
from typing import TYPE_CHECKING

from rp2040py.irq import MAX_HARDWARE_IRQ
from rp2040py.memory_map import APB_START_ADDRESS, SIO_START_ADDRESS
from rp2040py.utils.bit import Uint32Array, s32, u32

if TYPE_CHECKING:
    from rp2040py.rp2040 import RP2040

__all__ = (
    "SYSM_CONTROL",
    "SYSM_MSP",
    "SYSM_PRIMASK",
    "SYSM_PSP",
    "CortexM0Core",
)

EXC_RESET = 1
EXC_NMI = 2
EXC_HARDFAULT = 3
EXC_SVCALL = 11
EXC_PENDSV = 14
EXC_SYSTICK = 15

SYSM_APSR = 0
SYSM_IAPSR = 1
SYSM_EAPSR = 2
SYSM_XPSR = 3
SYSM_IPSR = 5
SYSM_EPSR = 6
SYSM_IEPSR = 7
SYSM_MSP = 8
SYSM_PSP = 9
SYSM_PRIMASK = 16
SYSM_CONTROL = 20

# Lowest possible exception priority
LOWEST_PRIORITY = 4


class ExecutionMode(IntEnum):
    MODE_THREAD = 0
    MODE_HANDLER = 1


def sign_extend8(value: int) -> int:
    return s32((value << 24) & 0xFFFFFFFF) >> 24


def sign_extend16(value: int) -> int:
    return s32((value << 16) & 0xFFFFFFFF) >> 16


SP_REGISTER = 13
PC_REGISTER = 15


class StackPointerBank(IntEnum):
    SP_MAIN = 0
    SP_PROCESS = 1


LOG_NAME = "CortexM0Core"


class CortexM0Core:
    def __init__(self, rp2040: "RP2040"):
        self.rp2040 = rp2040
        self.registers = Uint32Array(16)
        self.banked_sp = 0
        self.cycles = 0

        self.event_registered = False
        self.waiting = False

        # APSR fields
        self.n = False
        self.c = False
        self.z = False
        self.v = False

        # How many bytes to rewind the last break instruction
        self.break_rewind = 0

        # PRIMASK fields
        self.pm = False

        # CONTROL fields
        self.sp_sel = StackPointerBank.SP_MAIN
        self.n_priv = False

        self.current_mode = ExecutionMode.MODE_THREAD
        self.ipsr = 0
        self.interrupt_nmi_mask = 0
        self.pending_interrupts = 0
        self.enabled_interrupts = 0
        self.interrupt_priorities = [0xFFFFFFFF, 0x0, 0x0, 0x0]
        self.pending_nmi = False
        self.pending_pend_sv = False
        self.pending_svcall = False
        self.pending_systick = False
        self.interrupts_updated = False
        self.vtor = 0
        self.shpr2 = 0
        self.shpr3 = 0

        # Hook to listen for function calls - branch-link (BL/BLX) instructions
        self.bl_taken: Callable[[CortexM0Core, bool], None] = lambda core, blx: None

        self.sp = 0xFFFFFFFC
        self.banked_sp = 0xFFFFFFFC

    @property
    def logger(self):
        return self.rp2040.logger

    def reset(self) -> None:
        self.sp = self.rp2040.read_uint32(self.vtor)
        self.pc = self.rp2040.read_uint32(self.vtor + 4) & 0xFFFFFFFE
        self.cycles = 0

    @property
    def sp(self) -> int:
        return self.registers[13]

    @sp.setter
    def sp(self, value: int) -> None:
        self.registers[13] = value & ~0x3

    @property
    def lr(self) -> int:
        return self.registers[14]

    @lr.setter
    def lr(self, value: int) -> None:
        self.registers[14] = value

    @property
    def pc(self) -> int:
        return self.registers[15]

    @pc.setter
    def pc(self, value: int) -> None:
        self.registers[15] = value

    @property
    def apsr(self) -> int:
        return (
            (0x80000000 if self.n else 0)
            | (0x40000000 if self.z else 0)
            | (0x20000000 if self.c else 0)
            | (0x10000000 if self.v else 0)
        )

    @apsr.setter
    def apsr(self, value: int) -> None:
        self.n = bool(value & 0x80000000)
        self.z = bool(value & 0x40000000)
        self.c = bool(value & 0x20000000)
        self.v = bool(value & 0x10000000)

    @property
    def x_psr(self) -> int:
        return self.apsr | self.ipsr | (1 << 24)

    @x_psr.setter
    def x_psr(self, value: int) -> None:
        self.apsr = value
        self.ipsr = value & 0x3F

    def check_condition(self, cond: int) -> bool:
        # Evaluate base condition.
        result = False
        base = cond >> 1
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
            result = self.n == self.v and not self.z
        elif base == 0b111:
            result = True
        return not result if (cond & 0b1 and cond != 0b1111) else result

    def switch_stack(self, stack: StackPointerBank) -> None:
        if self.sp_sel != stack:
            temp = self.sp
            self.sp = self.banked_sp
            self.banked_sp = temp
            self.sp_sel = stack

    @property
    def sp_process(self) -> int:
        return self.sp if self.sp_sel == StackPointerBank.SP_PROCESS else self.banked_sp

    @sp_process.setter
    def sp_process(self, value: int) -> None:
        if self.sp_sel == StackPointerBank.SP_PROCESS:
            self.sp = value
        else:
            self.banked_sp = u32(value)

    @property
    def sp_main(self) -> int:
        return self.sp if self.sp_sel == StackPointerBank.SP_MAIN else self.banked_sp

    @sp_main.setter
    def sp_main(self, value: int) -> None:
        if self.sp_sel == StackPointerBank.SP_MAIN:
            self.sp = value
        else:
            self.banked_sp = u32(value)

    def exception_entry(self, exception_number: int) -> None:
        # PushStack:
        frame_ptr = 0
        frame_ptr_align = 0
        if self.sp_sel and self.current_mode == ExecutionMode.MODE_THREAD:
            frame_ptr_align = 1 if self.sp_process & 0b100 else 0
            self.sp_process = (self.sp_process - 0x20) & ~0b100
            frame_ptr = self.sp_process
        else:
            frame_ptr_align = 1 if self.sp_main & 0b100 else 0
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
        if self.current_mode == ExecutionMode.MODE_HANDLER:
            self.lr = 0xFFFFFFF1
        elif not self.sp_sel:
            self.lr = 0xFFFFFFF9
        else:
            self.lr = 0xFFFFFFFD
        # ExceptionTaken:
        self.current_mode = ExecutionMode.MODE_HANDLER  # Enter Handler Mode, now Privileged
        self.ipsr = exception_number
        self.switch_stack(StackPointerBank.SP_MAIN)
        self.event_registered = True
        vector_table = self.vtor
        self.pc = self.rp2040.read_uint32(vector_table + 4 * exception_number)

    def exception_return(self, exc_return: int) -> None:
        frame_ptr = self.sp_main
        selector = exc_return & 0xF
        if selector == 0b0001:  # Return to Handler
            self.current_mode = ExecutionMode.MODE_HANDLER
            self.switch_stack(StackPointerBank.SP_MAIN)
        elif selector == 0b1001:  # Return to Thread using Main stack
            self.current_mode = ExecutionMode.MODE_THREAD
            self.switch_stack(StackPointerBank.SP_MAIN)
        elif selector == 0b1101:  # Return to Thread using Process stack
            frame_ptr = self.sp_process
            self.current_mode = ExecutionMode.MODE_THREAD
            self.switch_stack(StackPointerBank.SP_PROCESS)
        # Assigning current_mode to MODE_THREAD causes a drop in privilege
        # if CONTROL.nPRIV is set to 1

        # PopStack:
        self.registers[0] = self.rp2040.read_uint32(
            frame_ptr
        )  # Stack accesses are performed as Unprivileged accesses if
        self.registers[1] = self.rp2040.read_uint32(
            frame_ptr + 0x4
        )  # CONTROL<0>=='1' && EXC_RETURN<3>=='1' Privileged otherwise
        self.registers[2] = self.rp2040.read_uint32(frame_ptr + 0x8)
        self.registers[3] = self.rp2040.read_uint32(frame_ptr + 0xC)
        self.registers[12] = self.rp2040.read_uint32(frame_ptr + 0x10)
        self.lr = self.rp2040.read_uint32(frame_ptr + 0x14)
        self.pc = self.rp2040.read_uint32(frame_ptr + 0x18)
        psr = self.rp2040.read_uint32(frame_ptr + 0x1C)

        frame_ptr_align = 0b100 if psr & (1 << 9) else 0

        if selector == 0b0001:  # noqa: SIM114 - Returning to Handler mode
            self.sp_main = (self.sp_main + 0x20) | frame_ptr_align
        elif selector == 0b1001:  # Returning to Thread mode using Main stack
            self.sp_main = (self.sp_main + 0x20) | frame_ptr_align
        elif selector == 0b1101:  # Returning to Thread mode using Process stack
            self.sp_process = (self.sp_process + 0x20) | frame_ptr_align

        self.apsr = psr & 0xF0000000
        force_thread = self.current_mode == ExecutionMode.MODE_THREAD and self.n_priv
        self.ipsr = 0 if force_thread else psr & 0x3F
        self.interrupts_updated = True
        # Thumb bit should always be one! EPSR<24> = psr<24>; // Load valid EPSR bits from memory
        self.event_registered = True
        # if CurrentMode == Mode_Thread && SCR.SLEEPONEXIT == '1' then
        # SleepOnExit(); // IMPLEMENTATION DEFINED

    @property
    def pend_sv_priority(self) -> int:
        return (self.shpr3 >> 22) & 0x3

    @property
    def sv_call_priority(self) -> int:
        return u32(self.shpr2) >> 30

    @property
    def systick_priority(self) -> int:
        return u32(self.shpr3) >> 30

    def exception_priority(self, n: int) -> int:
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
    def vect_pending(self) -> int:
        if self.pending_nmi:
            return EXC_NMI
        sv_call_priority, systick_priority, pend_sv_priority = (
            self.sv_call_priority,
            self.systick_priority,
            self.pend_sv_priority,
        )
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

    def set_interrupt(self, irq: int, value: bool) -> None:
        irq_bit = 1 << irq
        if value and not (self.pending_interrupts & irq_bit):
            self.pending_interrupts |= irq_bit
            self.interrupts_updated = True
            if self.waiting and self.check_for_interrupts():
                self.waiting = False
        elif not value:
            self.pending_interrupts &= ~irq_bit

    def check_for_interrupts(self) -> bool:
        """If we're waiting for an interrupt (i.e. WFI/WFE), the ARM says:
        > If PRIMASK.PM is set to 1, an asynchronous exception that has a higher group priority than any
        > active exception results in a WFI instruction exit. If the group priority of the exception is less than or
        > equal to the execution group priority, the exception is ignored.
        """
        if self.waiting:
            current_priority = self.exception_priority(self.ipsr) if self.pm else LOWEST_PRIORITY
        else:
            current_priority = min(self.exception_priority(self.ipsr), 0 if self.pm else LOWEST_PRIORITY)
        interrupt_set = self.pending_interrupts & self.enabled_interrupts
        sv_call_priority, systick_priority, pend_sv_priority = (
            self.sv_call_priority,
            self.systick_priority,
            self.pend_sv_priority,
        )
        if self.pending_nmi:
            self.pending_nmi = False
            self.exception_entry(EXC_NMI)
            return True
        for priority in range(current_priority):
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
                        if interrupt_number > MAX_HARDWARE_IRQ:
                            self.pending_interrupts &= ~(1 << interrupt_number)
                        self.exception_entry(16 + interrupt_number)
                        return True
        self.interrupts_updated = False
        return False

    def read_special_register(self, sysm: int) -> int:
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
            return (2 if self.sp_sel == StackPointerBank.SP_PROCESS else 0) | (1 if self.n_priv else 0)
        self.logger.warning(LOG_NAME, f"MRS with unimplemented SYSm value: {sysm}")
        return 0

    def write_special_register(self, sysm: int, value: int) -> None:
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
            if self.current_mode == ExecutionMode.MODE_THREAD:
                self.switch_stack(StackPointerBank.SP_PROCESS if value & 2 else StackPointerBank.SP_MAIN)
        else:
            self.logger.warning(LOG_NAME, f"MRS with unimplemented SYSm value: {sysm}")

    def bx_write_pc(self, address: int) -> None:
        if self.current_mode == ExecutionMode.MODE_HANDLER and u32(address) >> 28 == 0b1111:
            self.exception_return(address & 0x0FFFFFFF)
        else:
            self.pc = address & ~1

    def _subtract_update_flags(self, minuend: int, subtrahend: int) -> int:
        result = minuend - subtrahend
        self.n = bool(result & 0x80000000)
        self.z = (result & 0xFFFFFFFF) == 0
        self.c = minuend >= subtrahend
        self.v = (bool(result & 0x80000000) and not (minuend & 0x80000000) and bool(subtrahend & 0x80000000)) or (
            not (result & 0x80000000) and bool(minuend & 0x80000000) and not (subtrahend & 0x80000000)
        )
        return result

    def _add_update_flags(self, addend1: int, addend2: int) -> int:
        unsigned_sum = u32(addend1 + addend2)
        signed_sum = s32(addend1) + s32(addend2)
        result = addend1 + addend2
        self.n = bool(result & 0x80000000)
        self.z = (result & 0xFFFFFFFF) == 0
        self.c = result != unsigned_sum
        self.v = s32(result) != signed_sum
        return result & 0xFFFFFFFF

    def cycles_io(self, addr: int, write: bool = False) -> int:
        addr = u32(addr)
        if SIO_START_ADDRESS <= addr < SIO_START_ADDRESS + 0x10000000:
            return 0
        if APB_START_ADDRESS <= addr < APB_START_ADDRESS + 0x10000000:
            return 4 if write else 3
        return 1

    def execute_instruction(self) -> int:
        if self.interrupts_updated and self.check_for_interrupts():
            self.waiting = False
        # ARM Thumb instruction encoding - 16 bits / 2 bytes
        opcode_pc = self.registers[PC_REGISTER] & ~1  # ensure no LSB set PC are executed
        opcode = self.rp2040.read_uint16(opcode_pc)
        wide_instruction = opcode >> 12 == 0b1111 or opcode >> 11 == 0b11101
        opcode2 = self.rp2040.read_uint16(opcode_pc + 2) if wide_instruction else 0
        self.registers[PC_REGISTER] += 2
        delta_cycles = 1
        # ADCS
        if opcode >> 6 == 0b0100000101:
            rm = (opcode >> 3) & 0x7
            rdn = opcode & 0x7
            self.registers[rdn] = self._add_update_flags(self.registers[rm], self.registers[rdn] + (1 if self.c else 0))
        # ADD (register = SP plus immediate)
        elif opcode >> 11 == 0b10101:
            imm8 = opcode & 0xFF
            rd = (opcode >> 8) & 0x7
            self.registers[rd] = self.sp + (imm8 << 2)
        # ADD (SP plus immediate)
        elif opcode >> 7 == 0b101100000:
            imm32 = (opcode & 0x7F) << 2
            self.sp += imm32
        # ADDS (Encoding T1)
        elif opcode >> 9 == 0b0001110:
            imm3 = (opcode >> 6) & 0x7
            rn = (opcode >> 3) & 0x7
            rd = opcode & 0x7
            self.registers[rd] = self._add_update_flags(self.registers[rn], imm3)
        # ADDS (Encoding T2)
        elif opcode >> 11 == 0b00110:
            imm8 = opcode & 0xFF
            rdn = (opcode >> 8) & 0x7
            self.registers[rdn] = self._add_update_flags(self.registers[rdn], imm8)
        # ADDS (register)
        elif opcode >> 9 == 0b0001100:
            rm = (opcode >> 6) & 0x7
            rn = (opcode >> 3) & 0x7
            rd = opcode & 0x7
            self.registers[rd] = self._add_update_flags(self.registers[rn], self.registers[rm])
        # ADD (register)
        elif opcode >> 8 == 0b01000100:
            rm = (opcode >> 3) & 0xF
            rdn = ((opcode & 0x80) >> 4) | (opcode & 0x7)
            left_value = self.registers[PC_REGISTER] + 2 if rdn == PC_REGISTER else self.registers[rdn]
            right_value = self.registers[PC_REGISTER] + 2 if rm == PC_REGISTER else self.registers[rm]
            result = left_value + right_value
            if rdn != SP_REGISTER and rdn != PC_REGISTER:
                self.registers[rdn] = result
            elif rdn == PC_REGISTER:
                self.registers[rdn] = result & ~0x1
                delta_cycles += 1
            elif rdn == SP_REGISTER:
                self.registers[rdn] = result & ~0x3
        # ADR
        elif opcode >> 11 == 0b10100:
            imm8 = opcode & 0xFF
            rd = (opcode >> 8) & 0x7
            self.registers[rd] = (opcode_pc & 0xFFFFFFFC) + 4 + (imm8 << 2)
        # ANDS (Encoding T2)
        elif opcode >> 6 == 0b0100000000:
            rm = (opcode >> 3) & 0x7
            rdn = opcode & 0x7
            result = self.registers[rdn] & self.registers[rm]
            self.registers[rdn] = result
            self.n = bool(result & 0x80000000)
            self.z = (result & 0xFFFFFFFF) == 0
        # ASRS (immediate)
        elif opcode >> 11 == 0b00010:
            imm5 = (opcode >> 6) & 0x1F
            rm = (opcode >> 3) & 0x7
            rd = opcode & 0x7
            input_value = self.registers[rm]
            shift_n = imm5 if imm5 else 32
            result = s32(input_value) >> shift_n if shift_n < 32 else (0xFFFFFFFF if input_value & 0x80000000 else 0)
            self.registers[rd] = result
            self.n = bool(result & 0x80000000)
            self.z = (result & 0xFFFFFFFF) == 0
            self.c = bool(input_value & (1 << (shift_n - 1)))
        # ASRS (register)
        elif opcode >> 6 == 0b0100000100:
            rm = (opcode >> 3) & 0x7
            rdn = opcode & 0x7
            input_value = self.registers[rdn]
            shift_n = min(32, self.registers[rm] & 255)
            result = s32(input_value) >> shift_n if shift_n < 32 else (0xFFFFFFFF if input_value & 0x80000000 else 0)
            self.registers[rdn] = result
            self.n = bool(result & 0x80000000)
            self.z = (result & 0xFFFFFFFF) == 0
            # NOTE: shift_n can be 0 here (unlike the immediate form above), and JS `1 << -1`
            # wraps the shift amount mod 32 rather than raising - `& 31` replicates that so
            # Python's `<<` doesn't raise ValueError on a negative count.
            self.c = bool(input_value & (1 << ((shift_n - 1) & 31)))
        # B (with cond)
        elif opcode >> 12 == 0b1101 and ((opcode >> 9) & 0x7) != 0b111:
            imm8 = (opcode & 0xFF) << 1
            cond = (opcode >> 8) & 0xF
            if imm8 & (1 << 8):
                imm8 = (imm8 & 0x1FF) - 0x200
            if self.check_condition(cond):
                self.registers[PC_REGISTER] += imm8 + 2
                delta_cycles += 1
        # B
        elif opcode >> 11 == 0b11100:
            imm11 = (opcode & 0x7FF) << 1
            if imm11 & (1 << 11):
                imm11 = (imm11 & 0x7FF) - 0x800
            self.registers[PC_REGISTER] += imm11 + 2
            delta_cycles += 1
        # BICS
        elif opcode >> 6 == 0b0100001110:
            rm = (opcode >> 3) & 0x7
            rdn = opcode & 0x7
            result = self.registers[rdn] & ~self.registers[rm]
            self.registers[rdn] = result
            self.n = bool(result & 0x80000000)
            self.z = u32(result) == 0
        # BKPT
        elif opcode >> 8 == 0b10111110:
            imm8 = opcode & 0xFF
            self.break_rewind = 2
            self.rp2040.on_break(imm8)
        # BL
        elif opcode >> 11 == 0b11110 and opcode2 >> 14 == 0b11 and ((opcode2 >> 12) & 0x1) == 1:
            imm11 = opcode2 & 0x7FF
            j2 = (opcode2 >> 11) & 0x1
            j1 = (opcode2 >> 13) & 0x1
            imm10 = opcode & 0x3FF
            s = (opcode >> 10) & 0x1
            i1 = 1 - (s ^ j1)
            i2 = 1 - (s ^ j2)
            imm32 = ((0b11111111 if s else 0) << 24) | ((i1 << 23) | (i2 << 22) | (imm10 << 12) | (imm11 << 1))
            self.lr = (self.registers[PC_REGISTER] + 2) | 0x1
            self.registers[PC_REGISTER] += 2 + imm32
            delta_cycles += 2
            self.bl_taken(self, False)
        # BLX
        elif opcode >> 7 == 0b010001111 and (opcode & 0x7) == 0:
            rm = (opcode >> 3) & 0xF
            self.lr = self.registers[PC_REGISTER] | 0x1
            self.registers[PC_REGISTER] = self.registers[rm] & ~1
            delta_cycles += 1
            self.bl_taken(self, True)
        # BX
        elif opcode >> 7 == 0b010001110 and (opcode & 0x7) == 0:
            rm = (opcode >> 3) & 0xF
            self.bx_write_pc(self.registers[rm])
            delta_cycles += 1
        # CMN (register)
        elif opcode >> 6 == 0b0100001011:
            rm = (opcode >> 3) & 0x7
            rn = opcode & 0x7
            self._add_update_flags(self.registers[rn], self.registers[rm])
        # CMP immediate
        elif opcode >> 11 == 0b00101:
            rn = (opcode >> 8) & 0x7
            imm8 = opcode & 0xFF
            self._subtract_update_flags(self.registers[rn], imm8)
        # CMP (register)
        elif opcode >> 6 == 0b0100001010:
            rm = (opcode >> 3) & 0x7
            rn = opcode & 0x7
            self._subtract_update_flags(self.registers[rn], self.registers[rm])
        # CMP (register) encoding T2
        elif opcode >> 8 == 0b01000101:
            rm = (opcode >> 3) & 0xF
            rn = ((opcode >> 4) & 0x8) | (opcode & 0x7)
            self._subtract_update_flags(self.registers[rn], self.registers[rm])
        # CPSID i
        elif opcode == 0xB672:
            self.pm = True
        # CPSIE i
        elif opcode == 0xB662:
            self.pm = False
            self.interrupts_updated = True
        # DMB SY
        elif opcode == 0xF3BF and (opcode2 & 0xFFF0) == 0x8F50:  # noqa: SIM114
            self.registers[PC_REGISTER] += 2
            delta_cycles += 2
        # DSB SY
        elif opcode == 0xF3BF and (opcode2 & 0xFFF0) == 0x8F40:
            self.registers[PC_REGISTER] += 2
            delta_cycles += 2
        # EORS
        elif opcode >> 6 == 0b0100000001:
            rm = (opcode >> 3) & 0x7
            rdn = opcode & 0x7
            result = self.registers[rm] ^ self.registers[rdn]
            self.registers[rdn] = result
            self.n = bool(result & 0x80000000)
            self.z = u32(result) == 0
        # ISB SY
        elif opcode == 0xF3BF and (opcode2 & 0xFFF0) == 0x8F60:
            self.registers[PC_REGISTER] += 2
            delta_cycles += 2
        # LDMIA
        elif opcode >> 11 == 0b11001:
            rn = (opcode >> 8) & 0x7
            reg_list = opcode & 0xFF
            address = self.registers[rn]
            for i in range(8):
                if reg_list & (1 << i):
                    self.registers[i] = self.rp2040.read_uint32(address)
                    address += 4
                    delta_cycles += 1
            # Write back
            if not (reg_list & (1 << rn)):
                self.registers[rn] = address
        # LDR (immediate)
        elif opcode >> 11 == 0b01101:
            imm5 = ((opcode >> 6) & 0x1F) << 2
            rn = (opcode >> 3) & 0x7
            rt = opcode & 0x7
            addr = self.registers[rn] + imm5
            delta_cycles += self.cycles_io(addr)
            self.registers[rt] = self.rp2040.read_uint32(addr)
        # LDR (sp + immediate)
        elif opcode >> 11 == 0b10011:
            rt = (opcode >> 8) & 0x7
            imm8 = opcode & 0xFF
            addr = self.sp + (imm8 << 2)
            delta_cycles += self.cycles_io(addr)
            self.registers[rt] = self.rp2040.read_uint32(addr)
        # LDR (literal)
        elif opcode >> 11 == 0b01001:
            imm8 = (opcode & 0xFF) << 2
            rt = (opcode >> 8) & 7
            next_pc = self.registers[PC_REGISTER] + 2
            addr = (next_pc & 0xFFFFFFFC) + imm8
            delta_cycles += self.cycles_io(addr)
            self.registers[rt] = self.rp2040.read_uint32(addr)
        # LDR (register)
        elif opcode >> 9 == 0b0101100:
            rm = (opcode >> 6) & 0x7
            rn = (opcode >> 3) & 0x7
            rt = opcode & 0x7
            addr = self.registers[rm] + self.registers[rn]
            delta_cycles += self.cycles_io(addr)
            self.registers[rt] = self.rp2040.read_uint32(addr)
        # LDRB (immediate)
        elif opcode >> 11 == 0b01111:
            imm5 = (opcode >> 6) & 0x1F
            rn = (opcode >> 3) & 0x7
            rt = opcode & 0x7
            addr = self.registers[rn] + imm5
            delta_cycles += self.cycles_io(addr)
            self.registers[rt] = self.rp2040.read_uint8(addr)
        # LDRB (register)
        elif opcode >> 9 == 0b0101110:
            rm = (opcode >> 6) & 0x7
            rn = (opcode >> 3) & 0x7
            rt = opcode & 0x7
            addr = self.registers[rm] + self.registers[rn]
            delta_cycles += self.cycles_io(addr)
            self.registers[rt] = self.rp2040.read_uint8(addr)
        # LDRH (immediate)
        elif opcode >> 11 == 0b10001:
            imm5 = (opcode >> 6) & 0x1F
            rn = (opcode >> 3) & 0x7
            rt = opcode & 0x7
            addr = self.registers[rn] + (imm5 << 1)
            delta_cycles += self.cycles_io(addr)
            self.registers[rt] = self.rp2040.read_uint16(addr)
        # LDRH (register)
        elif opcode >> 9 == 0b0101101:
            rm = (opcode >> 6) & 0x7
            rn = (opcode >> 3) & 0x7
            rt = opcode & 0x7
            addr = self.registers[rm] + self.registers[rn]
            delta_cycles += self.cycles_io(addr)
            self.registers[rt] = self.rp2040.read_uint16(addr)
        # LDRSB
        elif opcode >> 9 == 0b0101011:
            rm = (opcode >> 6) & 0x7
            rn = (opcode >> 3) & 0x7
            rt = opcode & 0x7
            addr = self.registers[rm] + self.registers[rn]
            delta_cycles += self.cycles_io(addr)
            self.registers[rt] = sign_extend8(self.rp2040.read_uint8(addr))
        # LDRSH
        elif opcode >> 9 == 0b0101111:
            rm = (opcode >> 6) & 0x7
            rn = (opcode >> 3) & 0x7
            rt = opcode & 0x7
            addr = self.registers[rm] + self.registers[rn]
            delta_cycles += self.cycles_io(addr)
            self.registers[rt] = sign_extend16(self.rp2040.read_uint16(addr))
        # LSLS (immediate)
        elif opcode >> 11 == 0b00000:
            imm5 = (opcode >> 6) & 0x1F
            rm = (opcode >> 3) & 0x7
            rd = opcode & 0x7
            input_value = self.registers[rm]
            result = input_value << imm5
            self.registers[rd] = result
            self.n = bool(result & 0x80000000)
            self.z = u32(result) == 0
            self.c = bool(input_value & (1 << (32 - imm5))) if imm5 else self.c
        # LSLS (register)
        elif opcode >> 6 == 0b0100000010:
            rm = (opcode >> 3) & 0x7
            rdn = opcode & 0x7
            input_value = self.registers[rdn]
            shift_count = self.registers[rm] & 0xFF
            result = 0 if shift_count >= 32 else input_value << shift_count
            self.registers[rdn] = result
            self.n = bool(result & 0x80000000)
            self.z = u32(result) == 0
            # NOTE: shift_count ranges 0-255 here (unlike the immediate form's 0-31 imm5), so
            # `32 - shift_count` can go negative; `& 31` replicates JS's mod-32 shift-amount
            # wraparound instead of raising ValueError on a negative count in Python.
            self.c = bool(input_value & (1 << ((32 - shift_count) & 31))) if shift_count else self.c
        # LSRS (immediate)
        elif opcode >> 11 == 0b00001:
            imm5 = (opcode >> 6) & 0x1F
            rm = (opcode >> 3) & 0x7
            rd = opcode & 0x7
            input_value = self.registers[rm]
            result = input_value >> imm5 if imm5 else 0
            self.registers[rd] = result
            self.n = bool(result & 0x80000000)
            self.z = result == 0
            self.c = bool((input_value >> (imm5 - 1 if imm5 else 31)) & 0x1)
        # LSRS (register)
        elif opcode >> 6 == 0b0100000011:
            rm = (opcode >> 3) & 0x7
            rdn = opcode & 0x7
            shift_amount = self.registers[rm] & 0xFF
            input_value = self.registers[rdn]
            result = input_value >> shift_amount if shift_amount < 32 else 0
            self.registers[rdn] = result
            self.n = bool(result & 0x80000000)
            self.z = result == 0
            # NOTE: shift_amount can be 0 here, making `shift_amount - 1` negative; `& 31`
            # replicates JS's mod-32 shift-amount wraparound instead of raising ValueError.
            self.c = bool((input_value >> ((shift_amount - 1) & 31)) & 0x1) if shift_amount <= 32 else False
        # MOV
        elif opcode >> 8 == 0b01000110:
            rm = (opcode >> 3) & 0xF
            rd = ((opcode >> 4) & 0x8) | (opcode & 0x7)
            value = self.registers[PC_REGISTER] + 2 if rm == PC_REGISTER else self.registers[rm]
            if rd == PC_REGISTER:
                delta_cycles += 1
                value &= ~1
            elif rd == SP_REGISTER:
                value &= ~3
            self.registers[rd] = value
        # MOVS
        elif opcode >> 11 == 0b00100:
            value = opcode & 0xFF
            rd = (opcode >> 8) & 7
            self.registers[rd] = value
            self.n = bool(value & 0x80000000)
            self.z = value == 0
        # MRS
        elif opcode == 0b1111001111101111 and opcode2 >> 12 == 0b1000:
            sysm = opcode2 & 0xFF
            rd = (opcode2 >> 8) & 0xF
            self.registers[rd] = self.read_special_register(sysm)
            self.registers[PC_REGISTER] += 2
            delta_cycles += 2
        # MSR
        elif opcode >> 4 == 0b111100111000 and opcode2 >> 8 == 0b10001000:
            sysm = opcode2 & 0xFF
            rn = opcode & 0xF
            self.write_special_register(sysm, self.registers[rn])
            self.registers[PC_REGISTER] += 2
            delta_cycles += 2
        # MULS
        elif opcode >> 6 == 0b0100001101:
            rn = (opcode >> 3) & 0x7
            rdm = opcode & 0x7
            result = (self.registers[rn] * self.registers[rdm]) & 0xFFFFFFFF
            self.registers[rdm] = result
            self.n = bool(result & 0x80000000)
            self.z = u32(result) == 0
        # MVNS
        elif opcode >> 6 == 0b0100001111:
            rm = (opcode >> 3) & 7
            rd = opcode & 7
            result = ~self.registers[rm]
            self.registers[rd] = result
            self.n = bool(result & 0x80000000)
            self.z = u32(result) == 0
        # ORRS (Encoding T2)
        elif opcode >> 6 == 0b0100001100:
            rm = (opcode >> 3) & 0x7
            rdn = opcode & 0x7
            result = self.registers[rdn] | self.registers[rm]
            self.registers[rdn] = result
            self.n = bool(result & 0x80000000)
            self.z = (result & 0xFFFFFFFF) == 0
        # POP
        elif opcode >> 9 == 0b1011110:
            p = (opcode >> 8) & 1
            address = self.sp
            for i in range(8):
                if opcode & (1 << i):
                    self.registers[i] = self.rp2040.read_uint32(address)
                    address += 4
                    delta_cycles += 1
            if p:
                self.sp = address + 4
                self.bx_write_pc(self.rp2040.read_uint32(address))
                delta_cycles += 2
            else:
                self.sp = address
        # PUSH
        elif opcode >> 9 == 0b1011010:
            bit_count = 0
            for i in range(9):
                if opcode & (1 << i):
                    bit_count += 1
            address = self.sp - 4 * bit_count
            for i in range(8):
                if opcode & (1 << i):
                    self.rp2040.write_uint32(address, self.registers[i])
                    delta_cycles += 1
                    address += 4
            if opcode & (1 << 8):
                self.rp2040.write_uint32(address, self.registers[14])
            self.sp -= 4 * bit_count
        # REV
        elif opcode >> 6 == 0b1011101000:
            rm = (opcode >> 3) & 0x7
            rd = opcode & 0x7
            input_value = self.registers[rm]
            self.registers[rd] = (
                ((input_value & 0xFF) << 24)
                | (((input_value >> 8) & 0xFF) << 16)
                | (((input_value >> 16) & 0xFF) << 8)
                | ((input_value >> 24) & 0xFF)
            )
        # REV16
        elif opcode >> 6 == 0b1011101001:
            rm = (opcode >> 3) & 0x7
            rd = opcode & 0x7
            input_value = self.registers[rm]
            self.registers[rd] = (
                (((input_value >> 16) & 0xFF) << 24)
                | (((input_value >> 24) & 0xFF) << 16)
                | ((input_value & 0xFF) << 8)
                | ((input_value >> 8) & 0xFF)
            )
        # REVSH
        elif opcode >> 6 == 0b1011101011:
            rm = (opcode >> 3) & 0x7
            rd = opcode & 0x7
            input_value = self.registers[rm]
            self.registers[rd] = sign_extend16(((input_value & 0xFF) << 8) | ((input_value >> 8) & 0xFF))
        # ROR
        elif opcode >> 6 == 0b0100000111:
            rm = (opcode >> 3) & 0x7
            rdn = opcode & 0x7
            input_value = self.registers[rdn]
            shift = (self.registers[rm] & 0xFF) % 32
            result = (input_value >> shift) | (input_value << (32 - shift))
            self.registers[rdn] = result
            self.n = bool(result & 0x80000000)
            self.z = u32(result) == 0
            self.c = bool(result & 0x80000000)
        # NEGS / RSBS
        elif opcode >> 6 == 0b0100001001:
            rn = (opcode >> 3) & 0x7
            rd = opcode & 0x7
            self.registers[rd] = self._subtract_update_flags(0, self.registers[rn])
        # NOP
        elif opcode == 0b1011111100000000:
            pass  # Do nothing!
        # SBCS (Encoding T1)
        elif opcode >> 6 == 0b0100000110:
            rm = (opcode >> 3) & 0x7
            rdn = opcode & 0x7
            self.registers[rdn] = self._subtract_update_flags(
                self.registers[rdn], self.registers[rm] + (1 - (1 if self.c else 0))
            )
        # SEV
        elif opcode == 0b1011111101000000:
            self.logger.info(LOG_NAME, "SEV")
        # STMIA
        elif opcode >> 11 == 0b11000:
            rn = (opcode >> 8) & 0x7
            reg_list = opcode & 0xFF
            address = self.registers[rn]
            for i in range(8):
                if reg_list & (1 << i):
                    self.rp2040.write_uint32(address, self.registers[i])
                    address += 4
                    delta_cycles += 1
            # Write back
            if not (reg_list & (1 << rn)):
                self.registers[rn] = address
        # STR (immediate)
        elif opcode >> 11 == 0b01100:
            imm5 = ((opcode >> 6) & 0x1F) << 2
            rn = (opcode >> 3) & 0x7
            rt = opcode & 0x7
            address = self.registers[rn] + imm5
            delta_cycles += self.cycles_io(address, True)
            self.rp2040.write_uint32(address, self.registers[rt])
        # STR (sp + immediate)
        elif opcode >> 11 == 0b10010:
            rt = (opcode >> 8) & 0x7
            imm8 = opcode & 0xFF
            address = self.sp + (imm8 << 2)
            delta_cycles += self.cycles_io(address, True)
            self.rp2040.write_uint32(address, self.registers[rt])
        # STR (register)
        elif opcode >> 9 == 0b0101000:
            rm = (opcode >> 6) & 0x7
            rn = (opcode >> 3) & 0x7
            rt = opcode & 0x7
            address = self.registers[rm] + self.registers[rn]
            delta_cycles += self.cycles_io(address, True)
            self.rp2040.write_uint32(address, self.registers[rt])
        # STRB (immediate)
        elif opcode >> 11 == 0b01110:
            imm5 = (opcode >> 6) & 0x1F
            rn = (opcode >> 3) & 0x7
            rt = opcode & 0x7
            address = self.registers[rn] + imm5
            delta_cycles += self.cycles_io(address, True)
            self.rp2040.write_uint8(address, self.registers[rt])
        # STRB (register)
        elif opcode >> 9 == 0b0101010:
            rm = (opcode >> 6) & 0x7
            rn = (opcode >> 3) & 0x7
            rt = opcode & 0x7
            address = self.registers[rm] + self.registers[rn]
            delta_cycles += self.cycles_io(address, True)
            self.rp2040.write_uint8(address, self.registers[rt])
        # STRH (immediate)
        elif opcode >> 11 == 0b10000:
            imm5 = ((opcode >> 6) & 0x1F) << 1
            rn = (opcode >> 3) & 0x7
            rt = opcode & 0x7
            address = self.registers[rn] + imm5
            delta_cycles += self.cycles_io(address, True)
            self.rp2040.write_uint16(address, self.registers[rt])
        # STRH (register)
        elif opcode >> 9 == 0b0101001:
            rm = (opcode >> 6) & 0x7
            rn = (opcode >> 3) & 0x7
            rt = opcode & 0x7
            address = self.registers[rm] + self.registers[rn]
            delta_cycles += self.cycles_io(address, True)
            self.rp2040.write_uint16(address, self.registers[rt])
        # SUB (SP minus immediate)
        elif opcode >> 7 == 0b101100001:
            imm32 = (opcode & 0x7F) << 2
            self.sp -= imm32
        # SUBS (Encoding T1)
        elif opcode >> 9 == 0b0001111:
            imm3 = (opcode >> 6) & 0x7
            rn = (opcode >> 3) & 0x7
            rd = opcode & 0x7
            self.registers[rd] = self._subtract_update_flags(self.registers[rn], imm3)
        # SUBS (Encoding T2)
        elif opcode >> 11 == 0b00111:
            imm8 = opcode & 0xFF
            rdn = (opcode >> 8) & 0x7
            self.registers[rdn] = self._subtract_update_flags(self.registers[rdn], imm8)
        # SUBS (register)
        elif opcode >> 9 == 0b0001101:
            rm = (opcode >> 6) & 0x7
            rn = (opcode >> 3) & 0x7
            rd = opcode & 0x7
            self.registers[rd] = self._subtract_update_flags(self.registers[rn], self.registers[rm])
        # SVC
        elif opcode >> 8 == 0b11011111:
            self.pending_svcall = True
            self.interrupts_updated = True
        # SXTB
        elif opcode >> 6 == 0b1011001001:
            rm = (opcode >> 3) & 0x7
            rd = opcode & 0x7
            self.registers[rd] = sign_extend8(self.registers[rm])
        # SXTH
        elif opcode >> 6 == 0b1011001000:
            rm = (opcode >> 3) & 0x7
            rd = opcode & 0x7
            self.registers[rd] = sign_extend16(self.registers[rm])
        # TST
        elif opcode >> 6 == 0b0100001000:
            rm = (opcode >> 3) & 0x7
            rn = opcode & 0x7
            result = self.registers[rn] & self.registers[rm]
            self.n = bool(result & 0x80000000)
            self.z = result == 0
        # UDF
        elif opcode >> 8 == 0b11011110:
            imm8 = opcode & 0xFF
            self.break_rewind = 2
            self.rp2040.on_break(imm8)
        # UDF (Encoding T2)
        elif opcode >> 4 == 0b111101111111 and opcode2 >> 12 == 0b1010:
            imm4 = opcode & 0xF
            imm12 = opcode2 & 0xFFF
            self.break_rewind = 4
            self.rp2040.on_break((imm4 << 12) | imm12)
            self.registers[PC_REGISTER] += 2
        # UXTB
        elif opcode >> 6 == 0b1011001011:
            rm = (opcode >> 3) & 0x7
            rd = opcode & 0x7
            self.registers[rd] = self.registers[rm] & 0xFF
        # UXTH
        elif opcode >> 6 == 0b1011001010:
            rm = (opcode >> 3) & 0x7
            rd = opcode & 0x7
            self.registers[rd] = self.registers[rm] & 0xFFFF
        # WFE
        elif opcode == 0b1011111100100000:
            delta_cycles += 1
            if self.event_registered:
                self.event_registered = False
            else:
                self.waiting = True
        # WFI
        elif opcode == 0b1011111100110000:
            delta_cycles += 1
            self.waiting = True
        # YIELD
        elif opcode == 0b1011111100010000:
            # do nothing for now. Wait for event!
            self.logger.info(LOG_NAME, "Yield")
        else:
            self.logger.warning(LOG_NAME, f"Warning: Instruction at {opcode_pc:x} is not implemented yet!")
            self.logger.warning(LOG_NAME, f"Opcode: 0x{opcode:x} (0x{opcode2:x})")

        self.cycles += delta_cycles
        return delta_cycles
