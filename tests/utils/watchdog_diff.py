"""A lockstep differential oracle for the WATCHDOG (docs/records/0096-cpp-mcu-core.md, "The small blocks"): the same generated stimulus drives two chips and everything observable is compared after every step.

One chip's block is the *pure-Python* reference (``peripherals/_watchdog.py``, built explicitly because the facade would hand out the native one), the other's is whatever the facade gives (the C++
block in a native build). The operations are reads and writes of **every register offset through the bus and its XOR/SET/CLR aliases**, of offsets that are no register (so the warnings are compared),
**simulated time** (``clock.tick()`` - the countdown's alarm fires inside it), ``reset()``, and the **reset handler** the device installs: a recording one, or one that raises (the contract of
``core_host.hpp``: a failing host call leaves exactly the state the reference's exception would have left). After each step the register file read through the bus, the block's attributes, the scratch
words, ``raw_write_value``, the clock (whether an alarm is scheduled and when), and the ordered, timestamped log of the warnings and of the handler's calls are compared; an exception on one side and not on
the other is a difference like any other.

Logic mutants of the reference (one subclass each) prove that a change of *logic* is seen, not only a perturbation of the candidate's state.
"""

import random
from collections.abc import Callable
from typing import Any

from rp2040py.peripherals import _watchdog as W
from rp2040py.rp2040 import RP2040

ALIAS_BASES = (0x0000, 0x1000, 0x2000, 0x3000)  # normal, XOR, SET, CLR
BASE = 0x40058000
REGISTERS = (W.CTRL, W.LOAD, W.REASON, *W.SCRATCH_REGS, W.TICK)
UNIMPLEMENTED = (0x30, 0x34, 0x40, 0xFFC)
ATTRS = (
    "_reason",
    "_enable",
    "_tick_enable",
    "_tick_cycles",
    "_pause_dbg0",
    "_pause_dbg1",
    "_pause_jtag",
    "scratch_data",
)
TIMES = (1, 500, 20_000, 400_000, 5_000_000, 60_000_000)  # ns; 1 us = 2 counts at the 2 MHz countdown


class Boom(Exception):
    """What a failing reset handler raises."""


def _freeze(value: Any) -> Any:
    return tuple(value) if hasattr(value, "__iter__") else value


class Rig:
    """One chip plus the log of every warning and every call of the reset handler."""

    def __init__(self, kind: str, factory: "Callable[..., Any] | None" = None) -> None:
        self.chip = RP2040()
        self.log: list[tuple] = []
        self.raising = False
        if kind == "pure":
            self._replace_the_block(W.RPWatchdog)
        elif kind == "mutant":
            assert factory is not None
            self._replace_the_block(factory)
        self._tap()
        self.peripheral.on_watchdog_trigger = self._handler

    def _replace_the_block(self, factory: Callable[..., Any]) -> None:
        self.chip.peripherals[BASE >> 12] = factory(self.chip, "WATCHDOG_BASE")

    def _tap(self) -> None:
        log, chip = self.log, self.chip
        for method in ("warning", "error", "info", "debug"):
            setattr(
                chip.logger,
                method,
                lambda name, message, _m=method: log.append(
                    ("log", float(chip.clock.nanos), _m, str(name), str(message))
                ),
            )

    def _handler(self) -> None:
        self.log.append(("trigger", float(self.chip.clock.nanos), self.raising))
        if self.raising:
            raise Boom("the reset handler failed")

    @property
    def peripheral(self) -> Any:
        return self.chip.peripherals[BASE >> 12]

    def apply(self, op: tuple) -> Any:
        kind = op[0]
        chip = self.chip
        if kind == "write":
            chip.write_uint32(BASE + op[3] + op[1], op[2] & 0xFFFFFFFF)
        elif kind == "read":
            return int(chip.read_uint32(BASE + op[2] + op[1]))
        elif kind == "tick":
            chip.clock.tick(op[1])
        elif kind == "reset":
            self.peripheral.reset()
        elif kind == "raising":
            self.raising = op[1]
        else:
            raise ValueError(f"unknown op {op!r}")
        return None

    def snapshot(self) -> dict[str, Any]:
        peripheral, clock = self.peripheral, self.chip.clock
        return {
            "regs": tuple(
                int(self.chip.read_uint32(BASE + offset)) for offset in (W.CTRL, W.REASON, *W.SCRATCH_REGS, W.TICK)
            ),
            "attrs": tuple(_freeze(getattr(peripheral, name)) for name in ATTRS),
            "raw": int(peripheral.raw_write_value),
            "alarm": (bool(clock.has_scheduled_alarm), float(clock.nanos_to_next_alarm)),
            "time": float(clock.nanos),
            "timer": (bool(peripheral.timer.enable), bool(peripheral.alarm.enable), int(peripheral.alarm.target)),
        }


# --- logic mutants of the reference: what the oracle must see ----------------------------------------------------------------

MUTANTS = (
    "load_mask_20_bits",
    "load_ignored",
    "countdown_once_per_tick",
    "alarm_target_one",
    "timeout_reason_force",
    "trigger_reason_timer",
    "trigger_no_reason",
    "trigger_after_enable",
    "enable_ignores_tick",
    "alarm_ignores_tick",
    "alarm_ignores_enable",
    "tick_cycles_not_stored",
    "tick_cycles_mask_wrong",
    "tick_running_always",
    "tick_enable_not_stored",
    "pause_not_stored",
    "pause_reset_0",
    "scratch_mask_16",
    "scratch_index_wrong",
    "reset_keeps_scratch",
    "reset_keeps_reason",
    "load_read_zero",
    "reason_write_warns",
    "unimplemented_read_zero",
)


def mutant_rig(name: str) -> Rig:
    """A rig whose block is the pure-Python one with one piece of its logic changed, to prove the comparison sees a change of *logic*."""
    base = W.RPWatchdog

    class Mutant(base):  # type: ignore[valid-type, misc]
        def __init__(self, rp2040: Any, nm: str) -> None:
            super().__init__(rp2040, nm)
            if name == "countdown_once_per_tick":
                self.timer.frequency = W.TICK_FREQUENCY / 2
            elif name == "alarm_target_one":
                self.alarm.target = 1
            elif name == "pause_reset_0":
                self._pause_dbg0 = False

        def read_uint32(self, offset: int) -> int:
            if name == "unimplemented_read_zero" and offset not in REGISTERS:
                return 0
            if name == "load_read_zero" and offset == W.LOAD:
                return 0
            if name == "tick_running_always" and offset == W.TICK:
                return super().read_uint32(offset) | W.RUNNING
            return super().read_uint32(offset)

        def write_uint32(self, offset: int, value: int) -> None:
            value &= 0xFFFFFFFF
            if offset == W.REASON and name == "reason_write_warns":
                base.__mro__[1].write_uint32(self, offset, value)
                return
            if offset == W.LOAD:
                if name == "load_mask_20_bits":
                    self.timer.set(value & 0xFFFFF)
                    return
                if name == "load_ignored":
                    return
            if offset == W.CTRL:
                if name == "trigger_reason_timer" and value & W.TRIGGER:
                    self._reason = W.TIMER
                    self.on_watchdog_trigger()
                    self._finish_ctrl(value)
                    return
                if name == "trigger_no_reason" and value & W.TRIGGER:
                    self.on_watchdog_trigger()
                    self._finish_ctrl(value)
                    return
                if name == "trigger_after_enable" and value & W.TRIGGER:
                    self._finish_ctrl(value)
                    self._reason = W.FORCE
                    self.on_watchdog_trigger()
                    return
                if name == "pause_not_stored":
                    super().write_uint32(offset, value)
                    self._pause_dbg1 = True
                    return
                if name in ("enable_ignores_tick", "alarm_ignores_tick", "alarm_ignores_enable"):
                    super().write_uint32(offset, value)
                    self._skew()
                    return
            if offset == W.TICK:
                if name == "tick_cycles_not_stored":
                    super().write_uint32(offset, value)
                    self._tick_cycles = 0
                    return
                if name == "tick_cycles_mask_wrong":
                    super().write_uint32(offset, value)
                    self._tick_cycles = value & 0xFF
                    return
                if name == "tick_enable_not_stored":
                    enable = self._tick_enable
                    super().write_uint32(offset, value)
                    self._tick_enable = enable
                    return
                if name in ("enable_ignores_tick", "alarm_ignores_tick"):
                    super().write_uint32(offset, value)
                    self._skew()
                    return
            if offset in W.SCRATCH_REGS:
                if name == "scratch_mask_16":
                    self.scratch_data[(offset - W.SCRATCH0) >> 2] = value & 0xFFFF
                    return
                if name == "scratch_index_wrong" and offset == W.SCRATCH7:
                    self.scratch_data[6] = value
                    return
            super().write_uint32(offset, value)

        def _finish_ctrl(self, value: int) -> None:
            self._enable = bool(value & W.ENABLE)
            self.timer.enable = self._enable and self._tick_enable
            self.alarm.enable = self._enable and self._tick_enable
            self._pause_dbg0 = bool(value & W.PAUSE_DBG0)
            self._pause_dbg1 = bool(value & W.PAUSE_DBG1)
            self._pause_jtag = bool(value & W.PAUSE_JTAG)

        def _skew(self) -> None:
            if name == "enable_ignores_tick":
                self.timer.enable = self._enable
            elif name == "alarm_ignores_tick":
                self.alarm.enable = self._enable
            elif name == "alarm_ignores_enable":
                self.alarm.enable = self._tick_enable

        def reset(self) -> None:
            kept_scratch, kept_reason = list(self.scratch_data), self._reason
            super().reset()
            if name == "reset_keeps_scratch":
                self.scratch_data = kept_scratch
            elif name == "reset_keeps_reason":
                self._reason = kept_reason

    if name == "timeout_reason_force":
        # the alarm callback of the reference sets TIMER; this mutant sets FORCE
        class Mutant(Mutant):  # type: ignore[no-redef]
            def __init__(self, rp2040: Any, nm: str) -> None:
                super().__init__(rp2040, nm)

                def on_alarm() -> None:
                    self._reason = W.FORCE
                    self.on_watchdog_trigger()

                self.alarm.callback = on_alarm

    return Rig("mutant", Mutant)


# --- the stimulus ----------------------------------------------------------------------------------------------------------


def _value(r: random.Random, offset: int) -> int:
    if offset == W.CTRL:
        enable = W.ENABLE if r.random() < 0.6 else 0
        pauses = r.choice((0, W.PAUSE_DBG0, W.PAUSE_DBG1, W.PAUSE_JTAG, W.PAUSE_DBG0 | W.PAUSE_DBG1 | W.PAUSE_JTAG))
        trigger = W.TRIGGER if r.random() < 0.2 else 0
        return enable | pauses | trigger | (r.getrandbits(24) if r.random() < 0.2 else 0)
    if offset == W.LOAD:
        return r.choice((0, 1, 200, 2000, 20_000, 0xFFFFFF, 0x1000000, 0xFFFFFFFF, r.randrange(1, 100_000)))
    if offset == W.TICK:
        return r.choice(
            (0, W.TICK_ENABLE, 12 | W.TICK_ENABLE, 12, 0x1FF | W.TICK_ENABLE, 0xFFFFFFFF, r.getrandbits(32))
        )
    return r.choice((0, 1, 2, 0xFF, 0x6AB73121, 0xFFFFFFFF, r.getrandbits(32)))


def generate(seed: int, steps: int) -> list[tuple]:
    r = random.Random(seed)
    ops: list[tuple] = [("write", W.TICK, 12 | W.TICK_ENABLE, 0)]  # the SDK's watchdog_start_tick()
    while len(ops) < steps:
        roll = r.random()
        alias = 0 if r.random() < 0.6 else r.choice(ALIAS_BASES[1:])
        if roll < 0.15:
            ops.append(("read", r.choice(REGISTERS), alias))
        elif roll < 0.20:
            ops.append(("read", r.choice(UNIMPLEMENTED), alias if r.random() < 0.5 else 0))
        elif roll < 0.55:
            offset = r.choice(REGISTERS)
            ops.append(("write", offset, _value(r, offset), alias))
        elif roll < 0.60:
            ops.append(("write", r.choice(UNIMPLEMENTED), r.getrandbits(32), alias if r.random() < 0.5 else 0))
        elif roll < 0.85:
            ops.append(("tick", r.choice(TIMES)))
        elif roll < 0.92:
            ops.append(("reset",))
        else:
            ops.append(("raising", r.random() < 0.55))
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
    ops: list[tuple],
    *,
    reference: "Callable[[], Rig] | None" = None,
    candidate: "Callable[[], Rig] | None" = None,
    perturb: "Callable[[Rig, int], None] | None" = None,
) -> "Divergence | None":
    """Runs `ops` on both rigs and returns the first difference, or None. `perturb(candidate, step)` lets a test damage the candidate to prove the oracle sees it."""
    a = reference() if reference else Rig("pure")
    b = candidate() if candidate else Rig("default")
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


def coverage(ops: list[tuple]) -> dict[str, int]:
    """What the run exercised, measured on the reference: the counts a test asserts so that a green run means something."""
    rig = Rig("pure")
    counts: dict[str, int] = {}

    def count(name: str) -> None:
        counts[name] = counts.get(name, 0) + 1

    for op in ops:
        before_log = len(rig.log)
        if op[0] == "read":
            count("read.reg" if op[1] in REGISTERS else "read.unimplemented")
            if op[2]:
                count("read.alias")
        elif op[0] == "write":
            count("write.reg" if op[1] in REGISTERS else "write.unimplemented")
            if op[3]:
                count("write.alias")
        else:
            count(op[0])
        failed = _step(rig, op)[1] is not None
        for entry in rig.log[before_log:]:
            if entry[0] == "trigger":
                count("trigger.raising" if entry[2] else "trigger.ok")
                count("trigger.by_timeout" if op[0] == "tick" else "trigger.by_ctrl")
            else:
                count("log.entries")
        if failed:
            count("exception")
        if rig.peripheral.alarm.enable:
            count("state.armed")
    return counts
