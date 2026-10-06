"""A lockstep differential oracle for the PPB (SysTick, NVIC, the SCB registers): the same generated stimulus drives two chips and everything observable is compared after every step.

docs/records/0096-cpp-mcu-core.md, the PPB design note. The method is the PWM's, the ADC's and the others' (tests/utils/*_diff.py): one chip whose PPB is the *pure-Python* reference
(``peripherals/_ppb.py``, built explicitly because the facade would hand out the native one) and one whose PPB is whatever the facade gives (today the same class, later the C++ one),
fed one stream of operations:

* writes and reads of **every register offset** of the block (CPUID, ICSR, VTOR, SHPR2/3, SYST_CSR/RVR/CVR/CALIB, NVIC ISER/ICER/ISPR/ICPR and the eight IPR words), unimplemented
  offsets included (so the warnings are compared), and offsets inside the 4 KiB window that are not registers;
* the chip's **interrupt lines** (``set_interrupt``), which is what makes NVIC bits pending from outside;
* **simulated time** advanced by ``clock.tick()`` - SysTick's alarm fires inside it - and the chip's **clock retuned** (``systick_timer.frequency``, which is what ``update_clocks`` does);
* ``reset()``, and the core taking a pending exception (``core.check_for_interrupts()``), so ICSR's VECTACTIVE/VECTPENDING and the "pending" flags move for real.

After **each** step: the register file read through the bus (SYST_CSR excepted - reading it clears COUNTFLAG, so it is only ever read by an operation), SysTick's private state (the
flags, the reload, the timer's mode / frequency / prescaler / counter and the alarm's target), the core's interrupt state (pending, enabled, the four priority words, SHPR2/3, VTOR,
IPSR, the vector pending), whether an alarm is scheduled and when (seen through the clock), and the ordered, timestamped log of the warnings; an exception on one side and not on the other
is a difference like any other.

Logic mutants of the reference are made by changing its *source text* (``MUTATIONS``) and loading the result as a module of its own, so a mutant is exactly the reference with one
expression different.
"""

import gc
import inspect
import random
import types
from collections.abc import Callable
from typing import Any

from rp2040py.peripherals import _ppb as P
from rp2040py.rp2040 import RP2040
from utils.is32bit import IS32BIT

PPB_BASE = 0xE000E000
IRQ_COUNT = 32

NVIC_WORDS = (P.NVIC_ISER, P.NVIC_ICER, P.NVIC_ISPR, P.NVIC_ICPR)
REGISTERS = (
    P.CPUID,
    P.ICSR,
    P.VTOR,
    P.SHPR2,
    P.SHPR3,
    P.SYST_CSR,
    P.SYST_RVR,
    P.SYST_CVR,
    P.SYST_CALIB,
    *NVIC_WORDS,
    *P.NVIC_IPR_REGS,
)
# what the snapshot reads: everything but SYST_CSR (a read of it clears COUNTFLAG)
SNAPSHOT_REGISTERS = tuple(offset for offset in REGISTERS if offset != P.SYST_CSR)
UNIMPLEMENTED = (
    0x000,
    0x004,
    0x008,
    0x00C,
    0x020,
    0xD0C,
    0xD10,
    0xD14,
    0xD24,
    0xD2C,
    0xE00,
    0x104,
    0x300,
    0x420,
    0x500,
    0xFFC,
)
TICKS = (0, 1, 7, 8, 9, 50, 300, 1000, 5000, 20_000, 200_000)
BIG_TICKS = (5_000_000, 200_000_000)
RELOADS = (0, 1, 2, 5, 10, 100, 1000, 0xFFFFFF, 0x1000000, 0xFFFFFFFF)
# (no 0.0: a chip never runs at clk_sys == 0 - `update_clocks` ignores it - and the reference raises ZeroDivisionError when it schedules an alarm on such a timer, where the C++ computes an infinite delay)
FREQUENCIES = (125e6, 48e6, 133e6, 1e6, 12e6, 250e6)


class Rig:
    """One chip plus the timestamped log of everything that left its PPB."""

    def __init__(self, kind: str, factory: "Callable[..., Any] | None" = None) -> None:
        self.kind = kind
        self.chip = RP2040()
        self.log: list[tuple] = []
        # the chip's own PPB starts at its reset values (SysTick stopped); a replaced one is reset so that no alarm is left behind
        self.chip.ppb.reset()
        if kind == "pure":
            self._replace_the_ppb(P.RPPPB)
        elif kind == "mutant":
            assert factory is not None
            self._replace_the_ppb(factory)
        self._tap()

    def _replace_the_ppb(self, factory: Callable[..., Any]) -> None:
        chip = self.chip
        new = factory(chip, "PPB")
        chip.ppb = new
        new.reset()

    def _tap(self) -> None:
        chip, log = self.chip, self.log
        for method in ("warning", "error", "info", "debug"):
            setattr(
                chip.logger,
                method,
                lambda name, message, _m=method: log.append(("log", chip.clock.nanos, _m, str(name), str(message))),
            )

    # --- the operations --------------------------------------------------------------------------------------------------

    def apply(self, op: tuple) -> Any:
        kind = op[0]
        chip = self.chip
        if kind == "write":
            chip.write_uint32(PPB_BASE + op[1], op[2] & 0xFFFFFFFF)
        elif kind == "read":
            return int(chip.read_uint32(PPB_BASE + op[1]))
        elif kind == "tick":
            chip.clock.tick(op[1])
        elif kind == "irq":
            chip.set_interrupt(op[1], bool(op[2]))
        elif kind == "freq":  # what update_clocks() does to SysTick when clk_sys changes
            chip.ppb.systick_timer.frequency = op[1]
        elif kind == "lines_off":  # every interrupt line low: nothing hardware is pending
            for line in range(IRQ_COUNT):
                chip.set_interrupt(line, False)
        elif kind == "service":  # the core takes a pending exception (or not)
            return bool(chip.core.check_for_interrupts())
        elif kind == "reset":
            chip.ppb.reset()
        else:
            raise ValueError(f"unknown op {op!r}")
        return None

    # --- the observation -------------------------------------------------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        chip, ppb, core = self.chip, self.chip.ppb, self.chip.core
        timer = ppb.systick_timer
        return {
            "systick": (
                bool(ppb.systick_count_flag),
                bool(ppb.systick_clk_source),
                bool(ppb.systick_int_enable),
                int(ppb.systick_reload),
                bool(timer.enable),
                int(timer.mode),
                float(timer.frequency),
                float(timer.prescaler),
                int(timer.top),
                int(timer.raw_counter),
                int(timer.counter),
                int(ppb.systick_alarm.target),
                bool(ppb.systick_alarm.enable),
            ),
            "regs": tuple(int(chip.read_uint32(PPB_BASE + offset)) for offset in SNAPSHOT_REGISTERS),
            "core": (
                int(core.pending_interrupts),
                int(core.enabled_interrupts),
                tuple(int(word) for word in core.interrupt_priorities),
                bool(core.pending_nmi),
                bool(core.pending_pend_sv),
                bool(core.pending_svcall),
                bool(core.pending_systick),
                bool(core.interrupts_updated),
                int(core.vtor),
                int(core.shpr2),
                int(core.shpr3),
                int(core.ipsr),
                int(core.vect_pending),
            ),
            "alarms": (
                bool(chip.clock.has_scheduled_alarm),
                float(chip.clock.nanos_to_next_alarm),
            ),  # the chip has no other alarm in this rig
            "time": float(chip.clock.nanos),
        }


# --- logic mutants of the reference: what the oracle must see ----------------------------------------------------------------

# name -> (the exact text in _ppb.py, what replaces it, how many times it occurs). The count is checked, so a refactor of the reference cannot silently turn a mutant into a no-op.
MUTATIONS: dict[str, tuple[str, str, int]] = {
    # -- SysTick
    "systick_no_count_flag": ("            self.systick_count_flag = True\n", "            pass\n", 1),
    "systick_ignores_int_enable": ("            if self.systick_int_enable:\n", "            if True:\n", 1),
    "systick_no_reload": ("            self.systick_timer.set(self.systick_reload)\n", "            pass\n", 1),
    "systick_reload_zero": ("self.systick_timer.set(self.systick_reload)", "self.systick_timer.set(0)", 1),
    "systick_top_24_bit": ("self.systick_timer.top = 0xFFFFFF", "self.systick_timer.top = 0xFFFFFFFF", 1),
    "systick_counts_up": (
        "self.systick_timer.mode = TimerMode.DECREMENT",
        "self.systick_timer.mode = TimerMode.INCREMENT",
        1,
    ),
    "systick_target_one": ("self.systick_alarm.target = 0", "self.systick_alarm.target = 1", 1),
    "systick_alarm_off": ("self.systick_alarm.enable = True", "self.systick_alarm.enable = False", 1),
    "systick_pends_wrong": ("self.rp2040.core.pending_systick = True", "self.rp2040.core.pending_pend_sv = True", 1),
    "systick_no_interrupts_updated": (
        "                self.rp2040.core.interrupts_updated = True\n            self.systick_timer.set",
        "            self.systick_timer.set",
        1,
    ),
    "reset_reload_zero": ("self.write_uint32(SYST_RVR, 0xFFFFFF)", "self.write_uint32(SYST_RVR, 0)", 1),
    "reset_counter_zero": ("self.systick_timer.set(0xFFFFFF)", "self.systick_timer.set(0)", 1),
    "reset_csr_enabled": ("self.write_uint32(SYST_CSR, 0)", "self.write_uint32(SYST_CSR, 1)", 1),
    "csr_read_keeps_count_flag": (
        "            self.systick_count_flag = False\n            return",
        "            return",
        1,
    ),
    "csr_count_flag_bit": ("count_flag_value = (1 << 16) if", "count_flag_value = (1 << 15) if", 1),
    "csr_clk_source_bit": ("clk_source_value = (1 << 2) if", "clk_source_value = (1 << 3) if", 1),
    "csr_tickint_bit": ("tick_int_value = (1 << 1) if", "tick_int_value = (1 << 2) if", 1),
    "csr_enable_reads_zero": (
        "enable_flag_value = (1 << 0) if self.systick_timer.enable else 0",
        "enable_flag_value = 0",
        1,
    ),
    "csr_write_clk_source_bit": (
        "self.systick_clk_source = bool(value & (1 << 2))",
        "self.systick_clk_source = bool(value & (1 << 3))",
        1,
    ),
    "csr_write_int_enable_bit": (
        "self.systick_int_enable = bool(value & (1 << 1))",
        "self.systick_int_enable = bool(value & (1 << 2))",
        1,
    ),
    "csr_write_enable_ignored": (
        "            self.systick_timer.enable = bool(value & (1 << 0))\n",
        "            pass\n",
        1,
    ),
    "cvr_write_keeps_counter": ("            self.systick_timer.set(0)\n            return", "            return", 1),
    "cvr_read_16_bit": ("return self.systick_timer.counter\n", "return self.systick_timer.counter & 0xFFFF\n", 1),
    "rvr_unmasked_never": (
        "            self.systick_reload = value\n",
        "            self.systick_reload = value & 0xFFFFFF\n",
        1,
    ),
    "calib_value": ("return 0x0000270F", "return 0x0000270E", 1),
    "cpuid_value": ("return 0x410CC601", "return 0x410CC600", 1),
    # -- ICSR
    "icsr_nmi_bit": ("(NMIPENDSET if core.pending_nmi else 0)", "(PENDSVSET if core.pending_nmi else 0)", 1),
    "icsr_pendsv_reads_systick": (
        "(PENDSVSET if core.pending_pend_sv else 0)",
        "(PENDSVSET if core.pending_systick else 0)",
        1,
    ),
    "icsr_pendst_reads_pendsv": (
        "(PENDSTSET if core.pending_systick else 0)",
        "(PENDSTSET if core.pending_pend_sv else 0)",
        1,
    ),
    "icsr_isrpending_no_pendsv": ("or core.pending_pend_sv or core.pending_systick", "or core.pending_systick", 1),
    "icsr_vectpending_shift": ("(vect_pending << VECTPENDING_SHIFT)", "(vect_pending << (VECTPENDING_SHIFT + 1))", 1),
    "icsr_write_nmi_no_update": (
        "                core.pending_nmi = True\n                core.interrupts_updated = True\n",
        "                core.pending_nmi = True\n",
        1,
    ),
    "icsr_write_pendsv_clear_wins": (
        "            if value & PENDSVSET:\n                core.pending_pend_sv = True\n                core.interrupts_updated = True\n            if value & PENDSVCLR:\n                core.pending_pend_sv = False\n",
        "            if value & PENDSVCLR:\n                core.pending_pend_sv = False\n            if value & PENDSVSET:\n                core.pending_pend_sv = True\n                core.interrupts_updated = True\n",
        1,
    ),
    "icsr_write_pendst_no_update": (
        "                core.pending_systick = True\n                core.interrupts_updated = True\n            if value & PENDSTCLR:",
        "                core.pending_systick = True\n            if value & PENDSTCLR:",
        1,
    ),
    "icsr_write_pendstclr_ignored": (
        "            if value & PENDSTCLR:\n                core.pending_systick = False\n",
        "",
        1,
    ),
    "icsr_write_pendsvclr_sets": (
        "            if value & PENDSVCLR:\n                core.pending_pend_sv = False\n",
        "            if value & PENDSVCLR:\n                core.pending_pend_sv = True\n",
        1,
    ),
    # -- VTOR / SHPR
    "vtor_not_written": ("            core.vtor = value\n", "            pass\n", 1),
    "shpr2_writes_shpr3": ("            core.shpr2 = value\n", "            core.shpr3 = value\n", 1),
    "shpr3_reads_shpr2": ("            return core.shpr3\n", "            return core.shpr2\n", 1),
    # -- NVIC
    "ispr_no_update": (
        "            core.pending_interrupts |= value\n            core.interrupts_updated = True\n",
        "            core.pending_interrupts |= value\n",
        1,
    ),
    "icpr_clears_hardware_lines": (
        "core.pending_interrupts &= ~value | hardware_interrupt_mask",
        "core.pending_interrupts &= ~value",
        1,
    ),
    "icpr_clears_everything": (
        "core.pending_interrupts &= ~value | hardware_interrupt_mask",
        "core.pending_interrupts = 0",
        1,
    ),
    "iser_overwrites": ("core.enabled_interrupts |= value\n", "core.enabled_interrupts = value\n", 1),
    "iser_no_update": (
        "            core.enabled_interrupts |= value\n            core.interrupts_updated = True\n",
        "            core.enabled_interrupts |= value\n",
        1,
    ),
    "icer_toggles": ("core.enabled_interrupts &= ~value", "core.enabled_interrupts ^= value", 1),
    "icer_no_mask": ("core.enabled_interrupts &= ~value", "core.enabled_interrupts = 0", 1),
    "ispr_reads_enabled": (
        "        if offset == NVIC_ISPR:\n            return u32(core.pending_interrupts)",
        "        if offset == NVIC_ISPR:\n            return u32(core.enabled_interrupts)",
        1,
    ),
    "icpr_reads_enabled": (
        "        if offset == NVIC_ICPR:\n            return u32(core.pending_interrupts)",
        "        if offset == NVIC_ICPR:\n            return u32(core.enabled_interrupts)",
        1,
    ),
    "iser_reads_pending": (
        "        if offset == NVIC_ISER:\n            return u32(core.enabled_interrupts)",
        "        if offset == NVIC_ISER:\n            return u32(core.pending_interrupts)",
        1,
    ),
    "icer_reads_zero": (
        "        if offset == NVIC_ICER:\n            return u32(core.enabled_interrupts)",
        "        if offset == NVIC_ICER:\n            return 0",
        1,
    ),
    "ipr_read_shift": ("result |= priority << (8 * byte_index + 6)", "result |= priority << (8 * byte_index + 5)", 1),
    "ipr_read_index": (
        "interrupt_number = reg_index * 4 + byte_index\n                for priority in range(len(core.interrupt_priorities)):\n                    if",
        "interrupt_number = reg_index * 4 + byte_index + 1\n                for priority in range(len(core.interrupt_priorities)):\n                    if",
        1,
    ),
    "ipr_write_shift": (
        "new_priority = (value >> (8 * byte_index + 6)) & 0x3",
        "new_priority = (value >> (8 * byte_index + 5)) & 0x3",
        1,
    ),
    "ipr_write_mask": (
        "new_priority = (value >> (8 * byte_index + 6)) & 0x3",
        "new_priority = (value >> (8 * byte_index + 6)) & 0x1",
        1,
    ),
    "ipr_write_no_clear": (
        "                    core.interrupt_priorities[priority] &= ~(1 << interrupt_number)\n",
        "                    pass\n",
        1,
    ),
    "ipr_write_no_update": (
        "                core.interrupt_priorities[new_priority] |= 1 << interrupt_number\n            core.interrupts_updated = True\n",
        "                core.interrupt_priorities[new_priority] |= 1 << interrupt_number\n",
        1,
    ),
    "ipr_write_register_index": (
        "            reg_index = (offset - NVIC_IPR0) >> 2\n            for byte_index in range(4):\n                interrupt_number = reg_index * 4 + byte_index\n                new_priority",
        "            reg_index = (offset - NVIC_IPR0) >> 3\n            for byte_index in range(4):\n                interrupt_number = reg_index * 4 + byte_index\n                new_priority",
        1,
    ),
}
MUTANTS = tuple(MUTATIONS)


def _mutant_module(name: str) -> types.ModuleType:
    old, new, count = MUTATIONS[name]
    source = inspect.getsource(P)
    assert source.count(old) == count, f"{name}: {old!r} occurs {source.count(old)} times in _ppb.py, expected {count}"
    module = types.ModuleType(f"rp2040py.peripherals._ppb_mutant_{name}")
    exec(compile(source.replace(old, new), f"<_ppb mutant {name}>", "exec"), module.__dict__)  # noqa: S102 - the point of the exercise
    return module


def mutant_rig(name: str) -> Rig:
    """A rig whose PPB is the pure-Python one with one expression changed, to prove the comparison sees a change of *logic*."""
    return Rig("mutant", _mutant_module(name).RPPPB)


# --- the stimulus ----------------------------------------------------------------------------------------------------------


def _systick_scenario(r: random.Random) -> list[tuple]:
    """SysTick set up the way firmware does it - reload, clear the counter, enable - then time, with reads of the counter and of the status (COUNTFLAG)."""
    ops: list[tuple] = []
    if r.random() < 0.3:
        ops.append(("freq", r.choice(FREQUENCIES)))
    ops.append(("write", P.SYST_RVR, r.choice(RELOADS) if r.random() < 0.9 else r.getrandbits(32)))
    ops.append(("write", P.SYST_CVR, r.getrandbits(32)))
    csr = r.choice((0, 1, 3, 5, 7, 2, 4, 6)) | (r.getrandbits(32) & 0xFFFFFFF8 if r.random() < 0.1 else 0)
    ops.append(("write", P.SYST_CSR, csr | (1 if r.random() < 0.8 else 0)))
    for _ in range(r.choice((2, 3, 4, 6))):
        ops.append(("tick", r.choice(TICKS) if r.random() < 0.97 else r.choice(BIG_TICKS)))
        if r.random() < 0.6:
            ops.append(("read", r.choice((P.SYST_CSR, P.SYST_CSR, P.SYST_CVR, P.SYST_RVR, P.ICSR))))
        if r.random() < 0.15:
            ops.append(("write", r.choice((P.SYST_CVR, P.SYST_RVR)), r.choice(RELOADS)))
        if r.random() < 0.1:
            ops.append(("freq", r.choice(FREQUENCIES)))
        if r.random() < 0.2:
            ops.append(("service",))
    return ops


def _systick_fires_scenario(r: random.Random) -> list[tuple]:
    """SysTick running for long enough to fire: after a write to SYST_CVR the first period is a whole 2^24 ticks (the reference's counter sits on its target), so the time is
    hundreds of milliseconds, with a reload large enough that the periods after it are not millions of alarms."""
    ops: list[tuple] = []
    ops.append(("write", P.SYST_RVR, r.choice((400_000, 1_000_000, 0xFFFFFF))))
    ops.append(("write", P.SYST_CVR, 0))
    ops.append(("write", P.SYST_CSR, r.choice((1, 3, 5, 7))))
    for _ in range(r.choice((1, 2, 3))):
        ops.append(("tick", r.choice((140_000_000, 150_000_000))))
        ops.append(("read", r.choice((P.SYST_CSR, P.SYST_CVR, P.ICSR))))
        if r.random() < 0.5:
            ops.append(("read", P.SYST_CSR))
        if r.random() < 0.3:
            ops.append(("write", P.ICSR, r.choice((1 << 25, 1 << 26, (1 << 25) | (1 << 26)))))
        if r.random() < 0.3:
            ops.append(("service",))
    return ops


def _nvic_scenario(r: random.Random) -> list[tuple]:
    """Interrupts as firmware uses them: priorities, enable, a line raised from outside or the pending bit set by software, then the core taking it."""
    ops: list[tuple] = []
    line = r.randrange(IRQ_COUNT)
    ops.append(("write", P.NVIC_IPR0 + 4 * (line // 4), r.getrandbits(32)))
    ops.append(("write", P.NVIC_ISER, (1 << line) | (r.getrandbits(32) if r.random() < 0.2 else 0)))
    if r.random() < 0.5:
        ops.append(("irq", line, True))
    else:
        ops.append(("write", P.NVIC_ISPR, 1 << line))
    ops.append(("read", P.ICSR))
    if r.random() < 0.4:  # PendSV pended with nothing else pending: ICSR.ISRPENDING must still be set
        ops.append(("lines_off",))
        ops.append(("write", P.NVIC_ICPR, 0xFFFFFFFF))
        ops.append(("write", P.ICSR, (1 << 27) | (1 << 25)))
        ops.append(("write", P.ICSR, r.choice((1 << 28, 1 << 28, (1 << 28) | (1 << 26)))))
        ops.append(("read", P.ICSR))
        ops.append(("write", P.ICSR, (1 << 27) | (1 << 25)))
        ops.append(("read", P.ICSR))
    if (
        r.random() < 0.3
    ):  # the core has consumed its "interrupts updated" flag, then software pends an exception: the flag must come back
        ops.append(("service",))
        ops.append(("write", P.ICSR, r.choice((1 << 31, 1 << 28, 1 << 26))))
        ops.append(("read", P.ICSR))
    if r.random() < 0.6:
        ops.append(("service",))
        ops.append(("read", P.ICSR))
    if r.random() < 0.4:
        ops.append(("irq", line, False))
    ops.append(("write", r.choice((P.NVIC_ICPR, P.NVIC_ICER)), 1 << line if r.random() < 0.8 else r.getrandbits(32)))
    ops.append(("read", r.choice(NVIC_WORDS)))
    return ops


def _value(r: random.Random, offset: int) -> int:
    if offset == P.ICSR:
        return r.choice(
            (
                0,
                1 << 31,
                1 << 28,
                1 << 27,
                1 << 26,
                1 << 25,
                (1 << 28) | (1 << 27),
                (1 << 26) | (1 << 25),
                r.getrandbits(32),
            )
        )
    if offset == P.SYST_CSR:
        return r.choice((0, 1, 2, 3, 4, 5, 7, r.getrandbits(32)))
    if offset == P.SYST_RVR:
        return r.choice((*RELOADS, r.getrandbits(32)))
    if offset in (P.SHPR2, P.SHPR3):
        return r.choice((0, 0x40000000, 0x80000000, 0xC0000000, 0x00C00000, r.getrandbits(32)))
    if offset in NVIC_WORDS:
        return r.choice((0, 1, 0xFFFFFFFF, 1 << r.randrange(32), r.getrandbits(32)))
    return r.getrandbits(32)


def generate(seed: int, steps: int) -> list[tuple]:
    r = random.Random(seed)
    ops: list[tuple] = []
    while len(ops) < steps:
        roll = r.random()
        if roll < 0.12:
            ops.extend(_systick_scenario(r))
        elif roll < 0.18:
            ops.extend(_systick_fires_scenario(r))
        elif roll < 0.28:
            ops.extend(_nvic_scenario(r))
        elif roll < 0.46:
            offset = r.choice(REGISTERS)
            ops.append(("write", offset, _value(r, offset)))
        elif roll < 0.50:
            ops.append(("write", r.choice(UNIMPLEMENTED), r.getrandbits(32)))
        elif roll < 0.64:
            ops.append(("read", r.choice(REGISTERS + UNIMPLEMENTED)))
        elif roll < 0.76:
            ops.append(("tick", r.choice(TICKS)))
        elif roll < 0.84:
            ops.append(("irq", r.randrange(IRQ_COUNT), r.random() < 0.5))
        elif roll < 0.88:
            ops.append(("freq", r.choice(FREQUENCIES)))
        elif roll < 0.95:
            ops.append(("service",))
        else:
            ops.append(("reset",))
    return ops[:steps]


class Divergence:
    def __init__(self, step: int, op: tuple, what: str) -> None:
        self.step, self.op, self.what = step, op, what

    def __str__(self) -> str:
        return f"step {self.step} {self.op}: {self.what}"

    def __repr__(self) -> str:
        return f"Divergence({self})"


def _step(rig: Rig, op: tuple) -> tuple:
    """(result, exception name) of one operation: an exception on one side and not on the other is a difference like any other."""
    try:
        return rig.apply(op), None
    except Exception as error:  # noqa: BLE001 - what is compared is *that* it raised and what, not how it is handled
        return None, f"{type(error).__name__}: {error}"


def _snapshot(rig: Rig) -> dict[str, Any]:
    """The rig's snapshot, or - when reading it raises, which a broken block can do - that fact as the snapshot (so it is a difference like any other)."""
    try:
        return rig.snapshot()
    except Exception as error:  # noqa: BLE001
        return {"raised": f"{type(error).__name__}: {error}"}


def diff_snapshots(a: dict[str, Any], b: dict[str, Any]) -> str:
    parts = []
    for key, value_a in a.items():
        value_b = b.get(key, "<missing>")
        if value_a == value_b:
            continue
        if isinstance(value_a, tuple) and isinstance(value_b, tuple) and len(value_a) == len(value_b):
            where = [i for i, (x, y) in enumerate(zip(value_a, value_b, strict=True)) if x != y]
            parts.append(
                f"{key}[{where[:6]}] {[str(value_a[i])[:70] for i in where[:6]]} vs {[str(value_b[i])[:70] for i in where[:6]]}"
            )
        else:
            parts.append(f"{key}: {str(value_a)[:80]} vs {str(value_b)[:80]}")
    return "; ".join(parts)


def run_pair(
    ops: list[tuple],
    *,
    reference: Callable[[], Rig] = lambda: Rig("pure"),
    candidate: Callable[[], Rig] = lambda: Rig("default"),
    perturb: "Callable[[Rig, int], None] | None" = None,
) -> "Divergence | None":
    """Runs `ops` on both rigs and returns the first difference, or None. `perturb(candidate, step)` lets a test damage the candidate to prove the oracle sees it."""
    a, b = reference(), candidate()
    try:
        return _lockstep(a, b, ops, perturb)
    finally:
        del a, b
        if IS32BIT:
            gc.collect()  # two chips of 16 MB each per run, hundreds of runs per test file: do not leave them to the automatic collector (a 32-bit build runs out of address space)


def _lockstep(a: Rig, b: Rig, ops: list[tuple], perturb: "Callable[[Rig, int], None] | None") -> "Divergence | None":
    log_a = log_b = 0
    for step, op in enumerate(ops):
        result_a, error_a = _step(a, op)
        result_b, error_b = _step(b, op)
        if perturb is not None:
            perturb(b, step)
        if (result_a, error_a) != (result_b, error_b):
            return Divergence(step, op, f"result {result_a!r}/{error_a} vs {result_b!r}/{error_b}")
        new_a, new_b = a.log[log_a:], b.log[log_b:]
        log_a, log_b = len(a.log), len(b.log)
        if new_a != new_b:
            return Divergence(step, op, f"log: {new_a[:4]} vs {new_b[:4]}")
        snapshot_a, snapshot_b = _snapshot(a), _snapshot(b)
        if snapshot_a != snapshot_b:
            return Divergence(step, op, diff_snapshots(snapshot_a, snapshot_b))
    return None


def coverage(ops: list[tuple]) -> dict[str, int]:
    """What the run exercised, measured on the reference: the counts a test asserts so that a green run means something."""
    rig = Rig("pure")
    counts: dict[str, int] = {}

    def count(name: str) -> None:
        counts[name] = counts.get(name, 0) + 1

    ppb, core = rig.chip.ppb, rig.chip.core
    for op in ops:
        before = len(rig.log)
        was_flag, was_pending = ppb.systick_count_flag, core.pending_systick
        if op[0] == "tick":
            count("tick")
            if ppb.systick_timer.enable:
                count("tick.running")
        if op[0] == "reset":
            count("reset.running" if ppb.systick_timer.enable else "reset")
        _, error = _step(rig, op)
        if error is not None:
            count(f"raised.{error.split(':')[0]}")
        if not was_flag and ppb.systick_count_flag:
            count("systick.fired")
        if not was_pending and core.pending_systick:
            count("systick.pended")
        if op[0] == "read" and op[1] == P.SYST_CSR and was_flag:
            count("csr.read_clears_flag")
        if op[0] == "service" and rig.chip.core.ipsr:
            count("service.taken")
        if rig.chip.clock.has_scheduled_alarm:
            count("alarm.scheduled")
        if core.pending_interrupts:
            count("nvic.pending")
        if core.enabled_interrupts:
            count("nvic.enabled")
        if core.pending_interrupts & core.enabled_interrupts:
            count("nvic.deliverable")
        if any(core.interrupt_priorities[i] for i in (1, 2, 3)):
            count("nvic.priorities_set")
        if core.pending_pend_sv or core.pending_nmi:
            count("scb.pending")
        for event in rig.log[before:]:
            count(f"log.{event[0]}")
    return counts
