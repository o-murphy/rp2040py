"""A lockstep differential oracle for three small register blocks, PSM, XOSC and RESETS (docs/records/0096-cpp-mcu-core.md, "The small blocks"): the same generated stimulus drives two chips and everything
observable is compared after every step. The sibling of ``small_blocks_diff`` (BUSCTRL, SYSCFG, VREG): same method, own blocks.

One chip's block is the *pure-Python* reference (``peripherals/_psm.py`` / ``_xosc.py`` / ``_reset.py``, built explicitly because the facade would hand out the native one), the other's is whatever the
facade gives (the C++ block in a native build). The operations are reads and writes of **every register offset through the bus and its XOR/SET/CLR aliases**, of offsets that are no register (so the
warnings are compared, the ones above 0x1000 included) and, for the XOSC, ``reset()``. The XOSC's writes are drawn from the values that mean something (the ENABLE codes with and without FREQ_RANGE,
DORMANT and WAKE, the BADWRITE bit) as well as from random words. After each step the register file read through the bus, the block's attributes, ``raw_write_value`` and the ordered log of the warnings
are compared; an exception on one side and not on the other is a difference like any other.

Logic mutants of the reference (one subclass each) prove that a change of *logic* is seen, not only a perturbation of the candidate's state.
"""

import random
from collections.abc import Callable
from typing import Any

from rp2040py.peripherals import _psm as P
from rp2040py.peripherals import _reset as R
from rp2040py.peripherals import _xosc as X
from rp2040py.rp2040 import RP2040

ALIAS_BASES = (0x0000, 0x1000, 0x2000, 0x3000)  # normal, XOR, SET, CLR
BLOCKS: dict[str, dict[str, Any]] = {
    "psm": {
        "base": 0x40010000,
        "cls": P.RPPSM,
        "name": "PSM_BASE",
        "registers": (0x0, 0x4, 0x8, 0xC),
        "unimplemented": (0x10, 0x14, 0x40, 0xFFC),
        "attrs": ("wdsel", "_frce_on", "_frce_off"),
        "ops": (),
    },
    "resets": {
        "base": 0x4000C000,
        "cls": R.RPReset,
        "name": "RESETS_BASE",
        "registers": (0x0, 0x4, 0x8),
        "unimplemented": (0xC, 0x10, 0x40, 0xFFC),
        "attrs": ("wdsel", "_reset", "_wdsel"),
        "ops": (),
    },
    "xosc": {
        "base": 0x40024000,
        "cls": X.RPXOSC,
        "name": "XOSC_BASE",
        "registers": (0x00, 0x04, 0x08, 0x0C, 0x1C),
        "unimplemented": (0x10, 0x14, 0x18, 0x20, 0x40, 0xFFC),
        "attrs": ("_ctrl", "_status", "_dormant", "_startup", "_count", "_enabled", "_stable", "_is_dormant"),
        "ops": ("reset",),
    },
}
XOSC_VALUES = (
    X.CTRL_FREQ_RANGE_1_15MHZ,
    (X.CTRL_ENABLE_ENABLE << 12) | X.CTRL_FREQ_RANGE_1_15MHZ,
    (X.CTRL_ENABLE_DISABLE << 12) | X.CTRL_FREQ_RANGE_1_15MHZ,
    X.CTRL_ENABLE_ENABLE << 12,
    X.CTRL_ENABLE_DISABLE << 12,
    (0x123 << 12) | X.CTRL_FREQ_RANGE_1_15MHZ,
    0xAA1,
    X.DORMANT_VALUE,
    X.WAKE_VALUE,
    X.STATUS_BADWRITE,
    X.STARTUP_X4 | 0x3FFF,
    0xFF,
    0x100,
)


def _freeze(value: Any) -> Any:
    return tuple(value) if hasattr(value, "__iter__") else value


class Rig:
    """One chip plus the log of every warning its block produced."""

    def __init__(self, block: str, kind: str, factory: "Callable[..., Any] | None" = None) -> None:
        self.block = block
        self.spec = BLOCKS[block]
        self.chip = RP2040()
        self.log: list[tuple] = []
        if kind == "pure":
            self._replace_the_block(self.spec["cls"])
        elif kind == "mutant":
            assert factory is not None
            self._replace_the_block(factory)
        self._tap()

    def _replace_the_block(self, factory: Callable[..., Any]) -> None:
        self.chip.peripherals[self.spec["base"] >> 12] = factory(self.chip, self.spec["name"])

    def _tap(self) -> None:
        log = self.log
        for method in ("warning", "error", "info", "debug"):
            setattr(
                self.chip.logger,
                method,
                lambda name, message, _m=method: log.append(("log", _m, str(name), str(message))),
            )

    @property
    def peripheral(self) -> Any:
        return self.chip.peripherals[self.spec["base"] >> 12]

    def apply(self, op: tuple) -> Any:
        kind = op[0]
        chip, base = self.chip, self.spec["base"]
        if kind == "write":
            chip.write_uint32(base + op[3] + op[1], op[2] & 0xFFFFFFFF)
        elif kind == "read":
            return int(chip.read_uint32(base + op[2] + op[1]))
        elif kind == "reset":
            self.peripheral.reset()
        else:
            raise ValueError(f"unknown op {op!r}")
        return None

    def snapshot(self) -> dict[str, Any]:
        base, peripheral = self.spec["base"], self.peripheral
        return {
            "regs": tuple(int(self.chip.read_uint32(base + offset)) for offset in self.spec["registers"]),
            "attrs": tuple(_freeze(getattr(peripheral, name)) for name in self.spec["attrs"]),
            "raw": int(peripheral.raw_write_value),
        }


# --- logic mutants of the reference: what the oracle must see ----------------------------------------------------------------

PSM_MUTANTS = (
    "mask_16_bits",
    "mask_all_bits",
    "done_ignores_frce_on",
    "done_is_not_frce_off",
    "wdsel_not_stored",
    "frce_off_not_stored",
    "write_done_warns",
    "unimplemented_read_zero",
)
RESETS_MUTANTS = (
    "reset_mask_24_bits",
    "wdsel_mask_wrong",
    "done_constant",
    "done_is_reset",
    "done_ignores_mask",
    "write_done_warns",
    "unimplemented_read_zero",
)
XOSC_MUTANTS = (
    "ctrl_keeps_reserved",
    "freq_range_changeable",
    "freq_range_no_badwrite",
    "enable_ignored",
    "disable_keeps_stable",
    "disable_ignored",
    "enable_while_dormant",
    "invalid_enable_no_badwrite",
    "invalid_enable_enables",
    "dormant_invalid_stored",
    "dormant_invalid_no_badwrite",
    "dormant_reset_wrong",
    "wake_not_stable",
    "dormant_keeps_stable",
    "startup_reset_0",
    "startup_mask_wrong",
    "badwrite_not_cleared",
    "badwrite_set_by_write",
    "count_mask_wrong",
    "reset_keeps_enabled",
    "reset_startup_wrong",
    "reset_keeps_dormant",
    "unimplemented_read_zero",
)


def mutant_rig(block: str, name: str) -> Rig:
    """A rig whose block is the pure-Python one with one piece of its logic changed, to prove the comparison sees a change of *logic*."""
    base = BLOCKS[block]["cls"]
    registers = BLOCKS[block]["registers"]
    plain = base.__mro__[1]  # BasePeripheral: the "unimplemented" path

    class Mutant(base):  # type: ignore[valid-type, misc]
        def read_uint32(self, offset: int) -> int:
            if name == "unimplemented_read_zero" and offset not in registers:
                return 0
            if block == "psm" and offset == P.DONE:
                if name == "done_ignores_frce_on":
                    return P.PSM_BITS_MASK & ~self._frce_off
                if name == "done_is_not_frce_off":
                    return P.PSM_BITS_MASK & ~self._frce_on
            if block == "resets" and offset == R.RESET_DONE:
                if name == "done_constant":
                    return 0x1FFFFFF
                if name == "done_is_reset":
                    return self._reset
                if name == "done_ignores_mask":
                    return ~self._reset & 0xFFFFFFFF
            return super().read_uint32(offset)

        def write_uint32(self, offset: int, value: int) -> None:
            value &= 0xFFFFFFFF
            if block == "psm":
                if name == "mask_16_bits" and offset == P.FRCE_ON:
                    self._frce_on = value & 0xFFFF
                    return
                if name == "mask_all_bits" and offset == P.FRCE_OFF:
                    self._frce_off = value
                    return
                if name == "wdsel_not_stored" and offset == P.WDSEL:
                    return
                if name == "frce_off_not_stored" and offset == P.FRCE_OFF:
                    return
                if name == "write_done_warns" and offset == P.DONE:
                    plain.write_uint32(self, offset, value)
                    return
            elif block == "resets":
                if name == "reset_mask_24_bits" and offset == R.RESET:
                    self._reset = value & 0xFFFFFF
                    return
                if name == "wdsel_mask_wrong" and offset == R.WDSEL:
                    self._wdsel = value & 0xFFFFFFFF
                    return
                if name == "write_done_warns" and offset == R.RESET_DONE:
                    plain.write_uint32(self, offset, value)
                    return
            else:
                if self._write_xosc(offset, value):
                    return
            super().write_uint32(offset, value)

        def _write_xosc(self, offset: int, value: int) -> bool:
            enable = (value & X.CTRL_ENABLE_BITS) >> X.CTRL_ENABLE_LSB
            if offset == X.XOSC_CTRL:
                if name == "ctrl_keeps_reserved":
                    super().write_uint32(offset, value)
                    self._ctrl |= value & 0xFF000000
                    return True
                if name == "freq_range_changeable":
                    super().write_uint32(offset, value)
                    self._ctrl = (self._ctrl & ~0xFFF) | (value & 0xFFF)
                    return True
                if name == "freq_range_no_badwrite":
                    before = self._status
                    super().write_uint32(offset, value)
                    if enable in (0, X.CTRL_ENABLE_ENABLE, X.CTRL_ENABLE_DISABLE):
                        self._status = before
                    return True
                if name == "enable_ignored" and enable == X.CTRL_ENABLE_ENABLE:
                    return True
                if name == "disable_ignored" and enable == X.CTRL_ENABLE_DISABLE:
                    return True
                if name == "disable_keeps_stable" and enable == X.CTRL_ENABLE_DISABLE:
                    stable = self._stable
                    super().write_uint32(offset, value)
                    self._stable = stable
                    return True
                if name == "enable_while_dormant" and enable == X.CTRL_ENABLE_ENABLE and self._is_dormant:
                    self._enabled = True
                if name == "invalid_enable_no_badwrite" and enable not in (
                    0,
                    X.CTRL_ENABLE_ENABLE,
                    X.CTRL_ENABLE_DISABLE,
                ):
                    before = self._status
                    super().write_uint32(offset, value)
                    self._status = before | (self._status & ~X.STATUS_BADWRITE)
                    return True
                if name == "invalid_enable_enables" and enable not in (0, X.CTRL_ENABLE_ENABLE, X.CTRL_ENABLE_DISABLE):
                    super().write_uint32(offset, value)
                    self._enabled = self._stable = True
                    return True
            elif offset == X.XOSC_DORMANT:
                if name == "dormant_invalid_stored" and value not in (X.DORMANT_VALUE, X.WAKE_VALUE):
                    super().write_uint32(offset, value)
                    self._dormant = value
                    return True
                if name == "dormant_invalid_no_badwrite" and value not in (X.DORMANT_VALUE, X.WAKE_VALUE):
                    before = self._status
                    super().write_uint32(offset, value)
                    self._status = before
                    return True
                if name == "wake_not_stable" and value == X.WAKE_VALUE:
                    super().write_uint32(offset, value)
                    self._stable = False
                    return True
                if name == "dormant_keeps_stable" and value == X.DORMANT_VALUE:
                    stable = self._stable
                    super().write_uint32(offset, value)
                    self._stable = stable
                    return True
            elif offset == X.XOSC_STARTUP:
                if name == "startup_mask_wrong":
                    self._startup = value & (X.STARTUP_X4 | 0x1FFF)
                    return True
            elif offset == X.XOSC_STATUS:
                if name == "badwrite_not_cleared":
                    return True
                if name == "badwrite_set_by_write" and value & X.STATUS_BADWRITE:
                    self._status ^= X.STATUS_BADWRITE
                    return True
            elif offset == X.XOSC_COUNT and name == "count_mask_wrong":
                self._count = value & 0x7F
                return True
            return False

        def reset(self) -> None:
            kept_enabled, kept_dormant = (
                self._enabled if block == "xosc" else False,
                self._dormant if block == "xosc" else 0,
            )
            super().reset()
            if name == "reset_keeps_enabled":
                self._enabled = kept_enabled
            elif name == "reset_startup_wrong":
                self._startup = 0
            elif name == "reset_keeps_dormant":
                self._dormant = kept_dormant
            elif name == "startup_reset_0":
                self._startup = 0
            elif name == "dormant_reset_wrong":
                self._dormant = 0

    rig = Rig(block, "mutant", Mutant)
    if block == "xosc" and name in ("startup_reset_0", "dormant_reset_wrong"):
        # these two are reset values: the construction state is wrong too
        peripheral = rig.peripheral
        if name == "startup_reset_0":
            peripheral._startup = 0
        else:
            peripheral._dormant = 0
    return rig


# --- the stimulus ----------------------------------------------------------------------------------------------------------


def _value(r: random.Random, block: str) -> int:
    if block == "xosc" and r.random() < 0.7:
        return r.choice(XOSC_VALUES)
    return r.choice(
        (
            0,
            1,
            2,
            3,
            0x8,
            0x1F,
            0x1FFFF,
            0x20000,
            0x1000000,
            0x1FFFFFF,
            0x2000000,
            0xFFFFFFFF,
            r.getrandbits(32),
            r.getrandbits(32),
        )
    )


def generate(block: str, seed: int, steps: int) -> list[tuple]:
    r = random.Random(seed)
    spec = BLOCKS[block]
    registers, unimplemented, extra = spec["registers"], spec["unimplemented"], spec["ops"]
    ops: list[tuple] = []
    while len(ops) < steps:
        roll = r.random()
        alias = 0 if r.random() < 0.5 else r.choice(ALIAS_BASES[1:])
        if block == "xosc" and roll < 0.14:  # keep the oscillator going through its states: dormant, enabled, woken
            code = r.choice(("dormant", "dormant", "enable", "enable", "disable", "wake"))
            if code == "dormant":
                ops.append(("write", X.XOSC_DORMANT, X.DORMANT_VALUE, 0))
            elif code == "wake":
                ops.append(("write", X.XOSC_DORMANT, X.WAKE_VALUE, 0))
            else:
                value = X.CTRL_ENABLE_ENABLE if code == "enable" else X.CTRL_ENABLE_DISABLE
                ops.append(("write", X.XOSC_CTRL, (value << 12) | X.CTRL_FREQ_RANGE_1_15MHZ, 0))
        elif roll < 0.25:
            ops.append(("read", r.choice(registers), alias))
        elif roll < 0.33:
            ops.append(("read", r.choice(unimplemented), alias if r.random() < 0.5 else 0))
        elif roll < 0.80:
            ops.append(("write", r.choice(registers), _value(r, block), alias))
        elif roll < 0.90 or not extra:
            ops.append(("write", r.choice(unimplemented), _value(r, block), alias if r.random() < 0.5 else 0))
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


def run_pair(
    block: str,
    ops: list[tuple],
    *,
    reference: "Callable[[], Rig] | None" = None,
    candidate: "Callable[[], Rig] | None" = None,
    perturb: "Callable[[Rig, int], None] | None" = None,
) -> "Divergence | None":
    """Runs `ops` on both rigs and returns the first difference, or None. `perturb(candidate, step)` lets a test damage the candidate to prove the oracle sees it."""
    a = reference() if reference else Rig(block, "pure")
    b = candidate() if candidate else Rig(block, "default")
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
        snapshot_a, snapshot_b = a.snapshot(), b.snapshot()
        if snapshot_a != snapshot_b:
            return Divergence(step, op, f"{snapshot_a} vs {snapshot_b}")
    return None


def coverage(block: str, ops: list[tuple]) -> dict[str, int]:
    """What the run exercised, measured on the reference: the counts a test asserts so that a green run means something."""
    rig = Rig(block, "pure")
    counts: dict[str, int] = {}

    def count(name: str) -> None:
        counts[name] = counts.get(name, 0) + 1

    registers = BLOCKS[block]["registers"]
    for op in ops:
        before = len(rig.log)
        if op[0] == "read":
            count("read.reg" if op[1] in registers else "read.unimplemented")
            if op[2]:
                count("read.alias")
        elif op[0] == "write":
            count("write.reg" if op[1] in registers else "write.unimplemented")
            if op[3]:
                count("write.alias")
        else:
            count(op[0])
        _step(rig, op)
        if len(rig.log) > before:
            count("log.entries")
        if block == "xosc":
            peripheral = rig.peripheral
            count("state.enabled") if peripheral._enabled else None
            count("state.dormant") if peripheral._is_dormant else None
            count("state.badwrite") if peripheral._status & X.STATUS_BADWRITE else None
    return counts
