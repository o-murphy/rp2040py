"""A lockstep differential oracle for the ROSC (ring oscillator): the same generated stimulus drives two chips and everything observable is compared after every step.

docs/records/0096-cpp-mcu-core.md, docs/records/0098-datasheet-conformance-audit.md ("ROSC"). The method is the PPB's and the others' (tests/utils/*_diff.py): one chip whose ROSC is the
*pure-Python* reference (``peripherals/_rosc.py``, built explicitly because the facade would hand out the native one) and one whose ROSC is whatever the facade gives, fed one stream of
operations:

* writes and reads of **every register offset** (CTRL, FREQA, FREQB, DORMANT, DIV, PHASE, STATUS, RANDOMBIT, COUNT), through the bus **and its XOR/SET/CLR aliases**, with the values the
  datasheet's tables make interesting (the valid and invalid ENABLE / FREQ_RANGE codes, the passwords, DIV around 0xAA0 + 0..31, the dormant words), unimplemented offsets included (so the
  warnings are compared);
* **simulated time** (``clock.tick()``), which COUNT counts against, and ``reset()``;
* the chip's **clock tree**: ``rosc_freq``, ``clk_sys`` and ``clk_peri`` are compared after each step and every change of the frequency the chip holds is logged with its time;
* the **random bit source**: a deterministic generator installed as the block's ``random_bit`` hook, whose every call is logged - so *when* the block asks for a bit is compared too.

After **each** step: the register file read through the bus (RANDOMBIT excepted - it consumes the source), the block's private state, the derived frequency and the remaining count, the
chip's clocks, the ordered timestamped log; an exception on one side and not on the other is a difference like any other.

Logic mutants of the reference are made by changing its *source text* (``MUTATIONS``) and loading the result as a module of its own, so a mutant is exactly the reference with one expression
different.
"""

import gc
import inspect
import random
import types
from collections.abc import Callable
from typing import Any

from rp2040py.peripherals import _rosc as P
from rp2040py.rp2040 import RP2040
from utils.is32bit import IS32BIT

ROSC_BASE = 0x40060000
ALIASES = (0x0000, 0x1000, 0x2000, 0x3000)  # normal, XOR, SET, CLR

REGISTERS = (
    P.ROSC_CTRL,
    P.ROSC_FREQA,
    P.ROSC_FREQB,
    P.ROSC_DORMANT,
    P.ROSC_DIV,
    P.ROSC_PHASE,
    P.ROSC_STATUS,
    P.ROSC_COUNT,
)
WRITABLE = (
    P.ROSC_CTRL,
    P.ROSC_FREQA,
    P.ROSC_FREQB,
    P.ROSC_DORMANT,
    P.ROSC_DIV,
    P.ROSC_PHASE,
    P.ROSC_STATUS,
    P.ROSC_COUNT,
    P.ROSC_RANDOMBIT,
)
UNIMPLEMENTED = (0x24, 0x28, 0x40, 0x80, 0x100, 0xF00, 0xFFC)
TICKS = (0, 1, 7, 100, 1_000, 10_000, 100_000, 1_000_000, 10_000_000)
RANGES = (0xAA0, 0xFA4, 0xFA5, 0xFA7, 0xFA6)


class Rig:
    """One chip plus the timestamped log of everything that left its ROSC."""

    def __init__(self, kind: str, factory: "Callable[..., Any] | None" = None) -> None:
        self.kind = kind
        self.chip = RP2040()
        self.log: list[tuple] = []
        self._lfsr = 0xACE1
        if kind == "pure":
            self._replace_the_rosc(P.RPROSC)
        elif kind == "mutant":
            assert factory is not None
            self._replace_the_rosc(factory)
        self._tap()

    def _replace_the_rosc(self, factory: Callable[..., Any]) -> None:
        chip = self.chip
        new = factory(chip, "ROSC_BASE")
        chip.rosc = new
        chip.peripherals[ROSC_BASE >> 12] = new

    def _tap(self) -> None:
        chip, log = self.chip, self.log
        for method in ("warning", "error", "info", "debug"):
            setattr(
                chip.logger,
                method,
                lambda name, message, _m=method: log.append(("log", chip.clock.nanos, _m, str(name), str(message))),
            )

        def random_bit() -> int:
            # A 16-bit Fibonacci LFSR: deterministic, both rigs draw the same sequence if they ask the same number of times
            bit = ((self._lfsr >> 0) ^ (self._lfsr >> 2) ^ (self._lfsr >> 3) ^ (self._lfsr >> 5)) & 1
            self._lfsr = (self._lfsr >> 1) | (bit << 15)
            log.append(("bit", chip.clock.nanos, self._lfsr & 1))
            return self._lfsr  # more than one bit: the block must keep bit 0 only

        chip.rosc.random_bit = random_bit

    # --- the operations --------------------------------------------------------------------------------------------------

    def apply(self, op: tuple) -> Any:
        # The native ROSC reaches the clock tree without calling the chip's update_clocks(), so a retune is seen as the frequency the chip holds changing across the operation
        before = float(self.chip.rosc_freq)
        try:
            return self._apply(op)
        finally:
            after = float(self.chip.rosc_freq)
            if after != before:
                self.log.append(("clocks", self.chip.clock.nanos, after))

    def _apply(self, op: tuple) -> Any:
        kind = op[0]
        chip = self.chip
        if kind == "write":
            chip.write_uint32(ROSC_BASE + op[3] + op[1], op[2] & 0xFFFFFFFF)
        elif kind == "read":
            return int(chip.read_uint32(ROSC_BASE + op[2] + op[1]))
        elif kind == "tick":
            chip.clock.tick(op[1])
        elif kind == "reset":
            chip.rosc.reset()
        else:
            raise ValueError(f"unknown op {op!r}")
        return None

    # --- the observation -------------------------------------------------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        chip, rosc = self.chip, self.chip.rosc
        return {
            "regs": tuple(int(chip.read_uint32(ROSC_BASE + offset)) for offset in REGISTERS),
            "state": (tuple(sorted(rosc.snapshot().items())), int(rosc.raw_write_value)),
            "chip": (float(chip.rosc_freq), float(chip.clk_sys), float(chip.clk_peri), float(chip.clock.nanos)),
        }


# --- logic mutants of the reference: what the oracle must see ----------------------------------------------------------------

# name -> (the exact text in _rosc.py, what replaces it, how many times it occurs). The count is checked, so a refactor of the reference cannot silently turn a mutant into a no-op.
MUTATIONS: dict[str, tuple[str, str, int]] = {
    # -- CTRL
    "ctrl_reset_enable_missing": ("CTRL_RESET = (CTRL_ENABLE_ENABLE << CTRL_ENABLE_LSB) | 0xAA0", "CTRL_RESET = 0xAA0", 1),
    "ctrl_invalid_range_stored": ("                freq_range = self._ctrl & CTRL_FREQ_RANGE_BITS  # the range stays as it was\n", "", 1),
    "ctrl_invalid_range_silent": ('                self._bad_write(f"Invalid FREQ_RANGE value written: 0x{freq_range:x}")\n', "                pass\n", 1),
    "ctrl_invalid_enable_silent": ('                self._bad_write(f"Invalid ENABLE value written: 0x{enable_value:x}")\n', "                pass\n", 1),
    "ctrl_invalid_enable_disables": ("            enabled = enable_value != CTRL_ENABLE_DISABLE\n", "            enabled = enable_value == CTRL_ENABLE_ENABLE\n", 1),
    "ctrl_zero_enable_valid": ("            if enable_value not in (CTRL_ENABLE_ENABLE, CTRL_ENABLE_DISABLE):", "            if enable_value not in (CTRL_ENABLE_ENABLE, CTRL_ENABLE_DISABLE, 0):", 1),
    "ctrl_enable_swapped": ("CTRL_ENABLE_DISABLE = 0xD1E\nCTRL_ENABLE_ENABLE = 0xFAB", "CTRL_ENABLE_DISABLE = 0xFAB\nCTRL_ENABLE_ENABLE = 0xD1E", 1),
    "ctrl_range_aa0_invalid": ("    FREQ_RANGE_RESET: 1.0,\n", "", 1),
    "range_medium_ratio": ("    FREQ_RANGE_MEDIUM: 1.33,", "    FREQ_RANGE_MEDIUM: 1.5,", 1),
    "range_high_ratio": ("    FREQ_RANGE_HIGH: 2.0,", "    FREQ_RANGE_HIGH: 3.0,", 1),
    "range_toohigh_ratio": ("    FREQ_RANGE_TOOHIGH: 4.0,", "    FREQ_RANGE_TOOHIGH: 8.0,", 1),
    "range_codes_swapped": ("FREQ_RANGE_LOW, FREQ_RANGE_MEDIUM, FREQ_RANGE_HIGH, FREQ_RANGE_TOOHIGH = 0xFA4, 0xFA5, 0xFA7, 0xFA6", "FREQ_RANGE_LOW, FREQ_RANGE_MEDIUM, FREQ_RANGE_HIGH, FREQ_RANGE_TOOHIGH = 0xFA4, 0xFA5, 0xFA6, 0xFA7", 1),
    "enable_change_no_rebase": ("            if enabled != self._enabled:\n                self._rebase_count()\n                self._enabled = enabled\n", "            if enabled != self._enabled:\n                self._enabled = enabled\n", 1),
    # -- FREQA / FREQB
    "freq_passwd_ignored": ("            if (value >> 16) & 0xFFFF != FREQ_PASSWD:", "            if False:", 1),
    "freq_bad_password_keeps_drive": ("                drive = 0\n            stored", "                pass\n            stored", 1),
    "freq_bad_password_silent": ('                self._bad_write(f"Invalid FREQ password written: 0x{(value >> 16) & 0xFFFF:x}")\n', "                pass\n", 1),
    "freq_ds_mask": ("FREQ_DS_BITS = 0x7777", "FREQ_DS_BITS = 0xFFFF", 1),
    "freq_passwd_value": ("FREQ_PASSWD = 0x9696", "FREQ_PASSWD = 0x9669", 1),
    "freqa_writes_freqb": ("            if offset == ROSC_FREQA:\n                self._freqa = stored", "            if offset == ROSC_FREQB:\n                self._freqa = stored", 1),
    "freq_passwd_not_stored": ("            stored = (value & 0xFFFF0000) | drive", "            stored = drive", 1),
    # -- DORMANT
    "dormant_invalid_stored": ("                value = WAKE_VALUE\n            self._dormant = value", "                pass\n            self._dormant = value", 1),
    "dormant_invalid_silent": ('                self._bad_write(f"Invalid DORMANT value written: 0x{value:x}")\n', "                pass\n", 1),
    "dormant_reset_value": ("        self._dormant = WAKE_VALUE\n        self._div", "        self._dormant = 0\n        self._div", 1),
    # -- DIV
    "div_zero_is_one": ("    return div if div else 32", "    return div if div else 1", 1),
    "div_invalid_keeps": ("                value = DIV_INVALID  # \"any other value sets div=31\"\n", "                value = self._div\n", 1),
    "div_invalid_silent": ('                self._bad_write(f"Invalid DIV value written: 0x{value:x}")\n', "                pass\n", 1),
    "div_upper_bound": ("            if not DIV_PASS <= value <= DIV_PASS + 31:", "            if not DIV_PASS <= value <= DIV_PASS + 32:", 1),
    "div_lower_bound": ("            if not DIV_PASS <= value <= DIV_PASS + 31:", "            if not DIV_PASS < value <= DIV_PASS + 31:", 1),
    "div_reset": ("DIV_RESET = DIV_PASS + 16", "DIV_RESET = DIV_PASS + 8", 1),
    "div_no_frequency_update": ("            self._div = value\n            self._apply_frequency()\n", "            self._div = value\n", 1),
    "div_invalid_value": ("DIV_INVALID = DIV_PASS + 31", "DIV_INVALID = DIV_PASS + 30", 1),
    "frequency_nominal_div": ("NOMINAL_DIV = 16.0", "NOMINAL_DIV = 8.0", 1),
    "frequency_nominal": ("NOMINAL_HZ = 6.5e6", "NOMINAL_HZ = 6.0e6", 1),
    # -- PHASE
    "phase_reset": ("PHASE_RESET = 0x8", "PHASE_RESET = 0x0", 1),
    "phase_bad_password_keeps_shift": ("                enable, shift = 1, 0\n", "                enable = 1\n", 1),
    "phase_bad_password_keeps_enable": ("                enable, shift = 1, 0\n", "                shift = 0\n", 1),
    "phase_bad_password_silent": ('                self._bad_write(f"Invalid PHASE password written: 0x{(value >> 4) & 0xFF:x}")\n', "                pass\n", 1),
    "phase_passwd_value": ("PHASE_PASSWD = 0xAA", "PHASE_PASSWD = 0xA5", 1),
    "phase_flip_dropped": ("(value & 4) | shift", "shift", 1),
    "phase_passwd_not_stored": ("            self._phase = (value & 0xFF0) | (enable << 3) | (value & 4) | shift", "            self._phase = (enable << 3) | (value & 4) | shift", 1),
    # -- STATUS
    "status_enabled_missing": ("STATUS_STABLE | STATUS_DIV_RUNNING | STATUS_ENABLED if self._enabled else 0", "STATUS_STABLE | STATUS_DIV_RUNNING if self._enabled else 0", 1),
    "status_stable_missing": ("STATUS_STABLE | STATUS_DIV_RUNNING | STATUS_ENABLED if self._enabled else 0", "STATUS_DIV_RUNNING | STATUS_ENABLED if self._enabled else 0", 1),
    "status_div_running_missing": ("STATUS_STABLE | STATUS_DIV_RUNNING | STATUS_ENABLED if self._enabled else 0", "STATUS_STABLE | STATUS_ENABLED if self._enabled else 0", 1),
    "status_badwrite_sticky": ("                self._badwrite = False  # write 1 to clear\n", "                pass\n", 1),
    "status_badwrite_any_write": ("            if value & STATUS_BADWRITE:\n                self._badwrite = False", "            if True:\n                self._badwrite = False", 1),
    "status_badwrite_bit": ("STATUS_BADWRITE = 0x01000000", "STATUS_BADWRITE = 0x02000000", 1),
    "status_enabled_always": ("running = STATUS_STABLE | STATUS_DIV_RUNNING | STATUS_ENABLED if self._enabled else 0", "running = STATUS_STABLE | STATUS_DIV_RUNNING | STATUS_ENABLED", 1),
    # -- RANDOMBIT
    "randombit_holds_never": ("            if self._enabled:\n                self._last_bit = self.random_bit() & 1\n", "            self._last_bit = self.random_bit() & 1\n", 1),
    "randombit_unmasked": ("self._last_bit = self.random_bit() & 1", "self._last_bit = self.random_bit()", 1),
    "randombit_reset": ("        self._last_bit = 1  # RANDOMBIT resets to 1", "        self._last_bit = 0", 1),
    "randombit_write_stored": ("        elif offset == ROSC_RANDOMBIT:\n            return  # read only\n", "        elif offset == ROSC_RANDOMBIT:\n            self._last_bit = value & 1\n", 1),
    # -- COUNT
    "count_mask": ("COUNT_MASK = 0xFF", "COUNT_MASK = 0xFFF", 1),
    "count_no_epoch": ("            self._count = value & COUNT_MASK  # \"To start the counter write a non-zero value\"\n            self._count_epoch = self.rp2040.clock.ticks\n", "            self._count = value & COUNT_MASK\n", 1),
    "count_runs_disabled": ("        if self._count and self._enabled:", "        if self._count:", 1),
    "count_not_floored": ("            done = int(elapsed * self._hz / 1e9)", "            done = round(elapsed * self._hz / 1e9)", 1),
    "count_wraps": ("            return max(self._count - done, 0)", "            return (self._count - done) & 0xFF", 1),
    "count_uses_nominal": ("            done = int(elapsed * self._hz / 1e9)", "            done = int(elapsed * NOMINAL_HZ / 1e9)", 1),
    "count_no_rebase_on_frequency": ("        if frequency != self._hz:\n            self._rebase_count()\n            self._hz", "        if frequency != self._hz:\n            self._hz", 1),
    "count_reset_kept": ("        self._count = 0\n        self._count_epoch = 0  # in ticks of 1/16 ns\n", "", 1),
    "rebase_epoch_missing": ("        self._count = self._count_now()\n        self._count_epoch = self.rp2040.clock.ticks", "        self._count = self._count_now()", 1),
    # -- the chip's clock tree
    "frequency_not_told": ("            self._hz = frequency\n            self.rp2040.rosc_freq = frequency\n            self.rp2040.update_clocks()\n", "            self._hz = frequency\n", 1),
    "clocks_not_updated": ("            self.rp2040.rosc_freq = frequency\n            self.rp2040.update_clocks()\n", "            self.rp2040.rosc_freq = frequency\n", 1),
    "reset_no_frequency": ("        self._load_power_on_state()\n        self._apply_frequency()\n", "        self._load_power_on_state()\n", 1),
    "reset_keeps_badwrite": ("        self._badwrite = False\n        self._enabled = True", "        self._enabled = True", 1),
    "reset_disabled": ("        self._enabled = True  # \"started automatically during RP2040 power up\"", "        self._enabled = False", 1),
    "reset_div_kept": ("        self._div = DIV_RESET\n", "", 1),
}  # fmt: skip

MUTANTS = tuple(MUTATIONS)


def _mutant_module(name: str) -> types.ModuleType:
    old, new, count = MUTATIONS[name]
    source = inspect.getsource(P)
    assert source.count(old) == count, f"{name}: {old!r} occurs {source.count(old)} times in _rosc.py, expected {count}"
    module = types.ModuleType(f"rp2040py.peripherals._rosc_mutant_{name}")
    exec(compile(source.replace(old, new), f"<_rosc mutant {name}>", "exec"), module.__dict__)  # noqa: S102 - the point of the exercise
    return module


def mutant_rig(name: str) -> Rig:
    """A rig whose ROSC is the pure-Python one with one expression changed, to prove the comparison sees a change of *logic*."""
    return Rig("mutant", _mutant_module(name).RPROSC)


# --- the stimulus ----------------------------------------------------------------------------------------------------------


def _value(r: random.Random, offset: int) -> int:
    roll = r.random()
    if offset == P.ROSC_CTRL:
        enable = r.choice((0xFAB, 0xFAB, 0xD1E, 0xD1E, 0, r.getrandbits(12)))
        freq_range = r.choice((*RANGES, *RANGES, r.getrandbits(12)))
        return (enable << 12) | freq_range | (r.getrandbits(8) << 24 if roll < 0.1 else 0)
    if offset in (P.ROSC_FREQA, P.ROSC_FREQB):
        passwd = 0x9696 if roll < 0.7 else r.getrandbits(16)
        return (passwd << 16) | r.getrandbits(16)
    if offset == P.ROSC_DORMANT:
        return r.choice((P.DORMANT_VALUE, P.WAKE_VALUE, P.WAKE_VALUE, 0, r.getrandbits(32)))
    if offset == P.ROSC_DIV:
        return (P.DIV_PASS + r.randrange(34)) if roll < 0.8 else r.getrandbits(32)
    if offset == P.ROSC_PHASE:
        return (
            ((0xAA if roll < 0.7 else r.getrandbits(8)) << 4)
            | r.getrandbits(4)
            | (r.getrandbits(20) << 12 if roll > 0.95 else 0)
        )
    if offset == P.ROSC_STATUS:
        return r.choice((0, P.STATUS_BADWRITE, 0xFFFFFFFF, r.getrandbits(32)))
    if offset == P.ROSC_COUNT:
        return r.choice((0, 1, 2, 10, 100, 255, 256, r.getrandbits(8), r.getrandbits(32)))
    return r.getrandbits(32)


def _count_scenario(r: random.Random) -> list[tuple]:
    """Start the counter, let time pass in a few pieces (changing the frequency or ENABLE halfway), read it back."""
    ops: list[tuple] = [("write", P.ROSC_COUNT, r.choice((20, 100, 255, 7)), 0)]
    for _ in range(r.randrange(2, 6)):
        ops.append(("tick", r.choice((100, 500, 1_000, 5_000, 20_000))))
        ops.append(("read", P.ROSC_COUNT, 0))
        if r.random() < 0.4:
            ops.append(("write", P.ROSC_DIV, P.DIV_PASS + r.choice((1, 2, 8, 16, 31, 0)), 0))
        if r.random() < 0.2:
            ops.append(("write", P.ROSC_CTRL, (r.choice((0xFAB, 0xD1E)) << 12) | r.choice(RANGES), 0))
    return ops


def generate(seed: int, steps: int) -> list[tuple]:
    r = random.Random(seed)
    ops: list[tuple] = []
    while len(ops) < steps:
        roll = r.random()
        if roll < 0.10:
            ops.extend(_count_scenario(r))
        elif roll < 0.50:
            offset = r.choice(WRITABLE)
            alias = ALIASES[0] if r.random() < 0.8 else r.choice(ALIASES[1:])
            ops.append(("write", offset, _value(r, offset), alias))
        elif roll < 0.54:
            ops.append(("write", r.choice(UNIMPLEMENTED), r.getrandbits(32), ALIASES[0]))
        elif roll < 0.74:
            offset = r.choice((*REGISTERS, P.ROSC_RANDOMBIT, P.ROSC_RANDOMBIT))
            ops.append(("read", offset, r.choice(ALIASES) if r.random() < 0.1 else ALIASES[0]))
        elif roll < 0.78:
            ops.append(("read", r.choice(UNIMPLEMENTED), ALIASES[0]))
        elif roll < 0.93:
            ops.append(("tick", r.choice(TICKS)))
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
            gc.collect()


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

    rosc = rig.chip.rosc
    for op in ops:
        before = len(rig.log)
        was_count = rosc.snapshot()["count_now"]
        _, error = _step(rig, op)
        if error is not None:
            count(f"raised.{error.split(':')[0]}")
        for event in rig.log[before:]:
            count(f"log.{event[0]}")
        if op[0] == "write" and op[3] == 0:
            offset, value = op[1], op[2]
            if offset == P.ROSC_CTRL:
                count("ctrl.disable" if ((value >> 12) & 0xFFF) == P.CTRL_ENABLE_DISABLE else "ctrl.write")
            if offset == P.ROSC_DIV and P.DIV_PASS <= (value & 0xFFF) <= P.DIV_PASS + 31:
                count("div.valid")
            if offset == P.ROSC_COUNT and (value & 0xFF):
                count("count.start")
        state = rosc.snapshot()
        if was_count and state["count_now"] == 0:
            count("count.reached_zero")
        if was_count and 0 < state["count_now"] < was_count:
            count("count.counting")
        if not state["enabled"]:
            count("state.disabled")
        if state["badwrite"]:
            count("state.badwrite")
        if state["hz"] != P.NOMINAL_HZ:
            count("state.retuned")
    return counts
