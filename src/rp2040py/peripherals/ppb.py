from typing import TYPE_CHECKING

from rp2040py.irq import MAX_HARDWARE_IRQ
from rp2040py.peripherals.peripheral import BasePeripheral
from rp2040py.utils.bit import u32
from rp2040py.utils.timer32 import Timer32, Timer32PeriodicAlarm, TimerMode

if TYPE_CHECKING:
    from rp2040py.rp2040 import RP2040

__all__ = (
    "CPUID",
    "ICSR",
    "RPPPB",
    "SHPR2",
    "SHPR3",
    "VTOR",
)

CPUID = 0xD00
ICSR = 0xD04
VTOR = 0xD08
SHPR2 = 0xD1C
SHPR3 = 0xD20

SYST_CSR = 0x010  # SysTick Control and Status Register
SYST_RVR = 0x014  # SysTick Reload Value Register
SYST_CVR = 0x018  # SysTick Current Value Register
SYST_CALIB = 0x01C  # SysTick Calibration Value Register
NVIC_ISER = 0x100  # Interrupt Set-Enable Register
NVIC_ICER = 0x180  # Interrupt Clear-Enable Register
NVIC_ISPR = 0x200  # Interrupt Set-Pending Register
NVIC_ICPR = 0x280  # Interrupt Clear-Pending Register

# Interrupt priority registers:
NVIC_IPR0 = 0x400
NVIC_IPR1 = 0x404
NVIC_IPR2 = 0x408
NVIC_IPR3 = 0x40C
NVIC_IPR4 = 0x410
NVIC_IPR5 = 0x414
NVIC_IPR6 = 0x418
NVIC_IPR7 = 0x41C
NVIC_IPR_REGS = (
    NVIC_IPR0,
    NVIC_IPR1,
    NVIC_IPR2,
    NVIC_IPR3,
    NVIC_IPR4,
    NVIC_IPR5,
    NVIC_IPR6,
    NVIC_IPR7,
)

# ICSR Bits
NMIPENDSET = 1 << 31
PENDSVSET = 1 << 28
PENDSVCLR = 1 << 27
PENDSTSET = 1 << 26
PENDSTCLR = 1 << 25
ISRPREEMPT = 1 << 23
ISRPENDING = 1 << 22
VECTPENDING_MASK = 0x1FF
VECTPENDING_SHIFT = 12
VECTACTIVE_MASK = 0x1FF
VECTACTIVE_SHIFT = 0


class RPPPB(BasePeripheral):
    """PPB stands for Private Peripheral Bus.

    These are peripherals that are part of the ARM Cortex Core, and there's one copy for
    each processor core.

    Included peripheral: NVIC, SysTick timer
    """

    def __init__(self, rp2040: "RP2040", name: str):
        super().__init__(rp2040, name)
        # Systick
        self.systick_count_flag = False
        self.systick_clk_source = False
        self.systick_int_enable = False
        self.systick_reload = 0
        self.systick_timer = Timer32(self.rp2040.clock, self.rp2040.clk_sys)

        def _on_systick_alarm() -> None:
            self.systick_count_flag = True
            if self.systick_int_enable:
                self.rp2040.core.pending_systick = True
                self.rp2040.core.interrupts_updated = True
            self.systick_timer.set(self.systick_reload)

        self.systick_alarm = Timer32PeriodicAlarm(self.systick_timer, _on_systick_alarm)

        self.systick_timer.top = 0xFFFFFF
        self.systick_timer.mode = TimerMode.DECREMENT
        self.systick_alarm.target = 0
        self.systick_alarm.enable = True
        self.reset()

    def reset(self) -> None:
        self.write_uint32(SYST_CSR, 0)
        self.write_uint32(SYST_RVR, 0xFFFFFF)
        self.systick_timer.set(0xFFFFFF)

    def read_uint32(self, offset: int) -> int:
        core = self.rp2040.core

        if offset == CPUID:
            return 0x410CC601  # Verified against actual hardware

        if offset == ICSR:
            pending_interrupts = (
                core.pending_interrupts or core.pending_pend_sv or core.pending_systick or core.pending_svcall
            )
            vect_pending = core.vect_pending
            return (
                (NMIPENDSET if core.pending_nmi else 0)
                | (PENDSVSET if core.pending_pend_sv else 0)
                | (PENDSTSET if core.pending_systick else 0)
                | (ISRPENDING if pending_interrupts else 0)
                | (vect_pending << VECTPENDING_SHIFT)
                | ((core.ipsr & VECTACTIVE_MASK) << VECTACTIVE_SHIFT)
            )

        if offset == VTOR:
            return core.vtor

        # NVIC
        if offset == NVIC_ISPR:
            return u32(core.pending_interrupts)
        if offset == NVIC_ICPR:
            return u32(core.pending_interrupts)
        if offset == NVIC_ISER:
            return u32(core.enabled_interrupts)
        if offset == NVIC_ICER:
            return u32(core.enabled_interrupts)

        if offset in NVIC_IPR_REGS:
            reg_index = (offset - NVIC_IPR0) >> 2
            result = 0
            for byte_index in range(4):
                interrupt_number = reg_index * 4 + byte_index
                for priority in range(len(core.interrupt_priorities)):
                    if core.interrupt_priorities[priority] & (1 << interrupt_number):
                        result |= priority << (8 * byte_index + 6)
            return result

        if offset == SHPR2:
            return core.shpr2
        if offset == SHPR3:
            return core.shpr3

        # SysTick
        if offset == SYST_CSR:
            count_flag_value = (1 << 16) if self.systick_count_flag else 0
            clk_source_value = (1 << 2) if self.systick_clk_source else 0
            tick_int_value = (1 << 1) if self.systick_int_enable else 0
            enable_flag_value = (1 << 0) if self.systick_timer.enable else 0
            self.systick_count_flag = False
            return count_flag_value | clk_source_value | tick_int_value | enable_flag_value
        if offset == SYST_CVR:
            return self.systick_timer.counter
        if offset == SYST_RVR:
            return self.systick_reload
        if offset == SYST_CALIB:
            return 0x0000270F

        return super().read_uint32(offset)

    def write_uint32(self, offset: int, value: int) -> None:
        core = self.rp2040.core

        hardware_interrupt_mask = (1 << MAX_HARDWARE_IRQ) - 1

        if offset == ICSR:
            if value & NMIPENDSET:
                core.pending_nmi = True
                core.interrupts_updated = True
            if value & PENDSVSET:
                core.pending_pend_sv = True
                core.interrupts_updated = True
            if value & PENDSVCLR:
                core.pending_pend_sv = False
            if value & PENDSTSET:
                core.pending_systick = True
                core.interrupts_updated = True
            if value & PENDSTCLR:
                core.pending_systick = False
            return

        if offset == VTOR:
            core.vtor = value
            return

        # NVIC
        if offset == NVIC_ISPR:
            core.pending_interrupts |= value
            core.interrupts_updated = True
            return
        if offset == NVIC_ICPR:
            core.pending_interrupts &= ~value | hardware_interrupt_mask
            return
        if offset == NVIC_ISER:
            core.enabled_interrupts |= value
            core.interrupts_updated = True
            return
        if offset == NVIC_ICER:
            core.enabled_interrupts &= ~value
            return

        if offset in NVIC_IPR_REGS:
            reg_index = (offset - NVIC_IPR0) >> 2
            for byte_index in range(4):
                interrupt_number = reg_index * 4 + byte_index
                new_priority = (value >> (8 * byte_index + 6)) & 0x3
                for priority in range(len(core.interrupt_priorities)):
                    core.interrupt_priorities[priority] &= ~(1 << interrupt_number)
                core.interrupt_priorities[new_priority] |= 1 << interrupt_number
            core.interrupts_updated = True
            return

        if offset == SHPR2:
            core.shpr2 = value
            return
        if offset == SHPR3:
            core.shpr3 = value
            return

        # SysTick
        if offset == SYST_CSR:
            self.systick_clk_source = bool(value & (1 << 2))
            self.systick_int_enable = bool(value & (1 << 1))
            self.systick_timer.enable = bool(value & (1 << 0))
            return
        if offset == SYST_CVR:
            self.systick_timer.set(0)
            return
        if offset == SYST_RVR:
            self.systick_reload = value
            return

        super().write_uint32(offset, value)
