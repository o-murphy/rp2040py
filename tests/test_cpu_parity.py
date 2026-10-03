"""The native Cortex-M0+ core (C++ `Cpu`, docs/records/0096-cpp-mcu-core.md Phase 2 step 4b) against the pure-Python one.

Single-step differential: every trial draws a complete random machine state (registers, flags, stack selection, mode,
pending/enabled interrupts and priorities, vector table base, SRAM contents) and a random instruction - uniformly over the 16-bit
Thumb space, so undefined encodings and the wide ones (BL, MRS, MSR, DMB/DSB/ISB, UDF.W) come up as often as their share of
the opcode space - installs it identically on both cores, executes ONE instruction and compares everything observable: the
returned cycle count or the exception, every register, flag and exception-model field, SRAM, the messages logged and the
`on_break` calls. A parity test cannot be a replay of firmware here: firmware only visits the states a program reaches, this
visits the corners (shift by 0/32, carry-in at 0xFFFFFFFF, unaligned and unmapped accesses, exception entry from every
mode and stack, EXC_RETURN values, ...).
"""

import random

import pytest
from utils.chip_pair import Native, PurePython, make_chip

SRAM = 0x20000000
CODE = SRAM + 0x800  # where the instruction under test lives
DATA = SRAM + 0x1000  # the first 2 KiB above it are the memory the loads/stores should usually hit
SRAM_WORDS = 0x600  # words of SRAM given random contents (everything the trial can touch)
INTERESTING = [
    0, 1, 2, 3, 4, 7, 8, 0x1F, 0x20, 0x21, 0x7F, 0x80, 0xFF, 0x100, 0xFFFF, 0x10000, 0x7FFFFFFF, 0x80000000, 0x80000001,
    0xFFFFFFFE, 0xFFFFFFFF, 0xFFFFFFF0, 0xFFFFFFF1, 0xFFFFFFF9, 0xFFFFFFFD, 0x0FFFFFFF, 0xF0000000,
]  # fmt: skip

STATE_FIELDS = [
    "banked_sp", "cycles", "event_registered", "waiting", "n", "c", "z", "v", "break_rewind", "pm", "sp_sel", "n_priv",
    "current_mode", "ipsr", "interrupt_nmi_mask", "pending_interrupts", "enabled_interrupts", "pending_nmi",
    "pending_pend_sv", "pending_svcall", "pending_systick", "interrupts_updated", "vtor", "shpr2", "shpr3",
]  # fmt: skip


class Recorder:
    def __init__(self):
        self.messages = []

    def __getattr__(self, name):
        if name in ("debug", "info", "warning", "error"):
            return lambda component, message: self.messages.append((name, component, message))
        raise AttributeError(name)


class Side:
    """One implementation: a chip with its core, SRAM, and everything the core reported."""

    def __init__(self, cls):
        self.chip = make_chip(cls)
        self.chip.logger = Recorder()
        self.breaks = []
        self.chip.on_break = self.breaks.append
        self.core = self.chip.core

    def load(self, state):
        core = self.core
        for i, value in enumerate(state["registers"]):
            core.registers[i] = value
        for name in STATE_FIELDS:
            if name != "cycles":
                setattr(core, name, state[name])
        core.cycles = 0
        for i, value in enumerate(state["priorities"]):
            core.interrupt_priorities[i] = value
        sram = self.chip.sram
        sram[: len(state["sram"])] = state["sram"]
        self.chip.write_uint16(CODE, state["opcode"])
        self.chip.write_uint16(CODE + 2, state["opcode2"])
        self.chip.logger.messages.clear()
        self.breaks.clear()

    def step(self):
        try:
            return ("ok", self.core.execute_instruction())
        except Exception as error:  # noqa: BLE001 - the same failure on both sides is parity too
            return ("raised", type(error).__name__)

    def observe(self):
        core = self.core
        return {
            "registers": [core.registers[i] for i in range(16)],
            "priorities": [core.interrupt_priorities[i] for i in range(4)],
            **{name: getattr(core, name) for name in STATE_FIELDS},
            "sram": bytes(self.chip.sram[: SRAM_WORDS * 4]),
            "log": list(self.chip.logger.messages),
            "breaks": list(self.breaks),
        }


def _value(rng):
    roll = rng.random()
    if roll < 0.45:
        return DATA + rng.randrange(0, 0x400) * (1 if rng.random() < 0.3 else 4)  # in SRAM, sometimes unaligned
    if roll < 0.75:
        return rng.choice(INTERESTING)
    if roll < 0.78:
        return 0x10000000 + rng.randrange(0, 0x100000)  # flash
    if roll < 0.82:
        return 0xE000E000 + rng.choice([0x100, 0x180, 0x200, 0x280, 0x400, 0xD04, 0xD08, 0x10, 0x14, 0x18])  # the PPB
    if roll < 0.87:
        return 0x60000000 + rng.randrange(0, 64)  # unmapped
    return rng.getrandbits(32)


def _opcode(rng):
    if rng.random() < 0.25:  # the wide encodings and their neighbours get a fair share
        first = rng.choice([0xF000, 0xF3BF, 0xF3EF, 0xF380, 0xF7F0, 0xF7FF, 0xF3EF, 0xE800])
        first |= rng.getrandbits(4) if first in (0xF000, 0xF7F0) else rng.choice([0, 0, 0xF])
        second = rng.choice(
            [
                0xF800,
                0xD000,
                0x8F50,
                0x8F40,
                0x8F60,
                0x8000 | rng.getrandbits(8),
                0x8800 | rng.getrandbits(8),
                0xA000 | rng.getrandbits(12),
            ]
        )
        return first & 0xFFFF, (second | (rng.getrandbits(16) if rng.random() < 0.3 else 0)) & 0xFFFF
    roll = rng.random()
    if roll < 0.3:  # the data-processing group (0x4000-0x43FF) is 1.6% of the space but where the flag corners are
        return 0x4000 | rng.getrandbits(10), rng.getrandbits(16)
    if roll < 0.4:  # shifts, add/sub, move/compare immediate
        return rng.getrandbits(13), rng.getrandbits(16)
    return rng.getrandbits(16), rng.getrandbits(16)


def _state(rng):
    registers = [_value(rng) for _ in range(16)]
    registers[13] = (DATA + rng.randrange(0x40, 0x3C0) * 4) if rng.random() < 0.8 else rng.choice(INTERESTING) & ~3
    registers[15] = CODE if rng.random() < 0.95 else CODE | 1
    opcode, opcode2 = _opcode(rng)
    handler = rng.random() < 0.3
    state = {
        "registers": registers,
        "opcode": opcode,
        "opcode2": opcode2,
        "priorities": [rng.getrandbits(32) for _ in range(4)] if rng.random() < 0.6 else [0xFFFFFFFF, 0, 0, 0],
        "sram": rng.randbytes(SRAM_WORDS * 4),
        "banked_sp": (DATA + rng.randrange(0x40, 0x3C0) * 4) if rng.random() < 0.8 else rng.getrandbits(32) & ~3,
        "cycles": 0,
        "event_registered": rng.random() < 0.3,
        "waiting": False,  # a waiting core is the batch loop's business, not execute_instruction's
        "n": rng.random() < 0.5, "c": rng.random() < 0.5, "z": rng.random() < 0.5, "v": rng.random() < 0.5,
        "break_rewind": 0,
        "pm": rng.random() < 0.3,
        "sp_sel": rng.randrange(2),
        "n_priv": rng.random() < 0.3,
        "current_mode": 1 if handler else 0,
        "ipsr": rng.choice([0, 0, 15, 16, 17, 25, 63, rng.randrange(64)]) if handler else rng.choice([0, 0, 5, rng.randrange(64)]),
        "interrupt_nmi_mask": rng.getrandbits(32),
        "pending_interrupts": rng.getrandbits(26) if rng.random() < 0.4 else 0,
        "enabled_interrupts": rng.getrandbits(32) if rng.random() < 0.7 else 0xFFFFFFFF,
        "pending_nmi": rng.random() < 0.03,
        "pending_pend_sv": rng.random() < 0.1,
        "pending_svcall": rng.random() < 0.1,
        "pending_systick": rng.random() < 0.1,
        "interrupts_updated": rng.random() < 0.4,
        "vtor": SRAM + rng.randrange(0, 0x100) * 4 if rng.random() < 0.8 else rng.choice(INTERESTING),
        "shpr2": rng.getrandbits(32),
        "shpr3": rng.getrandbits(32),
    }  # fmt: skip
    return _align_halfword_access(state)


def _align_halfword_access(state):
    """An unaligned halfword access faults on silicon and the emulator has no defined answer for it (the pure and the
    native bus differ on an odd address - see test_memory_map_parity): make the trial's address even instead of comparing it."""
    registers, opcode = state["registers"], state["opcode"]
    if opcode >> 9 in (0b0101001, 0b0101101, 0b0101111):  # STRH / LDRH / LDRSH (register)
        rm, rn = (opcode >> 6) & 7, (opcode >> 3) & 7
        if (registers[rm] + registers[rn]) & 1:
            registers[rn] ^= 1
    elif opcode >> 11 in (0b10000, 0b10001):  # STRH / LDRH (immediate): the offset is even, so the base has to be
        registers[(opcode >> 3) & 7] &= ~1
    return state


@pytest.mark.parametrize("seed", range(24))
def test_the_native_core_matches_the_pure_python_core_one_instruction_at_a_time(seed):
    rng = random.Random(seed)
    pure, native = Side(PurePython), Side(Native)

    for trial in range(1500):
        state = _state(rng)
        pure.load(state)
        native.load(state)
        expected, got = pure.step(), native.step()
        label = f"trial {trial} opcode {state['opcode']:#06x} {state['opcode2']:#06x}"
        assert got == expected, f"{label}: result native {got!r}, pure {expected!r}"
        observed_native, observed_pure = native.observe(), pure.observe()
        for name, value in observed_pure.items():
            assert observed_native[name] == value, f"{label}: {name}: native {observed_native[name]!r}, pure {value!r}"
