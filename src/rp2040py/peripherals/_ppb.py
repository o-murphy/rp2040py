from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from rp2040py.irq import MAX_HARDWARE_IRQ
from rp2040py.peripherals.peripheral import BasePeripheral
from rp2040py.utils.bit import u32
from rp2040py.utils.timer32 import Timer32, Timer32PeriodicAlarm, TimerMode

if TYPE_CHECKING:
    from rp2040py._cortex_m0_core import CortexM0Core
    from rp2040py.rp2040 import RP2040

__all__ = (
    "AIRCR",
    "CCR",
    "CPUID",
    "ICSR",
    "MPU_CTRL",
    "MPU_RASR",
    "MPU_RBAR",
    "MPU_RNR",
    "MPU_TYPE",
    "RPPPB",
    "SCR",
    "SHCSR",
    "SHPR2",
    "SHPR3",
    "VTOR",
)

CPUID = 0xD00
ICSR = 0xD04
VTOR = 0xD08
AIRCR = 0xD0C
SCR = 0xD10
CCR = 0xD14
SHPR2 = 0xD1C
SHPR3 = 0xD20
SHCSR = 0xD24
MPU_TYPE = 0xD90
MPU_CTRL = 0xD94
MPU_RNR = 0xD98
MPU_RBAR = 0xD9C
MPU_RASR = 0xDA0

# Reserved bits do not stick (RP2040 datasheet 2.4.4): VTOR[7:0] are reserved, SHPR2 keeps PRI_11 (31:30), SHPR3 keeps PRI_15 (31:30) and PRI_14 (23:22).
VTOR_MASK = 0xFFFFFF00
SHPR2_MASK = 0xC0000000
SHPR3_MASK = 0xC0C00000
AIRCR_VECTKEY = 0x05FA
AIRCR_READ_VALUE = 0xFA050000  # "Reads as Unknown" in the datasheet, but VECTKEYSTAT reads 0xFA05
AIRCR_SYSRESETREQ = 1 << 2
SCR_MASK = 0x16  # SEVONPEND (4), SLEEPDEEP (2), SLEEPONEXIT (1)
CCR_VALUE = 0x208  # STKALIGN and UNALIGN_TRP read as 1, writes ignored
SHCSR_SVCALLPENDED = 1 << 15
MPU_TYPE_VALUE = 0x800  # 8 unified regions (DREGION = 8), no separate instruction regions
MPU_REGIONS = 8
MPU_CTRL_MASK = 0x7
MPU_RNR_MASK = 0xF
MPU_RBAR_ADDR_MASK = 0xFFFFFF00
MPU_RBAR_VALID = 1 << 4
MPU_RASR_MASK = 0x1707FF3F

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


# SysTick's external reference clock (SYST_CSR.CLKSOURCE = 0): "The SysTick timer uses a 1μs pulse as a clock enable. This is generated in the watchdog block as timer_tick"
# (RP2040 datasheet, 2.4.5.1.1 SysTick timer) - the watchdog's tick, nominally 1 MHz; it is what `tick_changed()` is told, and 0 while the tick is not running.
# (CLKSOURCE = 1 is "Processor clock"; SYST_CALIB.NOREF is 0, the reference is provided.)


class RPPPB(BasePeripheral):
    """PPB stands for Private Peripheral Bus.

    These are peripherals that are part of the ARM Cortex Core, and there's one copy for
    each processor core.

    Included peripheral: NVIC, SysTick timer
    """

    def __init__(self, rp2040: "RP2040", name: str, core: "CortexM0Core | None" = None):
        super().__init__(rp2040, name)
        self._core = core  # the core this PPB belongs to (core 1's has its own PPB); None: the chip's `core`, looked up when used
        # Systick
        self.systick_count_flag = False
        self.systick_clk_source = False
        self.systick_int_enable = False
        self.systick_enable = (
            False  # SYST_CSR.ENABLE as written; the counter also needs a running clock (the tick, or clk_sys)
        )
        self.systick_reload = 0
        self.tick_hz = 0.0  # the watchdog's tick: SysTick's reference clock
        # SYSRESETREQ (AIRCR bit 2): "Reset the core only" (datasheet 2.4.2.9) - the default handler resets the core and the PPB, not the chip.
        self.on_system_reset_request: Callable[[], None] = self._default_system_reset_request
        self.clk_sys = self.rp2040.clk_sys
        self.systick_timer = Timer32(self.rp2040.clock, self.clk_sys)

        def _on_systick_alarm() -> None:
            # The counter is a cycle RELOAD .. 1, 0 - the timer's TOP is RELOAD - so it comes round to RELOAD by itself
            self.systick_count_flag = True
            if self.systick_int_enable:
                self.core.pending_systick = True
                self.core.interrupts_updated = True

        self.systick_alarm = Timer32PeriodicAlarm(self.systick_timer, _on_systick_alarm)

        self.systick_timer.top = 0xFFFFFF
        self.systick_timer.mode = TimerMode.DECREMENT
        self.systick_alarm.target = 0
        self.systick_alarm.enable = True
        self.reset()

    def clk_sys_changed(self, clk_sys: float) -> None:
        """clk_sys is now `clk_sys` (called by `update_clocks`): SysTick follows it when its CLKSOURCE says processor clock."""
        self.clk_sys = clk_sys
        self._retune_systick()

    def snapshot(self) -> dict[str, Any]:
        """The block's state as plain values: SysTick (flags, reload, the countdown and its alarm, the clocks it follows) and the system-control registers (SCR, the MPU)."""
        timer = self.systick_timer
        return {
            "systick_count_flag": bool(self.systick_count_flag),
            "systick_clk_source": bool(self.systick_clk_source),
            "systick_int_enable": bool(self.systick_int_enable),
            "systick_enable": bool(self.systick_enable),
            "tick_hz": float(self.tick_hz),
            "systick_reload": int(self.systick_reload),
            "timer_enable": bool(timer.enable),
            "timer_mode": int(timer.mode),
            "clk_sys": float(self.clk_sys),
            "timer_frequency": float(timer.frequency),
            "timer_prescaler": float(timer.prescaler),
            "timer_top": int(timer.top),
            "timer_raw_counter": int(timer.raw_counter),
            "timer_counter": int(timer.counter),
            "alarm_target": int(self.systick_alarm.target),
            "alarm_enable": bool(self.systick_alarm.enable),
            "scr": int(self.scr),
            "mpu_ctrl": int(self.mpu_ctrl),
            "mpu_rnr": int(self.mpu_rnr),
            "mpu_rbar": tuple(int(word) for word in self.mpu_rbar),
            "mpu_rasr": tuple(int(word) for word in self.mpu_rasr),
        }

    def tick_changed(self, tick_hz: float) -> None:
        """The watchdog's tick is now `tick_hz` (0: stopped): SysTick follows it when its CLKSOURCE says reference clock."""
        self.tick_hz = tick_hz
        self._retune_systick()

    @property
    def core(self) -> "CortexM0Core":
        return self._core if self._core is not None else self.rp2040.core

    def _retune_systick(self) -> None:
        frequency = self.clk_sys if self.systick_clk_source else self.tick_hz
        if frequency and self.systick_timer.frequency != frequency:
            self.systick_timer.frequency = frequency
        self.systick_timer.enable = self.systick_enable and frequency > 0  # a stopped reference clock stops the counter

    def _default_system_reset_request(self) -> None:
        if (
            self._core is not None
        ):  # core 1's own: it goes back to the state of a core held in reset; core 0 and the entry point are not its business
            self._core.reset_state()
            self.reset()
            return
        self.core.reset()
        self.reset()
        self.rp2040.leave_reset()

    def reset(self) -> None:
        self.scr = 0
        self.mpu_ctrl = 0
        self.mpu_rnr = 0
        self.mpu_rbar = [0] * MPU_REGIONS
        self.mpu_rasr = [0] * MPU_REGIONS
        self.write_uint32(SYST_CSR, 0)
        self.write_uint32(SYST_RVR, 0xFFFFFF)
        self.systick_timer.set(0xFFFFFF)

    def read_uint32(self, offset: int) -> int:
        core = self.core

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
        if offset == AIRCR:
            return AIRCR_READ_VALUE
        if offset == SCR:
            return self.scr
        if offset == CCR:
            return CCR_VALUE
        if offset == SHCSR:
            return SHCSR_SVCALLPENDED if core.pending_svcall else 0
        if offset == MPU_TYPE:
            return MPU_TYPE_VALUE
        if offset == MPU_CTRL:
            return self.mpu_ctrl
        if offset == MPU_RNR:
            return self.mpu_rnr
        if offset == MPU_RBAR:
            region = self.mpu_rnr
            if region >= MPU_REGIONS:
                return 0
            return self.mpu_rbar[region] | region
        if offset == MPU_RASR:
            region = self.mpu_rnr
            return self.mpu_rasr[region] if region < MPU_REGIONS else 0

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
            enable_flag_value = (1 << 0) if self.systick_enable else 0
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
        core = self.core

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
            core.vtor = value & VTOR_MASK
            return
        if offset == AIRCR:
            # Only a write with VECTKEY 0x05FA is taken; SYSRESETREQ resets the core, VECTCLRACTIVE (the debug bit) is ignored
            if (value >> 16) & 0xFFFF == AIRCR_VECTKEY and value & AIRCR_SYSRESETREQ:
                self.on_system_reset_request()
            return
        if offset == SCR:
            self.scr = value & SCR_MASK
            return
        if offset == CCR or offset == MPU_TYPE:
            return  # read-only
        if offset == SHCSR:
            if value & SHCSR_SVCALLPENDED:
                core.pending_svcall = True
                core.interrupts_updated = True
            else:
                core.pending_svcall = False
            return
        if offset == MPU_CTRL:
            self.mpu_ctrl = value & MPU_CTRL_MASK
            return
        if offset == MPU_RNR:
            self.mpu_rnr = value & MPU_RNR_MASK
            return
        if offset == MPU_RBAR:
            if value & MPU_RBAR_VALID:
                self.mpu_rnr = value & MPU_RNR_MASK
            if self.mpu_rnr < MPU_REGIONS:
                self.mpu_rbar[self.mpu_rnr] = value & MPU_RBAR_ADDR_MASK
            return
        if offset == MPU_RASR:
            if self.mpu_rnr < MPU_REGIONS:
                self.mpu_rasr[self.mpu_rnr] = value & MPU_RASR_MASK
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
            core.shpr2 = value & SHPR2_MASK
            return
        if offset == SHPR3:
            core.shpr3 = value & SHPR3_MASK
            return

        # SysTick
        if offset == SYST_CSR:
            self.systick_clk_source = bool(value & (1 << 2))
            self.systick_int_enable = bool(value & (1 << 1))
            self.systick_enable = bool(value & (1 << 0))
            self._retune_systick()  # CLKSOURCE 0 is the reference clock (the watchdog's tick), 1 the processor clock (pico-sdk m0plus.h, SYST_CSR.CLKSOURCE)
            return
        if offset == SYST_CVR:
            # "Writing to it with any value clears the register to 0. Clearing this register also clears the COUNTFLAG bit" (RP2040 datasheet, SYST_CVR)
            self.systick_timer.set(0)
            self.systick_count_flag = False
            return
        if offset == SYST_RVR:
            # RELOAD is bits 23:0 (pico-sdk hardware/regs/m0plus.h, M0PLUS_SYST_RVR); the rest does not exist
            self.systick_reload = value & 0xFFFFFF
            # RP2040 datasheet, SYST_RVR: RELOAD is the "value to load into the SysTick Current Value Register when the counter reaches 0"; "To generate a multi-shot timer with
            # a period of N processor clock cycles, use a RELOAD value of N-1"; "a start value of 0 is possible, but has no effect because the SysTick interrupt and COUNTFLAG are
            # activated when counting from 1 to 0". So: a cycle RELOAD, RELOAD-1 ... 0, RELOAD ... - a TOP of RELOAD on the timer, a period of RELOAD + 1 ticks - and a RELOAD of
            # 0 never fires. A write of CVR clears the counter to 0 and the first period after it is modelled the same (the counter sits at 0 and the next tick loads RELOAD):
            # that last step is this model's, the datasheet defers to the ARMv6-M ARM, which could not be read (docs/records/0096, the PPB fixes).
            self.systick_timer.top = self.systick_reload
            self.systick_alarm.enable = self.systick_reload != 0
            return

        super().write_uint32(offset, value)
